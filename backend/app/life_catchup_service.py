"""Application lifecycle adapter for LIFE.4 startup catch-up and exit snapshot."""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime

from . import db, life_catchup, life_runtime

_heartbeat_task: asyncio.Task | None = None
_lease_token: str | None = None
_timezone_id: str | None = None


def detect_timezone_id() -> str:
    local = datetime.now().astimezone()
    offset = local.utcoffset()
    seconds = int(offset.total_seconds()) if offset is not None else 0
    if seconds == 0:
        return "UTC"
    if seconds == 8 * 3600:
        return "Asia/Shanghai"
    sign = "+" if seconds >= 0 else "-"
    absolute = abs(seconds)
    return f"UTC{sign}{absolute // 3600:02d}:{(absolute % 3600) // 60:02d}"


async def _heartbeat_loop(token: str) -> None:
    while True:
        await asyncio.sleep(10)
        if not life_runtime.heartbeat_lease(lease_token=token, now=db.now()):
            return


async def start() -> dict:
    global _heartbeat_task, _lease_token, _timezone_id
    now = db.now()
    token = uuid.uuid4().hex
    process_id = uuid.uuid4().hex
    boot_id = f"boot-{int(time.time())}-{process_id[:8]}"
    if not life_runtime.acquire_lease(
        process_instance_id=process_id, boot_session_id=boot_id, lease_token=token, now=now,
    ):
        return {"status": "skipped", "reason_code": "lease_held"}
    _lease_token = token
    _timezone_id = detect_timezone_id()
    result = life_catchup.run_catchup(
        interval_end=now, lease_token=token, timezone_snapshot=_timezone_id,
    )
    if life_runtime.get_state() is None:
        life_runtime.materialize(lease_token=token, now=now, timezone_id=_timezone_id)
    _heartbeat_task = asyncio.create_task(_heartbeat_loop(token))
    return result


async def stop() -> None:
    global _heartbeat_task, _lease_token, _timezone_id
    token, timezone_id = _lease_token, _timezone_id
    if token and timezone_id:
        life_catchup.record_exit_snapshot(exited_at=db.now(), timezone_snapshot=timezone_id)
    if _heartbeat_task:
        _heartbeat_task.cancel()
        try:
            await _heartbeat_task
        except asyncio.CancelledError:
            pass
    if token:
        life_runtime.release_lease(lease_token=token)
    _heartbeat_task = None
    _lease_token = None
    _timezone_id = None
