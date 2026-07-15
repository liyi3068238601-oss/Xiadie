"""把已净化的记忆观察结果原子写入正式 Fragment。

调用方必须已经开启 SQLite 事务；本模块不 commit，也不创建自己的连接。
"""
from __future__ import annotations

import json
import re
import sqlite3

from . import db, entities, memory, memory_observer as observer


class MemoryApplyError(RuntimeError):
    """只携带稳定错误码，不回显候选或消息正文。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def apply_observation_in_transaction(
    conn: sqlite3.Connection,
    *,
    run: dict,
    candidate: dict,
    audit: dict,
) -> list[str]:
    """复核来源并写 Fragment、实体关系、事件和 applied 状态。"""
    current = conn.execute(
        "SELECT status FROM memory_observer_runs WHERE id=?", (run["id"],)
    ).fetchone()
    if not current or current["status"] != "running":
        raise MemoryApplyError("observer_run_not_running")

    messages = _load_and_verify_sources(conn, run, candidate)
    payload = {
        "protocol_version": candidate.get("protocol_version"),
        "should_write": bool(candidate.get("items")),
        "items": candidate.get("items") or [],
    }
    try:
        revalidated = observer.parse_and_validate(payload, messages=messages)
    except observer.MemoryObserverValidationError as exc:
        raise MemoryApplyError(f"apply_{exc.code}") from exc
    if revalidated["items"] != (candidate.get("items") or []):
        raise MemoryApplyError("apply_candidate_changed")

    fragment_ids: list[str] = []
    for index, item in enumerate(revalidated["items"]):
        idempotency_key = (
            f"{observer.PROTOCOL_VERSION}:{run['source_assistant_message_id']}:{index}"
        )
        existing = conn.execute(
            "SELECT id FROM memory_fragments WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        if existing:
            fragment_ids.append(existing["id"])
            continue
        duplicate = _find_duplicate(conn, item)
        if duplicate:
            fragment_ids.append(duplicate["id"])
            continue

        fragment_id = db.new_id()
        now = db.now()
        primary_source = _primary_source_message(item, messages)
        conn.execute(
            "INSERT INTO memory_fragments("
            "id,layer,content,tags,source,source_session_id,source_message_id,confidence,"
            "sensitivity,status,enabled,created_at,updated_at,scope,kind,importance,emotion,"
            "inner_reason,observer_version,evidence_message_ids,source_assistant_message_id,"
            "idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,'active',?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                fragment_id, "L2", item["content"],
                f"observer,{item['scope']},{item['kind']}", "observer",
                run["source_session_id"], primary_source, item["confidence"],
                item["sensitivity"], 0 if item["sensitivity"] == "sensitive" else 1,
                now, now, item["scope"], item["kind"], item["importance"], item["emotion"],
                item["inner_reason"], observer.PROTOCOL_VERSION,
                json.dumps(item["evidence_message_ids"], ensure_ascii=False),
                run["source_assistant_message_id"], idempotency_key,
            ),
        )
        fragment = memory._get_fragment(conn, fragment_id)
        if item["sensitivity"] == "normal":
            _link_observed_entities(conn, fragment_id, item)
            entities.auto_link_fragment(fragment_id, item["content"], conn=conn)
        memory._event(
            conn, "fragment", fragment_id, "autonomous_created", None, fragment, "observer"
        )
        fragment_ids.append(fragment_id)

    now = db.now()
    conn.execute(
        "UPDATE memory_observer_runs SET status='applied',candidate_json=?,warnings_json=?,"
        " error_code=NULL,next_attempt_at=NULL,applied_fragment_ids_json=?,applied_at=?,"
        " input_chars=?,output_chars=?,prompt_tokens=?,completion_tokens=?,latency_ms=?,"
        " repair_attempted=?,updated_at=? WHERE id=?",
        (
            json.dumps(candidate, ensure_ascii=False),
            json.dumps(candidate.get("warnings") or [], ensure_ascii=False),
            json.dumps(fragment_ids, ensure_ascii=False), now,
            audit["input_chars"], audit["output_chars"], audit["prompt_tokens"],
            audit["completion_tokens"], audit["latency_ms"],
            1 if audit["repair_attempted"] else 0, now, run["id"],
        ),
    )
    return fragment_ids


def _load_and_verify_sources(conn, run: dict, candidate: dict) -> list[dict]:
    source_rows = conn.execute(
        "SELECT id,role,content,created_at FROM messages WHERE session_id=?"
        " AND id IN (?,?)",
        (
            run["source_session_id"], run["source_user_message_id"],
            run["source_assistant_message_id"],
        ),
    ).fetchall()
    source_by_id = {row["id"]: dict(row) for row in source_rows}
    user = source_by_id.get(run["source_user_message_id"])
    assistant = source_by_id.get(run["source_assistant_message_id"])
    if not user or user["role"] != "user" or not assistant or assistant["role"] != "assistant":
        raise MemoryApplyError("apply_source_unavailable")

    evidence_ids = list(dict.fromkeys(
        message_id
        for item in (candidate.get("items") or [])
        for message_id in (item.get("evidence_message_ids") or [])
    ))
    rows = []
    if evidence_ids:
        placeholders = ",".join("?" for _ in evidence_ids)
        rows = conn.execute(
            f"SELECT id,role,content,created_at FROM messages WHERE session_id=?"
            f" AND id IN ({placeholders})",
            (run["source_session_id"], *evidence_ids),
        ).fetchall()
    evidence_by_id = {row["id"]: dict(row) for row in rows}
    if set(evidence_by_id) != set(evidence_ids):
        raise MemoryApplyError("apply_evidence_unavailable")
    if any(row["created_at"] > assistant["created_at"] for row in evidence_by_id.values()):
        raise MemoryApplyError("apply_evidence_after_source")
    return list(evidence_by_id.values())


def _find_duplicate(conn, item: dict):
    target = _normalize(item["content"])
    rows = conn.execute(
        "SELECT id,content FROM memory_fragments WHERE status!='tombstone'"
        " AND scope=? AND kind=?",
        (item["scope"], item["kind"]),
    ).fetchall()
    for row in rows:
        value = _normalize(row["content"])
        # 只做保守等值去重；近似但可能含否定/时间变化的内容留给后续冲突系统。
        if value == target:
            return row
    return None


def _primary_source_message(item: dict, messages: list[dict]) -> str:
    by_id = {message["id"]: message for message in messages}
    for message_id in item["evidence_message_ids"]:
        if by_id[message_id]["role"] == "user":
            return message_id
    return item["evidence_message_ids"][0]


def _link_observed_entities(conn, fragment_id: str, item: dict) -> None:
    for name in item.get("entities") or []:
        if name in ("用户", "遐蝶"):
            continue
        entity = entities.create_entity(name, "concept", source="observer", conn=conn)
        entities.link_fragment(
            entity["id"], fragment_id, "mentions", item["confidence"], "observer", conn
        )


def _normalize(text: str) -> str:
    value = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", str(text)).casefold()
    for prefix in ("用户表示", "用户说", "用户", "遐蝶表示", "遐蝶说"):
        if value.startswith(prefix):
            return value[len(prefix):]
    return value
