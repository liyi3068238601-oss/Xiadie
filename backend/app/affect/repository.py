"""心境快照、关系快照和事件日志的 SQLite 存储。"""
from __future__ import annotations

import json

from .. import db
from . import engine

AFFECT_FIELDS = (
    "contact_need", "guardedness_transient", "valence", "arousal", "immersion",
    "activity_type", "activity_label", "activity_started_at", "last_user_message_at",
    "last_tick_at", "updated_at",
)
RELATIONSHIP_FIELDS = ("bond", "trust", "interaction_count", "updated_at")
AFFECT_EVENT_FIELDS = tuple(field for field in AFFECT_FIELDS if field != "updated_at")
RELATIONSHIP_EVENT_FIELDS = tuple(
    field for field in RELATIONSHIP_FIELDS if field != "updated_at"
)


def get_snapshot(*, advance_time: bool = True) -> dict:
    conn = db.connect()
    try:
        _ensure(conn)
        if not advance_time:
            return _load(conn)
        # 单用户桌面应用仍可能同时收到 SSE 聊天和状态栏读取。
        # IMMEDIATE 锁让“重新读取 → 推进 → 保存”成为一个不可被覆盖的事务。
        conn.execute("BEGIN IMMEDIATE")
        snapshot = _load(conn)
        now = db.now()
        elapsed = max(0.0, (now - snapshot["affect"]["last_tick_at"]) / 60)
        if elapsed < 1.0:
            conn.rollback()
            return snapshot
        before = snapshot
        after = engine.advance(before, elapsed)
        after["affect"]["last_tick_at"] = now
        after["affect"]["updated_at"] = now
        _save(conn, after)
        _event(
            conn, "tick", "engine", before, after,
            reason=f"按真实经过时间推进 {min(elapsed, engine.MAX_ELAPSED_MINUTES):.2f} 分钟",
        )
        conn.commit()
        return after
    finally:
        conn.close()


def get_preview_snapshot() -> dict:
    """计算截至当前的只读预览，不为生成前语调产生额外写事务。"""
    conn = db.connect()
    try:
        affect_exists = conn.execute("SELECT 1 FROM affect_state WHERE id=1").fetchone()
        relation_exists = conn.execute("SELECT 1 FROM relationship_state WHERE id=1").fetchone()
        if affect_exists is None or relation_exists is None:
            _ensure(conn)
        snapshot = _load(conn)
        now = db.now()
        elapsed = max(0.0, (now - snapshot["affect"]["last_tick_at"]) / 60)
        if elapsed < 1.0:
            return snapshot
        preview = engine.advance(snapshot, elapsed)
        preview["affect"]["last_tick_at"] = now
        preview["affect"]["updated_at"] = now
        return preview
    finally:
        conn.close()


def save_snapshot(
    snapshot: dict,
    *,
    event_type: str,
    source: str,
    reason: str,
    source_session_id: str | None = None,
    source_message_id: str | None = None,
) -> dict:
    conn = db.connect()
    try:
        _ensure(conn)
        conn.execute("BEGIN IMMEDIATE")
        before = _load(conn)
        after = engine.normalize(_internal_snapshot(snapshot))
        now = db.now()
        after["affect"]["updated_at"] = now
        after["relationship"]["updated_at"] = now
        _save(conn, after)
        _event(
            conn, event_type, source, before, after, reason=reason,
            source_session_id=source_session_id,
            source_message_id=source_message_id,
        )
        conn.commit()
        return after
    finally:
        conn.close()


def apply_interaction(
    user_text: str,
    *,
    source_session_id: str | None = None,
    source_message_id: str | None = None,
) -> dict:
    """在最新持久化快照上原子应用一次成功互动，避免流式响应期间的状态覆盖。"""
    conn = db.connect()
    try:
        _ensure(conn)
        conn.execute("BEGIN IMMEDIATE")
        before = _load(conn)
        now = db.now()
        elapsed = max(0.0, (now - before["affect"]["last_tick_at"]) / 60)
        base = before
        if elapsed >= 1.0:
            base = engine.advance(before, elapsed)
            base["affect"]["last_tick_at"] = now
            base["affect"]["updated_at"] = now
            _event(
                conn,
                "tick",
                "engine",
                before,
                base,
                reason=f"按真实经过时间推进 {min(elapsed, engine.MAX_ELAPSED_MINUTES):.2f} 分钟",
            )
        after = engine.apply_fallback_interaction(base, user_text)
        after["affect"]["last_user_message_at"] = now
        after["affect"]["updated_at"] = now
        after["relationship"]["updated_at"] = now
        _save(conn, after)
        _event(
            conn,
            "interaction",
            "fallback",
            base,
            after,
            reason="成功完成一轮对话，应用保守本地状态变化",
            source_session_id=source_session_id,
            source_message_id=source_message_id,
        )
        conn.commit()
        return after
    finally:
        conn.close()


def advance_by(minutes: float) -> dict:
    conn = db.connect()
    try:
        _ensure(conn)
        conn.execute("BEGIN IMMEDIATE")
        before = _load(conn)
        after = engine.advance(before, minutes)
        now = db.now()
        after["affect"]["last_tick_at"] = now
        after["affect"]["updated_at"] = now
        _save(conn, after)
        _event(
            conn, "tick", "developer", before, after,
            reason=f"手动推进 {minutes:.2f} 分钟",
        )
        conn.commit()
        return after
    finally:
        conn.close()


