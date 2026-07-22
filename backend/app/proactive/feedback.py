"""Grounded proactive feedback, learned preferences, and privacy-safe history."""
from __future__ import annotations

import json
from typing import Optional

from .. import db
from .protocols import PROACTIVE_FEEDBACK_V1
from .schemas import validate_proactive_feedback

FEEDBACK_KINDS = {
    "wrong_timing", "too_frequent", "wrong_content", "reject_topic",
    "reject_tone", "allow_more",
}
HIGH_CONFIDENCE_CUES = (
    ("wrong_timing", ("时机不对", "现在不方便", "现在别提醒")),
    ("too_frequent", ("太频繁", "别老是提醒", "少一点这种消息")),
    ("wrong_content", ("内容不对", "不是这个内容")),
    ("reject_topic", ("别再提", "不要再提")),
    ("reject_tone", ("别用这种语气", "别这样说")),
    ("allow_more", ("可以继续提醒", "继续提醒我")),
)
LOW_CONFIDENCE_CUES = ("不太喜欢", "有点烦", "不太合适")

NATURAL_REASONS = {
    "emotional_care": "想在你可能需要时关心一下",
    "return_followup": "记得你说过稍后回来",
    "milestone_followup": "想起了我们共同经历的一个节点",
    "chat_continuation": "想延续刚才没有说完的话题",
    "casual_greeting": "隔了一段时间，想轻轻问候一下",
    "life_share": "想和你分享一件生活里的小事",
}


def _event(conn, feedback_id: str, event_type: str, before: Optional[str], after: str,
           now: float, metadata: Optional[dict] = None) -> None:
    conn.execute(
        "INSERT INTO proactive_feedback_events(id,feedback_id,event_type,from_status,to_status,"
        "metadata_json,created_at) VALUES(?,?,?,?,?,?,?)",
        (db.new_id(), feedback_id, event_type, before, after,
         json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True), now),
    )


def _increment_revision_locked(conn) -> int:
    row = conn.execute(
        "SELECT value FROM settings WHERE key='proactive_settings_revision'"
    ).fetchone()
    try:
        revision = int(row["value"] if row else 0) + 1
    except (TypeError, ValueError):
        revision = 1
    conn.execute(
        "INSERT INTO settings(key,value) VALUES('proactive_settings_revision',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(revision),),
    )
    return revision


def _upsert_preference(conn, feedback_id: str, dimension: str, value: Optional[str],
                       cost_delta: float, acceptance_delta: float, now: float) -> None:
    if not value:
        return
    conn.execute(
        "INSERT INTO proactive_preference_weights(id,dimension,value,contact_cost_delta,"
        "acceptance_delta,source_feedback_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(dimension,value) DO UPDATE SET "
        "contact_cost_delta=MAX(-1,MIN(1,contact_cost_delta+excluded.contact_cost_delta)),"
        "acceptance_delta=MAX(-1,MIN(1,acceptance_delta+excluded.acceptance_delta)),"
        "source_feedback_id=excluded.source_feedback_id,updated_at=excluded.updated_at",
        (db.new_id(), dimension, value, cost_delta, acceptance_delta,
         feedback_id, now, now),
    )


