"""LIFE.3 deterministic LifeClock, SelfState and database materializer lease."""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import db
from .affect import repository as affect_repository

ALGORITHM_VERSION = "life-state-reducer-v1"
LEASE_TTL_SECONDS = 30.0
MAX_ADVANCE_SECONDS = 7 * 24 * 60 * 60
STEP_SECONDS = 5 * 60.0
MIN_ACTIVITY_SECONDS = 45 * 60.0
CLOCK_ROLLBACK_TOLERANCE = 5 * 60.0

ACTIVITIES = frozenset({"resting", "winding_down", "routine", "focused", "reflecting"})


class LifeRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Modulation:
    contact_need: float = 0.0
    valence: float = 0.0
    arousal: float = 0.0
    bond: float = 0.0
    trust: float = 0.0


@dataclass(frozen=True)
class SelfState:
    revision: int
    logical_time: float
    reliable_wall_time: float
    timezone_id: str
    current_activity: str
    activity_since: float
    energy: float
    focus: float
    rest_need: float
    social_openness: float
    conservative_mode: bool = False
    anomaly_code: str | None = None


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def default_state(*, now: float, timezone_id: str) -> SelfState:
    return SelfState(
        revision=0, logical_time=now, reliable_wall_time=now, timezone_id=timezone_id,
        current_activity="routine", activity_since=now,
        energy=0.62, focus=0.48, rest_need=0.30, social_openness=0.40,
    )


def read_affect_modulation() -> Modulation:
    """Read EAP state without advancing or writing it."""
    snapshot = affect_repository.get_snapshot(advance_time=False)
    affect, relationship = snapshot["affect"], snapshot["relationship"]
    return Modulation(
        contact_need=float(affect["contact_need"]), valence=float(affect["valence"]),
        arousal=float(affect["arousal"]), bond=float(relationship["bond"]),
        trust=float(relationship["trust"]),
    )


def _local_hour(timestamp: float, timezone_id: str) -> float:
    try:
        local = datetime.fromtimestamp(timestamp, ZoneInfo(timezone_id))
    except (ZoneInfoNotFoundError, OSError, ValueError):
        # Windows frozen Python may not ship the optional IANA tzdata package.
        # Keep a deliberately small deterministic compatibility map for runtime
        # continuity; calendar recurrence expands timezone coverage in LIFE.7.
        fixed_offsets = {
            "UTC": 0.0,
            "Etc/UTC": 0.0,
            "Asia/Shanghai": 8.0,
            "China Standard Time": 8.0,
        }
        if timezone_id not in fixed_offsets:
            raise LifeRuntimeError("timezone_invalid", "timezone is not available")
        local = datetime.fromtimestamp(
            timestamp, timezone.utc,
        ) + timedelta(hours=fixed_offsets[timezone_id])
    return local.hour + local.minute / 60 + local.second / 3600


def _preferred_activity(state: SelfState, local_hour: float) -> str:
    if local_hour >= 23 or local_hour < 6:
        return "resting"
    if local_hour >= 21 or state.rest_need >= 0.78:
        return "winding_down"
    if state.focus >= 0.62 and state.energy >= 0.42:
        return "focused"
    if state.energy < 0.34:
        return "reflecting"
    return "routine"


def reduce_state(state: SelfState, *, elapsed_seconds: float,
                 modulation: Modulation) -> SelfState:
    """Pure, bounded, stepwise reducer. Equal inputs always yield equal state."""
    remaining = max(0.0, min(float(elapsed_seconds), MAX_ADVANCE_SECONDS))
    result = state
    while remaining > 1e-9:
        step = min(STEP_SECONDS, remaining)
        hours = step / 3600.0
        logical_time = result.logical_time + step
        hour = _local_hour(logical_time, result.timezone_id)
        circadian_energy = 0.48 + 0.24 * math.cos((hour - 14.0) / 24.0 * 2 * math.pi)
        awake = not (hour >= 23 or hour < 6)
        energy_target = circadian_energy + 0.05 * modulation.valence - 0.03 * max(0.0, modulation.arousal)
        energy = result.energy + (energy_target - result.energy) * min(1.0, 0.10 * hours)
        rest_target = (0.18 if not awake else 0.42) + (0.18 if result.current_activity == "focused" else 0.0)
        rest_need = result.rest_need + (rest_target - result.rest_need) * min(1.0, 0.08 * hours)
        if not awake:
            rest_need = max(0.0, rest_need - 0.10 * hours)
        focus_target = _clamp(0.30 + 0.48 * energy + 0.06 * modulation.arousal)
        focus = result.focus + (focus_target - result.focus) * min(1.0, 0.12 * hours)
        social_target = _clamp(
            0.22 + 0.42 * modulation.contact_need + 0.08 * modulation.bond + 0.08 * modulation.trust
        )
        social = result.social_openness + (social_target - result.social_openness) * min(1.0, 0.08 * hours)
        candidate = replace(
            result, logical_time=logical_time, energy=_clamp(energy), focus=_clamp(focus),
            rest_need=_clamp(rest_need), social_openness=_clamp(social),
            conservative_mode=False, anomaly_code=None,
        )
        preferred = _preferred_activity(candidate, hour)
        if preferred != candidate.current_activity and logical_time - candidate.activity_since >= MIN_ACTIVITY_SECONDS:
            candidate = replace(candidate, current_activity=preferred, activity_since=logical_time)
        result = candidate
        remaining -= step
    return result


