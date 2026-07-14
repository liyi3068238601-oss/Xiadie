"""可追溯记忆基础：正式片段、待确认候选、来源链和审计事件。"""
from __future__ import annotations

import json
import re

from . import db

MAX_INJECT = 12
MAX_INJECT_CHARS = 2400
AUTO_HINTS = ("我叫", "我喜欢", "我在做", "我正在", "我的项目", "记住", "我偏好", "以后")
SENSITIVE_HINTS = (
    "密码", "密钥", "验证码", "身份证", "银行卡", "住址", "病历", "诊断", "收入", "账号",
)


def list_memories(layer: str | None = None, only_enabled: bool = False) -> list[dict]:
    conn = db.connect()
    try:
        sql = (
            "SELECT f.*, s.title AS source_session_title,"
            " CASE WHEN m.id IS NULL THEN 0 ELSE 1 END AS source_available"
            " FROM memory_fragments f"
            " LEFT JOIN sessions s ON s.id = f.source_session_id"
            " LEFT JOIN messages m ON m.id = f.source_message_id"
            " WHERE f.status != 'tombstone'"
        )
        params: list = []
        if layer:
            sql += " AND f.layer = ?"
            params.append(layer)
        if only_enabled:
            sql += " AND f.enabled = 1 AND f.status = 'active'"
        sql += " ORDER BY CASE f.layer WHEN 'L0' THEN 0 WHEN 'L1' THEN 1 ELSE 2 END, f.updated_at DESC"
        return [_fragment_row(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def create_memory(
    layer: str,
    content: str,
    tags: str = "",
    source: str = "manual",
    source_session_id: str | None = None,
    source_message_id: str | None = None,
    confidence: float = 1.0,
    sensitivity: str = "normal",
) -> dict:
    if layer not in ("L0", "L1", "L2"):
        layer = "L2"
    conn = db.connect()
    try:
        memory = _create_fragment(
            conn,
            layer=layer,
            content=content,
            tags=tags,
            source=source,
            source_session_id=source_session_id,
            source_message_id=source_message_id,
            confidence=confidence,
            sensitivity=sensitivity,
        )
        _event(conn, "fragment", memory["id"], "created", None, memory, source)
        conn.commit()
        return memory
    finally:
        conn.close()


def update_memory(mid: str, **fields) -> dict | None:
    allowed = {"layer", "content", "tags", "enabled", "status"}
    sets = {key: value for key, value in fields.items() if key in allowed and value is not None}
    if not sets:
        return get_memory(mid)
    conn = db.connect()
    try:
        before = _get_fragment(conn, mid)
        if not before:
            return None
        columns = ", ".join(f"{key} = ?" for key in sets)
        conn.execute(
            f"UPDATE memory_fragments SET {columns}, updated_at = ? WHERE id = ?",
            (*sets.values(), db.now(), mid),
        )
        after = _get_fragment(conn, mid)
        _event(conn, "fragment", mid, "updated", before, after, "user")
        conn.commit()
        return after
    finally:
        conn.close()


def delete_memory(mid: str) -> bool:
    """使用墓碑状态保留审计链；对列表和召回表现为已删除。"""
    conn = db.connect()
    try:
        before = _get_fragment(conn, mid)
        if not before:
            return False
        conn.execute(
            "UPDATE memory_fragments SET status='tombstone', enabled=0, updated_at=? WHERE id=?",
            (db.now(), mid),
        )
        after = _get_fragment(conn, mid)
        _event(conn, "fragment", mid, "deleted", before, after, "user")
        conn.commit()
        return True
    finally:
        conn.close()


def get_memory(mid: str) -> dict | None:
    conn = db.connect()
    try:
        return _get_fragment(conn, mid)
    finally:
        conn.close()


def search_memories(query: str, limit: int = MAX_INJECT) -> list[dict]:
    """FTS5 优先的相关记忆召回；短查询使用 LIKE 回退。"""
    if db.get_setting("memory_enabled", "1") != "1":
        return []
    match_query = _fts_query(query)
    conn = db.connect()
    try:
        if match_query:
            rows = conn.execute(
                "SELECT f.*, s.title AS source_session_title,"
                " CASE WHEN m.id IS NULL THEN 0 ELSE 1 END AS source_available,"
                " bm25(memory_fragments_fts, 1.0, 0.35) AS text_rank"
                " FROM memory_fragments_fts"
                " JOIN memory_fragments f ON f.rowid = memory_fragments_fts.rowid"
                " LEFT JOIN sessions s ON s.id = f.source_session_id"
                " LEFT JOIN messages m ON m.id = f.source_message_id"
                " WHERE memory_fragments_fts MATCH ?"
                " AND f.enabled = 1 AND f.status = 'active'"
                " ORDER BY text_rank LIMIT ?",
                (match_query, max(limit * 3, limit)),
            ).fetchall()
        else:
            terms = _fallback_terms(query)
            if not terms:
                return []
            clauses = " OR ".join("(f.content LIKE ? OR f.tags LIKE ?)" for _ in terms)
            params = [value for term in terms for value in (f"%{term}%", f"%{term}%")]
            rows = conn.execute(
                "SELECT f.*, s.title AS source_session_title,"
                " CASE WHEN m.id IS NULL THEN 0 ELSE 1 END AS source_available, 0 AS text_rank"
                " FROM memory_fragments f"
                " LEFT JOIN sessions s ON s.id = f.source_session_id"
                " LEFT JOIN messages m ON m.id = f.source_message_id"
                f" WHERE f.enabled = 1 AND f.status = 'active' AND ({clauses})"
                " ORDER BY f.updated_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        memories = [_fragment_row(row) for row in rows]
        memories.sort(key=_retrieval_score, reverse=True)
        return memories[:limit]
    finally:
        conn.close()


def build_digest(query: str) -> tuple[str, list[dict]]:
    if db.get_setting("memory_enabled", "1") != "1":
        return "", []
    memories = search_memories(query, MAX_INJECT)
    if not memories:
        return "", []
    lines = []
    used: list[dict] = []
    total_chars = 0
    for memory in memories:
        prefix = {"L0": "[核心]", "L1": "[近期]", "L2": "[长期]"}.get(memory["layer"], "")
        line = f"- {prefix} {memory['content']}"
        if lines and total_chars + len(line) > MAX_INJECT_CHARS:
            break
        lines.append(line)
        total_chars += len(line)
        used.append(memory)
    return "\n".join(lines), used


def maybe_create_candidate(
    user_text: str,
    source_session_id: str,
    source_message_id: str,
) -> dict | None:
    """保守识别明确记忆信号，只创建候选，不直接写入正式记忆。"""
    text = user_text.strip()
    if len(text) < 4 or len(text) > 240 or not any(hint in text for hint in AUTO_HINTS):
        return None
    conn = db.connect()
    try:
        duplicate = conn.execute(
            "SELECT * FROM memory_candidates WHERE source_message_id = ? AND content = ?",
            (source_message_id, text),
        ).fetchone()
        if duplicate:
            return _candidate_row(duplicate)
        cid = db.new_id()
        sensitivity = "sensitive" if any(hint in text for hint in SENSITIVE_HINTS) else "normal"
        confidence = 0.85 if "记住" in text else 0.7
        t = db.now()
        conn.execute(
            "INSERT INTO memory_candidates("
            "id, content, proposed_layer, tags, source_session_id, source_message_id, confidence,"
            " sensitivity, status, created_at) VALUES(?,?,?,?,?,?,?,?, 'pending', ?)",
            (
                cid, text, "L1", "auto", source_session_id, source_message_id,
                confidence, sensitivity, t,
            ),
        )
        candidate = _get_candidate(conn, cid)
        _event(conn, "candidate", cid, "proposed", None, candidate, "auto")
        conn.commit()
        return candidate
    finally:
        conn.close()


def list_candidates(status: str | None = "pending") -> list[dict]:
    conn = db.connect()
    try:
        sql = (
            "SELECT c.*, s.title AS source_session_title,"
            " CASE WHEN m.id IS NULL THEN 0 ELSE 1 END AS source_available"
            " FROM memory_candidates c"
            " LEFT JOIN sessions s ON s.id = c.source_session_id"
            " LEFT JOIN messages m ON m.id = c.source_message_id"
        )
        params: list = []
        if status:
            sql += " WHERE c.status = ?"
            params.append(status)
        sql += " ORDER BY c.created_at DESC"
        return [_candidate_row(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def get_candidate(cid: str) -> dict | None:
    conn = db.connect()
    try:
        return _get_candidate(conn, cid)
    finally:
        conn.close()


def accept_candidate(
    cid: str,
    content: str | None = None,
    layer: str | None = None,
    tags: str | None = None,
) -> dict | None:
    conn = db.connect()
    try:
        candidate = _get_candidate(conn, cid)
        if not candidate or candidate["status"] != "pending":
            return None
        chosen_content = (content if content is not None else candidate["content"]).strip()
        chosen_layer = layer or candidate["proposed_layer"]
        chosen_tags = tags if tags is not None else candidate["tags"]
        memory = _create_fragment(
            conn,
            layer=chosen_layer,
            content=chosen_content,
            tags=chosen_tags,
            source="auto_confirmed",
            source_session_id=candidate["source_session_id"],
            source_message_id=candidate["source_message_id"],
            confidence=candidate["confidence"],
            sensitivity=candidate["sensitivity"],
        )
        resolved_at = db.now()
        conn.execute(
            "UPDATE memory_candidates SET status='accepted', resolved_memory_id=?, resolved_at=?"
            " WHERE id=?",
            (memory["id"], resolved_at, cid),
        )
        accepted = _get_candidate(conn, cid)
        _event(conn, "candidate", cid, "accepted", candidate, accepted, "user")
        _event(conn, "fragment", memory["id"], "created", None, memory, "candidate")
        conn.commit()
        return {"candidate": accepted, "memory": memory}
    finally:
        conn.close()


def reject_candidate(cid: str, note: str = "") -> dict | None:
    conn = db.connect()
    try:
        candidate = _get_candidate(conn, cid)
        if not candidate or candidate["status"] != "pending":
            return None
        conn.execute(
            "UPDATE memory_candidates SET status='rejected', resolution_note=?, resolved_at=?"
            " WHERE id=?",
            (note.strip(), db.now(), cid),
        )
        rejected = _get_candidate(conn, cid)
        _event(conn, "candidate", cid, "rejected", candidate, rejected, "user")
        conn.commit()
        return rejected
    finally:
        conn.close()


def list_events(object_type: str, object_id: str) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM memory_events WHERE object_type=? AND object_id=? ORDER BY created_at",
            (object_type, object_id),
        ).fetchall()
        return [_event_row(row) for row in rows]
    finally:
        conn.close()


def _create_fragment(conn, **values) -> dict:
    mid = db.new_id()
    t = db.now()
    conn.execute(
        "INSERT INTO memory_fragments("
        "id, layer, content, tags, source, source_session_id, source_message_id, confidence,"
        " sensitivity, status, enabled, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,'active',1,?,?)",
        (
            mid,
            values["layer"],
            values["content"].strip(),
            values["tags"],
            values["source"],
            values["source_session_id"],
            values["source_message_id"],
            max(0.0, min(1.0, float(values["confidence"]))),
            values["sensitivity"],
            t,
            t,
        ),
    )
    return _get_fragment(conn, mid)


def _get_fragment(conn, mid: str) -> dict | None:
    row = conn.execute(
        "SELECT f.*, s.title AS source_session_title,"
        " CASE WHEN m.id IS NULL THEN 0 ELSE 1 END AS source_available"
        " FROM memory_fragments f"
        " LEFT JOIN sessions s ON s.id = f.source_session_id"
        " LEFT JOIN messages m ON m.id = f.source_message_id"
        " WHERE f.id = ?",
        (mid,),
    ).fetchone()
    return _fragment_row(row) if row else None


def _get_candidate(conn, cid: str) -> dict | None:
    row = conn.execute(
        "SELECT c.*, s.title AS source_session_title,"
        " CASE WHEN m.id IS NULL THEN 0 ELSE 1 END AS source_available"
        " FROM memory_candidates c"
        " LEFT JOIN sessions s ON s.id = c.source_session_id"
        " LEFT JOIN messages m ON m.id = c.source_message_id"
        " WHERE c.id = ?",
        (cid,),
    ).fetchone()
    return _candidate_row(row) if row else None


def _fragment_row(row) -> dict:
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    result["source_available"] = bool(result.get("source_available", False))
    return result


def _candidate_row(row) -> dict:
    result = dict(row)
    result["source_available"] = bool(result.get("source_available", False))
    return result


def _fts_query(query: str) -> str:
    terms = re.findall(r"[\u4e00-\u9fff]{3,}|[A-Za-z0-9_\-]{3,}", query)
    chunks: list[str] = []
    for term in terms:
        if re.fullmatch(r"[\u4e00-\u9fff]+", term):
            chunks.extend(term[index:index + 3] for index in range(len(term) - 2))
        else:
            chunks.append(term)
    unique = list(dict.fromkeys(chunks))[:16]
    return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in unique)


def _fallback_terms(query: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"[\u4e00-\u9fff]{1,2}|[A-Za-z0-9_\-]{2,}", query)))[:8]


def _retrieval_score(memory: dict) -> float:
    layer_bonus = {"L0": 0.22, "L1": 0.12, "L2": 0.06}.get(memory["layer"], 0)
    confidence_bonus = float(memory.get("confidence", 0)) * 0.08
    text_rank = -float(memory.get("text_rank", 0) or 0)
    return text_rank + layer_bonus + confidence_bonus


def _event(conn, object_type: str, object_id: str, action: str, before, after, source: str) -> None:
    conn.execute(
        "INSERT INTO memory_events(id, object_type, object_id, action, before_json, after_json,"
        " source, created_at) VALUES(?,?,?,?,?,?,?,?)",
        (
            db.new_id(), object_type, object_id, action,
            json.dumps(before, ensure_ascii=False) if before is not None else None,
            json.dumps(after, ensure_ascii=False) if after is not None else None,
            source, db.now(),
        ),
    )


def _event_row(row) -> dict:
    result = dict(row)
    result["before"] = json.loads(result.pop("before_json")) if result["before_json"] else None
    result["after"] = json.loads(result.pop("after_json")) if result["after_json"] else None
    return result
