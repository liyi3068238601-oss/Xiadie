"""可追溯记忆基础：正式片段、待确认候选、来源链和审计事件。"""
from __future__ import annotations

import json

from . import db

MAX_INJECT = 12
AUTO_HINTS = ("我叫", "我喜欢", "我在做", "我正在", "我的项目", "记住", "我偏好", "以后")
SENSITIVE_HINTS = (
    "密码", "密钥", "验证码", "身份证", "银行卡", "住址", "病历", "诊断", "收入", "账号",
)


def list_memories(layer: str | None = None, only_enabled: bool = False) -> list[dict]:
    conn = db.connect()
    try:
        sql = "SELECT * FROM memory_fragments WHERE status != 'tombstone'"
        params: list = []
        if layer:
            sql += " AND layer = ?"
            params.append(layer)
        if only_enabled:
            sql += " AND enabled = 1 AND status = 'active'"
        sql += " ORDER BY CASE layer WHEN 'L0' THEN 0 WHEN 'L1' THEN 1 ELSE 2 END, updated_at DESC"
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


def build_digest() -> tuple[str, bool]:
    if db.get_setting("memory_enabled", "1") != "1":
        return "", False
    memories = list_memories(only_enabled=True)[:MAX_INJECT]
    if not memories:
        return "", False
    lines = []
    for memory in memories:
        prefix = {"L0": "[核心]", "L1": "[近期]", "L2": "[长期]"}.get(memory["layer"], "")
        lines.append(f"- {prefix} {memory['content']}")
    return "\n".join(lines), True


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
        sql = "SELECT * FROM memory_candidates"
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC"
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
    row = conn.execute("SELECT * FROM memory_fragments WHERE id = ?", (mid,)).fetchone()
    return _fragment_row(row) if row else None


def _get_candidate(conn, cid: str) -> dict | None:
    row = conn.execute("SELECT * FROM memory_candidates WHERE id = ?", (cid,)).fetchone()
    return _candidate_row(row) if row else None


def _fragment_row(row) -> dict:
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    return result


def _candidate_row(row) -> dict:
    return dict(row)


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