def _apply_locked(conn, feedback_row, now: float) -> dict:
    kind = feedback_row["feedback_kind"]
    pressure_addition = {
        "wrong_timing": 0.5, "too_frequent": 1.0, "wrong_content": 0.25,
        "reject_topic": 0.5, "reject_tone": 0.25, "allow_more": 0.0,
    }[kind]
    if feedback_row["episode_id"]:
        if kind == "allow_more":
            conn.execute(
                "UPDATE contact_episodes SET unanswered_pressure=MAX(0,unanswered_pressure*0.5),"
                "updated_at=? WHERE id=?", (now, feedback_row["episode_id"]),
            )
        else:
            conn.execute(
                "UPDATE contact_episodes SET unanswered_pressure=MIN(5,unanswered_pressure+?),"
                "updated_at=? WHERE id=?", (pressure_addition, now, feedback_row["episode_id"]),
            )
    if kind == "wrong_timing":
        _upsert_preference(conn, feedback_row["id"], "kind", feedback_row["target_kind"], 0.10, -0.05, now)
    elif kind == "too_frequent":
        _upsert_preference(conn, feedback_row["id"], "kind", feedback_row["target_kind"], 0.25, -0.15, now)
    elif kind == "wrong_content":
        _upsert_preference(conn, feedback_row["id"], "topic", feedback_row["target_topic"], 0.20, -0.10, now)
    elif kind == "reject_topic":
        _upsert_preference(conn, feedback_row["id"], "topic", feedback_row["target_topic"], 0.50, -1.0, now)
    elif kind == "reject_tone":
        _upsert_preference(
            conn, feedback_row["id"], "expression_act",
            feedback_row["target_expression_act"] or "default", 0.0, -1.0, now,
        )
    elif kind == "allow_more":
        _upsert_preference(conn, feedback_row["id"], "kind", feedback_row["target_kind"], -0.15, 0.25, now)
    revision = _increment_revision_locked(conn)
    return {"pressure_addition": pressure_addition, "settings_revision": revision}


def create_feedback(
    delivery_id: str, feedback_kind: str, *, source: str = "explicit",
    request_nonce: Optional[str] = None, evidence_message_id: Optional[str] = None,
    evidence_quote: Optional[str] = None, confidence: float = 1.0,
    now: Optional[float] = None,
) -> dict:
    if feedback_kind not in FEEDBACK_KINDS:
        raise ValueError("invalid feedback_kind")
    if source not in {"explicit", "natural_language"}:
        raise ValueError("invalid feedback source")
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")
    now = db.now() if now is None else now
    nonce = request_nonce or db.new_id()
    idempotency_key = f"{source}:{delivery_id}:{nonce}:{feedback_kind}"
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM proactive_feedback WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        if existing:
            conn.commit()
            return _public(existing)
        context = conn.execute(
            "SELECT d.*,c.candidate_kind,pd.topic,pd.expression_act FROM proactive_deliveries d "
            "JOIN proactive_candidates c ON c.id=d.candidate_id "
            "JOIN proactive_decisions pd ON pd.id=d.decision_id WHERE d.id=?",
            (delivery_id,),
        ).fetchone()
        if context is None:
            raise ValueError("delivery not found")
        if context["status"] != "delivered":
            raise ValueError("feedback requires a delivered action")
        status = "applied" if source == "explicit" or confidence >= 0.85 else "pending"
        feedback_id = db.new_id()
        conn.execute(
            "INSERT INTO proactive_feedback(id,delivery_id,episode_id,session_id,feedback_kind,"
            "source,status,evidence_message_id,evidence_quote,target_topic,target_kind,"
            "target_expression_act,confidence,policy_effect_json,idempotency_key,protocol_version,"
            "resolved_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'{}',?,?,?,?,?)",
            (feedback_id, delivery_id, context["episode_id"], context["session_id"], feedback_kind,
             source, status, evidence_message_id, evidence_quote, context["topic"],
             context["candidate_kind"], context["expression_act"], confidence, idempotency_key,
             PROACTIVE_FEEDBACK_V1, now if status == "applied" else None, now, now),
        )
        row = conn.execute("SELECT * FROM proactive_feedback WHERE id=?", (feedback_id,)).fetchone()
        effect = _apply_locked(conn, row, now) if status == "applied" else {}
        if effect:
            conn.execute(
                "UPDATE proactive_feedback SET policy_effect_json=? WHERE id=?",
                (json.dumps(effect, sort_keys=True), feedback_id),
            )
        _event(conn, feedback_id, "created", None, status, now, {"source": source})
        conn.commit()
        return _public(conn.execute(
            "SELECT * FROM proactive_feedback WHERE id=?", (feedback_id,)
        ).fetchone())
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def capture_natural_feedback(session_id: str, message_id: str, user_text: str,
                             *, now: Optional[float] = None) -> Optional[dict]:
    """Capture only verbatim deterministic cues; vague cues remain pending."""
    now = db.now() if now is None else now
    found_kind, quote, confidence = None, None, 0.0
    for kind, cues in HIGH_CONFIDENCE_CUES:
        quote = next((cue for cue in cues if cue in user_text), None)
        if quote:
            found_kind, confidence = kind, 1.0
            break
    if found_kind is None:
        quote = next((cue for cue in LOW_CONFIDENCE_CUES if cue in user_text), None)
        if quote:
            found_kind, confidence = "wrong_content", 0.55
    if found_kind is None or quote is None:
        return None
    conn = db.connect()
    try:
        latest = conn.execute(
            "SELECT id FROM proactive_deliveries WHERE session_id=? AND status='delivered' "
            "AND COALESCE(delivered_at,created_at)>=? ORDER BY COALESCE(delivered_at,created_at) DESC LIMIT 1",
            (session_id, now - 7 * 24 * 3600),
        ).fetchone()
    finally:
        conn.close()
    if latest is None:
        return None
    validated = validate_proactive_feedback({
        "protocol_version": PROACTIVE_FEEDBACK_V1,
        "feedback_kind": found_kind,
        "delivery_id": latest["id"],
        "evidence": [{"quote": quote}],
        "target_topic": None,
        "target_kind": None,
        "confidence": confidence,
    }, user_text=user_text)
    return create_feedback(
        latest["id"], validated.feedback_kind, source="natural_language",
        request_nonce=message_id, evidence_message_id=message_id,
        evidence_quote=validated.evidence[0].quote, confidence=validated.confidence, now=now,
    )


