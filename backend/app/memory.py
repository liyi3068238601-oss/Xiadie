"""记忆逻辑（需求 6.3）：分层、注入摘要、轻量自动抽取。

设计原则：
- 记忆必须可见、可控、可删除 —— 存储在 memories 表，全部走 CRUD 暴露给前端。
- 敏感记忆不自动写入 —— 自动抽取只做保守的启发式，且标注 source='auto'。
- 聊天使用记忆有轻量提示 —— build_digest 返回是否命中，供前端展示"已参考记忆"。
"""
import json

from . import db

# 注入聊天上下文的记忆条数上限，避免无限增长挤占 token（需求：记忆不应无限增长）
MAX_INJECT = 12

# 触发自动抽取的关键信号（保守：只抓明确的偏好/事实陈述）
AUTO_HINTS = ("我叫", "我喜欢", "我在做", "我正在", "我的项目", "记住", "我偏好", "以后")


def list_memories(layer: str | None = None, only_enabled: bool = False) -> list[dict]:
    conn = db.connect()
    try:
        sql = "SELECT * FROM memories"
        clauses, params = [], []
        if layer:
            clauses.append("layer = ?")
            params.append(layer)
        if only_enabled:
            clauses.append("enabled = 1")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY CASE layer WHEN 'L0' THEN 0 WHEN 'L1' THEN 1 ELSE 2 END, updated_at DESC"
        return [_row(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def create_memory(layer: str, content: str, tags: str = "", source: str = "manual") -> dict:
    if layer not in ("L0", "L1", "L2"):
        layer = "L2"
    conn = db.connect()
    try:
        mid = db.new_id()
        t = db.now()
        conn.execute(
            "INSERT INTO memories(id, layer, content, tags, source, enabled, created_at, updated_at)"
            " VALUES(?,?,?,?,?,1,?,?)",
            (mid, layer, content.strip(), tags, source, t, t),
        )
        conn.commit()
        return _get(conn, mid)
    finally:
        conn.close()


def update_memory(mid: str, **fields) -> dict | None:
    allowed = {"layer", "content", "tags", "enabled"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not sets:
        return get_memory(mid)
    conn = db.connect()
    try:
        cols = ", ".join(f"{k} = ?" for k in sets)
        conn.execute(
            f"UPDATE memories SET {cols}, updated_at = ? WHERE id = ?",
            (*sets.values(), db.now(), mid),
        )
        conn.commit()
        return _get(conn, mid)
    finally:
        conn.close()


def delete_memory(mid: str) -> None:
    conn = db.connect()
    try:
        conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
        conn.commit()
    finally:
        conn.close()


def get_memory(mid: str) -> dict | None:
    conn = db.connect()
    try:
        return _get(conn, mid)
    finally:
        conn.close()


def build_digest() -> tuple[str, bool]:
    """构造注入 system prompt 的记忆摘要。返回 (文本, 是否命中记忆)。"""
    if db.get_setting("memory_enabled", "1") != "1":
        return "", False
    mems = list_memories(only_enabled=True)[:MAX_INJECT]
    if not mems:
        return "", False
    lines = []
    for m in mems:
        prefix = {"L0": "[核心]", "L1": "[近期]", "L2": "[长期]"}.get(m["layer"], "")
        lines.append(f"- {prefix} {m['content']}")
    return "\n".join(lines), True


def maybe_auto_extract(user_text: str) -> dict | None:
    """从用户消息里保守地抽取一条可记忆的偏好/事实。

    只在出现明确信号词时触发，写入 L1（近期状态）并标注 source='auto'，
    保证敏感信息不会静默进核心画像，且用户可在记忆页删除/编辑。
    """
    text = user_text.strip()
    if len(text) < 4 or len(text) > 120:
        return None
    if not any(h in text for h in AUTO_HINTS):
        return None
    # 去重：同内容不重复写
    for m in list_memories():
        if m["content"] == text:
            return None
    return create_memory("L1", text, tags="auto", source="auto")


def _row(r) -> dict:
    d = dict(r)
    d["enabled"] = bool(d["enabled"])
    return d


def _get(conn, mid: str) -> dict | None:
    row = conn.execute("SELECT * FROM memories WHERE id = ?", (mid,)).fetchone()
    return _row(row) if row else None
