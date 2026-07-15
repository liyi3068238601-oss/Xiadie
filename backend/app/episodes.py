"""Episode 候选与正式经历。

参考 MemoryConstellations Consolidator 的边界：2~20 条碎片、继承来源、时间范围和
独立 significance。第一版不用模型，以共同实体、时间窗口和文本重合生成可解释候选。
"""
from __future__ import annotations

import hashlib
import json
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

ROUTINE_HINTS = ("配置", "代码", "报错", "接口", "构建", "测试", "修复", "开发")
SIGNIFICANT_HINTS = ("第一次", "决定", "完成", "成功", "纪念", "搬到", "旅行", "毕业", "入职")


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
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_episode(episode_id: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM memory_episodes WHERE id=?", (episode_id,)).fetchone()
        return _episode_row(conn, row) if row else None
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
        row = conn.execute(
            "SELECT * FROM memory_episode_candidates WHERE id=? AND status='pending'",
            (candidate_id,),
        ).fetchone()
        if not row:
            return None
        candidate = _candidate_row(conn, row)
        allowed_ids = [fragment["id"] for fragment in candidate["fragments"]]
        chosen_ids = fragment_ids if fragment_ids is not None else allowed_ids
        chosen_ids = list(dict.fromkeys(fid for fid in chosen_ids if fid in allowed_ids))
        if len(chosen_ids) < MIN_GROUP_SIZE:
            raise ValueError("Episode 至少需要 2 条候选记忆")
        fragments = _load_fragments(conn, chosen_ids)
        if len(fragments) < MIN_GROUP_SIZE:
            raise ValueError("候选记忆不存在或已被其他 Episode 使用")
        episode_id = db.new_id()
        t = db.now()
        chosen_title = (title if title is not None else candidate["title"]).strip()
        chosen_summary = (summary if summary is not None else candidate["summary"]).strip()
        chosen_significance = max(1, min(10, int(
            significance if significance is not None else candidate["significance"]
        )))
        conn.execute(
            "INSERT INTO memory_episodes("
            "id,title,summary,start_at,end_at,significance,confidence,status,source,candidate_id,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,'active','candidate_confirmed',?,?,?)",
            (
                episode_id, chosen_title, chosen_summary,
                min(f["created_at"] for f in fragments), max(f["created_at"] for f in fragments),
                chosen_significance, candidate["confidence"], candidate_id, t, t,
            ),
        )
        for position, fragment in enumerate(fragments):
            conn.execute(
                "INSERT INTO memory_episode_fragments(episode_id,fragment_id,position,created_at)"
                " VALUES(?,?,?,?)",
                (episode_id, fragment["id"], position, t),
            )
        entity_rows = conn.execute(
            f"SELECT DISTINCT entity_id FROM memory_fragment_entities"
            f" WHERE fragment_id IN ({','.join('?' for _ in chosen_ids)})",
            chosen_ids,
        ).fetchall()
        for entity_row in entity_rows:
            conn.execute(
                "INSERT OR IGNORE INTO memory_episode_entities(episode_id,entity_id,created_at)"
                " VALUES(?,?,?)",
                (episode_id, entity_row["entity_id"], t),
            )
        conn.execute(
            "UPDATE memory_episode_candidates SET status='accepted', resolved_episode_id=?,"
            " resolved_at=? WHERE id=?",
            (episode_id, t, candidate_id),
        )
        episode = _episode_row(
            conn, conn.execute("SELECT * FROM memory_episodes WHERE id=?", (episode_id,)).fetchone()
        )
        _event(conn, "episode_candidate", candidate_id, "accepted", candidate, episode, "user")
        _event(conn, "episode", episode_id, "created", None, episode, "candidate")
        conn.commit()
        return episode
    finally:
        conn.close()


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
        now = db.now()
        warnings = [*fallback["warnings"], {"code": error_code}]
        conn.execute(
            "UPDATE memory_episode_candidates SET title=?,summary=?,"
            "summary_status='extractive_fallback',summary_protocol_version=?,"
            "summary_provider_id=?,summary_model=?,summary_evidence_json=?,"
            "summary_warnings_json=?,summary_error_code=?,summary_source_hash=?,"
            "summary_prompt_tokens=?,summary_completion_tokens=?,summary_repair_attempted=?,"
            "last_evaluated_at=? WHERE id=?",
            (
                fallback["title"], fallback["summary"], fallback["protocol_version"],
                provider_id, model, json.dumps(fallback["evidence_fragment_ids"]),
                json.dumps(warnings, ensure_ascii=False), error_code, fallback["source_hash"],
                prompt_tokens, completion_tokens, 1 if repair_attempted else 0, now, candidate_id,
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
    result = dict(row)
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