def resolve_feedback(feedback_id: str, *, accept: bool, now: Optional[float] = None) -> dict:
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM proactive_feedback WHERE id=?", (feedback_id,)).fetchone()
        if row is None:
            raise ValueError("feedback not found")
        if row["status"] != "pending":
            conn.commit()
            return _public(row)
        status = "applied" if accept else "rejected"
        effect = _apply_locked(conn, row, now) if accept else {}
        conn.execute(
            "UPDATE proactive_feedback SET status=?,policy_effect_json=?,resolved_at=?,updated_at=? "
            "WHERE id=?", (status, json.dumps(effect, sort_keys=True), now, now, feedback_id),
        )
        _event(conn, feedback_id, "resolved", "pending", status, now)
        conn.commit()
        return _public(conn.execute(
            "SELECT * FROM proactive_feedback WHERE id=?", (feedback_id,)
        ).fetchone())
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _public(row) -> dict:
    item = dict(row)
    item["policy_effect"] = json.loads(item.pop("policy_effect_json") or "{}")
    return item


def list_pending(limit: int = 50) -> list[dict]:
    conn = db.connect()
    try:
        return [_public(row) for row in conn.execute(
            "SELECT * FROM proactive_feedback WHERE status='pending' "
            "ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 100)),),
        ).fetchall()]
    finally:
        conn.close()


def list_history(limit: int = 50) -> list[dict]:
    """User-facing history: natural reasons and outcomes, never model scores/hashes."""
    conn = db.connect()
    try:
        deliveries = conn.execute(
            "SELECT d.id,d.session_id,d.level,d.channel,d.status,d.error_code,d.delivered_at,"
            "d.acknowledged_at,d.created_at,d.updated_at,c.candidate_kind,pd.topic "
            "FROM proactive_deliveries d JOIN proactive_candidates c ON c.id=d.candidate_id "
            "JOIN proactive_decisions pd ON pd.id=d.decision_id "
            "ORDER BY d.created_at DESC LIMIT ?", (max(1, min(limit, 200)),),
        ).fetchall()
        result = []
        for delivery in deliveries:
            item = dict(delivery)
            item["natural_reason"] = NATURAL_REASONS.get(
                item["candidate_kind"], "基于当前对话状态产生的一次克制接近"
            )
            item["feedback"] = [dict(row) for row in conn.execute(
                "SELECT id,delivery_id,feedback_kind,source,status,evidence_quote,created_at "
                "FROM proactive_feedback WHERE delivery_id=? ORDER BY created_at",
                (item["id"],),
            ).fetchall()]
            result.append(item)
        return result
    finally:
        conn.close()


