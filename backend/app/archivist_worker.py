"""Fragment Archivist 后台维护：20 小时懒调度、有限预算与可恢复任务。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress

from . import archivist, db

POLICY_VERSION = "archivist-worker-v1"
MAX_ATTEMPTS = 3
RUNNING_STALE_SECONDS = 5 * 60
FIRST_RETRY_DELAY_SECONDS = 5 * 60
MAINTENANCE_INTERVAL_SECONDS = 20 * 60 * 60
WORKER_IDLE_SECONDS = MAINTENANCE_INTERVAL_SECONDS
DEFAULT_SCAN_BUDGET = 50
DEFAULT_TRANSITION_BUDGET = 10
DEFAULT_RUNTIME_BUDGET_MS = 2_000
DEFAULT_MODEL_CALL_BUDGET = 0
TRIGGERS = frozenset({"startup", "idle", "manual"})
TERMINAL_STATUSES = frozenset({"cancelled", "completed", "exhausted", "skipped"})

_worker_task: asyncio.Task | None = None
_wake_event: asyncio.Event | None = None
_logger = logging.getLogger(__name__)


def enqueue(
    *, trigger: str, request_key: str | None = None,
    scan_budget: int = DEFAULT_SCAN_BUDGET,
    transition_budget: int = DEFAULT_TRANSITION_BUDGET,
    runtime_budget_ms: int = DEFAULT_RUNTIME_BUDGET_MS,
    model_call_budget: int = DEFAULT_MODEL_CALL_BUDGET,
) -> dict:
    if trigger not in TRIGGERS:
        raise ValueError("Archivist 触发类型无效")
    scan = max(1, min(int(scan_budget), 200))
    transitions = max(0, min(int(transition_budget), 100))
    runtime = max(100, min(int(runtime_budget_ms), 30_000))
    model_calls = max(0, min(int(model_call_budget), 20))
    stable = (request_key or "").strip() or db.new_id()
    key = f"{POLICY_VERSION}:{trigger}:{stable}"
    conn = db.connect()
    try:
        now = db.now()
        run_id = db.new_id()
        cursor = conn.execute(
            "INSERT OR IGNORE INTO archivist_runs("
            "id,idempotency_key,trigger,status,policy_version,max_attempts,next_attempt_at,"
            "scan_budget,transition_budget,runtime_budget_ms,model_call_budget,created_at,updated_at)"
            " VALUES(?,?,?,'queued',?,?,?,?,?,?,?,?,?)",
            (run_id, key, trigger, POLICY_VERSION, MAX_ATTEMPTS, now, scan, transitions,
             runtime, model_calls, now, now),
        )
        row = conn.execute(
            "SELECT * FROM archivist_runs WHERE idempotency_key=?", (key,)
        ).fetchone()
        if cursor.rowcount:
            _event(
                conn, run_id, "enqueued", None, "queued", "triggered",
                {"trigger": trigger, "scan_budget": scan,
                 "transition_budget": transitions, "runtime_budget_ms": runtime,
                 "model_call_budget": model_calls}, now,
            )
        conn.commit()
        result = _run_row(conn, row, include_events=True)
    finally:
        conn.close()
    wake_worker()
    return result


def enqueue_if_due(*, now: float | None = None, trigger: str = "idle") -> dict | None:
    timestamp = db.now() if now is None else float(now)
    try:
        last = float(db.get_setting("last_archivist_run", "0") or 0)
    except ValueError:
        last = 0
    if timestamp - last < MAINTENANCE_INTERVAL_SECONDS:
        return None
    bucket = int(timestamp // MAINTENANCE_INTERVAL_SECONDS)
    return enqueue(trigger=trigger, request_key=f"maintenance-window:{bucket}")


async def start_worker() -> None:
    global _worker_task, _wake_event
    if _worker_task and not _worker_task.done():
        return
    recover_stale_runs()
    _wake_event = asyncio.Event()
    enqueue_if_due(trigger="startup")
    _worker_task = asyncio.create_task(_worker_loop(), name="xiadie-archivist")


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
            _logger.exception("Archivist worker loop failed")
            processed = 0
        if processed:
            continue
        try:
            if _wake_event:
                await asyncio.wait_for(_wake_event.wait(), timeout=WORKER_IDLE_SECONDS)
            else:
                await asyncio.sleep(WORKER_IDLE_SECONDS)
        except asyncio.TimeoutError:
            enqueue_if_due(trigger="idle")


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
        await asyncio.to_thread(_evaluate_run, row)
    except asyncio.CancelledError:
        _mark_interrupted(row)
        raise
    except Exception:  # noqa: BLE001 - 单轮失败由有限重试吸收，不能影响聊天
        _mark_failure(row, "archivist_evaluation_failed")


def _evaluate_run(row: dict) -> None:
    fragment_ids = _due_fragment_ids(row["scan_budget"])
    if not fragment_ids:
        _finish_run(row["id"], "skipped", "no_due_fragments", 0, 0, 0)
        return
    started = time.monotonic()
    scanned = transitioned = conflicts = 0
    reason = "scan_complete"
    for fragment_id in fragment_ids:
        run_status = _current_status(row["id"])
        if run_status != "running":
            if run_status == "cancel_requested":
                _finish_cancel(row["id"])
            return
        if _runtime_exhausted(started, row["runtime_budget_ms"]):
            reason = "runtime_budget_reached"
            break
        if transitioned >= row["transition_budget"]:
            reason = "transition_budget_reached"
            break
        scanned += 1
        try:
            result = archivist.assess_and_transition(fragment_id)
        except archivist.ArchivistLifecycleError as exc:
            if exc.code in {"revision_conflict", "fragment_missing"}:
                conflicts += 1
                _mark_fragment_evaluated(fragment_id)
                continue
            raise
        _mark_fragment_evaluated(fragment_id)
        if result["changed"]:
            transitioned += 1
    if scanned >= row["scan_budget"]:
        reason = "scan_budget_reached"
    _finish_run(row["id"], "completed", reason, scanned, transitioned, conflicts)


def _runtime_exhausted(started: float, budget_ms: int) -> bool:
    return (time.monotonic() - started) * 1000 >= budget_ms


def _due_fragment_ids(limit: int) -> list[str]:
    conn = db.connect()
    try:
        now = db.now()
        rows = conn.execute(
            "SELECT id FROM memory_fragments"
            " WHERE status IN ('active','cooling') AND enabled=1"
            " AND ((status='active' AND COALESCE(last_recalled_at,created_at)<=?)"
            " OR (status='cooling' AND COALESCE(cooling_since,updated_at)<=?))"
            " ORDER BY COALESCE(last_archivist_evaluated_at,0),"
            " CASE WHEN status='cooling' THEN 0 ELSE 1 END,"
            " COALESCE(last_recalled_at,created_at),created_at,id LIMIT ?",
            (
                now - archivist.ACTIVE_TO_COOLING_DAYS * 86_400,
                now - archivist.COOLING_TO_FROZEN_DAYS * 86_400,
                max(1, min(int(limit), 200)),
            ),
        ).fetchall()
        return [row["id"] for row in rows]
    finally:
        conn.close()


def _mark_fragment_evaluated(fragment_id: str) -> None:
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET last_archivist_evaluated_at=? WHERE id=?",
            (db.now(), fragment_id),
        )
        conn.commit()
    finally:
        conn.close()


def _finish_run(
    run_id: str, status: str, reason: str, scanned: int, transitioned: int, conflicts: int,
) -> None:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT status FROM archivist_runs WHERE id=?", (run_id,)).fetchone()
        if not row or row["status"] != "running":
            conn.rollback()
            return
        now = db.now()
        conn.execute(
            "UPDATE archivist_runs SET status=?,scanned_count=?,transitioned_count=?,"
            "conflict_count=?,finished_at=?,updated_at=? WHERE id=?",
            (status, scanned, transitioned, conflicts, now, now, run_id),
        )
        conn.execute(
            "INSERT INTO settings(key,value) VALUES('last_archivist_run',?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(now),),
        )
        _event(
            conn, run_id, "processed", "running", status, reason,
            {"scanned_count": scanned, "transitioned_count": transitioned,
             "conflict_count": conflicts, "model_calls_used": 0}, now,
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
            "SELECT * FROM archivist_runs WHERE status IN ('running','cancel_requested')"
            " AND updated_at<?", (now - RUNNING_STALE_SECONDS,),
        ).fetchall()
        for row in rows:
            before = row["status"]
            if before == "cancel_requested":
                after, code, next_at, finished, action = (
                    "cancelled", "cancelled_after_interruption", None, now, "cancelled",
                )
            elif row["attempt_count"] >= row["max_attempts"]:
                after, code, next_at, finished, action = (
                    "exhausted", "archivist_interrupted", None, now, "exhausted",
                )
            else:
                after, code, next_at, finished, action = (
                    "recovery_pending", "archivist_interrupted", now, None,
                    "recovery_scheduled",
                )
            conn.execute(
                "UPDATE archivist_runs SET status=?,error_code=?,next_attempt_at=?,"
                "finished_at=?,updated_at=? WHERE id=?",
                (after, code, next_at, finished, now, row["id"]),
            )
            _event(conn, row["id"], action, before, after, code, {}, now)
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
            "SELECT * FROM archivist_runs WHERE"
            " (status='queued' OR (status='recovery_pending' AND next_attempt_at<=?))"
            " AND attempt_count<max_attempts ORDER BY created_at LIMIT 1", (now,),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        attempt = row["attempt_count"] + 1
        conn.execute(
            "UPDATE archivist_runs SET status='running',attempt_count=?,"
            "started_at=COALESCE(started_at,?),next_attempt_at=NULL,error_code=NULL,updated_at=?"
            " WHERE id=? AND status=?", (attempt, now, now, row["id"], row["status"]),
        )
        if conn.execute("SELECT changes() changed").fetchone()["changed"] != 1:
            conn.rollback()
            return None
        _event(
            conn, row["id"], "claimed", row["status"], "running", "worker_claimed",
            {"attempt": attempt}, now,
        )
        conn.commit()
        result = dict(row)
        result.update(status="running", attempt_count=attempt, updated_at=now)
        return result
    finally:
        conn.close()


def _mark_interrupted(row: dict) -> None:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT status FROM archivist_runs WHERE id=?", (row["id"],)).fetchone()
        if not current or current["status"] != "running":
            conn.rollback()
            return
        now = db.now()
        conn.execute(
            "UPDATE archivist_runs SET status='recovery_pending',error_code='worker_stopped',"
            "next_attempt_at=?,updated_at=? WHERE id=?", (now, now, row["id"]),
        )
        _event(
            conn, row["id"], "recovery_scheduled", "running", "recovery_pending",
            "worker_stopped", {}, now,
        )
        conn.commit()
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
        current = conn.execute("SELECT status FROM archivist_runs WHERE id=?", (row["id"],)).fetchone()
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
            "UPDATE archivist_runs SET status=?,error_code=?,next_attempt_at=?,"
            "finished_at=?,updated_at=? WHERE id=?",
            (status, code, next_at, now if exhausted else None, now, row["id"]),
        )
        _event(
            conn, row["id"], "exhausted" if exhausted else "retry_scheduled",
            "running", status, code, {"attempt": row["attempt_count"]}, now,
        )
        conn.commit()
    finally:
        conn.close()


def _current_status(run_id: str) -> str | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT status FROM archivist_runs WHERE id=?", (run_id,)).fetchone()
        return row["status"] if row else None
    finally:
        conn.close()


def _finish_cancel(run_id: str) -> bool:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT status FROM archivist_runs WHERE id=?", (run_id,)).fetchone()
        if not row or row["status"] != "cancel_requested":
            conn.rollback()
            return False
        now = db.now()
        conn.execute(
            "UPDATE archivist_runs SET status='cancelled',finished_at=?,updated_at=? WHERE id=?",
            (now, now, run_id),
        )
        _event(
            conn, run_id, "cancelled", "cancel_requested", "cancelled",
            "worker_cancelled", {}, now,
        )
        conn.commit()
        return True
    finally:
        conn.close()


def cancel(run_id: str) -> dict | None:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM archivist_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            conn.rollback()
            return None
        before = row["status"]
        if before in {"cancelled", "cancel_requested"}:
            conn.rollback()
            return _run_row(conn, row, include_events=True)
        if before in TERMINAL_STATUSES:
            conn.rollback()
            raise ValueError("已结束的 Archivist 任务不能取消")
        now = db.now()
        after = "cancel_requested" if before == "running" else "cancelled"
        conn.execute(
            "UPDATE archivist_runs SET status=?,next_attempt_at=NULL,finished_at=?,updated_at=?"
            " WHERE id=?", (after, None if after == "cancel_requested" else now, now, run_id),
        )
        _event(
            conn, run_id, "cancel_requested" if after == "cancel_requested" else "cancelled",
            before, after, "user_cancelled", {}, now,
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM archivist_runs WHERE id=?", (run_id,)).fetchone()
        return _run_row(conn, updated, include_events=True)
    finally:
        conn.close()


def get_run(run_id: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM archivist_runs WHERE id=?", (run_id,)).fetchone()
        return _run_row(conn, row, include_events=True) if row else None
    finally:
        conn.close()


def list_runs(limit: int = 50) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM archivist_runs ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
        return [_run_row(conn, row) for row in rows]
    finally:
        conn.close()


def _event(
    conn, run_id: str, action: str, before: str | None, after: str,
    reason: str | None, metadata: dict, now: float,
) -> None:
    conn.execute(
        "INSERT INTO archivist_run_events("
        "id,run_id,action,before_status,after_status,reason_code,metadata_json,created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (db.new_id(), run_id, action, before, after, reason,
         json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), now),
    )


def _run_row(conn, row, include_events: bool = False) -> dict:
    result = dict(row)
    if include_events:
        rows = conn.execute(
            "SELECT * FROM archivist_run_events WHERE run_id=? ORDER BY created_at,rowid",
            (result["id"],),
        ).fetchall()
        result["events"] = []
        for event in rows:
            item = dict(event)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result["events"].append(item)
    return result
