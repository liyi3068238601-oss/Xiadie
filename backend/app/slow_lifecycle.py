"""Episode/Saga 慢生命周期：确定性评估、来源保护与独立预算。"""
from __future__ import annotations

import json

from . import db, saga_lifecycle, saga_summary, sagas

POLICY_VERSION = "slow-lifecycle-v1"
EPISODE_MATURITY_DAYS = 180
EPISODE_ARCHIVE_DAYS = 180
SAGA_ARCHIVE_DAYS = 365
RECALL_PROTECTION_DAYS = 180
SIGNIFICANCE_PROTECTION = 8
EPISODE_BUDGET = 10
SAGA_BUDGET = 10


class SlowLifecycleError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def process_batch(*, now: float | None = None) -> dict:
    """独立预算处理到期 Episode 与 completed Saga；单条失败不产生半状态。"""
    at = float(db.now() if now is None else now)
    episode_ids = _due_episode_ids(at, EPISODE_BUDGET)
    saga_ids = _due_saga_ids(at, SAGA_BUDGET)
    episode_changed = saga_changed = conflicts = 0
    for episode_id in episode_ids:
        try:
            episode_changed += int(assess_episode(episode_id, now=at)["changed"])
        except SlowLifecycleError as exc:
            if exc.code in {"missing", "revision_conflict"}:
                conflicts += 1
            else:
                raise
    for saga_id in saga_ids:
        try:
            saga_changed += int(assess_saga(saga_id, now=at)["changed"])
        except SlowLifecycleError as exc:
            if exc.code in {"missing", "revision_conflict"}:
                conflicts += 1
            else:
                raise
    return {
        "episode_scanned": len(episode_ids), "episode_changed": episode_changed,
        "saga_scanned": len(saga_ids), "saga_changed": saga_changed,
        "conflict_count": conflicts, "model_calls_used": 0,
    }


