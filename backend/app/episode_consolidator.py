"""Episode 后台整理任务账本。

C.1 只提供幂等排队、查询、取消和状态审计；不运行分组、模型或 Episode 写入。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from contextlib import suppress

from . import db, episode_summary_service, episodes

POLICY_VERSION = "episode-consolidator-v1"
MAX_ATTEMPTS = 3
RUNNING_STALE_SECONDS = 5 * 60
FIRST_RETRY_DELAY_SECONDS = 60
WORKER_IDLE_SECONDS = 5 * 60
TRIGGERS = frozenset({"startup", "idle", "manual", "fragment"})
TERMINAL_STATUSES = frozenset({"cancelled", "applied", "exhausted", "skipped"})

_worker_task: asyncio.Task | None = None
_wake_event: asyncio.Event | None = None
_logger = logging.getLogger(__name__)


def enqueue(
    *, trigger: str, request_key: str | None = None,
    fragment_ids: list[str] | None = None,
) -> dict:
    """建立一个幂等 run；重复 request_key 返回原 run，不重复写事件。"""
    if trigger not in TRIGGERS:
        raise ValueError("Episode 整理触发类型无效")
    normalized_ids = sorted(set(fragment_ids or []))
    stable_key = (request_key or "").strip()
    if not stable_key and normalized_ids:
        stable_key = hashlib.sha256("|".join(normalized_ids).encode()).hexdigest()[:32]
    stable_key = stable_key or db.new_id()
    idempotency_key = f"{POLICY_VERSION}:{trigger}:{stable_key}"
    conn = db.connect()
    try:
        now = db.now()
        run_id = db.new_id()
        cursor = conn.execute(
            "INSERT OR IGNORE INTO episode_consolidator_runs("
            "id,idempotency_key,trigger,status,policy_version,max_attempts,next_attempt_at,"
            "input_fragment_ids_json,created_at,updated_at)"
            " VALUES(?,?,?,'queued',?,?,?,?,?,?)",
            (
                run_id, idempotency_key, trigger, POLICY_VERSION, MAX_ATTEMPTS,
                now, json.dumps(normalized_ids), now, now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM episode_consolidator_runs WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if cursor.rowcount:
            _event(
                conn, run_id, "enqueued", None, "queued", "triggered",
                {"trigger": trigger, "fragment_count": len(normalized_ids)},
            )
        conn.commit()
        result = _run_row(conn, row, include_events=True)
    finally:
        conn.close()
    wake_worker()
    return result


def enqueue_for_fragments(fragment_ids: list[str], *, request_key: str | None = None) -> dict | None:
    ids = sorted(set(fragment_ids))
    if not ids:
        return None
    return enqueue(trigger="fragment", request_key=request_key, fragment_ids=ids)


def enqueue_idle(*, now: float | None = None) -> dict:
    timestamp = db.now() if now is None else now
    bucket = int(timestamp // WORKER_IDLE_SECONDS)
    return enqueue(trigger="idle", request_key=f"idle-window:{bucket}")


async def start_worker() -> None:
    global _worker_task, _wake_event
    if _worker_task and not _worker_task.done():
        return
    recover_stale_runs()
    _wake_event = asyncio.Event()
    enqueue(trigger="startup", request_key=f"process-start:{db.new_id()}")
    _worker_task = asyncio.create_task(_worker_loop(), name="xiadie-episode-consolidator")


async def stop_worker() -> None:
    global _worker_task, _wake_event
    task = _worker_task
    _worker_task = None
    _wake_event = None
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def wake_worker() -> None:
    if _wake_event:
        _wake_event.set()


async def _worker_loop() -> None:
    while True:
        if _wake_event:
            _wake_event.clear()
        try:
            processed = await process_due(limit=3)
        except Exception:  # noqa: BLE001 - 后台循环必须能继续恢复
            _logger.exception("Episode Consolidator worker loop failed")
            processed = 0
        if processed:
            continue
        try:
            if _wake_event:
                await asyncio.wait_for(_wake_event.wait(), timeout=WORKER_IDLE_SECONDS)
            else:
                await asyncio.sleep(WORKER_IDLE_SECONDS)
        except asyncio.TimeoutError:
            enqueue_idle()


async def process_due(*, limit: int = 3) -> int:
    recover_stale_runs()
    count = 0
    for _ in range(max(1, min(int(limit), 20))):
        row = _claim_next()
        if not row:
            break
        await _process_claimed(row)
        count += 1
    return count


def recover_stale_runs() -> int:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = db.now()
        rows = conn.execute(
            "SELECT * FROM episode_consolidator_runs"
            " WHERE status IN ('running','cancel_requested') AND updated_at<?",
            (now - RUNNING_STALE_SECONDS,),
        ).fetchall()
        for row in rows:
            before = row["status"]
            if before == "cancel_requested":
                after, error_code, next_attempt_at, finished_at = (
                    "cancelled", "cancelled_after_interruption", None, now,
                )
                action = "cancelled"
            elif row["attempt_count"] >= row["max_attempts"]:
                after, error_code, next_attempt_at, finished_at = (
                    "exhausted", "consolidator_interrupted", None, now,
                )
                action = "exhausted"
            else:
                after, error_code, next_attempt_at, finished_at = (
                    "recovery_pending", "consolidator_interrupted", now, None,
                )
                action = "recovery_scheduled"
            conn.execute(
                "UPDATE episode_consolidator_runs SET status=?,error_code=?,next_attempt_at=?,"
                "finished_at=?,updated_at=? WHERE id=?",
                (after, error_code, next_attempt_at, finished_at, now, row["id"]),
            )
            _event(conn, row["id"], action, before, after, error_code, {})
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def _claim_next() -> dict | None:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = db.now()
        row = conn.execute(
            "SELECT * FROM episode_consolidator_runs WHERE"
            " (status='queued' OR (status='recovery_pending' AND next_attempt_at<=?))"
            " AND attempt_count<max_attempts ORDER BY created_at LIMIT 1",
            (now,),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        attempt = row["attempt_count"] + 1
        conn.execute(
            "UPDATE episode_consolidator_runs SET status='running',attempt_count=?,"
            "started_at=COALESCE(started_at,?),next_attempt_at=NULL,error_code=NULL,updated_at=?"
            " WHERE id=?",
            (attempt, now, now, row["id"]),
        )
        _event(conn, row["id"], "claimed", row["status"], "running", "worker_claimed", {
            "attempt": attempt,
        })
        conn.commit()
        result = dict(row)
        result.update(status="running", attempt_count=attempt, updated_at=now)
        return result
    finally:
        conn.close()


async def _process_claimed(row: dict) -> None:
    if _finish_cancel_if_requested(row["id"]):
        return
    try:
        created = await asyncio.to_thread(episodes.generate_candidates)
    except Exception:  # noqa: BLE001 - 失败只进入有限恢复，不破坏 Fragment
        if _finish_cancel_if_requested(row["id"]):
            return
        _mark_failure(row["id"], row["attempt_count"], row["max_attempts"])
        return
    if _finish_cancel_if_requested(row["id"]):
        return
    try:
        await episode_summary_service.enrich_candidates(created)
    except Exception:  # noqa: BLE001 - 候选已带安全抽取摘要，不能让 run 悬挂
        _logger.exception("Episode summary enrichment failed; keeping extractive fallback")
    if _finish_cancel_if_requested(row["id"]):
        return
    _finish_processed(row["id"], len(created))


def _finish_cancel_if_requested(run_id: str) -> bool:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status FROM episode_consolidator_runs WHERE id=?", (run_id,)
        ).fetchone()
        if not row or row["status"] != "cancel_requested":
            conn.rollback()
            return False
        now = db.now()
        conn.execute(
            "UPDATE episode_consolidator_runs SET status='cancelled',finished_at=?,updated_at=?"
            " WHERE id=?",
            (now, now, run_id),
        )
        _event(
            conn, run_id, "cancelled", "cancel_requested", "cancelled",
            "worker_cancelled", {},
        )
        conn.commit()
        return True
    finally:
        conn.close()


def _finish_processed(run_id: str, group_count: int) -> None:
    status = "applied" if group_count else "skipped"
    reason = "legacy_candidates_created" if group_count else "no_eligible_group"
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status FROM episode_consolidator_runs WHERE id=?", (run_id,)
        ).fetchone()
        if not row:
            conn.rollback()
            return
        now = db.now()
        if row["status"] == "cancel_requested":
            conn.execute(
                "UPDATE episode_consolidator_runs SET status='cancelled',finished_at=?,updated_at=?"
                " WHERE id=?",
                (now, now, run_id),
            )
            _event(
                conn, run_id, "cancelled", "cancel_requested", "cancelled",
                "worker_cancelled", {},
            )
            conn.commit()
            return
        if row["status"] != "running":
            conn.rollback()
            return
        conn.execute(
            "UPDATE episode_consolidator_runs SET status=?,group_count=?,error_code=NULL,"
            "finished_at=?,updated_at=? WHERE id=?",
            (status, group_count, now, now, run_id),
        )
        _event(conn, run_id, "processed", "running", status, reason, {
            "group_count": group_count,
        })
        conn.commit()
    finally:
        conn.close()


def _mark_failure(run_id: str, attempt_count: int, max_attempts: int) -> None:
    exhausted = attempt_count >= max_attempts
    status = "exhausted" if exhausted else "recovery_pending"
    now = db.now()
    delay = FIRST_RETRY_DELAY_SECONDS * (2 ** max(0, attempt_count - 1))
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status FROM episode_consolidator_runs WHERE id=?", (run_id,)
        ).fetchone()
        if not row:
            conn.rollback()
            return
        if row["status"] == "cancel_requested":
            conn.execute(
                "UPDATE episode_consolidator_runs SET status='cancelled',next_attempt_at=NULL,"
                "finished_at=?,updated_at=? WHERE id=?",
                (now, now, run_id),
            )
            _event(
                conn, run_id, "cancelled", "cancel_requested", "cancelled",
                "worker_cancelled", {},
            )
            conn.commit()
            return
        if row["status"] != "running":
            conn.rollback()
            return
        conn.execute(
            "UPDATE episode_consolidator_runs SET status=?,error_code='consolidator_failed',"
            "next_attempt_at=?,finished_at=?,updated_at=? WHERE id=?",
            (
                status, None if exhausted else now + delay, now if exhausted else None,
                now, run_id,
            ),
        )
        _event(
            conn, run_id, "exhausted" if exhausted else "retry_scheduled", "running",
            status, "consolidator_failed", {"attempt": attempt_count},
        )
        conn.commit()
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