def acquire_lease(*, process_instance_id: str, boot_session_id: str, lease_token: str,
                  now: float, ttl_seconds: float = LEASE_TTL_SECONDS) -> bool:
    if not all((process_instance_id, boot_session_id, lease_token)) or ttl_seconds <= 0:
        raise LifeRuntimeError("lease_identity_invalid", "lease identity is invalid")
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM life_runtime_lease WHERE id=1").fetchone()
        if row and row["expires_at"] > now and row["lease_token"] != lease_token:
            conn.rollback()
            return False
        acquired_at = row["acquired_at"] if row and row["lease_token"] == lease_token else now
        conn.execute(
            "INSERT INTO life_runtime_lease(id,process_instance_id,boot_session_id,lease_token,"
            "acquired_at,expires_at,heartbeat_at) VALUES(1,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET process_instance_id=excluded.process_instance_id,"
            "boot_session_id=excluded.boot_session_id,lease_token=excluded.lease_token,"
            "acquired_at=excluded.acquired_at,expires_at=excluded.expires_at,heartbeat_at=excluded.heartbeat_at",
            (process_instance_id, boot_session_id, lease_token, acquired_at, now + ttl_seconds, now),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def heartbeat_lease(*, lease_token: str, now: float,
                    ttl_seconds: float = LEASE_TTL_SECONDS) -> bool:
    conn = db.connect()
    try:
        cursor = conn.execute(
            "UPDATE life_runtime_lease SET heartbeat_at=?,expires_at=? WHERE id=1 "
            "AND lease_token=? AND expires_at>?", (now, now + ttl_seconds, lease_token, now),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def _from_row(row: sqlite3.Row) -> SelfState:
    return SelfState(
        revision=row["revision"], logical_time=row["logical_time"],
        reliable_wall_time=row["reliable_wall_time"], timezone_id=row["timezone_id"],
        current_activity=row["current_activity"], activity_since=row["activity_since"],
        energy=row["energy"], focus=row["focus"], rest_need=row["rest_need"],
        social_openness=row["social_openness"], conservative_mode=bool(row["conservative_mode"]),
        anomaly_code=row["anomaly_code"],
    )


def get_state() -> SelfState | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM life_runtime_state WHERE id=1").fetchone()
        return _from_row(row) if row else None
    finally:
        conn.close()


def materialize(*, lease_token: str, now: float, timezone_id: str,
                modulation: Modulation | None = None) -> SelfState:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        lease = conn.execute("SELECT * FROM life_runtime_lease WHERE id=1").fetchone()
        if not lease or lease["lease_token"] != lease_token or lease["expires_at"] <= now:
            raise LifeRuntimeError("lease_not_owned", "LIFE materializer lease is not owned")
        row = conn.execute("SELECT * FROM life_runtime_state WHERE id=1").fetchone()
        state = _from_row(row) if row else default_state(now=now, timezone_id=timezone_id)
        anomaly: str | None = None
        if timezone_id != state.timezone_id:
            anomaly = "timezone_changed"
        elif now + CLOCK_ROLLBACK_TOLERANCE < state.reliable_wall_time:
            anomaly = "wall_clock_rollback"
        if anomaly:
            next_state = replace(
                state, revision=state.revision + 1, reliable_wall_time=max(state.reliable_wall_time, now),
                timezone_id=timezone_id, conservative_mode=True, anomaly_code=anomaly,
            )
            elapsed = 0.0
            event_type = "conservative_hold"
        else:
            elapsed = max(0.0, min(now - state.reliable_wall_time, MAX_ADVANCE_SECONDS))
            reduced = reduce_state(state, elapsed_seconds=elapsed, modulation=modulation or read_affect_modulation())
            next_state = replace(
                reduced, revision=state.revision + 1, reliable_wall_time=now,
                timezone_id=timezone_id, conservative_mode=False, anomaly_code=None,
            )
            event_type = "advanced"
        conn.execute(
            "INSERT INTO life_runtime_state(id,algorithm_version,revision,logical_time,reliable_wall_time,"
            "timezone_id,current_activity,activity_since,energy,focus,rest_need,social_openness,"
            "conservative_mode,anomaly_code,updated_at) VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET algorithm_version=excluded.algorithm_version,revision=excluded.revision,"
            "logical_time=excluded.logical_time,reliable_wall_time=excluded.reliable_wall_time,"
            "timezone_id=excluded.timezone_id,current_activity=excluded.current_activity,"
            "activity_since=excluded.activity_since,energy=excluded.energy,focus=excluded.focus,"
            "rest_need=excluded.rest_need,social_openness=excluded.social_openness,"
            "conservative_mode=excluded.conservative_mode,anomaly_code=excluded.anomaly_code,updated_at=excluded.updated_at",
            (ALGORITHM_VERSION, next_state.revision, next_state.logical_time, next_state.reliable_wall_time,
             next_state.timezone_id, next_state.current_activity, next_state.activity_since,
             next_state.energy, next_state.focus, next_state.rest_need, next_state.social_openness,
             int(next_state.conservative_mode), next_state.anomaly_code, now),
        )
        conn.execute(
            "INSERT INTO life_runtime_events(id,from_revision,to_revision,elapsed_seconds,event_type,"
            "anomaly_code,algorithm_version,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (db.new_id(), state.revision, next_state.revision, elapsed, event_type,
             next_state.anomaly_code, ALGORITHM_VERSION, now),
        )
        conn.commit()
        return next_state
    finally:
        conn.close()
