"""Validated settings and conservative runtime policy for proactive companionship."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from .. import db


PROACTIVE_KINDS = (
    "emotional_care",
    "return_followup",
    "milestone_followup",
    "chat_continuation",
    "casual_greeting",
    "life_share",
)


def _boolean(value: str) -> str:
    if value not in {"0", "1"}:
        raise ValueError("must be '0' or '1'")
    return value


def _hour(value: str) -> str:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("must be an integer hour") from exc
    if str(parsed) != value or not 0 <= parsed <= 23:
        raise ValueError("must be an integer from 0 to 23")
    return str(parsed)


def _frequency(value: str) -> str:
    if value not in {"restrained", "standard", "custom"}:
        raise ValueError("must be restrained, standard, or custom")
    return value


def _pause_until(value: str) -> str:
    if value == "":
        return value
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("must be an ISO-8601 timestamp with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _csv(value: str) -> str:
    if len(value) > 4000:
        raise ValueError("must not exceed 4000 characters")
    return ",".join(part.strip() for part in value.split(",") if part.strip())


def _nonnegative_int(value: str) -> str:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("must be a non-negative integer") from exc
    if str(parsed) != value or parsed < 0:
        raise ValueError("must be a non-negative integer")
    return str(parsed)


def _nonnegative_float(value: str) -> str:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("must be a non-negative finite number") from exc
    if parsed < 0 or parsed != parsed or parsed in {float("inf"), float("-inf")}:
        raise ValueError("must be a non-negative finite number")
    return str(parsed)


@dataclass(frozen=True)
class SettingSpec:
    default: str
    validator: Callable[[str], str]
    public: bool = True


SETTING_REGISTRY: dict[str, SettingSpec] = {
    "proactive_enabled": SettingSpec("1", _boolean),
    # R4 rollout is explicit opt-in until the R5 feedback/control loop is complete.
    "proactive_local_delivery_enabled": SettingSpec("0", _boolean),
    "proactive_emergency_stop": SettingSpec("0", _boolean),
    "proactive_desktop_notification_enabled": SettingSpec("0", _boolean),
    # This compatibility key is readable, but external delivery is not available in EAP.
    "proactive_external_channels_enabled": SettingSpec("0", _boolean),
    "proactive_quiet_hours_start": SettingSpec("23", _hour),
    "proactive_quiet_hours_end": SettingSpec("9", _hour),
    "proactive_frequency_mode": SettingSpec("restrained", _frequency),
    "proactive_pause_until": SettingSpec("", _pause_until),
    "proactive_show_advanced_diagnostics": SettingSpec("0", _boolean),
    "proactive_rejected_topics": SettingSpec("", _csv, public=False),
    "proactive_rejected_kinds": SettingSpec("", _csv, public=False),
    "proactive_settings_revision": SettingSpec("0", _nonnegative_int, public=False),
    "proactive_last_reliable_now": SettingSpec("0", _nonnegative_float, public=False),
    "proactive_resume_guard_until": SettingSpec("0", _nonnegative_float, public=False),
    **{
        f"proactive_kind_{kind}_enabled": SettingSpec("1", _boolean)
        for kind in PROACTIVE_KINDS
    },
}

DEFAULTS = {key: spec.default for key, spec in SETTING_REGISTRY.items()}


def validate_setting(key: str, value: str, *, public_write: bool = True) -> str:
    spec = SETTING_REGISTRY.get(key)
    if spec is None:
        raise ValueError("unknown proactive setting")
    if public_write and not spec.public:
        raise ValueError("setting is managed internally")
    normalized = spec.validator(value)
    if key == "proactive_external_channels_enabled" and normalized != "0":
        raise ValueError("external proactive channels are hard disabled")
    return normalized


def load_settings(overrides: Optional[dict[str, str]] = None) -> dict[str, str]:
    values = {
        key: db.get_setting(key, spec.default)
        for key, spec in SETTING_REGISTRY.items()
    }
    if overrides:
        values.update(overrides)
    # Corrupt or legacy values fail closed instead of escaping into policy code.
    validated: dict[str, str] = {}
    for key, spec in SETTING_REGISTRY.items():
        try:
            validated[key] = validate_setting(key, values[key], public_write=False)
        except ValueError:
            if key == "proactive_emergency_stop":
                validated[key] = "1"
            elif key == "proactive_pause_until":
                validated[key] = "invalid"
            elif spec.validator is _boolean:
                validated[key] = "0"
            else:
                validated[key] = spec.default
    validated["proactive_external_channels_enabled"] = "0"
    return validated


def write_public_setting(key: str, value: str) -> tuple[str, int]:
    """Atomically update a public setting and its authorization revision."""
    normalized = validate_setting(key, value, public_write=True)
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT value FROM settings WHERE key='proactive_settings_revision'"
        ).fetchone()
        try:
            revision = int(row["value"] if row else 0) + 1
        except (TypeError, ValueError):
            revision = 1
        for setting_key, setting_value in (
            (key, normalized), ("proactive_settings_revision", str(revision)),
        ):
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (setting_key, setting_value),
            )
        conn.commit()
        return normalized, revision
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reset_public_settings() -> tuple[dict[str, str], int]:
    """Reset the whole proactive control surface in one authorization revision."""
    public_defaults = {
        key: spec.default for key, spec in SETTING_REGISTRY.items() if spec.public
    }
    defaults = dict(public_defaults)
    defaults.update({
        "proactive_rejected_topics": "",
        "proactive_rejected_kinds": "",
    })
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT value FROM settings WHERE key='proactive_settings_revision'"
        ).fetchone()
        try:
            revision = int(row["value"] if row else 0) + 1
        except (TypeError, ValueError):
            revision = 1
        for key, value in defaults.items():
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        conn.execute(
            "INSERT INTO settings(key,value) VALUES('proactive_settings_revision',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(revision),),
        )
        conn.execute("DELETE FROM proactive_preference_weights")
        conn.commit()
        return public_defaults, revision
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


FREQUENCY_COST_ADDITION = {
    "restrained": 0.15,
    "standard": 0.0,
    "custom": 0.10,  # Conservative until custom numeric controls are introduced.
}


@dataclass(frozen=True)
class EffectivePolicy:
    settings: dict[str, str]
    blocked_reasons: tuple[str, ...]
    frequency_cost_addition: float

    @property
    def allows_non_silent(self) -> bool:
        return not self.blocked_reasons


def effective_policy(
    *,
    now: Optional[float] = None,
    last_seen_now: Optional[float] = None,
    candidate_kind: Optional[str] = None,
    overrides: Optional[dict[str, str]] = None,
) -> EffectivePolicy:
    """Resolve hard boundaries; clock rollback and invalid pause values fail closed."""
    now = db.now() if now is None else now
    values = load_settings(overrides)
    if last_seen_now is None:
        last_seen_now = float(values["proactive_last_reliable_now"])
    reasons: list[str] = []
    if values["proactive_enabled"] != "1":
        reasons.append("proactive_disabled")
    if values["proactive_emergency_stop"] == "1":
        reasons.append("emergency_stop")
    if last_seen_now is not None and now < last_seen_now:
        reasons.append("clock_rollback")
    if now < float(values["proactive_resume_guard_until"]):
        reasons.append("system_resume_guard")
    pause = values["proactive_pause_until"]
    if pause:
        try:
            if datetime.fromisoformat(pause.replace("Z", "+00:00")).timestamp() > now:
                reasons.append("proactive_paused")
        except (OverflowError, ValueError):
            reasons.append("invalid_pause_until")
    if candidate_kind is not None:
        if candidate_kind not in PROACTIVE_KINDS:
            reasons.append("unknown_candidate_kind")
        elif values[f"proactive_kind_{candidate_kind}_enabled"] != "1":
            reasons.append("candidate_kind_disabled")
    mode = values["proactive_frequency_mode"]
    return EffectivePolicy(values, tuple(reasons), FREQUENCY_COST_ADDITION[mode])


def observe_reliable_clock(now: float) -> bool:
    """Persist a monotonic wall-clock watermark; a rollback fails closed."""
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT value FROM settings WHERE key='proactive_last_reliable_now'"
        ).fetchone()
        try:
            previous = float(row["value"] if row else 0)
        except (TypeError, ValueError):
            previous = now
        if now < previous:
            conn.commit()
            return False
        conn.execute(
            "INSERT INTO settings(key,value) VALUES('proactive_last_reliable_now',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(float(now)),),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_system_resume(now: Optional[float] = None, *, grace_seconds: float = 300.0) -> float:
    """Fail closed briefly after an OS resume so overdue work cannot burst out."""
    now = db.now() if now is None else now
    if grace_seconds < 0:
        raise ValueError("grace_seconds must be non-negative")
    guard_until = now + grace_seconds
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT value FROM settings WHERE key='proactive_resume_guard_until'"
        ).fetchone()
        try:
            existing = float(row["value"] if row else 0)
        except (TypeError, ValueError):
            existing = 0
        guard_until = max(existing, guard_until)
        conn.execute(
            "INSERT INTO settings(key,value) VALUES('proactive_resume_guard_until',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(float(guard_until)),),
        )
        conn.commit()
        return guard_until
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