def reset() -> dict:
    now = db.now()
    snapshot = {
        "affect": {**engine.DEFAULT_AFFECT, "last_tick_at": now, "updated_at": now},
        "relationship": {**engine.DEFAULT_RELATIONSHIP, "updated_at": now},
    }
    return save_snapshot(snapshot, event_type="reset", source="user", reason="用户重置状态")


def list_events(limit: int = 50) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM affect_events ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for key in ("before_json", "delta_json", "after_json"):
                item[key.removesuffix("_json")] = json.loads(item.pop(key))
            result.append(item)
        return result
    finally:
        conn.close()


def _ensure(conn) -> None:
    now = db.now()
    affect = engine.DEFAULT_AFFECT
    relation = engine.DEFAULT_RELATIONSHIP
    conn.execute(
        "INSERT OR IGNORE INTO affect_state("
        "id,contact_need,guardedness_transient,valence,arousal,immersion,"
        "activity_type,activity_label,activity_started_at,last_user_message_at,"
        "last_tick_at,updated_at) VALUES(1,?,?,?,?,?,?,?,?,?,?,?)",
        (
            affect["contact_need"], affect["guardedness_transient"], affect["valence"],
            affect["arousal"], affect["immersion"], affect["activity_type"],
            affect["activity_label"], affect["activity_started_at"],
            affect["last_user_message_at"], now, now,
        ),
    )
    conn.execute(
        "INSERT OR IGNORE INTO relationship_state(id,bond,trust,interaction_count,updated_at)"
        " VALUES(1,?,?,?,?)",
        (relation["bond"], relation["trust"], relation["interaction_count"], now),
    )
    conn.commit()


def _load(conn) -> dict:
    affect_row = conn.execute("SELECT * FROM affect_state WHERE id=1").fetchone()
    relation_row = conn.execute("SELECT * FROM relationship_state WHERE id=1").fetchone()
    return {"affect": dict(affect_row), "relationship": dict(relation_row)}


def _save(conn, snapshot: dict) -> None:
    affect = snapshot["affect"]
    relation = snapshot["relationship"]
    conn.execute(
        "INSERT INTO affect_state("
        "id,contact_need,guardedness_transient,valence,arousal,immersion,activity_type,"
        "activity_label,activity_started_at,last_user_message_at,last_tick_at,updated_at)"
        " VALUES(1,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "contact_need=excluded.contact_need,guardedness_transient=excluded.guardedness_transient,"
        "valence=excluded.valence,arousal=excluded.arousal,immersion=excluded.immersion,"
        "activity_type=excluded.activity_type,activity_label=excluded.activity_label,"
        "activity_started_at=excluded.activity_started_at,"
        "last_user_message_at=excluded.last_user_message_at,last_tick_at=excluded.last_tick_at,"
        "updated_at=excluded.updated_at",
        (
            affect["contact_need"], affect["guardedness_transient"], affect["valence"],
            affect["arousal"], affect["immersion"], affect.get("activity_type"),
            affect.get("activity_label"), affect.get("activity_started_at"),
            affect.get("last_user_message_at"), affect["last_tick_at"], affect["updated_at"],
        ),
    )
    conn.execute(
        "INSERT INTO relationship_state(id,bond,trust,interaction_count,updated_at)"
        " VALUES(1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET bond=excluded.bond,"
        "trust=excluded.trust,interaction_count=excluded.interaction_count,"
        "updated_at=excluded.updated_at",
        (
            relation["bond"], relation["trust"], relation["interaction_count"],
            relation["updated_at"],
        ),
    )


def _event(
    conn,
    event_type: str,
    source: str,
    before: dict,
    after: dict,
    *,
    reason: str,
    source_session_id: str | None = None,
    source_message_id: str | None = None,
) -> None:
    before_clean = _public_numbers(before)
    after_clean = _public_numbers(after)
    delta = {
        section: {
            key: round(after_clean[section][key] - value, 8)
            for key, value in before_clean[section].items()
            if isinstance(value, (int, float))
            and key in after_clean[section]
            and isinstance(after_clean[section][key], (int, float))
        }
        for section in ("affect", "relationship")
    }
    conn.execute(
        "INSERT INTO affect_events(id,event_type,source,source_session_id,source_message_id,"
        "before_json,delta_json,after_json,reason,algorithm_version,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            db.new_id(), event_type, source, source_session_id, source_message_id,
            json.dumps(before_clean, ensure_ascii=False),
            json.dumps(delta, ensure_ascii=False),
            json.dumps(after_clean, ensure_ascii=False),
            reason, engine.ALGORITHM_VERSION, db.now(),
        ),
    )


def _public_numbers(snapshot: dict) -> dict:
    return {
        "affect": {key: snapshot["affect"].get(key) for key in AFFECT_EVENT_FIELDS},
        "relationship": {
            key: snapshot["relationship"].get(key) for key in RELATIONSHIP_EVENT_FIELDS
        },
    }


def _internal_snapshot(snapshot: dict) -> dict:
    return {
        "affect": {key: snapshot["affect"].get(key) for key in AFFECT_FIELDS},
        "relationship": {
            key: snapshot["relationship"].get(key) for key in RELATIONSHIP_FIELDS
        },
    }
