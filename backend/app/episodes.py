"""Episode 候选与正式经历。

参考 MemoryConstellations Consolidator 的边界：2~20 条碎片、继承来源、时间范围和
独立 significance。第一版不用模型，以共同实体、时间窗口和文本重合生成可解释候选。
"""
from __future__ import annotations

import hashlib
import json
import re

from . import db

MIN_GROUP_SIZE = 2
MAX_GROUP_SIZE = 20
WINDOW_SECONDS = 7 * 24 * 60 * 60
MIN_TEXT_SIMILARITY = 0.12

ROUTINE_HINTS = ("配置", "代码", "报错", "接口", "构建", "测试", "修复", "开发")
SIGNIFICANT_HINTS = ("第一次", "决定", "完成", "成功", "纪念", "搬到", "旅行", "毕业", "入职")


def generate_candidates() -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT f.id FROM memory_fragments f"
            " WHERE f.status='active' AND f.enabled=1"
            " AND NOT EXISTS (SELECT 1 FROM memory_episode_fragments ef WHERE ef.fragment_id=f.id)"
            " AND NOT EXISTS (SELECT 1 FROM memory_episode_candidate_fragments ecf"
            " JOIN memory_episode_candidates ec ON ec.id=ecf.candidate_id"
            " WHERE ecf.fragment_id=f.id AND ec.status='pending')"
            " ORDER BY f.created_at"
        ).fetchall()
        created = []
        for row in rows:
            candidate = _maybe_generate(conn, row["id"])
            if candidate:
                created.append(candidate)
        conn.commit()
        return created
    finally:
        conn.close()


def maybe_generate_for_fragment(fragment_id: str, conn=None) -> dict | None:
    own_conn = conn is None
    conn = conn or db.connect()
    try:
        result = _maybe_generate(conn, fragment_id)
        if own_conn:
            conn.commit()
        return result
    finally:
        if own_conn:
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


def _maybe_generate(conn, fragment_id: str) -> dict | None:
    anchor = conn.execute(
        "SELECT * FROM memory_fragments WHERE id=? AND status='active' AND enabled=1"
        " AND NOT EXISTS (SELECT 1 FROM memory_episode_fragments ef WHERE ef.fragment_id=memory_fragments.id)",
        (fragment_id,),
    ).fetchone()
    if not anchor or conn.execute(
        "SELECT 1 FROM memory_episode_candidate_fragments ecf"
        " JOIN memory_episode_candidates ec ON ec.id=ecf.candidate_id"
        " WHERE ecf.fragment_id=? AND ec.status='pending' LIMIT 1",
        (fragment_id,),
    ).fetchone():
        return None
    entity_ids = [row["entity_id"] for row in conn.execute(
        "SELECT entity_id FROM memory_fragment_entities WHERE fragment_id=?", (fragment_id,)
    ).fetchall()]
    params: list = [fragment_id, anchor["created_at"] - WINDOW_SECONDS, anchor["created_at"] + WINDOW_SECONDS]
    relation_clause = ""
    if entity_ids:
        relation_clause = (
            f" AND EXISTS (SELECT 1 FROM memory_fragment_entities fe WHERE fe.fragment_id=f.id"
            f" AND fe.entity_id IN ({','.join('?' for _ in entity_ids)}))"
        )
        params.extend(entity_ids)
    elif anchor["source_session_id"]:
        relation_clause = " AND f.source_session_id=?"
        params.append(anchor["source_session_id"])
    else:
        return None
    rows = conn.execute(
        "SELECT f.* FROM memory_fragments f WHERE f.id!=? AND f.status='active' AND f.enabled=1"
        " AND f.created_at BETWEEN ? AND ?"
        " AND NOT EXISTS (SELECT 1 FROM memory_episode_fragments ef WHERE ef.fragment_id=f.id)"
        " AND NOT EXISTS (SELECT 1 FROM memory_episode_candidate_fragments ecf"
        " JOIN memory_episode_candidates ec ON ec.id=ecf.candidate_id"
        " WHERE ecf.fragment_id=f.id AND ec.status='pending')"
        + relation_clause + " ORDER BY f.created_at DESC LIMIT 60",
        params,
    ).fetchall()
    matches = []
    for row in rows:
        similarity = _text_similarity(anchor["content"], row["content"])
        same_session = bool(
            anchor["source_session_id"] and anchor["source_session_id"] == row["source_session_id"]
        )
        if similarity >= MIN_TEXT_SIMILARITY or same_session:
            matches.append((row, similarity))
    if not matches:
        return None
    matches.sort(key=lambda item: (item[1], item[0]["created_at"]), reverse=True)
    fragments = [dict(anchor), *[dict(item[0]) for item in matches[:MAX_GROUP_SIZE - 1]]]
    fragments.sort(key=lambda item: item["created_at"])
    fragment_ids = [fragment["id"] for fragment in fragments]
    grouping_key = hashlib.sha256("|".join(sorted(fragment_ids)).encode()).hexdigest()[:32]
    existing = conn.execute(
        "SELECT * FROM memory_episode_candidates WHERE grouping_key=?", (grouping_key,)
    ).fetchone()
    if existing:
        return None
    entity_names = [row["name"] for row in conn.execute(
        f"SELECT DISTINCT e.name FROM memory_entities e"
        f" JOIN memory_fragment_entities fe ON fe.entity_id=e.id"
        f" WHERE fe.fragment_id IN ({','.join('?' for _ in fragment_ids)}) AND e.status='active'",
        fragment_ids,
    ).fetchall()]
    subject = entity_names[0] if entity_names else "这段对话"
    title = f"关于{subject}的一段经历"
    summary = "；".join(fragment["content"].strip() for fragment in fragments)[:300]
    significance = _estimate_significance(fragments)
    avg_similarity = sum(item[1] for item in matches[:MAX_GROUP_SIZE - 1]) / len(matches[:MAX_GROUP_SIZE - 1])
    confidence = min(0.95, 0.68 + avg_similarity * 0.5)
    candidate_id = db.new_id()
    t = db.now()
    conn.execute(
        "INSERT INTO memory_episode_candidates("
        "id,title,summary,start_at,end_at,significance,confidence,status,grouping_key,created_at)"
        " VALUES(?,?,?,?,?,?,?,'pending',?,?)",
        (
            candidate_id, title, summary, fragments[0]["created_at"], fragments[-1]["created_at"],
            significance, confidence, grouping_key, t,
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
    _event(conn, "episode_candidate", candidate_id, "proposed", None, candidate, "rule")
    return candidate


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