def assess_episode(episode_id: str, *, now: float | None = None) -> dict:
    at = float(db.now() if now is None else now)
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM memory_episodes WHERE id=?", (episode_id,)).fetchone()
        if not row:
            raise SlowLifecycleError("missing", "Episode 不存在")
        snapshot = dict(row)
        target = None
        reason = "no_transition"
        protected = _episode_protection(conn, snapshot, at)
        if snapshot["status"] == "active":
            reference = max(float(snapshot["end_at"]), float(snapshot["updated_at"]))
            if at - reference < EPISODE_MATURITY_DAYS * 86_400:
                reason = "maturity_not_due"
            elif protected:
                reason = protected[0]
            else:
                target, reason = "completed", "episode_matured"
        elif snapshot["status"] == "completed":
            reference = float(snapshot.get("completed_at") or snapshot["updated_at"])
            if at - reference < EPISODE_ARCHIVE_DAYS * 86_400:
                reason = "archive_not_due"
            elif protected:
                reason = protected[0]
            else:
                target, reason = "archived", "episode_archive_due"
        if target:
            changed = _transition_episode_locked(
                conn, snapshot, target, reason_code=reason, source="archivist", now=at
            )
            conn.commit()
            return {"changed": True, "episode": changed, "reason_code": reason,
                    "protection_reasons": protected}
        conn.execute(
            "UPDATE memory_episodes SET last_lifecycle_evaluated_at=? WHERE id=?",
            (at, episode_id),
        )
        conn.commit()
        return {"changed": False, "episode": snapshot, "reason_code": reason,
                "protection_reasons": protected}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def transition_episode(
    episode_id: str, target_status: str, *, trigger: str, reason: str = "",
    expected_revision: int | None = None, now: float | None = None,
) -> dict:
    sources = {"user": "user", "new_evidence": "new_evidence"}
    if trigger not in sources:
        raise SlowLifecycleError("trigger_invalid", "Episode 生命周期来源无效")
    if target_status not in {"active", "tombstone"}:
        raise SlowLifecycleError("target_invalid", "用户接口只允许恢复或删除 Episode")
    at = float(db.now() if now is None else now)
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM memory_episodes WHERE id=?", (episode_id,)).fetchone()
        if not row:
            raise SlowLifecycleError("missing", "Episode 不存在")
        snapshot = dict(row)
        if expected_revision is not None and snapshot["lifecycle_revision"] != expected_revision:
            raise SlowLifecycleError("revision_conflict", "Episode 已变化，请刷新后重试")
        if snapshot["status"] == "tombstone":
            raise SlowLifecycleError("tombstone_terminal", "已删除 Episode 不可恢复")
        if target_status == "tombstone" and trigger != "user":
            raise SlowLifecycleError("automatic_tombstone_forbidden", "自动任务不能删除 Episode")
        if target_status == "tombstone" and not reason.strip():
            raise SlowLifecycleError("reason_required", "删除 Episode 必须说明原因")
        if snapshot["status"] == target_status:
            conn.commit()
            return snapshot
        changed = _transition_episode_locked(
            conn, snapshot, target_status,
            reason_code=("episode_reactivated_by_new_evidence" if trigger == "new_evidence"
                         else "episode_reactivated_by_user" if target_status == "active"
                         else "episode_deleted_by_user"),
            source=sources[trigger], now=at,
            metadata={"reason": reason.strip()} if reason.strip() else None,
        )
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def assess_saga(saga_id: str, *, now: float | None = None) -> dict:
    at = float(db.now() if now is None else now)
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM memory_sagas WHERE id=?", (saga_id,)).fetchone()
        if not row:
            raise SlowLifecycleError("missing", "Saga 不存在")
        snapshot = dict(row)
        reason = "not_completed"
        protected: list[str] = []
        if snapshot["status"] == "completed":
            completed_at = float(snapshot.get("completed_at") or snapshot["updated_at"])
            if at - completed_at < SAGA_ARCHIVE_DAYS * 86_400:
                reason = "saga_archive_not_due"
            elif snapshot.get("completion_revision") is None or int(
                snapshot["completion_revision"]
            ) != int(snapshot["revision"]):
                reason = "saga_revision_changed_since_completion"
            elif float(snapshot["updated_at"]) > completed_at:
                reason = "saga_changed_since_completion"
            else:
                protected = _saga_protection(conn, snapshot, at)
                reason = protected[0] if protected else "saga_archive_due"
        if reason == "saga_archive_due":
            sources = _active_saga_sources(conn, saga_id)
            try:
                saga_summary.validate_source_chain(sources)
            except saga_summary.SagaSummaryValidationError as exc:
                raise SlowLifecycleError("source_chain_invalid", str(exc)) from exc
            if saga_summary.source_hash(sources) != snapshot["source_hash"]:
                raise SlowLifecycleError("saga_source_hash_mismatch", "Saga 来源链已变化")
            revision = int(snapshot["revision"])
            conn.execute(
                "UPDATE memory_sagas SET status='archived',archived_at=?,updated_at=?,"
                "last_lifecycle_evaluated_at=?,lifecycle_policy_version=?,revision=revision+1"
                " WHERE id=? AND status='completed' AND revision=?",
                (at, at, at, POLICY_VERSION, saga_id, revision),
            )
            if conn.execute("SELECT changes() changed").fetchone()["changed"] != 1:
                raise SlowLifecycleError("revision_conflict", "Saga 已变化，请稍后重试")
            after = dict(conn.execute("SELECT * FROM memory_sagas WHERE id=?", (saga_id,)).fetchone())
            saga_lifecycle._event(
                conn, saga_id, "archived", snapshot, after, reason, "archivist",
                {"source_episode_count": len(sources)}, at,
            )
            conn.commit()
            return {"changed": True, "saga": after, "reason_code": reason,
                    "protection_reasons": protected}
        conn.execute(
            "UPDATE memory_sagas SET last_lifecycle_evaluated_at=? WHERE id=?", (at, saga_id)
        )
        conn.commit()
        return {"changed": False, "saga": snapshot, "reason_code": reason,
                "protection_reasons": protected}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _episode_protection(conn, episode: dict, now: float) -> list[str]:
    reasons = []
    if int(episode["significance"]) >= SIGNIFICANCE_PROTECTION:
        reasons.append("high_significance")
    recent = conn.execute(
        "SELECT MAX(f.last_recalled_at) recalled FROM memory_episode_fragments ef"
        " JOIN memory_fragments f ON f.id=ef.fragment_id WHERE ef.episode_id=?",
        (episode["id"],),
    ).fetchone()["recalled"]
    if recent is not None and now - float(recent) < RECALL_PROTECTION_DAYS * 86_400:
        reasons.append("recent_source_recall")
    if conn.execute(
        "SELECT 1 FROM memory_saga_episodes se JOIN memory_sagas s ON s.id=se.saga_id"
        " WHERE se.episode_id=? AND se.removed_at IS NULL AND s.status='active' LIMIT 1",
        (episode["id"],),
    ).fetchone():
        reasons.append("active_saga_source")
    return reasons


