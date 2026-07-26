"""CDS.12 body-free feedback and per-decision calibration profiles.

Profiles are advisory metadata while every registered decision kind remains
Shadow-only. Feedback can tune only two bounded preference parameters; safety,
privacy, ownership, schemas and application gates are immutable.
"""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any

from . import db
from .proactive import run_ledger

PROFILE_VERSION = "cognition-calibration-profile-v1"
FEEDBACK_PROTOCOL_VERSION = "cognition-feedback-v1"


class FeedbackDomain(str, Enum):
    RECALL = "recall"
    PROACTIVE = "proactive"
    RELATIONSHIP = "relationship"
    MEMORY = "memory"


class FeedbackKind(str, Enum):
    HELPFUL = "helpful"
    NOT_HELPFUL = "not_helpful"
    MISSING = "missing"
    WRONG_SOURCE = "wrong_source"
    QUICK_REPLY = "quick_reply"
    LATER_REPLY = "later_reply"
    UNANSWERED = "unanswered"
    REJECTED = "rejected"
    CORRECTED = "corrected"


DECISION_DOMAINS = {
    "recall_planner": FeedbackDomain.RECALL,
    "candidate_reranker": FeedbackDomain.RECALL,
    "presence_thread_observer": FeedbackDomain.PROACTIVE,
    "companion_cognition": FeedbackDomain.RELATIONSHIP,
    "memory_conflict_proposal": FeedbackDomain.MEMORY,
    "memory_retention_proposal": FeedbackDomain.MEMORY,
    "episode_boundary_proposal": FeedbackDomain.MEMORY,
    "saga_transition_proposal": FeedbackDomain.MEMORY,
}

DOMAIN_FEEDBACK = {
    FeedbackDomain.RECALL: frozenset({
        FeedbackKind.HELPFUL, FeedbackKind.NOT_HELPFUL, FeedbackKind.MISSING,
        FeedbackKind.WRONG_SOURCE, FeedbackKind.CORRECTED,
    }),
    FeedbackDomain.PROACTIVE: frozenset({
        FeedbackKind.QUICK_REPLY, FeedbackKind.LATER_REPLY, FeedbackKind.UNANSWERED,
        FeedbackKind.REJECTED, FeedbackKind.CORRECTED,
    }),
    FeedbackDomain.RELATIONSHIP: frozenset({
        FeedbackKind.HELPFUL, FeedbackKind.NOT_HELPFUL, FeedbackKind.REJECTED,
        FeedbackKind.CORRECTED,
    }),
    FeedbackDomain.MEMORY: frozenset({
        FeedbackKind.HELPFUL, FeedbackKind.NOT_HELPFUL, FeedbackKind.MISSING,
        FeedbackKind.REJECTED, FeedbackKind.CORRECTED,
    }),
}

ADJUSTABLE_PARAMS = frozenset({"selection_bias", "caution_bias"})
IMMUTABLE_BOUNDARIES = frozenset({
    "application_owner", "fallback_owner", "privacy_class", "mode_ceiling",
    "source_revision", "candidate_allowlist", "validator", "protocol_version",
})
DEFAULT_PARAMETERS = {"selection_bias": 0.0, "caution_bias": 0.0}
PARAMETER_LIMITS = {
    "selection_bias": (-0.20, 0.20),
    "caution_bias": (0.0, 0.40),
}

