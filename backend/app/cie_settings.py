"""CIE feature gate and frozen fallback contract.

CIE.0 intentionally does not import this module from the chat hot path.  The
single flag is established here so later stages have one fail-closed control
instead of adding per-feature switches with inconsistent rollback semantics.
"""
from __future__ import annotations

from . import db

PROTOCOL_VERSION = "cie-settings-v1"
SETTING_KEY = "cie_enabled"
DEFAULT_ENABLED = False


def is_enabled() -> bool:
    """Return the CIE gate; missing and malformed values fail closed."""
    return db.get_setting(SETTING_KEY, "0") == "1"


def set_enabled(enabled: bool) -> bool:
    """Set the single CIE gate and return its normalized value."""
    db.set_setting(SETTING_KEY, "1" if enabled else "0")
    return is_enabled()


def snapshot() -> dict[str, object]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "setting_key": SETTING_KEY,
        "enabled": is_enabled(),
        "default_enabled": DEFAULT_ENABLED,
        "fallback": {
            "turn_mode": "single_message",
            "generation_mode": "single_generation",
            "transport": "text_sse",
            "attachment_mode": "local_text_extraction",
            "native_image": False,
            "context_contribution": False,
        },
    }
