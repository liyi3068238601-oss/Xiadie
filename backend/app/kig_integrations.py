"""Proposal-only KIG integration contracts for MEM, Episode, Saga and LIFE.

The owner system is always the sole writer.  These adapters expose typed,
bounded proposals or read-only SourceRef envelopes and never mutate owner rows.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from . import db, kig_sources, self_timeline

PROTOCOL_VERSION = "kig-system-proposal-v1"
PROPOSAL_TARGETS = {
    "memory_classification": "memory",
    "memory_conflict": "memory",
    "episode_boundary": "episode",
    "saga_transition": "saga",
    "memory_alias_sync": "memory",
}
_BODY_KEYS = frozenset({
    "body", "content", "text", "raw", "excerpt", "transcript", "prompt", "model_output",
})


class IntegrationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SystemProposal:
    id: str
    proposal_kind: str
    target_system: str
    source_kind: str
    source_id: str
    payload: dict
    confidence: float
    status: str
    protocol_version: str


def create_proposal(*, proposal_kind: str, source_kind: str, source_id: str,
                    payload: dict, confidence: float) -> dict:
    target = PROPOSAL_TARGETS.get(proposal_kind)
    if target is None:
        raise IntegrationError("proposal_kind_invalid", "system proposal kind is not allowlisted")
    ref = kig_sources.registry.resolve(source_kind, source_id)
    if ref.status != "active":
        raise IntegrationError("source_inactive", "proposal source must be active")
    if not isinstance(payload, dict) or len(json.dumps(payload, ensure_ascii=False)) > 8_000:
        raise IntegrationError("proposal_payload_invalid", "proposal payload is invalid or too large")
    if _contains_body_key(payload):
        raise IntegrationError(
            "proposal_body_forbidden", "system proposals may contain ids and classifications, not source bodies",
        )
    proposal_id, now = db.new_id(), db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO kig_system_proposals(id,proposal_kind,target_system,source_kind,source_id,"
            "payload_json,confidence,status,protocol_version,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (proposal_id, proposal_kind, target, source_kind, source_id,
             json.dumps(payload, ensure_ascii=False, sort_keys=True), float(confidence),
             "proposed", PROTOCOL_VERSION, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    try:
        kig_sources.bind_dependency(
            derived_kind="system_proposal", derived_id=proposal_id, source_ref=ref,
        )
    except Exception:
        conn = db.connect()
        try:
            conn.execute("DELETE FROM kig_system_proposals WHERE id=?", (proposal_id,))
            conn.commit()
        finally:
            conn.close()
        raise
    return get_proposal(proposal_id)


def get_proposal(proposal_id: str) -> dict:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM kig_system_proposals WHERE id=?", (proposal_id,)).fetchone()
        if not row:
            raise IntegrationError("proposal_missing", "system proposal does not exist")
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result
    finally:
        conn.close()


def decide_proposal(proposal_id: str, *, accepted: bool, owner_system: str) -> dict:
    """Record the owner's decision; applying the proposal remains owner code's job."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM kig_system_proposals WHERE id=?", (proposal_id,)).fetchone()
        if not row or row["status"] != "proposed":
            raise IntegrationError("proposal_stale", "proposal is missing or already decided")
        if row["target_system"] != owner_system:
            raise IntegrationError("owner_mismatch", "only the target owner may decide this proposal")
        conn.execute(
            "UPDATE kig_system_proposals SET status=?,updated_at=? WHERE id=?",
            ("accepted" if accepted else "rejected", db.now(), proposal_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_proposal(proposal_id)


def life_timeline_readonly(query: str, *, limit: int = 8) -> list[dict]:
    """Read the LIFE v1 SelfTimeline projection without writing any LIFE state."""
    if not _setting_enabled("life_enabled", default=True):
        return []
    return self_timeline.search(query, limit=max(1, min(limit, 20)))


def tool_run_source(tool_run_id: str) -> dict:
    ref = kig_sources.registry.resolve("tool_run", tool_run_id)
    return ref.to_dict()


def eap_readonly_snapshot() -> dict:
    """Expose a body-free EAP status snapshot without entering its frozen write protocols."""
    conn = db.connect()
    try:
        affect = conn.execute(
            "SELECT updated_at FROM affect_state WHERE id=1"
        ).fetchone()
        relationship = conn.execute(
            "SELECT interaction_count,updated_at FROM relationship_state WHERE id=1"
        ).fetchone()
        candidate_counts = {
            row["status"]: row["total"] for row in conn.execute(
                "SELECT status,COUNT(*) total FROM proactive_candidates GROUP BY status"
            ).fetchall()
        }
        delivery_counts = {
            row["status"]: row["total"] for row in conn.execute(
                "SELECT status,COUNT(*) total FROM proactive_deliveries GROUP BY status"
            ).fetchall()
        }
        return {
            "adapter_version": "eap-readonly-adapter-v1", "read_only": True,
            "affect_updated_at": affect["updated_at"] if affect else None,
            "relationship_updated_at": relationship["updated_at"] if relationship else None,
            "interaction_count": relationship["interaction_count"] if relationship else 0,
            "candidate_status_counts": candidate_counts,
            "delivery_status_counts": delivery_counts,
            "writes_performed": 0,
        }
    finally:
        conn.close()


def propose_memory_alias_sync(*, pwm_entity_id: str, memory_entity_id: str, alias: str,
                              source_kind: str, source_id: str, confidence: float) -> dict:
    """Create an auditable one-way proposal; neither entity store is modified."""
    conn = db.connect()
    try:
        pwm_row = conn.execute("SELECT id FROM pwm_entities WHERE id=?", (pwm_entity_id,)).fetchone()
        memory_row = conn.execute("SELECT id FROM memory_entities WHERE id=?", (memory_entity_id,)).fetchone()
    finally:
        conn.close()
    if not pwm_row or not memory_row or not alias.strip():
        raise IntegrationError("entity_link_invalid", "both owner entities and an alias are required")
    return create_proposal(
        proposal_kind="memory_alias_sync", source_kind=source_kind, source_id=source_id,
        payload={
            "pwm_entity_id": pwm_entity_id, "memory_entity_id": memory_entity_id,
            "alias": alias.strip(), "direction": "proposal_to_memory_owner",
            "automatic_merge": False, "bidirectional_overwrite": False,
        },
        confidence=confidence,
    )


def source_allowed(*, source: str, temporary_chat: bool = False) -> bool:
    if not _setting_enabled("kig_enabled", default=True):
        return False
    if source == "history":
        return not temporary_chat and _setting_value(
            "conversation_history_recall_mode", "explicit_only"
        ) != "off"
    if source == "memory":
        return _setting_enabled("memory_enabled", default=True) and not temporary_chat
    if source == "life":
        return _setting_enabled("life_enabled", default=True)
    if source == "knowledge":
        return _setting_value("knowledge_recall_mode", "explicit") != "off"
    return source in {"task", "lore"}


def _setting_value(key: str, default: str) -> str:
    conn = db.connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default
    finally:
        conn.close()


def _setting_enabled(key: str, *, default: bool) -> bool:
    return _setting_value(key, "1" if default else "0") == "1"


def _contains_body_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).casefold() in _BODY_KEYS
            or str(key).casefold().endswith(("_body", "_content", "_text", "_excerpt"))
            or str(key).casefold().startswith(("raw_", "prompt_", "transcript_"))
            or _contains_body_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_body_key(item) for item in value)
    return False
