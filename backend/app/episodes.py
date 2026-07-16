"""Episode 候选与正式经历。

参考 MemoryConstellations Consolidator 的边界：2~20 条碎片、继承来源、时间范围和
独立 significance。第一版不用模型，以共同实体、时间窗口和文本重合生成可解释候选。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re

from . import db, episode_summary

MIN_GROUP_SIZE = 2
MAX_GROUP_SIZE = 20
WINDOW_SECONDS = 7 * 24 * 60 * 60
GROUP_THRESHOLD = 0.50
GROUP_POLICY_VERSION = "episode-group-v1"
ENTITY_WEIGHT = 0.35
TEXT_WEIGHT = 0.25
TIME_WEIGHT = 0.20
COHERENCE_WEIGHT = 0.20
APPLICATION_VERSION = "episode-application-v1"
AUTOMATIC_BATCH_LIMIT = 20
APPLICATION_MAX_ATTEMPTS = 3
_logger = logging.getLogger(__name__)

ROUTINE_HINTS = ("配置", "代码", "报错", "接口", "构建", "测试", "修复", "开发")
SIGNIFICANT_HINTS = ("第一次", "决定", "完成", "成功", "纪念", "搬到", "旅行", "毕业", "入职")


class EpisodeApplyError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def generate_candidates(*, now: float | None = None) -> list[dict]:
    conn = db.connect()
    try:
        timestamp = db.now() if now is None else now
        _expire_group_candidates(conn, timestamp)
        proposals = _build_group_proposals(conn, timestamp)
        created = []
        used_fragment_ids: set[str] = set()
        for proposal in sorted(
            proposals,
            key=lambda item: (
                -item["scores"]["total"], -len(item["fragments"]),
                item["fragments"][0]["created_at"], item["fingerprint"],
            ),
        ):
            fragment_ids = {fragment["id"] for fragment in proposal["fragments"]}
            if fragment_ids & used_fragment_ids:
                continue
            used_fragment_ids.update(fragment_ids)
            if proposal["scores"]["total"] >= GROUP_THRESHOLD:
                candidate = _create_scored_candidate(conn, proposal, timestamp)
                if candidate:
                    created.append(candidate)
            else:
                _record_low_score_group(conn, proposal, timestamp)
        conn.commit()
        return created
    finally:
        conn.close()


def list_group_candidates(status: str = "observing") -> list[dict]:
    if status not in ("observing", "qualified", "superseded", "expired"):
        raise ValueError("非法的 Episode 分组候选状态")
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM episode_group_candidates WHERE status=?"
            " ORDER BY last_evaluated_at DESC,id",
            (status,),
        ).fetchall()
        return [_group_candidate_row(row) for row in rows]
    finally:
        conn.close()


def list_candidates(status: str = "pending") -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM memory_episode_candidates WHERE status=? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
        return [_candidate_row(conn, row) for row in rows]
    finally:
        conn.close()


def get_candidate(candidate_id: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM memory_episode_candidates WHERE id=?", (candidate_id,)
        ).fetchone()
        return _candidate_row(conn, row) if row else None
    finally:
        conn.close()


def pending_candidates(
    limit: int = AUTOMATIC_BATCH_LIMIT, *, include_changed_exhausted: bool = False,
) -> list[dict]:
    conn = db.connect()
    try:
        wanted = max(1, min(int(limit), AUTOMATIC_BATCH_LIMIT))
        if not include_changed_exhausted:
            rows = conn.execute(
                "SELECT * FROM memory_episode_candidates WHERE status='pending'"
                " AND application_attempt_count<?"
                " ORDER BY confidence DESC,created_at,id LIMIT ?",
                (APPLICATION_MAX_ATTEMPTS, wanted),
            ).fetchall()
            return [_candidate_row(conn, row) for row in rows]
        rows = conn.execute(
            "SELECT * FROM memory_episode_candidates WHERE status='pending'"
            " ORDER BY confidence DESC,created_at,id LIMIT 100"
        ).fetchall()
        candidates = [_candidate_row(conn, row) for row in rows]
        return [
            item for item in candidates
            if item["application_attempt_count"] < APPLICATION_MAX_ATTEMPTS
            or _candidate_sources_need_refresh(item)
        ][:wanted]
    finally:
        conn.close()


def list_episodes(status: str = "active") -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT e.*, COUNT(ef.fragment_id) AS fragment_count"
            " FROM memory_episodes e"
            " LEFT JOIN memory_episode_fragments ef ON ef.episode_id=e.id"
            " WHERE e.status=? GROUP BY e.id ORDER BY e.end_at DESC",
            (status,),
        ).fetchall()
        return [_episode_list_row(row) for row in rows]
    finally:
        conn.close()


def get_episode(episode_id: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM memory_episodes WHERE id=?", (episode_id,)).fetchone()
        return _episode_row(conn, row) if row else None
    finally:
        conn.close()


def correct_episode(
    episode_id: str, *, title: str | None = None, summary: str | None = None,
    significance: int | None = None, note: str = "",
) -> dict | None:
    """纠正正式经历；来源关系不变，摘要改写使用独立审计语义。"""
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM memory_episodes WHERE id=? AND status='active'", (episode_id,)
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        before = _episode_row(conn, row)
        next_title = before["title"] if title is None else title.strip()
        next_summary = before["summary"] if summary is None else summary.strip()
        if not next_title or not next_summary:
            raise ValueError("Episode 标题和摘要不能为空")
        if not episode_summary.is_safe_source(next_title) or not episode_summary.is_safe_source(
            next_summary
        ):
            raise ValueError("Episode 标题或摘要包含不安全内容")
        next_significance = before["significance"] if significance is None else int(significance)
        if not 1 <= next_significance <= 10:
            raise ValueError("重要度必须在 1 到 10 之间")
        summary_changed = next_title != before["title"] or next_summary != before["summary"]
        now = db.now()
        conn.execute(
            "UPDATE memory_episodes SET title=?,summary=?,significance=?,correction_note=?,"
            "corrected_at=?,updated_at=?,summary_status=?,summary_protocol_version=?,"
            "summary_evidence_json=? WHERE id=?",
            (
                next_title, next_summary, next_significance, note.strip(), now, now,
                "user_edited" if summary_changed else before["summary_status"],
                "manual-v1" if summary_changed else before["summary_protocol_version"],
                "[]" if summary_changed else json.dumps(
                    before["summary_evidence_fragment_ids"]
                ),
                episode_id,
            ),
        )
        after = _episode_row(
            conn, conn.execute("SELECT * FROM memory_episodes WHERE id=?", (episode_id,)).fetchone()
        )
        _event(
            conn, "episode", episode_id, "corrected", before,
            {**after, "correction_note": note.strip()}, "user_correction",
        )
        conn.commit()
        return after
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def accept_candidate(
    candidate_id: str,
    title: str | None = None,
    summary: str | None = None,
    significance: int | None = None,
    fragment_ids: list[str] | None = None,
) -> dict | None:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM memory_episode_candidates WHERE id=? AND status='pending'",
            (candidate_id,),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        episode = _apply_candidate_locked(
            conn, row, source="candidate_confirmed", actor="user", title=title,
            summary=summary, significance=significance, fragment_ids=fragment_ids,
        )
        conn.commit()
        return episode
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def apply_candidates_for_run(run_id: str, candidate_ids: list[str]) -> list[dict]:
    """在一个短事务中提交正式 Episode、来源关系、审计与 run 终态。"""
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        run = conn.execute(
            "SELECT * FROM episode_consolidator_runs WHERE id=?", (run_id,)
        ).fetchone()
        if not run or run["status"] != "running":
            conn.rollback()
            raise EpisodeApplyError("application_run_not_running", "整理任务已不在运行状态")
        ordered_ids = list(dict.fromkeys(str(value) for value in candidate_ids if value))
        applied = []
        for candidate_id in ordered_ids[:AUTOMATIC_BATCH_LIMIT]:
            row = conn.execute(
                "SELECT * FROM memory_episode_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
            if not row or row["status"] != "pending":
                continue
            applied.append(_apply_candidate_locked(
                conn, row, source="consolidator_auto", actor="consolidator"
            ))
        now = db.now()
        status = "applied" if applied else "skipped"
        reason = "formal_episodes_created" if applied else "no_eligible_group"
        episode_ids = [item["id"] for item in applied]
        conn.execute(
            "UPDATE episode_consolidator_runs SET status=?,group_count=?,error_code=NULL,"
            "result_episode_ids_json=?,finished_at=?,updated_at=? WHERE id=?",
            (status, len(applied), json.dumps(episode_ids), now, now, run_id),
        )
        _consolidator_event(
            conn, run_id, "processed", "running", status, reason,
            {"group_count": len(applied), "episode_ids": episode_ids},
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if applied:
        try:
            from . import saga_consolidator
            saga_consolidator.enqueue_for_episodes([item["id"] for item in applied])
        except Exception:  # noqa: BLE001 - Saga 排队失败不能回滚正式 Episode
            _logger.exception("Failed to enqueue Saga consolidation after Episode apply")
    return applied


def record_application_failure(candidate_ids: list[str], error_code: str) -> None:
    ids = list(dict.fromkeys(str(value) for value in candidate_ids if value))
    if not ids:
        return
    conn = db.connect()
    try:
        now = db.now()
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE memory_episode_candidates SET application_attempt_count="
            f"application_attempt_count+1,application_error_code=?,last_application_at=?"
            f" WHERE status='pending' AND id IN ({placeholders})",
            (error_code, now, *ids),
        )
        conn.commit()
    finally:
        conn.close()


def _apply_candidate_locked(
    conn, row, *, source: str, actor: str, title: str | None = None,
    summary: str | None = None, significance: int | None = None,
    fragment_ids: list[str] | None = None,
) -> dict:
    candidate = _candidate_row(conn, row)
    candidate_fragments = candidate["fragments"]
    allowed_ids = [fragment["id"] for fragment in candidate_fragments]
    requested_ids = allowed_ids if fragment_ids is None else list(dict.fromkeys(fragment_ids))
    if any(fragment_id not in allowed_ids for fragment_id in requested_ids):
        raise ValueError("Episode 来源必须属于当前候选")
    chosen_ids = [fragment_id for fragment_id in allowed_ids if fragment_id in requested_ids]
    if len(chosen_ids) < MIN_GROUP_SIZE:
        raise ValueError("Episode 至少需要 2 条候选记忆")
    fragments = [
        fragment for fragment in candidate_fragments if fragment["id"] in set(chosen_ids)
    ]
    if len(fragments) != len(chosen_ids):
        raise EpisodeApplyError("application_source_missing", "候选来源已经缺失")
    if any(
        fragment["status"] != "active" or not fragment["enabled"]
        or fragment["sensitivity"] != "normal"
        or not episode_summary.is_safe_source(fragment["content"])
        for fragment in fragments
    ):
        raise EpisodeApplyError("application_source_ineligible", "候选来源已不再适合合成")
    placeholders = ",".join("?" for _ in chosen_ids)
    occupied = conn.execute(
        f"SELECT fragment_id FROM memory_episode_fragments"
        f" WHERE fragment_id IN ({placeholders}) LIMIT 1", chosen_ids,
    ).fetchone()
    if occupied:
        raise EpisodeApplyError("application_source_owned", "候选来源已归属其他 Episode")
    current_hash = episode_summary.source_hash(fragments)
    legacy_without_hash = (
        not candidate["summary_source_hash"] and candidate["summary_status"] == "legacy_rule"
    )
    if candidate["summary_source_hash"] and current_hash != candidate["summary_source_hash"]:
        raise EpisodeApplyError("application_source_changed", "候选来源在正式应用前发生变化")
    automatic = source == "consolidator_auto"
    if automatic and legacy_without_hash:
        raise EpisodeApplyError("application_summary_unvalidated", "候选摘要尚未通过安全整理")
    if automatic and candidate["summary_status"] not in (
        "extractive_fallback", "model_validated",
    ):
        raise EpisodeApplyError("application_summary_unvalidated", "候选摘要尚未通过安全整理")
    evidence_ids = candidate["summary_evidence_fragment_ids"]
    if automatic and (
        not evidence_ids or any(fragment_id not in chosen_ids for fragment_id in evidence_ids)
    ):
        raise EpisodeApplyError("application_evidence_invalid", "候选摘要来源集合无效")
    shared_entities = shared_entity_names(conn, chosen_ids)
    if automatic and not shared_entities:
        raise EpisodeApplyError("application_shared_entity_missing", "候选已失去共同实体")

    chosen_title = (title if title is not None else candidate["title"]).strip()
    chosen_summary = (summary if summary is not None else candidate["summary"]).strip()
    if not chosen_title or not chosen_summary:
        raise ValueError("Episode 标题和摘要不能为空")
    if not episode_summary.is_safe_source(chosen_title) or not episode_summary.is_safe_source(
        chosen_summary
    ):
        raise EpisodeApplyError("application_content_unsafe", "Episode 标题或摘要不安全")
    custom_source_set = chosen_ids != allowed_ids
    user_edited = not automatic and (
        title is not None or summary is not None or custom_source_set
        or candidate["summary_status"] == "legacy_rule"
    )
    if custom_source_set and (title is None or summary is None):
        raise ValueError("调整 Episode 来源时必须同时提供标题和摘要")
    chosen_significance = max(1, min(10, int(
        significance if significance is not None else candidate["significance"]
    )))
    summary_status = "user_edited" if user_edited else candidate["summary_status"]
    summary_protocol = "manual-v1" if user_edited else candidate["summary_protocol_version"]
    summary_evidence = [] if user_edited else evidence_ids
    episode_id = db.new_id()
    now = db.now()
    conn.execute(
        "INSERT INTO memory_episodes("
        "id,title,summary,start_at,end_at,significance,confidence,status,source,candidate_id,"
        "created_at,updated_at,grouping_fingerprint,policy_version,source_fragment_ids_json,"
        "source_hash,summary_status,summary_protocol_version,summary_provider_id,summary_model,"
        "summary_evidence_json,application_version)"
        " VALUES(?,?,?,?,?,?,?,'active',?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            episode_id, chosen_title, chosen_summary,
            min(fragment["created_at"] for fragment in fragments),
            max(fragment["created_at"] for fragment in fragments),
            chosen_significance, candidate["confidence"], source, candidate["id"], now, now,
            candidate["grouping_key"], candidate["policy_version"], json.dumps(chosen_ids),
            current_hash, summary_status, summary_protocol, candidate["summary_provider_id"],
            candidate["summary_model"], json.dumps(summary_evidence), APPLICATION_VERSION,
        ),
    )
    for position, fragment in enumerate(fragments):
        conn.execute(
            "INSERT INTO memory_episode_fragments(episode_id,fragment_id,position,created_at)"
            " VALUES(?,?,?,?)", (episode_id, fragment["id"], position, now),
        )
    entity_rows = conn.execute(
        f"SELECT DISTINCT fe.entity_id FROM memory_fragment_entities fe"
        f" JOIN memory_entities e ON e.id=fe.entity_id AND e.status='active'"
        f" WHERE fe.fragment_id IN ({placeholders}) ORDER BY fe.entity_id", chosen_ids,
    ).fetchall()
    for entity_row in entity_rows:
        conn.execute(
            "INSERT INTO memory_episode_entities(episode_id,entity_id,created_at) VALUES(?,?,?)",
            (episode_id, entity_row["entity_id"], now),
        )
    cursor = conn.execute(
        "UPDATE memory_episode_candidates SET status='accepted',resolved_episode_id=?,"
        "resolved_at=?,application_attempt_count=application_attempt_count+1,"
        "application_error_code=NULL,last_application_at=? WHERE id=? AND status='pending'",
        (episode_id, now, now, candidate["id"]),
    )
    if cursor.rowcount != 1:
        raise EpisodeApplyError("application_candidate_changed", "候选状态已经变化")
    episode = _episode_row(
        conn, conn.execute("SELECT * FROM memory_episodes WHERE id=?", (episode_id,)).fetchone()
    )
    _event(conn, "episode_candidate", candidate["id"], "accepted", candidate, episode, actor)
    _event(conn, "episode", episode_id, "created", None, episode, source)
    return episode


def _candidate_sources_need_refresh(candidate: dict) -> bool:
    fragments = candidate["fragments"]
    return (
        len(fragments) >= MIN_GROUP_SIZE
        and all(
            fragment["status"] == "active" and fragment["enabled"]
            and fragment["sensitivity"] == "normal"
            and episode_summary.is_safe_source(fragment["content"])
            for fragment in fragments
        )
        and episode_summary.source_hash(fragments) != candidate["summary_source_hash"]
    )


def reject_candidate(candidate_id: str, note: str = "") -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM memory_episode_candidates WHERE id=? AND status='pending'",
            (candidate_id,),
        ).fetchone()
        if not row:
            return None
        before = _candidate_row(conn, row)
        conn.execute(
            "UPDATE memory_episode_candidates SET status='rejected', resolution_note=?,"
            " resolved_at=? WHERE id=?",
            (note.strip(), db.now(), candidate_id),
        )
        after = _candidate_row(
            conn,
            conn.execute("SELECT * FROM memory_episode_candidates WHERE id=?", (candidate_id,)).fetchone(),
        )
        _event(conn, "episode_candidate", candidate_id, "rejected", before, after, "user")
        conn.commit()
        return after
    finally:
        conn.close()


def _build_group_proposals(conn, now: float) -> list[dict]:
    rows = conn.execute(
        "SELECT f.* FROM memory_fragments f WHERE f.status='active' AND f.enabled=1"
        " AND f.created_at BETWEEN ? AND ?"
        " AND NOT EXISTS (SELECT 1 FROM memory_episode_fragments ef WHERE ef.fragment_id=f.id)"
        " AND NOT EXISTS (SELECT 1 FROM memory_episode_candidate_fragments ecf"
        " JOIN memory_episode_candidates ec ON ec.id=ecf.candidate_id"
        " WHERE ecf.fragment_id=f.id AND ec.status='pending')"
        " ORDER BY f.created_at,f.id",
        (now - WINDOW_SECONDS, now),
    ).fetchall()
    fragments = {
        row["id"]: dict(row) for row in rows
        if row["sensitivity"] == "normal" and episode_summary.is_safe_source(row["content"])
    }
    if len(fragments) < MIN_GROUP_SIZE:
        return []
    links = conn.execute(
        f"SELECT fe.fragment_id,fe.entity_id FROM memory_fragment_entities fe"
        f" JOIN memory_entities e ON e.id=fe.entity_id AND e.status='active'"
        f" WHERE fe.fragment_id IN ({','.join('?' for _ in fragments)})"
        " ORDER BY fe.entity_id,fe.fragment_id",
        list(fragments),
    ).fetchall()
    entity_by_fragment = {fragment_id: set() for fragment_id in fragments}
    fragments_by_entity: dict[str, list[dict]] = {}
    for link in links:
        entity_by_fragment[link["fragment_id"]].add(link["entity_id"])
        fragments_by_entity.setdefault(link["entity_id"], []).append(
            fragments[link["fragment_id"]]
        )

    proposals: dict[str, dict] = {}
    for entity_id in sorted(fragments_by_entity):
        candidates = sorted(
            {item["id"]: item for item in fragments_by_entity[entity_id]}.values(),
            key=lambda item: (item["created_at"], item["id"]),
        )
        offset = 0
        while offset < len(candidates):
            anchor_time = candidates[offset]["created_at"]
            group = [
                item for item in candidates[offset:]
                if item["created_at"] - anchor_time <= WINDOW_SECONDS
            ][:MAX_GROUP_SIZE]
            if len(group) < MIN_GROUP_SIZE:
                break
            ids = [item["id"] for item in group]
            fingerprint = _grouping_fingerprint(ids)
            proposals[fingerprint] = {
                "fingerprint": fingerprint,
                "fragments": group,
                "shared_entity_ids": sorted(set.intersection(
                    *(entity_by_fragment[item["id"]] for item in group)
                )),
                "scores": score_group(group, entity_by_fragment),
            }
            offset += len(group)
    return list(proposals.values())


def score_group(fragments: list[dict], entity_by_fragment: dict[str, set[str]]) -> dict:
    if not MIN_GROUP_SIZE <= len(fragments) <= MAX_GROUP_SIZE:
        raise ValueError("Episode 分组必须包含 2 到 20 条 Fragment")
    ordered = sorted(fragments, key=lambda item: (item["created_at"], item["id"]))
    span = ordered[-1]["created_at"] - ordered[0]["created_at"]
    if span < 0 or span > WINDOW_SECONDS:
        raise ValueError("Episode 分组时间跨度不能超过 7 天")
    entity_sets = [entity_by_fragment.get(item["id"], set()) for item in ordered]
    shared = set.intersection(*entity_sets) if entity_sets and all(entity_sets) else set()
    union = set.union(*entity_sets) if entity_sets else set()
    entity_score = len(shared) / len(union) if union else 0.0
    similarities = [
        _text_similarity(left["content"], right["content"])
        for index, left in enumerate(ordered)
        for right in ordered[index + 1:]
    ]
    text_score = sum(similarities) / len(similarities) if similarities else 0.0
    time_score = max(0.0, 1.0 - span / WINDOW_SECONDS)
    coherence_score = sum(
        _dominant_ratio(ordered, field) for field in ("emotion", "scope", "kind")
    ) / 3
    return combine_scores(entity_score, text_score, time_score, coherence_score)


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


def _create_scored_candidate(conn, proposal: dict, now: float) -> dict | None:
    fingerprint = proposal["fingerprint"]
    existing = conn.execute(
        "SELECT * FROM memory_episode_candidates WHERE grouping_key=?", (fingerprint,)
    ).fetchone()
    if existing:
        return None
    fragments = proposal["fragments"]
    fragment_ids = [fragment["id"] for fragment in fragments]
    entity_names = [row["name"] for row in conn.execute(
        f"SELECT e.name FROM memory_entities e WHERE e.id IN"
        f" ({','.join('?' for _ in proposal['shared_entity_ids'])}) ORDER BY e.name",
        proposal["shared_entity_ids"],
    ).fetchall()] if proposal["shared_entity_ids"] else []
    fallback = episode_summary.extractive_fallback(
        fragments=fragments, entity_names=entity_names
    )
    title = fallback["title"]
    summary = fallback["summary"]
    significance = _estimate_significance(fragments)
    scores = proposal["scores"]
    candidate_id = db.new_id()
    conn.execute(
        "INSERT INTO memory_episode_candidates("
        "id,title,summary,start_at,end_at,significance,confidence,status,grouping_key,created_at,"
        "entity_score,text_score,time_score,coherence_score,score_details_json,policy_version,"
        "expires_at,last_evaluated_at,summary_status,summary_protocol_version,"
        "summary_evidence_json,summary_warnings_json,summary_error_code,summary_source_hash)"
        " VALUES(?,?,?,?,?,?,?,'pending',?,?,?,?,?,?,?,?,?,?,'extractive_fallback',?,?,?,?,?)",
        (
            candidate_id, title, summary, fragments[0]["created_at"], fragments[-1]["created_at"],
            significance, scores["total"], fingerprint, now, scores["entity"], scores["text"],
            scores["time"], scores["coherence"], json.dumps(scores, separators=(",", ":")),
            GROUP_POLICY_VERSION, now + WINDOW_SECONDS, now,
            fallback["protocol_version"],
            json.dumps(fallback["evidence_fragment_ids"]),
            json.dumps(fallback["warnings"], ensure_ascii=False),
            "summary_not_attempted", fallback["source_hash"],
        ),
    )
    for position, fid in enumerate(fragment_ids):
        conn.execute(
            "INSERT INTO memory_episode_candidate_fragments(candidate_id,fragment_id,position)"
            " VALUES(?,?,?)",
            (candidate_id, fid, position),
        )
    candidate = _candidate_row(
        conn,
        conn.execute("SELECT * FROM memory_episode_candidates WHERE id=?", (candidate_id,)).fetchone(),
    )
    _supersede_low_groups(conn, fragment_ids, fingerprint, candidate_id, now)
    _event(conn, "episode_candidate", candidate_id, "proposed", None, candidate, "rule")
    return candidate


def _record_low_score_group(conn, proposal: dict, now: float) -> None:
    fingerprint = proposal["fingerprint"]
    existing = conn.execute(
        "SELECT * FROM episode_group_candidates WHERE grouping_fingerprint=?", (fingerprint,)
    ).fetchone()
    scores = proposal["scores"]
    if existing:
        if existing["status"] != "observing":
            return
        conn.execute(
            "UPDATE episode_group_candidates SET entity_score=?,text_score=?,time_score=?,"
            "coherence_score=?,total_score=?,evaluation_count=evaluation_count+1,"
            "last_evaluated_at=? WHERE id=?",
            (
                scores["entity"], scores["text"], scores["time"], scores["coherence"],
                scores["total"], now, existing["id"],
            ),
        )
        return
    fragment_ids = [fragment["id"] for fragment in proposal["fragments"]]
    _supersede_low_groups(conn, fragment_ids, fingerprint, None, now)
    conn.execute(
        "INSERT INTO episode_group_candidates("
        "id,grouping_fingerprint,status,fragment_ids_json,shared_entity_ids_json,entity_score,"
        "text_score,time_score,coherence_score,total_score,policy_version,first_seen_at,"
        "last_evaluated_at,expires_at) VALUES(?,?,'observing',?,?,?,?,?,?,?,?,?,?,?)",
        (
            db.new_id(), fingerprint, json.dumps(fragment_ids),
            json.dumps(proposal["shared_entity_ids"]), scores["entity"], scores["text"],
            scores["time"], scores["coherence"], scores["total"], GROUP_POLICY_VERSION,
            now, now, now + WINDOW_SECONDS,
        ),
    )


def _supersede_low_groups(
    conn, fragment_ids: list[str], fingerprint: str, promoted_id: str | None, now: float,
) -> None:
    wanted = set(fragment_ids)
    rows = conn.execute(
        "SELECT * FROM episode_group_candidates WHERE status='observing'"
    ).fetchall()
    for row in rows:
        current = set(json.loads(row["fragment_ids_json"]))
        if not current & wanted:
            continue
        status = "qualified" if row["grouping_fingerprint"] == fingerprint and promoted_id else "superseded"
        conn.execute(
            "UPDATE episode_group_candidates SET status=?,promoted_candidate_id=?,"
            "last_evaluated_at=? WHERE id=?",
            (status, promoted_id, now, row["id"]),
        )


def _expire_group_candidates(conn, now: float) -> int:
    cursor = conn.execute(
        "UPDATE episode_group_candidates SET status='expired',last_evaluated_at=?"
        " WHERE status='observing' AND expires_at<=?",
        (now, now),
    )
    return cursor.rowcount


def _load_fragments(conn, fragment_ids: list[str]) -> list[dict]:
    if not fragment_ids:
        return []
    rows = conn.execute(
        f"SELECT f.* FROM memory_fragments f WHERE f.id IN ({','.join('?' for _ in fragment_ids)})"
        " AND f.status='active' AND NOT EXISTS ("
        " SELECT 1 FROM memory_episode_fragments ef WHERE ef.fragment_id=f.id) ORDER BY f.created_at",
        fragment_ids,
    ).fetchall()
    return [dict(row) for row in rows]


def _candidate_row(conn, row) -> dict:
    result = dict(row)
    result["score_details"] = json.loads(result.pop("score_details_json", "{}"))
    result["summary_evidence_fragment_ids"] = json.loads(
        result.pop("summary_evidence_json", "[]")
    )
    result["summary_warnings"] = json.loads(result.pop("summary_warnings_json", "[]"))
    fragments = conn.execute(
        "SELECT f.*, ecf.position, s.title AS source_session_title,"
        " CASE WHEN m.id IS NULL THEN 0 ELSE 1 END AS source_available"
        " FROM memory_episode_candidate_fragments ecf"
        " JOIN memory_fragments f ON f.id=ecf.fragment_id"
        " LEFT JOIN sessions s ON s.id=f.source_session_id"
        " LEFT JOIN messages m ON m.id=f.source_message_id"
        " WHERE ecf.candidate_id=? ORDER BY ecf.position",
        (result["id"],),
    ).fetchall()
    result["fragments"] = [_fragment_row(fragment) for fragment in fragments]
    return result


def shared_entity_names(conn, fragment_ids: list[str]) -> list[str]:
    if not fragment_ids:
        return []
    placeholders = ",".join("?" for _ in fragment_ids)
    rows = conn.execute(
        f"SELECT e.name,COUNT(DISTINCT fe.fragment_id) AS linked_count"
        f" FROM memory_entities e JOIN memory_fragment_entities fe ON fe.entity_id=e.id"
        f" WHERE e.status='active' AND fe.fragment_id IN ({placeholders})"
        " GROUP BY e.id HAVING linked_count=? ORDER BY e.name",
        (*fragment_ids, len(set(fragment_ids))),
    ).fetchall()
    return [row["name"] for row in rows]


def apply_model_summary(
    candidate_id: str, raw: str | dict, *, provider_id: str, model: str,
    prompt_tokens: int | None, completion_tokens: int | None,
    repair_attempted: bool, expected_source_hash: str,
) -> dict | None:
    """在写锁内重新读取来源并校验；原始模型输出永不落库。"""
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM memory_episode_candidates WHERE id=? AND status='pending'",
            (candidate_id,),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        before = _candidate_row(conn, row)
        fragment_ids = [fragment["id"] for fragment in before["fragments"]]
        if episode_summary.source_hash(before["fragments"]) != expected_source_hash:
            conn.rollback()
            raise episode_summary.EpisodeSummaryValidationError(
                "summary_source_changed", "模型调用期间 Episode 来源发生变化"
            )
        entity_names = shared_entity_names(conn, fragment_ids)
        validated = episode_summary.parse_and_validate(
            raw, fragments=before["fragments"], entity_names=entity_names
        )
        now = db.now()
        conn.execute(
            "UPDATE memory_episode_candidates SET title=?,summary=?,summary_status='model_validated',"
            "summary_protocol_version=?,summary_provider_id=?,summary_model=?,"
            "summary_evidence_json=?,summary_warnings_json=?,summary_error_code=NULL,"
            "summary_source_hash=?,summary_prompt_tokens=?,summary_completion_tokens=?,"
            "summary_repair_attempted=?,last_evaluated_at=? WHERE id=?",
            (
                validated["title"], validated["summary"], validated["protocol_version"],
                provider_id, model, json.dumps(validated["evidence_fragment_ids"]),
                json.dumps(validated["warnings"], ensure_ascii=False), validated["source_hash"],
                prompt_tokens, completion_tokens, 1 if repair_attempted else 0, now, candidate_id,
            ),
        )
        after = _candidate_row(
            conn, conn.execute(
                "SELECT * FROM memory_episode_candidates WHERE id=?", (candidate_id,)
            ).fetchone(),
        )
        _event(conn, "episode_candidate", candidate_id, "summary_validated", before, after, "model")
        conn.commit()
        return after
    except episode_summary.EpisodeSummaryValidationError:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_summary_fallback(
    candidate_id: str, error_code: str, *, provider_id: str | None = None,
    model: str | None = None, prompt_tokens: int | None = None,
    completion_tokens: int | None = None, repair_attempted: bool = False,
) -> dict | None:
    """从当前来源重新生成抽取摘要，避免模型失败时保留过期或幻觉文本。"""
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM memory_episode_candidates WHERE id=? AND status='pending'",
            (candidate_id,),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        before = _candidate_row(conn, row)
        fragment_ids = [fragment["id"] for fragment in before["fragments"]]
        fallback = episode_summary.extractive_fallback(
            fragments=before["fragments"],
            entity_names=shared_entity_names(conn, fragment_ids),
        )
        source_changed = fallback["source_hash"] != before["summary_source_hash"]
        now = db.now()
        warnings = [*fallback["warnings"], {"code": error_code}]
        conn.execute(
            "UPDATE memory_episode_candidates SET title=?,summary=?,"
            "summary_status='extractive_fallback',summary_protocol_version=?,"
            "summary_provider_id=?,summary_model=?,summary_evidence_json=?,"
            "summary_warnings_json=?,summary_error_code=?,summary_source_hash=?,"
            "summary_prompt_tokens=?,summary_completion_tokens=?,summary_repair_attempted=?,"
            "application_attempt_count=?,application_error_code=?,last_evaluated_at=? WHERE id=?",
            (
                fallback["title"], fallback["summary"], fallback["protocol_version"],
                provider_id, model, json.dumps(fallback["evidence_fragment_ids"]),
                json.dumps(warnings, ensure_ascii=False), error_code, fallback["source_hash"],
                prompt_tokens, completion_tokens, 1 if repair_attempted else 0,
                0 if source_changed else before["application_attempt_count"],
                None if source_changed else before["application_error_code"], now, candidate_id,
            ),
        )
        after = _candidate_row(
            conn, conn.execute(
                "SELECT * FROM memory_episode_candidates WHERE id=?", (candidate_id,)
            ).fetchone(),
        )
        _event(conn, "episode_candidate", candidate_id, "summary_fallback", before, after, "system")
        conn.commit()
        return after
    finally:
        conn.close()


def _group_candidate_row(row) -> dict:
    result = dict(row)
    result["fragment_ids"] = json.loads(result.pop("fragment_ids_json"))
    result["shared_entity_ids"] = json.loads(result.pop("shared_entity_ids_json"))
    return result


def _episode_row(conn, row) -> dict:
    result = _episode_list_row(row)
    fragments = conn.execute(
        "SELECT f.*, ef.position, s.title AS source_session_title,"
        " CASE WHEN m.id IS NULL THEN 0 ELSE 1 END AS source_available"
        " FROM memory_episode_fragments ef JOIN memory_fragments f ON f.id=ef.fragment_id"
        " LEFT JOIN sessions s ON s.id=f.source_session_id"
        " LEFT JOIN messages m ON m.id=f.source_message_id"
        " WHERE ef.episode_id=? ORDER BY ef.position",
        (result["id"],),
    ).fetchall()
    entities = conn.execute(
        "SELECT e.id,e.name,e.entity_type FROM memory_episode_entities ee"
        " JOIN memory_entities e ON e.id=ee.entity_id WHERE ee.episode_id=?",
        (result["id"],),
    ).fetchall()
    result["fragments"] = [_fragment_row(fragment) for fragment in fragments]
    result["entities"] = [dict(entity) for entity in entities]
    result["fragment_count"] = len(fragments)
    return result


def _episode_list_row(row) -> dict:
    result = dict(row)
    result["source_fragment_ids"] = json.loads(
        result.pop("source_fragment_ids_json", "[]")
    )
    result["summary_evidence_fragment_ids"] = json.loads(
        result.pop("summary_evidence_json", "[]")
    )
    return result


def _fragment_row(row) -> dict:
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    result["source_available"] = bool(result.get("source_available", False))
    return result


def _text_similarity(left: str, right: str) -> float:
    a, b = _grams(left), _grams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _grouping_fingerprint(fragment_ids: list[str]) -> str:
    stable = f"{GROUP_POLICY_VERSION}|{'|'.join(sorted(set(fragment_ids)))}"
    return hashlib.sha256(stable.encode()).hexdigest()


def _dominant_ratio(fragments: list[dict], field: str) -> float:
    counts: dict[str, int] = {}
    for fragment in fragments:
        value = str(fragment.get(field) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return max(counts.values()) / len(fragments) if fragments else 0.0


def _unit(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 6)


def _grams(text: str) -> set[str]:
    clean = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", text.casefold())
    if len(clean) < 3:
        return {clean} if clean else set()
    return {clean[index:index + 3] for index in range(len(clean) - 2)}


def _estimate_significance(fragments: list[dict]) -> int:
    text = " ".join(fragment["content"] for fragment in fragments)
    score = 3 + min(2, len(fragments) - 2)
    if any(hint in text for hint in SIGNIFICANT_HINTS):
        score += 2
    if all(any(hint in fragment["content"] for hint in ROUTINE_HINTS) for fragment in fragments):
        score = min(score, 4)
    return max(1, min(10, score))


def _event(conn, object_type: str, object_id: str, action: str, before, after, source: str) -> None:
    conn.execute(
        "INSERT INTO memory_events(id,object_type,object_id,action,before_json,after_json,source,created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (
            db.new_id(), object_type, object_id, action,
            json.dumps(before, ensure_ascii=False) if before is not None else None,
            json.dumps(after, ensure_ascii=False) if after is not None else None,
            source, db.now(),
        ),
    )


def _consolidator_event(
    conn, run_id: str, action: str, before_status: str | None, after_status: str,
    reason_code: str | None, metadata: dict,
) -> None:
    conn.execute(
        "INSERT INTO episode_consolidator_events("
        "id,run_id,action,before_status,after_status,reason_code,metadata_json,created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (
            db.new_id(), run_id, action, before_status, after_status, reason_code,
            json.dumps(metadata, ensure_ascii=False), db.now(),
        ),
    )