_DELTAS = {
    FeedbackKind.HELPFUL: {"selection_bias": 0.02, "caution_bias": -0.01},
    FeedbackKind.NOT_HELPFUL: {"selection_bias": -0.02, "caution_bias": 0.02},
    FeedbackKind.MISSING: {"selection_bias": 0.03, "caution_bias": 0.0},
    FeedbackKind.WRONG_SOURCE: {"selection_bias": -0.02, "caution_bias": 0.03},
    FeedbackKind.QUICK_REPLY: {"selection_bias": 0.02, "caution_bias": -0.01},
    FeedbackKind.LATER_REPLY: {"selection_bias": 0.0, "caution_bias": 0.02},
    FeedbackKind.UNANSWERED: {"selection_bias": -0.02, "caution_bias": 0.05},
    FeedbackKind.REJECTED: {"selection_bias": -0.04, "caution_bias": 0.08},
    FeedbackKind.CORRECTED: {"selection_bias": -0.03, "caution_bias": 0.04},
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _idempotency_key(*parts: str) -> str:
    digest = hashlib.sha256(_canonical_json(parts).encode("utf-8")).hexdigest()
    return f"{FEEDBACK_PROTOCOL_VERSION}:{digest}"


def _bounded_parameters(current: dict[str, float], delta: dict[str, float]) -> dict[str, float]:
    if set(current) != ADJUSTABLE_PARAMS or not set(delta) <= ADJUSTABLE_PARAMS:
        raise ValueError("calibration parameters are outside the adjustable allowlist")
    result: dict[str, float] = {}
    for key in sorted(ADJUSTABLE_PARAMS):
        low, high = PARAMETER_LIMITS[key]
        result[key] = round(min(high, max(low, float(current[key]) + delta.get(key, 0.0))), 6)
    return result


def _profile_from_row(row) -> dict[str, Any]:
    return {
        "decision_kind": row["decision_kind"],
        "domain": row["feedback_domain"],
        "profile_version": row["profile_version"],
        "revision": row["revision"],
        "parameters": json.loads(row["parameters_json"]),
        "feedback_count": row["feedback_count"],
        "updated_at": row["updated_at"],
    }


def get_profile(decision_kind: str) -> dict[str, Any]:
    domain = DECISION_DOMAINS.get(decision_kind)
    if domain is None:
        raise ValueError("decision kind does not accept calibration feedback")
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM cognition_calibration_profiles WHERE decision_kind=?",
            (decision_kind,),
        ).fetchone()
    finally:
        conn.close()
    if row:
        return _profile_from_row(row)
    return {
        "decision_kind": decision_kind, "domain": domain.value,
        "profile_version": PROFILE_VERSION, "revision": 0,
        "parameters": dict(DEFAULT_PARAMETERS), "feedback_count": 0, "updated_at": None,
    }


def list_profiles() -> list[dict[str, Any]]:
    return [get_profile(kind) for kind in sorted(DECISION_DOMAINS)]


