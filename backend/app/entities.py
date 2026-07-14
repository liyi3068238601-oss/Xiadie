"""实体档案与碎片关联。

参考 MemoryConstellations 的 EntityProfile / EntityResolver：
- 优先通过规范名称和别名匹配；
- 只对高置信度句式创建新实体；
- 不确定时保持未关联，允许用户手动修正。
"""
from __future__ import annotations

import json
import re

from . import db

ENTITY_TYPES = {
    "person", "pet", "organization", "place", "event", "project", "work",
    "hobby", "concept",
}

PATTERNS = [
    ("pet", re.compile(r"(?:猫|狗|宠物)(?:咪)?(?:叫|名叫)([A-Za-z0-9_\-·\u4e00-\u9fff]{1,16})")),
    ("person", re.compile(r"(?:朋友|同事|老师|同学|妈妈|爸爸|母亲|父亲|姐姐|妹妹|哥哥|弟弟)(?:叫|名叫)([A-Za-z0-9_\-·\u4e00-\u9fff]{1,16})")),
    ("person", re.compile(r"我叫([A-Za-z0-9_\-·\u4e00-\u9fff]{1,16})")),
    ("project", re.compile(r"(?:项目|产品)(?:叫|名为|是)([A-Za-z0-9_\-·\u4e00-\u9fff ]{2,24})")),
    ("organization", re.compile(r"(?:在|加入)([A-Za-z0-9_\-·\u4e00-\u9fff]{2,20}(?:公司|大学|学校|团队))")),
    ("place", re.compile(r"(?:住在|生活在|搬到)([A-Za-z0-9_\-·\u4e00-\u9fff]{2,16})")),
]


def list_entities(status: str = "active") -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT e.*, COUNT(fe.fragment_id) AS fragment_count"
            " FROM memory_entities e"
            " LEFT JOIN memory_fragment_entities fe ON fe.entity_id = e.id"
            " WHERE e.status = ?"
            " GROUP BY e.id ORDER BY fragment_count DESC, e.updated_at DESC, e.name",
            (status,),
        ).fetchall()
        return [_entity_row(row) for row in rows]
    finally:
        conn.close()


def get_entity(eid: str) -> dict | None:
    conn = db.connect()
    try:
        return _get_entity(conn, eid, include_fragments=True)
    finally:
        conn.close()


def create_entity(
    name: str,
    entity_type: str = "concept",
    aliases: list[str] | None = None,
    summary: str = "",
    tags: list[str] | None = None,
    source: str = "manual",
    conn=None,
) -> dict:
    own_conn = conn is None
    conn = conn or db.connect()
    try:
        clean_name = _clean_name(name)
        existing = _find_by_name_or_alias(conn, clean_name)
        if existing:
            return existing
        eid = db.new_id()
        t = db.now()
        safe_type = entity_type if entity_type in ENTITY_TYPES else "concept"
        clean_aliases = _clean_list(aliases or [], exclude=clean_name)
        conn.execute(
            "INSERT INTO memory_entities("
            "id, name, entity_type, summary, aliases, tags, status, source, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,'active',?,?,?)",
            (
                eid, clean_name, safe_type, summary.strip(),
                json.dumps(clean_aliases, ensure_ascii=False),
                json.dumps(_clean_list(tags or []), ensure_ascii=False), source, t, t,
            ),
        )
        entity = _get_entity(conn, eid)
        _event(conn, eid, "created", None, entity, source)
        _link_existing_mentions(conn, entity)
        if own_conn:
            conn.commit()
        return entity
    finally:
        if own_conn:
            conn.close()


def update_entity(eid: str, **fields) -> dict | None:
    conn = db.connect()
    try:
        before = _get_entity(conn, eid)
        if not before or before["status"] != "active":
            return None
        updates = {}
        if fields.get("name") is not None:
            proposed_name = _clean_name(fields["name"])
            conflict = _find_by_name_or_alias(conn, proposed_name)
            if conflict and conflict["id"] != eid:
                raise ValueError("名称或别名已被另一个实体使用")
            updates["name"] = proposed_name
        if fields.get("entity_type") is not None:
            updates["entity_type"] = (
                fields["entity_type"] if fields["entity_type"] in ENTITY_TYPES else "concept"
            )
        for key in ("summary", "current_status", "status_since"):
            if fields.get(key) is not None:
                updates[key] = str(fields[key]).strip()
        for key in ("aliases", "tags"):
            if fields.get(key) is not None:
                clean_values = _clean_list(
                    fields[key], exclude=updates.get("name", before["name"])
                )
                if key == "aliases":
                    for alias in clean_values:
                        conflict = _find_by_name_or_alias(conn, alias)
                        if conflict and conflict["id"] != eid:
                            raise ValueError(f"别名“{alias}”已被另一个实体使用")
                updates[key] = json.dumps(clean_values, ensure_ascii=False)
        if not updates:
            return _get_entity(conn, eid, include_fragments=True)
        columns = ", ".join(f"{key}=?" for key in updates)
        conn.execute(
            f"UPDATE memory_entities SET {columns}, updated_at=? WHERE id=?",
            (*updates.values(), db.now(), eid),
        )
        after = _get_entity(conn, eid)
        _event(conn, eid, "updated", before, after, "user")
        if "name" in updates or "aliases" in updates:
            _link_existing_mentions(conn, after)
        conn.commit()
        return _get_entity(conn, eid, include_fragments=True)
    finally:
        conn.close()


