"""Saga 候选预筛与可解释评分。

D.2 只读取正式 Episode 并保存最小候选账本，不创建或修改正式 Saga。
所有门槛均为本地确定性规则，后续模型摘要不能绕过这些门槛。
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re

from . import db, saga_summary

MIN_GROUP_SIZE = 2
MAX_GROUP_SIZE = 12
MAX_SPAN_SECONDS = 180 * 24 * 60 * 60
MAX_ADJACENT_GAP_SECONDS = 60 * 24 * 60 * 60
CANDIDATE_TTL_SECONDS = 21 * 24 * 60 * 60
GROUP_THRESHOLD = 0.52
SHARED_ENTITY_TEXT_GATE = 0.10
TEXT_ONLY_GATE = 0.48
POLICY_VERSION = "saga-group-v1"
ENTITY_WEIGHT = 0.30
TEXT_WEIGHT = 0.35
TIME_WEIGHT = 0.15
COHERENCE_WEIGHT = 0.20
EPISODE_SCAN_LIMIT = 100
APPLICATION_BATCH_LIMIT = 5


class SagaApplyError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def generate_candidates(*, now: float | None = None) -> list[dict]:
    """评估当前正式 Episode；返回本轮新晋级的 Saga 候选。"""
    conn = db.connect()
    try:
        timestamp = db.now() if now is None else float(now)
        _expire_candidates(conn, timestamp)
        episodes = _load_eligible_episodes(conn)
        entity_map = _load_episode_entities(conn, [item["id"] for item in episodes])
        proposals = _build_proposals(episodes, entity_map)
        qualified: list[dict] = []
        used_episode_ids: set[str] = set()
        for proposal in sorted(
            proposals,
            key=lambda item: (
                -item["scores"]["total"], -len(item["episodes"]),
                item["episodes"][0]["start_at"], item["fingerprint"],
            ),
        ):
            episode_ids = {item["id"] for item in proposal["episodes"]}
            if episode_ids & used_episode_ids:
                continue
            candidate = _record_proposal(conn, proposal, timestamp)
            if candidate["status"] == "qualified":
                # A candidate that has already produced a Saga is historical evidence,
                # not a competitor for this generation pass. Reserving its Episodes
                # here would hide a longer proposal that appends new Episodes to that
                # Saga (for example, {A, B} would block {A, B, C}).
                if candidate.get("promoted_saga_id") is None:
                    used_episode_ids.update(episode_ids)
                if candidate.pop("newly_qualified", False):
                    qualified.append(candidate)
        conn.commit()
        return qualified
    finally:
        conn.close()


def list_group_candidates(status: str = "observing") -> list[dict]:
    if status not in {"observing", "qualified", "conflicted", "expired"}:
        raise ValueError("非法的 Saga 分组候选状态")
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM saga_group_candidates WHERE status=?"
            " ORDER BY last_evaluated_at DESC,id",
            (status,),
        ).fetchall()
        return [_candidate_row(row) for row in rows]
    finally:
        conn.close()


def get_group_candidate(candidate_id: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM saga_group_candidates WHERE id=?", (candidate_id,)
        ).fetchone()
        if not row:
            return None
        result = _candidate_row(row)
        result["episodes"] = _load_candidate_episodes(conn, result["episode_ids"])
        result["shared_entity_names"] = _shared_entity_names(
            conn, result["shared_entity_ids"]
        )
        result["current_source_hash"] = saga_summary.source_hash(result["episodes"])
        return result
    finally:
        conn.close()


def qualified_candidates(limit: int = 20) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id FROM saga_group_candidates WHERE status='qualified'"
            " AND promoted_saga_id IS NULL AND application_attempt_count<3"
            " ORDER BY total_score DESC,first_seen_at,id LIMIT ?",
            (max(1, min(int(limit), 20)),),
        ).fetchall()
    finally:
        conn.close()
    return [item for row in rows if (item := get_group_candidate(row["id"]))]


def apply_model_summary(
    candidate_id: str, raw: str | dict, *, provider_id: str, model: str,
    prompt_tokens: int | None, completion_tokens: int | None,
    repair_attempted: bool, expected_source_hash: str,
) -> dict | None:
    """事务内重读 Episode→Fragment 来源；旧模型结果不能覆盖新来源。"""
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM saga_group_candidates WHERE id=? AND status='qualified'",
            (candidate_id,),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        before = _candidate_row(row)
        sources = _load_candidate_episodes(conn, before["episode_ids"])
        current_hash = saga_summary.source_hash(sources)
        if current_hash != expected_source_hash:
            conn.rollback()
            raise saga_summary.SagaSummaryValidationError(
                "summary_source_changed", "模型调用期间 Saga 来源发生变化"
            )
        validated = saga_summary.parse_and_validate(
            raw, episodes=sources,
            entity_names=_shared_entity_names(conn, before["shared_entity_ids"]),
        )
        now = db.now()
        conn.execute(
            "UPDATE saga_group_candidates SET title=?,summary=?,theme=?,current_stage=?,"
            "lifecycle_signal=?,summary_status='model_validated',summary_protocol_version=?,"
            "summary_provider_id=?,summary_model=?,summary_evidence_episode_ids_json=?,"
            "completion_evidence_episode_ids_json=?,summary_warnings_json=?,"
            "summary_error_code=NULL,summary_source_hash=?,summary_prompt_tokens=?,"
            "summary_completion_tokens=?,summary_repair_attempted=?,last_evaluated_at=? WHERE id=?",
            (
                validated["title"], validated["summary"], validated["theme"],
                validated["current_stage"], validated["lifecycle_signal"],
                validated["protocol_version"], provider_id, model,
                json.dumps(validated["evidence_episode_ids"]),
                json.dumps(validated["completion_evidence_episode_ids"]),
                json.dumps(validated["warnings"], ensure_ascii=False), validated["source_hash"],
                prompt_tokens, completion_tokens, 1 if repair_attempted else 0, now, candidate_id,
            ),
        )
        _summary_event(
            conn, candidate_id, "summary_validated", None,
            {
                "provider_id": provider_id, "model": model,
                "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                "repair_attempted": bool(repair_attempted), "source_hash": current_hash,
            }, now,
        )
        conn.commit()
        return get_group_candidate(candidate_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_summary_fallback(
    candidate_id: str, error_code: str, *, provider_id: str | None = None,
    model: str | None = None, prompt_tokens: int | None = None,
    completion_tokens: int | None = None, repair_attempted: bool = False,
) -> dict | None:
    """永远从当前 Episode 来源重建回退摘要，不保留过期模型文本。"""
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM saga_group_candidates WHERE id=? AND status='qualified'",
            (candidate_id,),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        candidate = _candidate_row(row)
        sources = _load_candidate_episodes(conn, candidate["episode_ids"])
        fallback = saga_summary.extractive_fallback(
            episodes=sources,
            entity_names=_shared_entity_names(conn, candidate["shared_entity_ids"]),
        )
        warnings = [*fallback["warnings"], {"code": error_code}]
        now = db.now()
        conn.execute(
            "UPDATE saga_group_candidates SET title=?,summary=?,theme=?,current_stage=?,"
            "lifecycle_signal=?,summary_status='extractive_fallback',summary_protocol_version=?,"
            "summary_provider_id=?,summary_model=?,summary_evidence_episode_ids_json=?,"
            "completion_evidence_episode_ids_json=?,summary_warnings_json=?,summary_error_code=?,"
            "summary_source_hash=?,summary_prompt_tokens=?,summary_completion_tokens=?,"
            "summary_repair_attempted=?,last_evaluated_at=? WHERE id=?",
            (
                fallback["title"], fallback["summary"], fallback["theme"],
                fallback["current_stage"], fallback["lifecycle_signal"],
                fallback["protocol_version"], provider_id, model,
                json.dumps(fallback["evidence_episode_ids"]),
                json.dumps(fallback["completion_evidence_episode_ids"]),
                json.dumps(warnings, ensure_ascii=False), error_code, fallback["source_hash"],
                prompt_tokens, completion_tokens, 1 if repair_attempted else 0, now, candidate_id,
            ),
        )
        _summary_event(
            conn, candidate_id, "summary_fallback", error_code,
            {
                "provider_id": provider_id, "model": model,
                "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                "repair_attempted": bool(repair_attempted),
                "source_hash": fallback["source_hash"],
            }, now,
        )
        conn.commit()
        return get_group_candidate(candidate_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_summary_rejection(candidate_id: str, error_code: str) -> None:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute(
            "SELECT 1 FROM saga_group_candidates WHERE id=?", (candidate_id,)
        ).fetchone():
            now = db.now()
            conn.execute(
                "UPDATE saga_group_candidates SET summary_error_code=?,last_evaluated_at=?"
                " WHERE id=?",
                (error_code, now, candidate_id),
            )
            _summary_event(
                conn, candidate_id, "summary_rejected", error_code,
                {"raw_output_stored": False}, now,
            )
        conn.commit()
    finally:
        conn.close()


def list_summary_events(candidate_id: str) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM saga_candidate_summary_events WHERE candidate_id=?"
            " ORDER BY created_at,id", (candidate_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result.append(item)
        return result
    finally:
        conn.close()


def apply_candidates_for_run(run_id: str, candidate_ids: list[str]) -> list[dict]:
    """在一个短事务中创建/追加正式 Saga，并同步提交 run 终态。"""
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        run = conn.execute(
            "SELECT * FROM saga_consolidator_runs WHERE id=?", (run_id,)
        ).fetchone()
        if not run or run["status"] != "running":
            conn.rollback()
            raise SagaApplyError("application_run_not_running", "Saga 整理任务已不在运行状态")
        ordered_ids = list(dict.fromkeys(str(value) for value in candidate_ids if value))
        results = []
        for candidate_id in ordered_ids[:APPLICATION_BATCH_LIMIT]:
            row = conn.execute(
                "SELECT * FROM saga_group_candidates WHERE id=? AND status='qualified'",
                (candidate_id,),
            ).fetchone()
            if not row:
                continue
            candidate = _candidate_row(row)
            if candidate.get("promoted_saga_id"):
                continue
            results.append(_apply_candidate_locked(conn, candidate))
        now = db.now()
        result_ids = list(dict.fromkeys(item["id"] for item in results))
        status = "applied" if result_ids else "skipped"
        conn.execute(
            "UPDATE saga_consolidator_runs SET status=?,result_saga_ids_json=?,"
            "candidate_count=?,finished_at=?,updated_at=? WHERE id=?",
            (status, json.dumps(result_ids), len(ordered_ids), now, now, run_id),
        )
        conn.execute(
            "INSERT INTO settings(key,value) VALUES('last_saga_consolidator_run',?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(now),),
        )
        _run_event(
            conn, run_id, "processed", "running", status,
            "formal_sagas_applied" if result_ids else "no_eligible_group",
            {"candidate_count": len(ordered_ids), "saga_count": len(result_ids)}, now,
        )
        conn.commit()
        return results
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_application_failure(candidate_ids: list[str], error_code: str) -> None:
    ids = list(dict.fromkeys(candidate_ids))[:APPLICATION_BATCH_LIMIT]
    if not ids:
        return
    conn = db.connect()
    try:
        now = db.now()
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE saga_group_candidates SET application_attempt_count="
            f"application_attempt_count+1,application_error_code=?,last_application_at=?"
            f" WHERE id IN ({placeholders})",
            (error_code, now, *ids),
        )
        conn.commit()
    finally:
        conn.close()


def _apply_candidate_locked(conn, candidate: dict) -> dict:
    episode_ids = candidate["episode_ids"]
    if grouping_fingerprint(episode_ids) != candidate["grouping_fingerprint"]:
        raise SagaApplyError("application_fingerprint_changed", "Saga 候选指纹不匹配")
    if candidate.get("summary_status") not in {"model_validated", "extractive_fallback"}:
        raise SagaApplyError("application_summary_missing", "Saga 候选尚无安全摘要")
    sources = _load_candidate_episodes(conn, episode_ids)
    if len(sources) != len(episode_ids):
        raise SagaApplyError("application_source_missing", "Saga 来源 Episode 不完整")
    try:
        saga_summary.validate_source_chain(sources)
    except saga_summary.SagaSummaryValidationError as exc:
        raise SagaApplyError(f"application_{exc.code}", str(exc)) from exc
    current_hash = saga_summary.source_hash(sources)
    if current_hash != candidate.get("summary_source_hash"):
        raise SagaApplyError("application_source_changed", "Saga 来源在应用前发生变化")
    if candidate.get("lifecycle_signal") == "completed":
        from . import saga_lifecycle
        try:
            saga_lifecycle._validate_completion_evidence(
                episode_ids, sources,
                candidate.get("completion_evidence_episode_ids") or [],
            )
        except saga_lifecycle.SagaLifecycleError as exc:
            raise SagaApplyError(f"application_{exc.code}", str(exc)) from exc
    links = conn.execute(
        f"SELECT episode_id,saga_id FROM memory_saga_episodes WHERE removed_at IS NULL"
        f" AND episode_id IN ({','.join('?' for _ in episode_ids)}) ORDER BY episode_id",
        episode_ids,
    ).fetchall()
    now = db.now()
    if candidate.get("application_mode") == "append":
        target_id = candidate.get("target_saga_id")
        target = conn.execute(
            "SELECT * FROM memory_sagas WHERE id=? AND status IN ('active','completed')",
            (target_id,),
        ).fetchone()
        if not target:
            raise SagaApplyError("application_target_missing", "目标 Saga 不可追加")
        existing_rows = conn.execute(
            "SELECT episode_id,position FROM memory_saga_episodes"
            " WHERE saga_id=? AND removed_at IS NULL ORDER BY position", (target_id,),
        ).fetchall()
        existing_ids = [row["episode_id"] for row in existing_rows]
        existing_sources = _load_candidate_episodes(conn, existing_ids)
        if (
            len(existing_sources) != len(existing_ids)
            or saga_summary.source_hash(existing_sources) != target["source_hash"]
        ):
            raise SagaApplyError("application_target_source_changed", "目标 Saga 的旧来源已经变化")
        if not set(existing_ids) < set(episode_ids):
            raise SagaApplyError("application_append_sources_invalid", "追加来源集合不完整")
        if episode_ids[:len(existing_ids)] != existing_ids:
            raise SagaApplyError("application_append_order_changed", "追加来源不再是原 Saga 的有序后缀")
        if any(row["saga_id"] != target_id for row in links):
            raise SagaApplyError("application_cross_saga_conflict", "Episode 已属于其他 Saga")
        new_ids = [episode_id for episode_id in episode_ids if episode_id not in set(existing_ids)]
        for position, episode_id in enumerate(new_ids, start=len(existing_ids)):
            conn.execute(
                "INSERT INTO memory_saga_episodes(saga_id,episode_id,position,role,added_at)"
                " VALUES(?,?,?,'development',?)", (target_id, episode_id, position, now),
            )
        before = dict(target)
        conn.execute(
            "UPDATE memory_sagas SET title=?,summary=?,theme=?,current_stage=?,end_at=?,"
            "significance=?,confidence=?,grouping_fingerprint=?,policy_version=?,"
            "source_episode_ids_json=?,source_hash=?,summary_status=?,summary_protocol_version=?,"
            "summary_provider_id=?,summary_model=?,summary_evidence_json=?,updated_at=?,"
            "revision=revision+1 WHERE id=?",
            (
                candidate["title"], candidate["summary"], candidate["theme"],
                candidate["current_stage"], max(item["end_at"] for item in sources),
                _significance(sources), candidate["total_score"], candidate["grouping_fingerprint"],
                POLICY_VERSION, json.dumps(episode_ids), current_hash,
                candidate["summary_status"], candidate["summary_protocol_version"],
                candidate.get("summary_provider_id"), candidate.get("summary_model"),
                json.dumps(candidate["summary_evidence_episode_ids"]), now, target_id,
            ),
        )
        saga_id, action = target_id, "episode_appended"
        after = {"id": saga_id, "episode_ids": episode_ids, "source_hash": current_hash}
        _saga_event(conn, saga_id, action, before, after, "consolidator", now)
    else:
        if links:
            raise SagaApplyError("application_cross_saga_conflict", "Episode 已属于有效 Saga")
        saga_id = db.new_id()
        conn.execute(
            "INSERT INTO memory_sagas("
            "id,title,summary,theme,current_stage,start_at,end_at,significance,confidence,status,source,"
            "grouping_fingerprint,policy_version,source_episode_ids_json,source_hash,"
            "summary_status,summary_protocol_version,summary_provider_id,summary_model,"
            "summary_evidence_json,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?, 'active','automatic',?,?,?,?,?,?,?,?,?,?,?)",
            (
                saga_id, candidate["title"], candidate["summary"], candidate["theme"],
                candidate["current_stage"], min(item["start_at"] for item in sources),
                max(item["end_at"] for item in sources),
                _significance(sources), candidate["total_score"], candidate["grouping_fingerprint"],
                POLICY_VERSION, json.dumps(episode_ids), current_hash, candidate["summary_status"],
                candidate["summary_protocol_version"], candidate.get("summary_provider_id"),
                candidate.get("summary_model"),
                json.dumps(candidate["summary_evidence_episode_ids"]), now, now,
            ),
        )
        for position, episode_id in enumerate(episode_ids):
            role = "anchor" if position == 0 else "development"
            conn.execute(
                "INSERT INTO memory_saga_episodes(saga_id,episode_id,position,role,added_at)"
                " VALUES(?,?,?,?,?)", (saga_id, episode_id, position, role, now),
            )
        after = {"id": saga_id, "episode_ids": episode_ids, "source_hash": current_hash}
        _saga_event(conn, saga_id, "created", None, after, "consolidator", now)
    _sync_saga_entities(conn, saga_id, episode_ids, now)
    _apply_candidate_lifecycle_locked(
        conn, saga_id, candidate, previous_status=target["status"] if candidate.get("application_mode") == "append" else "active",
        new_episode_ids=new_ids if candidate.get("application_mode") == "append" else episode_ids,
        now=now,
    )
    conn.execute(
        "UPDATE saga_group_candidates SET promoted_saga_id=?,application_attempt_count="
        "application_attempt_count+1,application_error_code=NULL,last_application_at=? WHERE id=?",
        (saga_id, now, candidate["id"]),
    )
    row = conn.execute("SELECT * FROM memory_sagas WHERE id=?", (saga_id,)).fetchone()
    return dict(row)


def _apply_candidate_lifecycle_locked(
    conn, saga_id: str, candidate: dict, *, previous_status: str,
    new_episode_ids: list[str], now: float,
) -> None:
    """Apply only lifecycle signals already validated by saga-summary-v1."""
    from . import saga_lifecycle

    signal = candidate.get("lifecycle_signal") or "active"
    evidence_ids = candidate.get("completion_evidence_episode_ids") or []
    if previous_status == "completed" and signal == "active":
        before = {"status": "completed"}
        conn.execute(
            "UPDATE memory_sagas SET status='active',completion_reason='',"
            "completion_evidence_episode_ids_json='[]',lifecycle_policy_version=?,"
            "updated_at=?,revision=revision+1"
            " WHERE id=?", (saga_lifecycle.POLICY_VERSION, now, saga_id),
        )
        conn.execute(
            "UPDATE saga_relationship_delta_suggestions SET status='revoked',"
            "revocation_reason='saga_reactivated',revoked_at=?"
            " WHERE saga_id=? AND status='proposed'", (now, saga_id),
        )
        saga_lifecycle._event(
            conn, saga_id, "reactivated", before, {"status": "active"},
            "new_development_episode", "consolidator",
            {"evidence_episode_ids": new_episode_ids}, now,
        )
    elif signal == "completed" and evidence_ids:
        was_completed = previous_status == "completed"
        conn.execute(
            "UPDATE memory_sagas SET status='completed',completion_reason=?,completed_at=?,"
            "completion_evidence_episode_ids_json=?,lifecycle_policy_version=?,"
            "updated_at=?,revision=revision+1"
            " WHERE id=?",
            (
                "grounded_completion_evidence", now, json.dumps(evidence_ids),
                saga_lifecycle.POLICY_VERSION, now, saga_id,
            ),
        )
        if not was_completed:
            event_id = saga_lifecycle._event(
                conn, saga_id, "completed", {"status": "active"},
                {"status": "completed"}, "grounded_completion_evidence", "consolidator",
                {"evidence_episode_ids": evidence_ids}, now,
            )
            saga_lifecycle._relationship_suggestion(
                conn, saga_id, event_id, evidence_ids, _significance(
                    _load_candidate_episodes(conn, candidate["episode_ids"])
                ), now,
            )


def _sync_saga_entities(conn, saga_id: str, episode_ids: list[str], now: float) -> None:
    rows = conn.execute(
        f"SELECT DISTINCT ee.entity_id FROM memory_episode_entities ee"
        f" JOIN memory_entities e ON e.id=ee.entity_id AND e.status='active'"
        f" WHERE ee.episode_id IN ({','.join('?' for _ in episode_ids)})",
        episode_ids,
    ).fetchall()
    for row in rows:
        conn.execute(
            "INSERT INTO memory_saga_entities("
            "saga_id,entity_id,source,created_at,updated_at) VALUES(?,?,'episode_derived',?,?)"
            " ON CONFLICT(saga_id,entity_id) DO UPDATE SET updated_at=excluded.updated_at",
            (saga_id, row["entity_id"], now, now),
        )


def _significance(sources: list[dict]) -> int:
    value = round(sum(int(item.get("significance") or 4) for item in sources) / len(sources))
    return max(1, min(10, value))


def _saga_event(
    conn, saga_id: str, action: str, before: dict | None, after: dict,
    source: str, created_at: float,
) -> None:
    if action not in {"created", "episode_appended"}:
        raise ValueError("非法的 Saga 应用事件")
    conn.execute(
        "INSERT INTO memory_saga_events("
        "id,saga_id,action,before_json,after_json,reason_code,source,policy_version,created_at)"
        " VALUES(?,?,?,?,?,'qualified_group',?,?,?)",
        (
            db.new_id(), saga_id, action,
            json.dumps(before, ensure_ascii=False) if before is not None else None,
            json.dumps(after, ensure_ascii=False), source, POLICY_VERSION, created_at,
        ),
    )


def _run_event(
    conn, run_id: str, action: str, before_status: str | None, after_status: str,
    reason_code: str, metadata: dict, created_at: float,
) -> None:
    conn.execute(
        "INSERT INTO saga_consolidator_events("
        "id,run_id,action,before_status,after_status,reason_code,metadata_json,created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (
            db.new_id(), run_id, action, before_status, after_status, reason_code,
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), created_at,
        ),
    )


def assess_group(episodes: list[dict], entity_by_episode: dict[str, set[str]]) -> dict:
    """对一个有序 Episode 集合评分；不读写数据库。"""
    if not MIN_GROUP_SIZE <= len(episodes) <= MAX_GROUP_SIZE:
        raise ValueError("Saga 分组必须包含 2 到 12 个 Episode")
    ordered = sorted(episodes, key=lambda item: (item["start_at"], item["id"]))
    if len({item["id"] for item in ordered}) != len(ordered):
        raise ValueError("Saga 分组不能包含重复 Episode")
    if len({_local_date(item["start_at"]) for item in ordered}) < 2:
        raise ValueError("Saga 至少需要跨越两个自然日")
    span = max(item["end_at"] for item in ordered) - min(
        item["start_at"] for item in ordered
    )
    if span <= 0 or span > MAX_SPAN_SECONDS:
        raise ValueError("Saga 分组时间跨度必须在 180 天以内")
    gaps = [
        max(0.0, right["start_at"] - left["end_at"])
        for left, right in zip(ordered, ordered[1:])
    ]
    if gaps and max(gaps) > MAX_ADJACENT_GAP_SECONDS:
        raise ValueError("Saga 相邻 Episode 间隔不能超过 60 天")

    entity_sets = [entity_by_episode.get(item["id"], set()) for item in ordered]
    shared = set.intersection(*entity_sets) if entity_sets and all(entity_sets) else set()
    union = set.union(*entity_sets) if entity_sets else set()
    entity_score = len(shared) / len(union) if union else 0.0
    pair_similarities = [
        _text_similarity(_episode_text(left), _episode_text(right))
        for index, left in enumerate(ordered)
        for right in ordered[index + 1:]
    ]
    adjacent_similarities = [
        _text_similarity(_episode_text(left), _episode_text(right))
        for left, right in zip(ordered, ordered[1:])
    ]
    text_score = (
        sum(pair_similarities) / len(pair_similarities) if pair_similarities else 0.0
    )
    max_gap = max(gaps) if gaps else span
    time_score = max(0.0, 1.0 - max_gap / MAX_ADJACENT_GAP_SECONDS)
    source_confidence = sum(
        _unit(float(item.get("confidence", 0.0))) for item in ordered
    ) / len(ordered)
    adjacent_score = (
        sum(adjacent_similarities) / len(adjacent_similarities)
        if adjacent_similarities else 0.0
    )
    entity_continuity = sum(
        _jaccard(entity_sets[index], entity_sets[index + 1])
        for index in range(len(entity_sets) - 1)
    ) / max(1, len(entity_sets) - 1)
    coherence_score = (
        adjacent_score * 0.50 + entity_continuity * 0.30 + source_confidence * 0.20
    )
    scores = combine_scores(entity_score, text_score, time_score, coherence_score)
    scores["theme_gate"] = bool(
        (shared and scores["text"] >= SHARED_ENTITY_TEXT_GATE)
        or scores["text"] >= TEXT_ONLY_GATE
    )
    scores["qualified"] = bool(
        scores["theme_gate"] and scores["total"] >= GROUP_THRESHOLD
    )
    scores["span_seconds"] = round(span, 3)
    scores["max_gap_seconds"] = round(max_gap, 3)
    return scores


def combine_scores(entity: float, text: float, time: float, coherence: float) -> dict:
    values = {
        "entity": _unit(entity), "text": _unit(text), "time": _unit(time),
        "coherence": _unit(coherence),
    }
    values["total"] = round(
        values["entity"] * ENTITY_WEIGHT + values["text"] * TEXT_WEIGHT
        + values["time"] * TIME_WEIGHT + values["coherence"] * COHERENCE_WEIGHT,
        6,
    )
    return values


def grouping_fingerprint(episode_ids: list[str]) -> str:
    stable_ids = sorted(set(str(value) for value in episode_ids if value))
    if len(stable_ids) < MIN_GROUP_SIZE:
        raise ValueError("Saga 分组指纹至少需要两个 Episode")
    stable = f"{POLICY_VERSION}|{'|'.join(stable_ids)}"
    return hashlib.sha256(stable.encode()).hexdigest()


def _build_proposals(
    episodes: list[dict], entity_by_episode: dict[str, set[str]],
) -> list[dict]:
    proposals: dict[str, dict] = {}
    by_id = {item["id"]: item for item in episodes}
    episodes_by_entity: dict[str, list[dict]] = {}
    for episode in episodes:
        for entity_id in entity_by_episode.get(episode["id"], set()):
            episodes_by_entity.setdefault(entity_id, []).append(episode)

    for entity_id in sorted(episodes_by_entity):
        ordered = sorted(
            episodes_by_entity[entity_id], key=lambda item: (item["start_at"], item["id"])
        )
        for offset in range(len(ordered) - 1):
            group = _bounded_group(ordered[offset:])
            _add_proposal(proposals, group, entity_by_episode)

    ordered = sorted(episodes, key=lambda item: (item["start_at"], item["id"]))
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            if right["start_at"] - left["end_at"] > MAX_ADJACENT_GAP_SECONDS:
                break
            if _local_date(left["start_at"]) == _local_date(right["start_at"]):
                continue
            if _text_similarity(_episode_text(left), _episode_text(right)) >= TEXT_ONLY_GATE:
                _add_proposal(proposals, [by_id[left["id"]], by_id[right["id"]]], entity_by_episode)
    return list(proposals.values())


def _bounded_group(episodes: list[dict]) -> list[dict]:
    if not episodes:
        return []
    group = [episodes[0]]
    for episode in episodes[1:]:
        if len(group) >= MAX_GROUP_SIZE:
            break
        if episode["end_at"] - group[0]["start_at"] > MAX_SPAN_SECONDS:
            break
        if episode["start_at"] - group[-1]["end_at"] > MAX_ADJACENT_GAP_SECONDS:
            break
        group.append(episode)
    return group


def _add_proposal(
    proposals: dict[str, dict], episodes: list[dict], entity_map: dict[str, set[str]],
) -> None:
    if len(episodes) < MIN_GROUP_SIZE:
        return
    try:
        scores = assess_group(episodes, entity_map)
    except ValueError:
        return
    ids = [item["id"] for item in sorted(episodes, key=lambda item: (item["start_at"], item["id"]))]
    fingerprint = grouping_fingerprint(ids)
    shared = set.intersection(*(entity_map.get(item, set()) for item in ids))
    proposals[fingerprint] = {
        "fingerprint": fingerprint,
        "episodes": [next(episode for episode in episodes if episode["id"] == item) for item in ids],
        "shared_entity_ids": sorted(shared),
        "scores": scores,
    }


def _record_proposal(conn, proposal: dict, now: float) -> dict:
    fingerprint = proposal["fingerprint"]
    existing = conn.execute(
        "SELECT * FROM saga_group_candidates WHERE grouping_fingerprint=?", (fingerprint,)
    ).fetchone()
    episode_ids = [item["id"] for item in proposal["episodes"]]
    placeholders = ",".join("?" for _ in episode_ids)
    conflicts = conn.execute(
        f"SELECT episode_id,saga_id FROM memory_saga_episodes"
        f" WHERE removed_at IS NULL AND episode_id IN ({placeholders}) ORDER BY episode_id",
        episode_ids,
    ).fetchall()
    append_target = _append_target(conn, conflicts, episode_ids)
    status = "conflicted" if conflicts and not append_target else (
        "qualified" if proposal["scores"]["qualified"] else "observing"
    )
    conflict_reason = "episode_already_in_saga" if conflicts and not append_target else None
    application_mode = "append" if append_target else "create"
    scores = proposal["scores"]
    if existing:
        newly_qualified = existing["status"] != "qualified" and status == "qualified"
        if existing["status"] in {"qualified", "conflicted", "expired"}:
            result = _candidate_row(existing)
            result["newly_qualified"] = False
            return result
        conn.execute(
            "UPDATE saga_group_candidates SET status=?,shared_entity_ids_json=?,"
            "entity_score=?,text_score=?,time_score=?,coherence_score=?,total_score=?,"
            "score_details_json=?,conflict_reason=?,evaluation_count=evaluation_count+1,"
            "last_evaluated_at=?,application_mode=?,target_saga_id=? WHERE id=?",
            (
                status, json.dumps(proposal["shared_entity_ids"]), scores["entity"],
                scores["text"], scores["time"], scores["coherence"], scores["total"],
                json.dumps(scores, separators=(",", ":")), conflict_reason, now,
                application_mode, append_target, existing["id"],
            ),
        )
        row = conn.execute(
            "SELECT * FROM saga_group_candidates WHERE id=?", (existing["id"],)
        ).fetchone()
        result = _candidate_row(row)
        result["newly_qualified"] = newly_qualified
        return result

    candidate_id = db.new_id()
    conn.execute(
        "INSERT INTO saga_group_candidates("
        "id,grouping_fingerprint,status,episode_ids_json,shared_entity_ids_json,"
        "entity_score,text_score,time_score,coherence_score,total_score,score_details_json,"
        "policy_version,conflict_reason,first_seen_at,last_evaluated_at,expires_at"
        ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            candidate_id, fingerprint, status, json.dumps(episode_ids),
            json.dumps(proposal["shared_entity_ids"]), scores["entity"], scores["text"],
            scores["time"], scores["coherence"], scores["total"],
            json.dumps(scores, separators=(",", ":")), POLICY_VERSION, conflict_reason,
            now, now, now + CANDIDATE_TTL_SECONDS,
        ),
    )
    if append_target:
        conn.execute(
            "UPDATE saga_group_candidates SET application_mode='append',target_saga_id=?"
            " WHERE id=?", (append_target, candidate_id),
        )
    result = _candidate_row(conn.execute(
        "SELECT * FROM saga_group_candidates WHERE id=?", (candidate_id,)
    ).fetchone())
    result["newly_qualified"] = status == "qualified"
    return result


def _append_target(conn, conflicts, episode_ids: list[str]) -> str | None:
    saga_ids = {row["saga_id"] for row in conflicts}
    if len(saga_ids) != 1:
        return None
    saga_id = next(iter(saga_ids))
    saga = conn.execute(
        "SELECT status FROM memory_sagas WHERE id=?", (saga_id,)
    ).fetchone()
    if not saga or saga["status"] not in {"active", "completed"}:
        return None
    existing = {
        row["episode_id"] for row in conn.execute(
            "SELECT episode_id FROM memory_saga_episodes"
            " WHERE saga_id=? AND removed_at IS NULL", (saga_id,),
        ).fetchall()
    }
    proposed = set(episode_ids)
    return saga_id if len(existing) >= 2 and existing < proposed else None


def _load_eligible_episodes(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT e.* FROM memory_episodes e"
        " WHERE e.status IN ('active','completed')"
        " ORDER BY e.start_at DESC,e.id DESC LIMIT ?",
        (EPISODE_SCAN_LIMIT,),
    ).fetchall()
    return sorted((dict(row) for row in rows), key=lambda item: (item["start_at"], item["id"]))


def _load_episode_entities(conn, episode_ids: list[str]) -> dict[str, set[str]]:
    result = {episode_id: set() for episode_id in episode_ids}
    if not episode_ids:
        return result
    placeholders = ",".join("?" for _ in episode_ids)
    rows = conn.execute(
        f"SELECT ee.episode_id,ee.entity_id FROM memory_episode_entities ee"
        f" JOIN memory_entities e ON e.id=ee.entity_id"
        f" WHERE e.status='active' AND ee.episode_id IN ({placeholders})",
        episode_ids,
    ).fetchall()
    for row in rows:
        result[row["episode_id"]].add(row["entity_id"])
    return result


def _load_candidate_episodes(conn, episode_ids: list[str]) -> list[dict]:
    if not episode_ids:
        return []
    placeholders = ",".join("?" for _ in episode_ids)
    rows = conn.execute(
        f"SELECT * FROM memory_episodes WHERE id IN ({placeholders})",
        episode_ids,
    ).fetchall()
    by_id = {row["id"]: dict(row) for row in rows}
    result = []
    for episode_id in episode_ids:
        episode = by_id.get(episode_id)
        if not episode:
            continue
        fragments = conn.execute(
            "SELECT f.*,ef.position FROM memory_episode_fragments ef"
            " JOIN memory_fragments f ON f.id=ef.fragment_id"
            " WHERE ef.episode_id=? ORDER BY ef.position,f.id",
            (episode_id,),
        ).fetchall()
        episode["fragments"] = [dict(fragment) for fragment in fragments]
        result.append(episode)
    return result


def _shared_entity_names(conn, entity_ids: list[str]) -> list[str]:
    if not entity_ids:
        return []
    placeholders = ",".join("?" for _ in entity_ids)
    rows = conn.execute(
        f"SELECT name FROM memory_entities WHERE status='active'"
        f" AND id IN ({placeholders}) ORDER BY name",
        entity_ids,
    ).fetchall()
    return [row["name"] for row in rows]


def _summary_event(
    conn, candidate_id: str, action: str, error_code: str | None,
    metadata: dict, created_at: float,
) -> None:
    if action not in {"summary_validated", "summary_fallback", "summary_rejected"}:
        raise ValueError("非法的 Saga 候选摘要事件")
    conn.execute(
        "INSERT INTO saga_candidate_summary_events("
        "id,candidate_id,action,error_code,metadata_json,created_at) VALUES(?,?,?,?,?,?)",
        (
            db.new_id(), candidate_id, action, error_code,
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), created_at,
        ),
    )


def _expire_candidates(conn, now: float) -> int:
    cursor = conn.execute(
        "UPDATE saga_group_candidates SET status='expired',last_evaluated_at=?"
        " WHERE status='observing' AND expires_at<=?",
        (now, now),
    )
    return cursor.rowcount


def _candidate_row(row) -> dict:
    result = dict(row)
    result["episode_ids"] = json.loads(result.pop("episode_ids_json"))
    result["shared_entity_ids"] = json.loads(result.pop("shared_entity_ids_json"))
    result["score_details"] = json.loads(result.pop("score_details_json"))
    result["summary_evidence_episode_ids"] = json.loads(
        result.pop("summary_evidence_episode_ids_json", "[]")
    )
    result["completion_evidence_episode_ids"] = json.loads(
        result.pop("completion_evidence_episode_ids_json", "[]")
    )
    result["summary_warnings"] = json.loads(result.pop("summary_warnings_json", "[]"))
    if result.get("summary_status") == "not_started":
        for field in ("title", "summary", "theme", "current_stage"):
            result.pop(field, None)
    return result


def _episode_text(episode: dict) -> str:
    return f"{episode.get('title', '')} {episode.get('summary', '')}".strip()


def _local_date(timestamp: float):
    return datetime.fromtimestamp(float(timestamp)).date()


def _text_similarity(left: str, right: str) -> float:
    a, b = _grams(left), _grams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _grams(text: str) -> set[str]:
    clean = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", text.casefold())
    if len(clean) < 3:
        return {clean} if clean else set()
    return {clean[index:index + 3] for index in range(len(clean) - 2)}


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _unit(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 6)