def submit_feedback(*, decision_kind: str, feedback_kind: str,
                    source_run_id: str | None, request_nonce: str) -> dict[str, Any]:
    domain = DECISION_DOMAINS.get(decision_kind)
    try:
        kind = FeedbackKind(feedback_kind)
    except ValueError as exc:
        raise ValueError("unknown cognition feedback kind") from exc
    if domain is None or kind not in DOMAIN_FEEDBACK[domain]:
        raise ValueError("feedback kind is not valid for this decision domain")
    if not request_nonce or len(request_nonce) > 128:
        raise ValueError("request_nonce must contain 1..128 characters")
    if source_run_id:
        run = run_ledger.get_run(source_run_id)
        if run is None or run.task_kind != decision_kind:
            raise ValueError("feedback source run does not match the decision kind")

    key = _idempotency_key("feedback", decision_kind, kind.value, source_run_id or "", request_nonce)
    now = db.now()
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM cognition_feedback_signals WHERE idempotency_key=?", (key,),
        ).fetchone()
        if existing:
            profile = conn.execute(
                "SELECT * FROM cognition_calibration_profiles WHERE decision_kind=?",
                (decision_kind,),
            ).fetchone()
            conn.commit()
            return {"created": False, "feedback_id": existing["id"],
                    "profile": _profile_from_row(profile)}

        row = conn.execute(
            "SELECT * FROM cognition_calibration_profiles WHERE decision_kind=?",
            (decision_kind,),
        ).fetchone()
        current = json.loads(row["parameters_json"]) if row else dict(DEFAULT_PARAMETERS)
        updated = _bounded_parameters(current, _DELTAS[kind])
        revision = (row["revision"] if row else 0) + 1
        count = (row["feedback_count"] if row else 0) + 1
        feedback_id = db.new_id()
        conn.execute(
            "INSERT INTO cognition_feedback_signals("
            "id,decision_kind,feedback_domain,feedback_kind,source_run_id,idempotency_key,"
            "parameter_delta_json,profile_revision,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (feedback_id, decision_kind, domain.value, kind.value, source_run_id, key,
             _canonical_json(_DELTAS[kind]), revision, now),
        )
        conn.execute(
            "INSERT INTO cognition_calibration_profiles("
            "decision_kind,feedback_domain,profile_version,revision,parameters_json,"
            "feedback_count,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(decision_kind) DO UPDATE SET revision=excluded.revision,"
            "parameters_json=excluded.parameters_json,feedback_count=excluded.feedback_count,"
            "updated_at=excluded.updated_at",
            (decision_kind, domain.value, PROFILE_VERSION, revision, _canonical_json(updated),
             count, row["created_at"] if row else now, now),
        )
        conn.execute(
            "INSERT INTO cognition_calibration_events("
            "id,decision_kind,event_type,from_revision,to_revision,changes_json,"
            "idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (db.new_id(), decision_kind, "feedback_applied", revision - 1, revision,
             _canonical_json(_DELTAS[kind]), key, now),
        )
        profile = conn.execute(
            "SELECT * FROM cognition_calibration_profiles WHERE decision_kind=?",
            (decision_kind,),
        ).fetchone()
        conn.commit()
        return {"created": True, "feedback_id": feedback_id,
                "profile": _profile_from_row(profile)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def rollback_profile(*, decision_kind: str, request_nonce: str) -> dict[str, Any]:
    domain = DECISION_DOMAINS.get(decision_kind)
    if domain is None:
        raise ValueError("decision kind does not accept calibration feedback")
    if not request_nonce or len(request_nonce) > 128:
        raise ValueError("request_nonce must contain 1..128 characters")
    key = _idempotency_key("rollback", decision_kind, request_nonce)
    now = db.now()
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        event = conn.execute(
            "SELECT to_revision FROM cognition_calibration_events WHERE idempotency_key=?", (key,),
        ).fetchone()
        if event:
            profile = conn.execute(
                "SELECT * FROM cognition_calibration_profiles WHERE decision_kind=?",
                (decision_kind,),
            ).fetchone()
            conn.commit()
            return {"rolled_back": False, "profile": _profile_from_row(profile)}
        row = conn.execute(
            "SELECT * FROM cognition_calibration_profiles WHERE decision_kind=?",
            (decision_kind,),
        ).fetchone()
        from_revision = row["revision"] if row else 0
        revision = from_revision + 1
        conn.execute(
            "INSERT INTO cognition_calibration_profiles("
            "decision_kind,feedback_domain,profile_version,revision,parameters_json,"
            "feedback_count,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(decision_kind) DO UPDATE SET revision=excluded.revision,"
            "parameters_json=excluded.parameters_json,feedback_count=0,updated_at=excluded.updated_at",
            (decision_kind, domain.value, PROFILE_VERSION, revision,
             _canonical_json(DEFAULT_PARAMETERS), 0, row["created_at"] if row else now, now),
        )
        conn.execute(
            "INSERT INTO cognition_calibration_events("
            "id,decision_kind,event_type,from_revision,to_revision,changes_json,"
            "idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (db.new_id(), decision_kind, "profile_rolled_back", from_revision, revision,
             _canonical_json(DEFAULT_PARAMETERS), key, now),
        )
        profile = conn.execute(
            "SELECT * FROM cognition_calibration_profiles WHERE decision_kind=?",
            (decision_kind,),
        ).fetchone()
        conn.commit()
        return {"rolled_back": True, "profile": _profile_from_row(profile)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def diagnostics(limit: int = 100) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    conn = db.connect()
    try:
        feedback = [dict(row) for row in conn.execute(
            "SELECT id,decision_kind,feedback_domain,feedback_kind,source_run_id,"
            "profile_revision,created_at FROM cognition_feedback_signals "
            "ORDER BY created_at DESC,id DESC LIMIT ?", (limit,),
        ).fetchall()]
        events = [dict(row) for row in conn.execute(
            "SELECT id,decision_kind,event_type,from_revision,to_revision,created_at "
            "FROM cognition_calibration_events ORDER BY created_at DESC,id DESC LIMIT ?",
            (limit,),
        ).fetchall()]
    finally:
        conn.close()
    return {
        "feedback_protocol_version": FEEDBACK_PROTOCOL_VERSION,
        "profile_version": PROFILE_VERSION,
        "adjustable_params": sorted(ADJUSTABLE_PARAMS),
        "immutable_boundaries": sorted(IMMUTABLE_BOUNDARIES),
        "profiles": list_profiles(), "feedback": feedback, "events": events,
    }
