"""Saga 后台整理：周级懒调度、有限恢复与原子应用。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from contextlib import suppress

from . import db, saga_summary_service, sagas

POLICY_VERSION = "saga-consolidator-v1"
MAX_ATTEMPTS = 3
RUNNING_STALE_SECONDS = 5 * 60
FIRST_RETRY_DELAY_SECONDS = 5 * 60
WEEKLY_INTERVAL_SECONDS = 6 * 24 * 60 * 60
WORKER_IDLE_SECONDS = 5 * 60
TRIGGERS = frozenset({"startup", "idle", "weekly", "manual", "episode"})
TERMINAL_STATUSES = frozenset({"cancelled", "applied", "exhausted", "skipped"})

_worker_task: asyncio.Task | None = None
_wake_event: asyncio.Event | None = None
_logger = logging.getLogger(__name__)


def enqueue(
    *, trigger: str, request_key: str | None = None,
    episode_ids: list[str] | None = None,
) -> dict:
    if trigger not in TRIGGERS:
        raise ValueError("Saga 整理触发类型无效")
    ids = sorted(set(episode_ids or []))
    stable = (request_key or "").strip()
    if not stable and ids:
        stable = hashlib.sha256("|".join(ids).encode()).hexdigest()[:32]
    stable = stable or db.new_id()
    key = f"{POLICY_VERSION}:{trigger}:{stable}"
    conn = db.connect()
    try:
        now = db.now()
        run_id = db.new_id()
        cursor = conn.execute(
            "INSERT OR IGNORE INTO saga_consolidator_runs("
            "id,idempotency_key,trigger,status,policy_version,max_attempts,next_attempt_at,"
            "input_episode_ids_json,created_at,updated_at)"
            " VALUES(?,?,?,'queued',?,?,?,?,?,?)",
            (run_id, key, trigger, POLICY_VERSION, MAX_ATTEMPTS, now, json.dumps(ids), now, now),
        )
        row = conn.execute(
            "SELECT * FROM saga_consolidator_runs WHERE idempotency_key=?", (key,)
        ).fetchone()
        if cursor.rowcount:
            sagas._run_event(
                conn, run_id, "enqueued", None, "queued", "triggered",
                {"trigger": trigger, "episode_count": len(ids)}, now,
            )
        conn.commit()
        result = _run_row(conn, row, include_events=True)
    finally:
        conn.close()
    wake_worker()
    return result


def enqueue_for_episodes(episode_ids: list[str]) -> dict | None:
    ids = sorted(set(episode_ids))
    return enqueue(trigger="episode", episode_ids=ids) if ids else None


def enqueue_weekly(*, now: float | None = None, trigger: str = "weekly") -> dict | None:
    timestamp = db.now() if now is None else float(now)
    try:
        last = float(db.get_setting("last_saga_consolidator_run", "0") or 0)
    except ValueError:
        last = 0
    if timestamp - last < WEEKLY_INTERVAL_SECONDS:
        return None
    bucket = int(timestamp // WEEKLY_INTERVAL_SECONDS)
    return enqueue(trigger=trigger, request_key=f"weekly-window:{bucket}")


async def start_worker() -> None:
    global _worker_task, _wake_event
    if _worker_task and not _worker_task.done():
        return
    recover_stale_runs()
    _wake_event = asyncio.Event()
    enqueue_weekly(trigger="startup")
    _worker_task = asyncio.create_task(_worker_loop(), name="xiadie-saga-consolidator")


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
            processed = await process_due(limit=1)
        except Exception:  # noqa: BLE001
            _logger.exception("Saga Consolidator worker loop failed")
            processed = 0
        if processed:
            continue
        try:
            if _wake_event:
                await asyncio.wait_for(_wake_event.wait(), timeout=WORKER_IDLE_SECONDS)
            else:
                await asyncio.sleep(WORKER_IDLE_SECONDS)
        except asyncio.TimeoutError:
            enqueue_weekly(trigger="idle")


async def process_due(*, limit: int = 1) -> int:
    recover_stale_runs()
    count = 0
    for _ in range(max(1, min(int(limit), 5))):
        row = _claim_next()
        if not row:
            break
        await _process_claimed(row)
        count += 1
    return count


async def _process_claimed(row: dict) -> None:
    if _finish_cancel(row["id"]):
        return
    try:
        from . import slow_lifecycle

        slow_result = await asyncio.to_thread(slow_lifecycle.process_batch)
        _record_slow_lifecycle_result(row["id"], slow_result)
        if _finish_cancel(row["id"]):
            return
        await asyncio.to_thread(sagas.generate_candidates)
        candidates = await asyncio.to_thread(sagas.qualified_candidates, sagas.APPLICATION_BATCH_LIMIT)
        if _finish_cancel(row["id"]):
            return
        await saga_summary_service.enrich_candidates(candidates)
        if _finish_cancel(row["id"]):
            return
        refreshed = [
            await asyncio.to_thread(sagas.get_group_candidate, item["id"])
            for item in candidates
        ]
        candidate_ids = [
            item["id"] for item in refreshed
            if item and item.get("summary_status") in {"model_validated", "extractive_fallback"}
        ]
        await asyncio.to_thread(sagas.apply_candidates_for_run, row["id"], candidate_ids)
    except asyncio.CancelledError:
        _mark_interrupted(row)
        raise
    except sagas.SagaApplyError as exc:
        _record_failure_safely(locals().get("candidate_ids", []), exc.code)
        _mark_failure(row, exc.code)
    except Exception:  # noqa: BLE001
        _record_failure_safely(locals().get("candidate_ids", []), "saga_application_failed")
        _mark_failure(row, "saga_application_failed")


def _record_slow_lifecycle_result(run_id: str, result: dict) -> None:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status FROM saga_consolidator_runs WHERE id=?", (run_id,)
        ).fetchone()
        if not row or row["status"] != "running":
            conn.rollback()
            return
        now = db.now()
        sagas._run_event(
            conn, run_id, "slow_lifecycle_processed", "running", "running",
            "bounded_slow_lifecycle", result, now,
        )
        conn.commit()
    finally:
        conn.close()


def _mark_interrupted(row: dict) -> None:
    """Return a claimed run to recovery immediately during app shutdown."""
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT status FROM saga_consolidator_runs WHERE id=?", (row["id"],)
        ).fetchone()
        if not current or current["status"] != "running":
            conn.rollback()
            return
        now = db.now()
        conn.execute(
            "UPDATE saga_consolidator_runs SET status='recovery_pending',"
            "error_code='worker_stopped',next_attempt_at=?,updated_at=? WHERE id=?",
            (now, now, row["id"]),
        )
        sagas._run_event(
            conn, row["id"], "recovery_scheduled", "running", "recovery_pending",
            "worker_stopped", {}, now,
        )
        conn.commit()
    finally:
        conn.close()


def recover_stale_runs() -> int:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = db.now()
        rows = conn.execute(
            "SELECT * FROM saga_consolidator_runs"
            " WHERE status IN ('running','cancel_requested') AND updated_at<?",
            (now - RUNNING_STALE_SECONDS,),
        ).fetchall()
        for row in rows:
            before = row["status"]
            if before == "cancel_requested":
                after, code, next_at, finished, action = (
                    "cancelled", "cancelled_after_interruption", None, now, "cancelled",
                )
            elif row["attempt_count"] >= row["max_attempts"]:
                after, code, next_at, finished, action = (
                    "exhausted", "consolidator_interrupted", None, now, "exhausted",
                )
            else:
                after, code, next_at, finished, action = (
                    "recovery_pending", "consolidator_interrupted", now, None,
                    "recovery_scheduled",
                )
            conn.execute(
                "UPDATE saga_consolidator_runs SET status=?,error_code=?,next_attempt_at=?,"
                "finished_at=?,updated_at=? WHERE id=?",
                (after, code, next_at, finished, now, row["id"]),
            )
            sagas._run_event(conn, row["id"], action, before, after, code, {}, now)
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
            "SELECT * FROM saga_consolidator_runs WHERE"
            " (status='queued' OR (status='recovery_pending' AND next_attempt_at<=?))"
            " AND attempt_count<max_attempts ORDER BY created_at LIMIT 1", (now,),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        attempt = row["attempt_count"] + 1
        conn.execute(
            "UPDATE saga_consolidator_runs SET status='running',attempt_count=?,"
            "started_at=COALESCE(started_at,?),next_attempt_at=NULL,error_code=NULL,updated_at=?"
            " WHERE id=?", (attempt, now, now, row["id"]),
        )
        sagas._run_event(
            conn, row["id"], "claimed", row["status"], "running", "worker_claimed",
            {"attempt": attempt}, now,
        )
        conn.commit()
        result = dict(row)
        result.update(status="running", attempt_count=attempt, updated_at=now)
        return result
    finally:
        conn.close()


def _finish_cancel(run_id: str) -> bool:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status FROM saga_consolidator_runs WHERE id=?", (run_id,)
        ).fetchone()
        if not row or row["status"] != "cancel_requested":
            conn.rollback()
            return False
        now = db.now()
        conn.execute(
            "UPDATE saga_consolidator_runs SET status='cancelled',finished_at=?,updated_at=?"
            " WHERE id=?", (now, now, run_id),
        )
        sagas._run_event(
            conn, run_id, "cancelled", "cancel_requested", "cancelled",
            "worker_cancelled", {}, now,
        )
        conn.commit()
        return True
    finally:
        conn.close()


def _mark_failure(row: dict, code: str) -> None:
    exhausted = row["attempt_count"] >= row["max_attempts"]
    status = "exhausted" if exhausted else "recovery_pending"
    now = db.now()
    next_at = None if exhausted else now + FIRST_RETRY_DELAY_SECONDS * 2 ** (
        row["attempt_count"] - 1
    )
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT status FROM saga_consolidator_runs WHERE id=?", (row["id"],)
        ).fetchone()
        if not current:
            conn.rollback()
            return
        if current["status"] == "cancel_requested":
            conn.rollback()
            _finish_cancel(row["id"])
            return
        if current["status"] != "running":
            conn.rollback()
            return
        conn.execute(
            "UPDATE saga_consolidator_runs SET status=?,error_code=?,next_attempt_at=?,"
            "finished_at=?,updated_at=? WHERE id=?",
            (status, code, next_at, now if exhausted else None, now, row["id"]),
        )
        sagas._run_event(
            conn, row["id"], "exhausted" if exhausted else "retry_scheduled",
            "running", status, code, {"attempt": row["attempt_count"]}, now,
        )
        conn.commit()
    finally:
        conn.close()


def _record_failure_safely(candidate_ids: list[str], code: str) -> None:
    try:
        sagas.record_application_failure(candidate_ids, code)
    except Exception:  # noqa: BLE001
        _logger.exception("Failed to record Saga application error")


def cancel(run_id: str) -> dict | None:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM saga_consolidator_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            conn.rollback()
            return None
        before = row["status"]
        if before in {"cancelled", "cancel_requested"}:
            conn.rollback()
            return _run_row(conn, row, include_events=True)
        if before in TERMINAL_STATUSES:
            conn.rollback()
            raise ValueError("已结束的 Saga 整理任务不能取消")
        now = db.now()
        after = "cancel_requested" if before == "running" else "cancelled"
        conn.execute(
            "UPDATE saga_consolidator_runs SET status=?,next_attempt_at=NULL,finished_at=?,"
            "updated_at=? WHERE id=?",
            (after, None if after == "cancel_requested" else now, now, run_id),
        )
        sagas._run_event(
            conn, run_id, "cancel_requested" if after == "cancel_requested" else "cancelled",
            before, after, "user_cancelled", {}, now,
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM saga_consolidator_runs WHERE id=?", (run_id,)).fetchone()
        return _run_row(conn, updated, include_events=True)
    finally:
        conn.close()


def get_run(run_id: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM saga_consolidator_runs WHERE id=?", (run_id,)).fetchone()
        return _run_row(conn, row, include_events=True) if row else None
    finally:
        conn.close()


def list_runs(limit: int = 50) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM saga_consolidator_runs ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
        return [_run_row(conn, row) for row in rows]
    finally:
        conn.close()


def _run_row(conn, row, include_events: bool = False) -> dict:
    result = dict(row)
    result["input_episode_ids"] = json.loads(result.pop("input_episode_ids_json"))
    result["result_saga_ids"] = json.loads(result.pop("result_saga_ids_json"))
    if include_events:
        rows = conn.execute(
            "SELECT * FROM saga_consolidator_events WHERE run_id=? ORDER BY created_at,rowid",
            (result["id"],),
        ).fetchall()
        result["events"] = []
        for event in rows:
            item = dict(event)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result["events"].append(item)
    return result