def archive_entity(eid: str) -> bool:
    conn = db.connect()
    try:
        before = _get_entity(conn, eid)
        if not before or before["status"] != "active":
            return False
        linked = conn.execute(
            "SELECT fragment_id FROM memory_fragment_entities WHERE entity_id=?", (eid,)
        ).fetchall()
        conn.execute("DELETE FROM memory_fragment_entities WHERE entity_id=?", (eid,))
        conn.execute(
            "UPDATE memory_entities SET status='archived', updated_at=? WHERE id=?",
            (db.now(), eid),
        )
        after = _get_entity(conn, eid)
        _event(
            conn, eid, "archived", before,
            {**after, "unlinked_fragment_ids": [row["fragment_id"] for row in linked]}, "user",
        )
        conn.commit()
        return True
    finally:
        conn.close()


def link_fragment(
    eid: str,
    fragment_id: str,
    relation: str = "mentions",
    confidence: float = 1.0,
    source: str = "user",
    conn=None,
) -> bool:
    own_conn = conn is None
    conn = conn or db.connect()
    try:
        entity = _get_entity(conn, eid)
        fragment = conn.execute(
            "SELECT id FROM memory_fragments WHERE id=? AND status != 'tombstone'", (fragment_id,)
        ).fetchone()
        if not entity or entity["status"] != "active" or not fragment:
            return False
        before = conn.total_changes
        conn.execute(
            "INSERT OR IGNORE INTO memory_fragment_entities("
            "fragment_id, entity_id, relation, created_at, confidence) VALUES(?,?,?,?,?)",
            (fragment_id, eid, relation.strip() or "mentions", db.now(), _clamp(confidence)),
        )
        created = conn.total_changes > before
        if created:
            conn.execute("UPDATE memory_entities SET updated_at=? WHERE id=?", (db.now(), eid))
            _event(
                conn, eid, "fragment_linked", None,
                {"fragment_id": fragment_id, "relation": relation, "confidence": _clamp(confidence)},
                source,
            )
        if own_conn:
            conn.commit()
        return True
    finally:
        if own_conn:
            conn.close()


def unlink_fragment(eid: str, fragment_id: str) -> bool:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT relation, confidence FROM memory_fragment_entities"
            " WHERE entity_id=? AND fragment_id=?",
            (eid, fragment_id),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "DELETE FROM memory_fragment_entities WHERE entity_id=? AND fragment_id=?",
            (eid, fragment_id),
        )
        conn.execute("UPDATE memory_entities SET updated_at=? WHERE id=?", (db.now(), eid))
        _event(
            conn, eid, "fragment_unlinked",
            {"fragment_id": fragment_id, **dict(row)}, None, "user",
        )
        conn.commit()
        return True
    finally:
        conn.close()


def merge_entities(target_id: str, source_id: str) -> dict | None:
    """把 source 并入 target；保留 source 档案和 merged_into_id 供审计。"""
    if target_id == source_id:
        return None
    conn = db.connect()
    try:
        target = _get_entity(conn, target_id)
        source = _get_entity(conn, source_id)
        if not target or not source or target["status"] != "active" or source["status"] != "active":
            return None
        rows = conn.execute(
            "SELECT fragment_id, relation, confidence, created_at"
            " FROM memory_fragment_entities WHERE entity_id=?",
            (source_id,),
        ).fetchall()
        for row in rows:
            conn.execute(
                "INSERT OR IGNORE INTO memory_fragment_entities("
                "fragment_id, entity_id, relation, confidence, created_at) VALUES(?,?,?,?,?)",
                (row["fragment_id"], target_id, row["relation"], row["confidence"], row["created_at"]),
            )
        aliases = _clean_list(
            [*target["aliases"], source["name"], *source["aliases"]], exclude=target["name"]
        )
        conn.execute("DELETE FROM memory_fragment_entities WHERE entity_id=?", (source_id,))
        conn.execute(
            "UPDATE memory_entities SET aliases=?, updated_at=? WHERE id=?",
            (json.dumps(aliases, ensure_ascii=False), db.now(), target_id),
        )
        conn.execute(
            "UPDATE memory_entities SET status='merged', merged_into_id=?, updated_at=? WHERE id=?",
            (target_id, db.now(), source_id),
        )
        _event(
            conn, target_id, "merged_in", target,
            {"source_entity_id": source_id, "source_name": source["name"]}, "user",
        )
        _event(
            conn, source_id, "merged", source,
            {"merged_into_id": target_id, "merged_into_name": target["name"]}, "user",
        )
        conn.commit()
        return _get_entity(conn, target_id, include_fragments=True)
    finally:
        conn.close()