def _saga_protection(conn, saga: dict, now: float) -> list[str]:
    reasons = []
    if int(saga["significance"]) >= SIGNIFICANCE_PROTECTION:
        reasons.append("high_significance")
    recent = conn.execute(
        "SELECT MAX(f.last_recalled_at) recalled FROM memory_saga_episodes se"
        " JOIN memory_episode_fragments ef ON ef.episode_id=se.episode_id"
        " JOIN memory_fragments f ON f.id=ef.fragment_id"
        " WHERE se.saga_id=? AND se.removed_at IS NULL", (saga["id"],),
    ).fetchone()["recalled"]
    if recent is not None and now - float(recent) < RECALL_PROTECTION_DAYS * 86_400:
        reasons.append("recent_source_recall")
    if conn.execute(
        "SELECT 1 FROM saga_group_candidates WHERE target_saga_id=?"
        " AND application_mode='append' AND status IN ('observing','qualified') LIMIT 1",
        (saga["id"],),
    ).fetchone():
        reasons.append("pending_append_candidate")
    return reasons


def _active_saga_sources(conn, saga_id: str) -> list[dict]:
    ids = [row["episode_id"] for row in conn.execute(
        "SELECT episode_id FROM memory_saga_episodes WHERE saga_id=? AND removed_at IS NULL"
        " ORDER BY position", (saga_id,),
    ).fetchall()]
    return sagas._load_candidate_episodes(conn, ids)


def _transition_episode_locked(
    conn, snapshot: dict, target: str, *, reason_code: str, source: str, now: float,
    metadata: dict | None = None,
) -> dict:
    current = snapshot["status"]
    allowed = {
        ("active", "completed"), ("completed", "archived"),
        ("completed", "active"), ("archived", "active"),
        ("active", "tombstone"), ("completed", "tombstone"), ("archived", "tombstone"),
    }
    if (current, target) not in allowed:
        raise SlowLifecycleError("transition_invalid", f"不允许 {current} → {target}")
    revision = int(snapshot["lifecycle_revision"]) + 1
    completed_at = now if target == "completed" else (
        None if target == "active" else snapshot.get("completed_at")
    )
    archived_at = now if target == "archived" else (
        None if target == "active" else snapshot.get("archived_at")
    )
    tombstoned_at = now if target == "tombstone" else snapshot.get("tombstoned_at")
    conn.execute(
        "UPDATE memory_episodes SET status=?,completed_at=?,archived_at=?,tombstoned_at=?,"
        "lifecycle_policy_version=?,lifecycle_revision=?,last_lifecycle_evaluated_at=?,updated_at=?"
        " WHERE id=? AND lifecycle_revision=?",
        (target, completed_at, archived_at, tombstoned_at, POLICY_VERSION, revision, now, now,
         snapshot["id"], snapshot["lifecycle_revision"]),
    )
    if conn.execute("SELECT changes() changed").fetchone()["changed"] != 1:
        raise SlowLifecycleError("revision_conflict", "Episode 已变化，请稍后重试")
    conn.execute(
        "INSERT INTO memory_episode_lifecycle_events("
        "id,episode_id,revision,from_status,to_status,reason_code,source,policy_version,"
        "metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (db.new_id(), snapshot["id"], revision, current, target, reason_code, source,
         POLICY_VERSION, json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")), now),
    )
    return dict(conn.execute("SELECT * FROM memory_episodes WHERE id=?", (snapshot["id"],)).fetchone())


def _due_episode_ids(now: float, limit: int) -> list[str]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id FROM memory_episodes WHERE"
            " (status='active' AND MAX(end_at,updated_at)<=?)"
            " OR (status='completed' AND COALESCE(completed_at,updated_at)<=?)"
            " ORDER BY COALESCE(last_lifecycle_evaluated_at,0),end_at,id LIMIT ?",
            (now - EPISODE_MATURITY_DAYS * 86_400,
             now - EPISODE_ARCHIVE_DAYS * 86_400, limit),
        ).fetchall()
        return [row["id"] for row in rows]
    finally:
        conn.close()


def _due_saga_ids(now: float, limit: int) -> list[str]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id FROM memory_sagas WHERE status='completed'"
            " AND COALESCE(completed_at,updated_at)<=?"
            " ORDER BY COALESCE(last_lifecycle_evaluated_at,0),completed_at,id LIMIT ?",
            (now - SAGA_ARCHIVE_DAYS * 86_400, limit),
        ).fetchall()
        return [row["id"] for row in rows]
    finally:
        conn.close()