def diagnostics(limit: int = 100) -> dict:
    """Body-free diagnostics: IDs, gates, statuses and protocol versions only."""
    conn = db.connect()
    try:
        deliveries = [dict(row) for row in conn.execute(
            "SELECT id,decision_id,candidate_id,episode_id,session_id,level,channel,status,"
            "attempt_count,error_code,created_at,updated_at FROM proactive_deliveries "
            "ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),),
        ).fetchall()]
        candidates = [dict(row) for row in conn.execute(
            "SELECT id,session_id,episode_id,candidate_kind,status,runtime_source_id,due_at,"
            "expires_at,protocol_version,created_at,updated_at FROM proactive_candidates "
            "ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),),
        ).fetchall()]
        sagas = [dict(row) for row in conn.execute(
            "SELECT candidate_id,decision_id,intensity_plan_id,expression_plan_id,status,"
            "attempt_count,error_code,created_at,updated_at FROM proactive_runtime_sagas "
            "ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),),
        ).fetchall()]
        decisions = []
        for row in conn.execute(
            "SELECT id,candidate_id,session_id,decision,reason_codes,layer1_blocked,"
            "layer1_block_reasons,layer2_deferred,layer2_defer_reasons,protocol_version,created_at "
            "FROM proactive_decisions ORDER BY created_at DESC LIMIT ?",
            (max(1, min(limit, 200)),),
        ).fetchall():
            item = dict(row)
            reasons = json.loads(item["reason_codes"] or "[]")
            item["reason_codes"] = [reason for reason in reasons if "=" not in reason]
            item["layer1_block_reasons"] = json.loads(item["layer1_block_reasons"] or "[]")
            item["layer2_defer_reasons"] = json.loads(item["layer2_defer_reasons"] or "[]")
            decisions.append(item)
        return {"protocol_version": PROACTIVE_FEEDBACK_V1,
                "deliveries": deliveries, "candidates": candidates,
                "decisions": decisions, "sagas": sagas}
    finally:
        conn.close()


def clear_pending_and_history() -> dict:
    """Clear only proactive derived state; preserve chat, memory, relationship and LIFE rows."""
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        counts = {
            "deliveries": conn.execute("SELECT COUNT(*) FROM proactive_deliveries").fetchone()[0],
            "candidates": conn.execute("SELECT COUNT(*) FROM proactive_candidates").fetchone()[0],
            "episodes": conn.execute("SELECT COUNT(*) FROM contact_episodes").fetchone()[0],
        }
        conn.execute("UPDATE messages SET proactive_delivery_id=NULL WHERE proactive_delivery_id IS NOT NULL")
        # Delete only derived proactive state. Preference rows survive feedback deletion
        # through ON DELETE SET NULL so an explicit user preference is not forgotten.
        conn.execute("DELETE FROM proactive_feedback")
        conn.execute("DELETE FROM proactive_deliveries")
        conn.execute("DELETE FROM expression_plans")
        conn.execute("DELETE FROM proactive_intensity_plans")
        conn.execute("DELETE FROM proactive_decisions")
        conn.execute("DELETE FROM proactive_candidate_claims")
        conn.execute("DELETE FROM proactive_runtime_sagas")
        conn.execute("DELETE FROM proactive_runtime_sources")
        conn.execute("DELETE FROM proactive_candidates")
        conn.execute("DELETE FROM contact_episodes")
        conn.commit()
        return {**counts, "chat_preserved": True, "memory_preserved": True,
                "relationship_preserved": True, "life_preserved": True}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
