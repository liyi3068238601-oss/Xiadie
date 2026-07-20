"""CTX.6 user controls for conversation continuity.

These controls are deliberately independent from long-term memory.  Disabling
either control never deletes raw conversation messages.
"""
from __future__ import annotations

from . import db, history_recall

SUMMARY_INJECTION_KEY = "conversation_summary_injection_enabled"
HISTORY_MODE_KEY = "conversation_history_recall_mode"


def read() -> dict[str, object]:
    mode = history_recall.settings()["mode"]
    return {
        "reference_chat_history": mode != "off",
        "summary_injection_enabled": db.get_setting(SUMMARY_INJECTION_KEY, "1") == "1",
        "summary_automatic": True,
        "history_mode": mode,
        "ordinary_history_recall": "shadow",
        "memory_enabled": db.get_setting("memory_enabled", db.DEFAULT_MEMORY_ENABLED) == "1",
    }


def update(*, reference_chat_history: bool | None = None,
           summary_injection_enabled: bool | None = None) -> dict[str, object]:
    if reference_chat_history is not None:
        # CTX.6 does not graduate ordinary recall from shadow mode.  The normal
        # switch enables explicit, user-requested recall only.
        db.set_setting(HISTORY_MODE_KEY, "explicit_only" if reference_chat_history else "off")
    if summary_injection_enabled is not None:
        db.set_setting(SUMMARY_INJECTION_KEY, "1" if summary_injection_enabled else "0")
    return read()


def summary_injection_enabled() -> bool:
    return db.get_setting(SUMMARY_INJECTION_KEY, "1") == "1"
