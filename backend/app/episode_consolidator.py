"""Episode 后台整理任务账本。

C.1 只提供幂等排队、查询、取消和状态审计；不运行分组、模型或 Episode 写入。
"""
from __future__ import annotations

import json

from . import db

POLICY_VERSION = "episode-consolidator-v1"
MAX_ATTEMPTS = 3
TRIGGERS = frozenset({"startup", "idle", "manual", "fragment"})
TERMINAL_STATUSES = frozenset({"cancelled", "applied", "exhausted", "skipped"})


def enqueue(*, trigger: str, request_key: str | None = None) -> dict:
    """建立一个幂等 run；重复 request_key 返回原 run，不重复写事件。"""
    if trigger not in TRIGGERS:
        raise ValueError("Episode 整理触发类型无效")
    stable_key = (request_key or "").strip() or db.new_id()
    idempotency_key = f"{POLICY_VERSION}:{trigger}:{stable_key}"
    conn = db.connect()
    try:
        now = db.now()
        run_id = db.new_id()
        cursor = conn.execute(
            "INSERT OR IGNORE INTO episode_consolidator_runs("
            "id,idempotency_key,trigger,status,policy_version,max_attempts,next_attempt_at,"
            "created_at,updated_at) VALUES(?,?,?,'queued',?,?,?,?,?)",
            (
                run_id, idempotency_key, trigger, POLICY_VERSION, MAX_ATTEMPTS,
                now, now, now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM episode_consolidator_runs WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if cursor.rowcount:
            _event(conn, run_id, "enqueued", None, "queued", "triggered", {"trigger": trigger})
        conn.commit()
        return _run_row(conn, row, include_events=True)
    finally:
        conn.close()


def list_runs(*, limit: int = 50) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM episode_consolidator_runs ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
        return [_run_row(conn, row) for row in rows]
    finally:
        conn.close()


def get_run(run_id: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM episode_consolidator_runs WHERE id=?", (run_id,)
        ).fetchone()
        return _run_row(conn, row, include_events=True) if row else None
    finally:
        conn.close()


def cancel(run_id: str) -> dict | None:
    """queued/recovery_pending 立即取消；running 只请求协作式取消。"""
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM episode_consolidator_runs WHERE id=?", (run_id,)
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        before = row["status"]
        if before == "cancelled" or before == "cancel_requested":
            conn.rollback()
            return _run_row(conn, row, include_events=True)
        if before in TERMINAL_STATUSES:
            conn.rollback()
            raise ValueError("已结束的 Episode 整理任务不能取消")
        now = db.now()
        if before in ("queued", "recovery_pending"):
            after = "cancelled"
            conn.execute(
                "UPDATE episode_consolidator_runs SET status=?,next_attempt_at=NULL,"
                "finished_at=?,updated_at=? WHERE id=?",
                (after, now, now, run_id),
            )
        elif before == "running":
            after = "cancel_requested"
            conn.execute(
                "UPDATE episode_consolidator_runs SET status=?,updated_at=? WHERE id=?",
                (after, now, run_id),
            )
        else:
            conn.rollback()
            raise ValueError(f"不能从 {before} 状态取消 Episode 整理任务")
        _event(conn, run_id, "cancelled" if after == "cancelled" else "cancel_requested",
               before, after, "user_cancelled", {})
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM episode_consolidator_runs WHERE id=?", (run_id,)
        ).fetchone()
        return _run_row(conn, updated, include_events=True)
    finally:
        conn.close()


def _event(
    conn, run_id: str, action: str, before_status: str | None, after_status: str,
    reason_code: str | None, metadata: dict,
) -> None:
    conn.execute(
        "INSERT INTO episode_consolidator_events("
        "id,run_id,action,before_status,after_status,reason_code,metadata_json,created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (
            db.new_id(), run_id, action, before_status, after_status, reason_code,
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), db.now(),
        ),
    )


def _run_row(conn, row, *, include_events: bool = False) -> dict:
    result = dict(row)
    result["input_fragment_ids"] = json.loads(result.pop("input_fragment_ids_json"))
    result["result_episode_ids"] = json.loads(result.pop("result_episode_ids_json"))
    if include_events:
        events = conn.execute(
            "SELECT * FROM episode_consolidator_events WHERE run_id=?"
            " ORDER BY created_at,rowid",
            (result["id"],),
        ).fetchall()
        result["events"] = []
        for event in events:
            item = dict(event)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result["events"].append(item)
    return result
