"""LIFE.4 bounded offline-world catch-up performed only at the next startup."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from . import db, life_runtime

MODE_CONTINUOUS = "continuous_simulated"
MODE_PAUSED = "paused"
MODE_DISABLED = "disabled"
MODES = frozenset({MODE_CONTINUOUS, MODE_PAUSED, MODE_DISABLED})
MAX_CANDIDATES = 16
MAX_MODEL_CALLS = 2


class CatchUpError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DateCrossing:
    id: str
    revision: str
    occurrence_at: float


def get_mode() -> str:
    mode = db.get_setting("life_continuity_mode", MODE_CONTINUOUS)
    return mode if mode in MODES else MODE_CONTINUOUS


def set_mode(mode: str) -> str:
    if mode not in MODES:
        raise CatchUpError("mode_invalid", "life continuity mode is invalid")
    db.set_setting("life_continuity_mode", mode)
    return mode


def record_exit_snapshot(*, exited_at: float, timezone_snapshot: str,
                         schedule_revision: str = "none") -> dict[str, Any]:
    if not timezone_snapshot or not schedule_revision:
        raise CatchUpError("snapshot_invalid", "exit snapshot is incomplete")
    state = life_runtime.get_state()
    snapshot_id = db.new_id()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO life_exit_snapshots(id,exited_at,timezone_snapshot,schedule_revision,"
            "state_revision,algorithm_version,created_at) VALUES(?,?,?,?,?,?,?)",
            (snapshot_id, exited_at, timezone_snapshot, schedule_revision,
             state.revision if state else 0, life_runtime.ALGORITHM_VERSION, exited_at),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "id": snapshot_id, "exited_at": exited_at, "timezone_snapshot": timezone_snapshot,
        "schedule_revision": schedule_revision, "state_revision": state.revision if state else 0,
        "algorithm_version": life_runtime.ALGORITHM_VERSION,
    }


def _strategy(elapsed: float) -> str:
    if elapsed <= 2 * 3600:
        return "detailed"
    if elapsed <= 3 * 24 * 3600:
        return "daily"
    if elapsed <= 30 * 24 * 3600:
        return "weekly"
    return "regression_transition"


def _identity(snapshot: dict[str, Any], interval_end: float) -> tuple[str, str, str]:
    frozen = {
        "interval_start": snapshot["exited_at"], "interval_end": interval_end,
        "timezone_snapshot": snapshot["timezone_snapshot"],
        "schedule_revision": snapshot["schedule_revision"],
        "state_revision": snapshot["state_revision"],
        "algorithm_version": snapshot["algorithm_version"],
    }
    encoded = json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    return "catchup-" + digest[:16], digest, "life-catchup-v1:" + digest


def _candidate_specs(*, start: float, end: float, strategy: str,
                     date_crossings: tuple[DateCrossing, ...]) -> list[tuple[str, float, str | None, str | None]]:
    elapsed = end - start
    specs: list[tuple[str, float, str | None, str | None]] = []
    if elapsed > 0:
        if strategy == "detailed":
            specs.append(("continuity_transition", end, None, None))
        elif strategy == "daily":
            count = max(1, min(7, int((elapsed + 86399) // 86400)))
            specs.extend(("simulated_day", min(end, start + (i + 1) * 86400), None, None) for i in range(count))
        elif strategy == "weekly":
            count = max(1, min(8, int((elapsed + 604799) // 604800)))
            specs.extend(("simulated_week", min(end, start + (i + 1) * 604800), None, None) for i in range(count))
        else:
            specs.extend([
                ("simulated_week", start + elapsed * 0.25, None, None),
                ("simulated_week", start + elapsed * 0.75, None, None),
                ("continuity_transition", end, None, None),
            ])
    for crossing in sorted(date_crossings, key=lambda item: (item.occurrence_at, item.id)):
        if start < crossing.occurrence_at <= end:
            specs.append(("important_date_crossing", crossing.occurrence_at, crossing.id, crossing.revision))
    return sorted(specs, key=lambda item: (item[1], item[0], item[2] or ""))[:MAX_CANDIDATES]


def _latest_snapshot() -> dict[str, Any] | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM life_exit_snapshots ORDER BY exited_at DESC,id DESC LIMIT 1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def run_catchup(*, interval_end: float, lease_token: str, timezone_snapshot: str,
                modulation: life_runtime.Modulation | None = None,
                date_crossings: tuple[DateCrossing, ...] = ()) -> dict[str, Any]:
    mode = get_mode()
    if mode != MODE_CONTINUOUS:
        return {"status": "skipped", "reason_code": f"mode_{mode}", "candidate_count": 0}
    snapshot = _latest_snapshot()
    if snapshot is None:
        return {"status": "skipped", "reason_code": "no_exit_snapshot", "candidate_count": 0}
    if interval_end < snapshot["exited_at"]:
        return {"status": "skipped", "reason_code": "wall_clock_rollback", "candidate_count": 0}
    catchup_id, seed, idempotency_key = _identity(snapshot, interval_end)
    strategy = _strategy(interval_end - snapshot["exited_at"])
    target_revision = int(snapshot["state_revision"]) + 1
    conn = db.connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO life_catchup_requests(catchup_id,exit_snapshot_id,interval_start,"
            "interval_end,timezone_snapshot,schedule_revision,state_revision,algorithm_version,"
            "deterministic_seed,materialization_revision,span_strategy,status,candidate_count,"
            "model_call_count,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (catchup_id, snapshot["id"], snapshot["exited_at"], interval_end,
             snapshot["timezone_snapshot"], snapshot["schedule_revision"], snapshot["state_revision"],
             snapshot["algorithm_version"], seed, target_revision, strategy, "queued", 0, 0,
             idempotency_key, interval_end),
        )
        conn.commit()
        request = conn.execute("SELECT * FROM life_catchup_requests WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if request["status"] == "applied":
            return dict(request) | {"duplicate": True}
    finally:
        conn.close()
    current = life_runtime.get_state()
    if current is None or current.revision < target_revision:
        life_runtime.materialize(
            lease_token=lease_token, now=interval_end, timezone_id=timezone_snapshot,
            modulation=modulation,
        )
    specs = _candidate_specs(
        start=snapshot["exited_at"], end=interval_end, strategy=strategy,
        date_crossings=date_crossings,
    )
    conn = db.connect()
    try:
        for ordinal, (kind, occurred_at, source_id, source_revision) in enumerate(specs):
            conn.execute(
                "INSERT OR IGNORE INTO life_catchup_candidates(id,catchup_id,ordinal,candidate_kind,"
                "occurred_at,source_id,source_revision,world_layer,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (db.new_id(), catchup_id, ordinal, kind, occurred_at, source_id,
                 source_revision, "simulated", interval_end),
            )
        conn.execute(
            "UPDATE life_catchup_requests SET status='applied',candidate_count=?,model_call_count=0,"
            "completed_at=? WHERE catchup_id=?", (len(specs), interval_end, catchup_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM life_catchup_requests WHERE catchup_id=?", (catchup_id,)).fetchone()
        return dict(row) | {"duplicate": False}
    finally:
        conn.close()


def list_candidates(catchup_id: str) -> list[dict[str, Any]]:
    conn = db.connect()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT id,catchup_id,ordinal,candidate_kind,occurred_at,source_id,source_revision,"
            "world_layer,created_at FROM life_catchup_candidates WHERE catchup_id=? ORDER BY ordinal",
            (catchup_id,),
        ).fetchall()]
    finally:
        conn.close()