def auto_link_fragment(fragment_id: str, content: str, conn=None) -> list[dict]:
    """名称/别名匹配 + 高置信度句式抽取；不做代词猜测。"""
    own_conn = conn is None
    conn = conn or db.connect()
    linked: list[dict] = []
    try:
        existing = conn.execute(
            "SELECT * FROM memory_entities WHERE status='active' ORDER BY length(name) DESC"
        ).fetchall()
        seen_ids: set[str] = set()
        lowered = content.casefold()
        for row in existing:
            entity = _entity_row(row)
            names = [entity["name"], *entity["aliases"]]
            if any(len(name.strip()) >= 2 and name.casefold() in lowered for name in names):
                link_fragment(entity["id"], fragment_id, "mentions", 0.95, "rule", conn)
                linked.append(entity)
                seen_ids.add(entity["id"])

        for entity_type, pattern in PATTERNS:
            for match in pattern.finditer(content):
                name = _clean_extracted_name(match.group(1))
                if not name:
                    continue
                entity = create_entity(name, entity_type, source="rule", conn=conn)
                if entity["id"] in seen_ids:
                    continue
                link_fragment(entity["id"], fragment_id, "mentions", 0.9, "rule", conn)
                linked.append(entity)
                seen_ids.add(entity["id"])
        if own_conn:
            conn.commit()
        return linked
    finally:
        if own_conn:
            conn.close()


def _get_entity(conn, eid: str, include_fragments: bool = False) -> dict | None:
    row = conn.execute(
        "SELECT e.*, (SELECT COUNT(*) FROM memory_fragment_entities fe"
        " WHERE fe.entity_id=e.id) AS fragment_count FROM memory_entities e WHERE e.id=?",
        (eid,),
    ).fetchone()
    if not row:
        return None
    entity = _entity_row(row)
    if include_fragments:
        fragments = conn.execute(
            "SELECT f.*, fe.relation, fe.confidence, s.title AS source_session_title,"
            " CASE WHEN m.id IS NULL THEN 0 ELSE 1 END AS source_available"
            " FROM memory_fragment_entities fe"
            " JOIN memory_fragments f ON f.id=fe.fragment_id"
            " LEFT JOIN sessions s ON s.id=f.source_session_id"
            " LEFT JOIN messages m ON m.id=f.source_message_id"
            " WHERE fe.entity_id=? AND f.status!='tombstone' ORDER BY f.updated_at DESC",
            (eid,),
        ).fetchall()
        entity["fragments"] = [_fragment_row(row) for row in fragments]
    return entity


def _find_by_name_or_alias(conn, name: str) -> dict | None:
    rows = conn.execute("SELECT * FROM memory_entities WHERE status='active'").fetchall()
    folded = name.casefold()
    for row in rows:
        entity = _entity_row(row)
        if entity["name"].casefold() == folded:
            return entity
        if any(alias.casefold() == folded for alias in entity["aliases"]):
            return entity
    return None


def _link_existing_mentions(conn, entity: dict) -> None:
    names = [entity["name"], *entity["aliases"]]
    names = [name.casefold() for name in names if len(name.strip()) >= 2]
    if not names:
        return
    rows = conn.execute(
        "SELECT id, content FROM memory_fragments WHERE status != 'tombstone'"
    ).fetchall()
    for row in rows:
        content = row["content"].casefold()
        if any(name in content for name in names):
            link_fragment(entity["id"], row["id"], "mentions", 0.9, "rule", conn)


def _entity_row(row) -> dict:
    result = dict(row)
    result["aliases"] = _json_list(result.get("aliases"))
    result["tags"] = _json_list(result.get("tags"))
    return result


def _fragment_row(row) -> dict:
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    result["source_available"] = bool(result.get("source_available", False))
    return result


def _event(conn, eid: str, action: str, before, after, source: str) -> None:
    conn.execute(
        "INSERT INTO memory_events(id, object_type, object_id, action, before_json, after_json,"
        " source, created_at) VALUES(?,'entity',?,?,?,?,?,?)",
        (
            db.new_id(), eid, action,
            json.dumps(before, ensure_ascii=False) if before is not None else None,
            json.dumps(after, ensure_ascii=False) if after is not None else None,
            source, db.now(),
        ),
    )


def _clean_name(name: str) -> str:
    clean = re.sub(r"\s+", " ", str(name)).strip(" ，。！？,.!?：:;；\"'“”")
    if not clean or len(clean) > 40:
        raise ValueError("实体名称不能为空且不能超过 40 个字符")
    return clean


def _clean_extracted_name(name: str) -> str:
    clean = re.split(r"[，。！？,.!?：:;；\s]|喜欢|正在|以后|然后", name.strip(), maxsplit=1)[0]
    if not clean or clean in {"这个", "那个", "一个", "项目", "公司", "学校", "团队"}:
        return ""
    return clean[:24]


def _clean_list(values, exclude: str = "") -> list[str]:
    if isinstance(values, str):
        values = re.split(r"[,，、\n]", values)
    result = []
    for value in values or []:
        clean = str(value).strip()
        if clean and clean.casefold() != exclude.casefold() and clean not in result:
            result.append(clean[:40])
    return result[:20]


def _json_list(value) -> list[str]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
