"""CDS.13 safe settings and rollback controls for cognitive decisions."""
from __future__ import annotations

import json
from typing import Any

from . import cognitive_decision as cds
from . import db

SETTINGS_VERSION = "cognition-settings-v1"
_CONFIG_KEY = "cognition_control_config"
_BINDINGS_KEY = "cognition_model_bindings"
_MODE_RANK = {"off": -1, "shadow": 0, "advisory": 1, "active": 2}
ROLE_VALUES = ("fast", "reasoning", "creative")


def _registry_modes() -> dict[str, str]:
    return {item["decision_kind"]: item["mode"] for item in cds.REGISTRY.public_snapshot()}


def _default_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "diagnostics_visible": False,
        "decision_modes": {kind: ceiling for kind, ceiling in _registry_modes().items()},
    }


def _load_stored() -> dict[str, Any]:
    try:
        value = json.loads(db.get_setting(_CONFIG_KEY, "{}") or "{}")
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}


def _load_bindings() -> dict[str, dict[str, str]]:
    try:
        value = json.loads(db.get_setting(_BINDINGS_KEY, "{}") or "{}")
    except (TypeError, ValueError):
        value = {}
    if not isinstance(value, dict):
        return {}
    return {
        role: {"provider_id": str(item["provider_id"]), "model": str(item["model"])}
        for role, item in value.items()
        if role in ROLE_VALUES and isinstance(item, dict)
        and item.get("provider_id") and item.get("model")
    }


def get_settings() -> dict[str, Any]:
    defaults = _default_config()
    stored = _load_stored()
    ceilings = _registry_modes()
    modes = dict(defaults["decision_modes"])
    candidate_modes = stored.get("decision_modes")
    if isinstance(candidate_modes, dict):
        for kind, mode in candidate_modes.items():
            if kind in ceilings and mode in _MODE_RANK and _MODE_RANK[mode] <= _MODE_RANK[ceilings[kind]]:
                modes[kind] = mode
    return {
        "settings_version": SETTINGS_VERSION,
        "enabled": bool(stored.get("enabled", defaults["enabled"])),
        "diagnostics_visible": bool(
            stored.get("diagnostics_visible", defaults["diagnostics_visible"])
        ),
        "decision_modes": modes,
        "mode_ceilings": ceilings,
        "model_bindings": _load_bindings(),
        "roles": list(ROLE_VALUES),
        "privacy": {
            "raw_output_persisted": False,
            "body_in_diagnostics": False,
            "remote_body_bearing_requires_authorization": True,
        },
        "natural_capabilities": [
            "更稳妥地理解当前对话",
            "在需要时整理可用的回忆与资料",
            "从反馈中调整谨慎程度",
        ],
    }


def _validate_binding(role: str, value: dict[str, Any]) -> dict[str, str]:
    if role not in ROLE_VALUES or not isinstance(value, dict):
        raise ValueError("invalid cognition model role")
    provider_id = str(value.get("provider_id") or "")
    model = str(value.get("model") or "")
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT models FROM providers WHERE id=? AND enabled=1", (provider_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise ValueError("cognition model Provider is unavailable")
    try:
        models = json.loads(row["models"] or "[]")
    except (TypeError, ValueError):
        models = []
    if model not in models:
        raise ValueError("cognition model is not registered for the Provider")
    return {"provider_id": provider_id, "model": model}


def update_settings(*, enabled: bool | None = None,
                    diagnostics_visible: bool | None = None,
                    decision_modes: dict[str, str] | None = None,
                    model_bindings: dict[str, dict[str, str] | None] | None = None) -> dict[str, Any]:
    current = get_settings()
    ceilings = current["mode_ceilings"]
    modes = dict(current["decision_modes"])
    if decision_modes is not None:
        if not isinstance(decision_modes, dict):
            raise ValueError("decision_modes must be an object")
        for kind, mode in decision_modes.items():
            if kind not in ceilings:
                raise ValueError("unknown decision kind")
            if mode not in _MODE_RANK or _MODE_RANK[mode] > _MODE_RANK[ceilings[kind]]:
                raise ValueError("decision mode exceeds the frozen ceiling")
            modes[kind] = mode
    bindings = dict(current["model_bindings"])
    if model_bindings is not None:
        if not isinstance(model_bindings, dict):
            raise ValueError("model_bindings must be an object")
        for role, value in model_bindings.items():
            if role not in ROLE_VALUES:
                raise ValueError("invalid cognition model role")
            if value is None:
                bindings.pop(role, None)
            else:
                bindings[role] = _validate_binding(role, value)
    config = {
        "enabled": current["enabled"] if enabled is None else bool(enabled),
        "diagnostics_visible": (
            current["diagnostics_visible"]
            if diagnostics_visible is None else bool(diagnostics_visible)
        ),
        "decision_modes": modes,
    }
    db.set_setting(_CONFIG_KEY, json.dumps(config, ensure_ascii=False, sort_keys=True))
    db.set_setting(_BINDINGS_KEY, json.dumps(bindings, ensure_ascii=False, sort_keys=True))
    return get_settings()


def rollback_to_legacy() -> dict[str, Any]:
    """One switch disables every model decision while preserving data and fallbacks."""
    config = _default_config()
    config["enabled"] = False
    config["diagnostics_visible"] = False
    config["decision_modes"] = {kind: "off" for kind in config["decision_modes"]}
    db.set_setting(_CONFIG_KEY, json.dumps(config, ensure_ascii=False, sort_keys=True))
    db.set_setting(_BINDINGS_KEY, "{}")
    return get_settings()


def decision_allows(decision_kind: str, mode: cds.DecisionMode) -> bool:
    settings = get_settings()
    configured = settings["decision_modes"].get(decision_kind, "off")
    return bool(settings["enabled"] and configured != "off"
                and _MODE_RANK[mode.value] <= _MODE_RANK[configured])
