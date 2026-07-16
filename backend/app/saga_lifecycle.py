"""Saga D.5：查询、精确生命周期、纠错与只读关系建议。"""
from __future__ import annotations

import json

from . import db, episode_summary, saga_summary, sagas

POLICY_VERSION = "saga-lifecycle-v1"
RELATIONSHIP_POLICY_VERSION = "saga-relationship-suggestion-v1"
STATES = frozenset({"active", "completed", "archived", "tombstone"})
USER_SOURCES = frozenset({"user", "privacy"})


class SagaLifecycleError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def list_sagas(status: str | None = None, *, limit: int = 50, offset: int = 0) -> list[dict]:
    if status is not None and status not in STATES:
        raise SagaLifecycleError("invalid_status", "非法的 Saga 状态")
    conn = db.connect()
    try:
        where, params = (" WHERE status=?", [status]) if status else ("", [])
        rows = conn.execute(
            f"SELECT * FROM memory_sagas{where} ORDER BY end_at DESC,id LIMIT ? OFFSET ?",
            (*params, max(1, min(int(limit), 200)), max(0, int(offset))),
        ).fetchall()
        return [_saga_row(row) for row in rows]
    finally:
        conn.close()


def get_saga(saga_id: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM memory_sagas WHERE id=?", (saga_id,)).fetchone()
        if not row:
            return None
        result = _saga_row(row)
        links = conn.execute(
            "SELECT episode_id,position,role,added_at,removed_at FROM memory_saga_episodes"
            " WHERE saga_id=? ORDER BY position,added_at,episode_id", (saga_id,),
        ).fetchall()
        all_ids = [link["episode_id"] for link in links]
        episodes = {item["id"]: item for item in sagas._load_candidate_episodes(conn, all_ids)}
        result["timeline"] = [
            {**dict(link), "episode": episodes.get(link["episode_id"])} for link in links
        ]
        entity_rows = conn.execute(
            "SELECT se.*,e.name,e.entity_type,e.status AS entity_status"
            " FROM memory_saga_entities se JOIN memory_entities e ON e.id=se.entity_id"
            " WHERE se.saga_id=? ORDER BY e.name,e.id", (saga_id,),
        ).fetchall()
        result["entities"] = [dict(item) for item in entity_rows]
        result["events"] = _events(conn, saga_id)
        result["relationship_suggestions"] = _suggestions(conn, saga_id)
        return result
    finally:
        conn.close()


def list_events(saga_id: str) -> list[dict]:
    conn = db.connect()
    try:
        return _events(conn, saga_id)
    finally:
        conn.close()


def list_relationship_suggestions(saga_id: str) -> list[dict]:
    conn = db.connect()
    try:
        return _suggestions(conn, saga_id)
    finally:
        conn.close()


def transition(
    saga_id: str, target_status: str, *, reason: str, source: str = "user",
    evidence_episode_ids: list[str] | None = None, expected_revision: int | None = None,
) -> dict | None:
    if target_status not in STATES:
        raise SagaLifecycleError("invalid_status", "非法的 Saga 目标状态")
    note = reason.strip()
    if not note:
        raise SagaLifecycleError("reason_required", "生命周期变化必须说明原因")
    evidence_ids = list(dict.fromkeys(evidence_episode_ids or []))
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM memory_sagas WHERE id=?", (saga_id,)).fetchone()
        if not row:
            conn.rollback()
            return None
        _check_revision(row, expected_revision)
        current = row["status"]
        if current == target_status:
            raise SagaLifecycleError("lifecycle_noop", "Saga 已经处于目标状态")
        if current == "tombstone":
            raise SagaLifecycleError("tombstone_terminal", "已删除的 Saga 不可恢复")
        source_ids, sources = _active_sources(conn, saga_id)
        now = db.now()
        fields: dict[str, object] = {
            "status": target_status, "updated_at": now,
            "lifecycle_policy_version": POLICY_VERSION,
        }
        action = "status_changed"
        validated_completion: list[str] = []
        if target_status != "tombstone":
            _require_valid_source_chain(sources)

        if current == "active" and target_status == "completed":
            if source != "user" or evidence_ids:
                validated_completion = _validate_completion_evidence(
                    source_ids, sources, evidence_ids
                )
            fields.update(
                completion_reason=note, completed_at=now,
                completion_evidence_episode_ids_json=json.dumps(validated_completion),
            )
            action = "completed"
        elif current == "completed" and target_status == "active":
            if source != "user":
                _validate_new_development(source_ids, sources, evidence_ids)
            fields.update(
                completion_reason="", completion_evidence_episode_ids_json="[]"
            )
            action = "reactivated"
        elif current == "completed" and target_status == "archived":
            if source not in {"user", "archivist"}:
                raise SagaLifecycleError("archive_source_forbidden", "只有用户或 Archivist 可以归档")
            fields["archived_at"] = now
            action = "archived"
        elif current == "archived" and target_status == "active":
            if source != "user":
                raise SagaLifecycleError("automatic_restore_forbidden", "归档 Saga 只能由用户恢复")
            fields.update(
                completion_reason="", completion_evidence_episode_ids_json="[]"
            )
            action = "reactivated"
        elif target_status == "tombstone" and current in {"active", "completed", "archived"}:
            if source not in USER_SOURCES:
                raise SagaLifecycleError("automatic_tombstone_forbidden", "自动任务不能删除 Saga")
            fields["tombstoned_at"] = now
            action = "tombstoned"
        else:
            raise SagaLifecycleError(
                "illegal_lifecycle_transition", f"不允许 {current} → {target_status}"
            )

        before = _saga_row(row)
        _update_fields(conn, saga_id, fields, increment_revision=True)
        if target_status in {"active", "tombstone"}:
            conn.execute(
                "UPDATE saga_relationship_delta_suggestions SET status='revoked',"
                "revocation_reason=?,revoked_at=? WHERE saga_id=? AND status='proposed'",
                (
                    "saga_reactivated" if target_status == "active" else "saga_tombstoned",
                    now, saga_id,
                ),
            )
        after_row = conn.execute("SELECT * FROM memory_sagas WHERE id=?", (saga_id,)).fetchone()
        event_id = _event(
            conn, saga_id, action, before, _saga_row(after_row), note, source,
            {"evidence_episode_ids": evidence_ids}, now,
        )
        if target_status == "completed" and validated_completion:
            _relationship_suggestion(
                conn, saga_id, event_id, validated_completion, int(row["significance"]), now
            )
        conn.commit()
        return get_saga(saga_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def correct_content(
    saga_id: str, *, title: str | None = None, summary: str | None = None,
    theme: str | None = None, current_stage: str | None = None,
    significance: int | None = None, note: str, expected_revision: int | None = None,
) -> dict | None:
    values = {
        "title": title, "summary": summary, "theme": theme,
        "current_stage": current_stage, "significance": significance,
    }
    if all(value is None for value in values.values()):
        raise SagaLifecycleError("correction_empty", "至少提供一个需要纠正的字段")
    cleaned = _validate_content_values(values)
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM memory_sagas WHERE id=?", (saga_id,)).fetchone()
        if not row:
            conn.rollback()
            return None
        if row["status"] == "tombstone":
            raise SagaLifecycleError("tombstone_terminal", "已删除的 Saga 不可纠正")
        _check_revision(row, expected_revision)
        now = db.now()
        fields: dict[str, object] = {**cleaned, "correction_note": note.strip(), "corrected_at": now}
        if any(key in cleaned for key in ("title", "summary", "theme", "current_stage")):
            fields.update(
                summary_status="user_edited", summary_protocol_version="manual-v1",
                summary_provider_id=None, summary_model=None, summary_evidence_json="[]",
            )
        before = _saga_row(row)
        _update_fields(conn, saga_id, fields, increment_revision=True)
        after = conn.execute("SELECT * FROM memory_sagas WHERE id=?", (saga_id,)).fetchone()
        _event(
            conn, saga_id, "content_corrected", before, _saga_row(after),
            note.strip() or "user_correction", "user", {"changed_fields": sorted(cleaned)}, now,
        )
        conn.commit()
        return get_saga(saga_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def correct_sources(
    saga_id: str, episode_ids: list[str], *, note: str,
    expected_revision: int | None = None,
) -> dict | None:
    ordered_ids = list(dict.fromkeys(str(value) for value in episode_ids if value))
    if len(ordered_ids) < sagas.MIN_GROUP_SIZE:
        raise SagaLifecycleError("source_count_too_small", "Saga 至少需要两个正式 Episode")
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM memory_sagas WHERE id=?", (saga_id,)).fetchone()
        if not row:
            conn.rollback()
            return None
        if row["status"] == "tombstone":
            raise SagaLifecycleError("tombstone_terminal", "已删除的 Saga 不可纠正")
        _check_revision(row, expected_revision)
        current_ids, _ = _active_sources(conn, saga_id)
        if current_ids == ordered_ids:
            raise SagaLifecycleError("source_correction_noop", "Saga 来源没有变化")
        sources = sagas._load_candidate_episodes(conn, ordered_ids)
        if len(sources) != len(ordered_ids):
            raise SagaLifecycleError("source_episode_missing", "纠正来源中存在无效 Episode")
        chronological_ids = [
            item["id"] for item in sorted(
                sources, key=lambda value: (value["start_at"], value["id"])
            )
        ]
        if chronological_ids != ordered_ids:
            raise SagaLifecycleError("source_order_invalid", "Saga 来源必须按 Episode 时间顺序排列")
        _require_valid_source_chain(sources)
        conflicts = conn.execute(
            f"SELECT episode_id,saga_id FROM memory_saga_episodes WHERE removed_at IS NULL"
            f" AND saga_id<>? AND episode_id IN ({','.join('?' for _ in ordered_ids)})",
            (saga_id, *ordered_ids),
        ).fetchall()
        if conflicts:
            raise SagaLifecycleError("source_cross_saga_conflict", "Episode 已属于其他 Saga")
        fingerprint = sagas.grouping_fingerprint(ordered_ids)
        if conn.execute(
            "SELECT 1 FROM memory_sagas WHERE id<>? AND grouping_fingerprint=?",
            (saga_id, fingerprint),
        ).fetchone():
            raise SagaLifecycleError("source_grouping_conflict", "相同来源组合已存在 Saga")
        fallback = saga_summary.extractive_fallback(
            episodes=sources, entity_names=sagas._shared_entity_names(
                conn, sorted(set.intersection(*(
                    sagas._load_episode_entities(conn, ordered_ids).get(item, set())
                    for item in ordered_ids
                )))
            ),
        )
        now = db.now()
        existing = {
            item["episode_id"]: dict(item) for item in conn.execute(
                "SELECT * FROM memory_saga_episodes WHERE saga_id=?", (saga_id,)
            ).fetchall()
        }
        old_active_ids = [
            item["episode_id"] for item in sorted(
                existing.values(), key=lambda value: (value["position"], value["episode_id"])
            ) if item["removed_at"] is None
        ]
        conn.execute(
            "UPDATE memory_saga_episodes SET removed_at=?"
            " WHERE saga_id=? AND removed_at IS NULL", (now, saga_id),
        )
        for position, episode_id in enumerate(ordered_ids):
            role = "anchor" if position == 0 else "development"
            if episode_id in existing:
                conn.execute(
                    "UPDATE memory_saga_episodes SET position=?,role=?,removed_at=NULL,added_at=?"
                    " WHERE saga_id=? AND episode_id=?",
                    (position, role, now, saga_id, episode_id),
                )
            else:
                conn.execute(
                    "INSERT INTO memory_saga_episodes(saga_id,episode_id,position,role,added_at)"
                    " VALUES(?,?,?,?,?)", (saga_id, episode_id, position, role, now),
                )
        before = _saga_row(row)
        next_status = "active" if row["status"] == "completed" else row["status"]
        fields = {
            "title": fallback["title"], "summary": fallback["summary"],
            "theme": fallback["theme"], "current_stage": fallback["current_stage"],
            "start_at": min(item["start_at"] for item in sources),
            "end_at": max(item["end_at"] for item in sources),
            "status": next_status, "source_episode_ids_json": json.dumps(ordered_ids),
            "source_hash": fallback["source_hash"], "grouping_fingerprint": fingerprint,
            "policy_version": sagas.POLICY_VERSION,
            "summary_status": "extractive_fallback",
            "summary_protocol_version": fallback["protocol_version"],
            "summary_provider_id": None, "summary_model": None,
            "summary_evidence_json": json.dumps(fallback["evidence_episode_ids"]),
            "completion_evidence_episode_ids_json": "[]", "completion_reason": "",
            "lifecycle_policy_version": POLICY_VERSION,
            "correction_note": note.strip(), "corrected_at": now,
        }
        _update_fields(conn, saga_id, fields, increment_revision=True)
        conn.execute(
            "DELETE FROM memory_saga_entities WHERE saga_id=? AND source='episode_derived'",
            (saga_id,),
        )
        sagas._sync_saga_entities(conn, saga_id, ordered_ids, now)
        conn.execute(
            "UPDATE saga_relationship_delta_suggestions SET status='revoked',"
            "revocation_reason='source_group_corrected',revoked_at=?"
            " WHERE saga_id=? AND status='proposed'", (now, saga_id),
        )
        after = conn.execute("SELECT * FROM memory_sagas WHERE id=?", (saga_id,)).fetchone()
        _event(
            conn, saga_id, "sources_corrected", before, _saga_row(after),
            note.strip() or "source_group_correction", "user",
            {"before_episode_ids": before["source_episode_ids"], "after_episode_ids": ordered_ids},
            now,
        )
        removed_ids = [item for item in old_active_ids if item not in set(ordered_ids)]
        added_ids = [item for item in ordered_ids if item not in set(old_active_ids)]
        if removed_ids:
            _event(
                conn, saga_id, "episodes_removed", {"episode_ids": old_active_ids},
                {"episode_ids": ordered_ids}, "source_group_correction", "user",
                {"episode_ids": removed_ids}, now,
            )
        if added_ids:
            _event(
                conn, saga_id, "episodes_added", {"episode_ids": old_active_ids},
                {"episode_ids": ordered_ids}, "source_group_correction", "user",
                {"episode_ids": added_ids}, now,
            )
        if row["status"] == "completed":
            _event(
                conn, saga_id, "reactivated", {"status": "completed"},
                {"status": "active"}, "completion_evidence_invalidated", "system", {}, now,
            )
        conn.commit()
        return get_saga(saga_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _active_sources(conn, saga_id: str) -> tuple[list[str], list[dict]]:
    ids = [
        item["episode_id"] for item in conn.execute(
            "SELECT episode_id FROM memory_saga_episodes"
            " WHERE saga_id=? AND removed_at IS NULL ORDER BY position", (saga_id,),
        ).fetchall()
    ]
    return ids, sagas._load_candidate_episodes(conn, ids)


def _validate_completion_evidence(
    source_ids: list[str], sources: list[dict], evidence_ids: list[str],
) -> list[str]:
    _require_valid_source_chain(sources)
    if not evidence_ids or not set(evidence_ids) <= set(source_ids):
        raise SagaLifecycleError("completion_evidence_invalid", "完成证据必须来自当前 Saga")
    latest_id = source_ids[-1]
    by_id = {item["id"]: item for item in sources}
    if latest_id not in evidence_ids or any(
        not any(
            hint in f"{by_id[item].get('title', '')} {by_id[item].get('summary', '')}"
            for hint in saga_summary.COMPLETION_HINTS
        ) for item in evidence_ids
    ):
        raise SagaLifecycleError("completion_evidence_not_grounded", "最新 Episode 不支持 Saga 完成")
    return evidence_ids


def _validate_new_development(
    source_ids: list[str], sources: list[dict], evidence_ids: list[str],
) -> None:
    _require_valid_source_chain(sources)
    if not evidence_ids or not set(evidence_ids) <= set(source_ids) or source_ids[-1] not in evidence_ids:
        raise SagaLifecycleError("development_evidence_invalid", "自动恢复必须由最新发展 Episode 支持")


def _validate_content_values(values: dict) -> dict:
    result = {}
    limits = {"title": 80, "summary": 1200, "theme": 80, "current_stage": 300}
    for key, limit in limits.items():
        if values[key] is not None:
            text = str(values[key]).strip()
            if not text or len(text) > limit:
                raise SagaLifecycleError("correction_value_invalid", f"{key} 内容无效")
            if not episode_summary.is_safe_source(text):
                raise SagaLifecycleError("correction_unsafe", f"{key} 包含不安全内容")
            result[key] = text
    if values["significance"] is not None:
        number = int(values["significance"])
        if not 1 <= number <= 10:
            raise SagaLifecycleError("significance_invalid", "重要度必须在 1 到 10 之间")
        result["significance"] = number
    return result


def _require_valid_source_chain(sources: list[dict]) -> None:
    try:
        saga_summary.validate_source_chain(sources)
    except saga_summary.SagaSummaryValidationError as exc:
        raise SagaLifecycleError(f"lifecycle_{exc.code}", str(exc)) from exc


def _check_revision(row, expected: int | None) -> None:
    if expected is not None and int(row["revision"]) != int(expected):
        raise SagaLifecycleError("revision_conflict", "Saga 已被其他操作更新，请刷新后重试")


def _update_fields(conn, saga_id: str, fields: dict, *, increment_revision: bool) -> None:
    assignments = [f"{key}=?" for key in fields]
    if increment_revision:
        assignments.append("revision=revision+1")
    conn.execute(
        f"UPDATE memory_sagas SET {','.join(assignments)} WHERE id=?",
        (*fields.values(), saga_id),
    )


def _event(
    conn, saga_id: str, action: str, before: dict | None, after: dict | None,
    reason: str, source: str, metadata: dict, now: float,
) -> str:
    event_id = db.new_id()
    conn.execute(
        "INSERT INTO memory_saga_events("
        "id,saga_id,action,before_json,after_json,reason_code,source,policy_version,"
        "metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            event_id, saga_id, action,
            json.dumps(before, ensure_ascii=False) if before is not None else None,
            json.dumps(after, ensure_ascii=False) if after is not None else None,
            reason[:240], source, POLICY_VERSION,
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), now,
        ),
    )
    return event_id


def _relationship_suggestion(
    conn, saga_id: str, event_id: str, evidence_ids: list[str],
    significance: int, now: float,
) -> None:
    bond = min(0.02, round(0.004 + significance * 0.001, 4))
    trust = min(0.01, round(bond * 0.5, 4))
    conn.execute(
        "INSERT OR IGNORE INTO saga_relationship_delta_suggestions("
        "id,saga_id,source_event_id,signal_type,bond_delta,trust_delta,"
        "evidence_episode_ids_json,policy_version,created_at)"
        " VALUES(?,?,?,'shared_saga_completed',?,?,?,?,?)",
        (
            db.new_id(), saga_id, event_id, bond, trust, json.dumps(evidence_ids),
            RELATIONSHIP_POLICY_VERSION, now,
        ),
    )


def _events(conn, saga_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM memory_saga_events WHERE saga_id=? ORDER BY created_at,id",
        (saga_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["before"] = json.loads(item.pop("before_json")) if item["before_json"] else None
        item["after"] = json.loads(item.pop("after_json")) if item["after_json"] else None
        item["metadata"] = json.loads(item.pop("metadata_json"))
        result.append(item)
    return result


def _suggestions(conn, saga_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM saga_relationship_delta_suggestions"
        " WHERE saga_id=? ORDER BY created_at,id", (saga_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["evidence_episode_ids"] = json.loads(item.pop("evidence_episode_ids_json"))
        result.append(item)
    return result


def _saga_row(row) -> dict:
    result = dict(row)
    result["source_episode_ids"] = json.loads(result.pop("source_episode_ids_json"))
    result["summary_evidence_episode_ids"] = json.loads(result.pop("summary_evidence_json"))
    result["completion_evidence_episode_ids"] = json.loads(
        result.pop("completion_evidence_episode_ids_json", "[]")
    )
    return result
