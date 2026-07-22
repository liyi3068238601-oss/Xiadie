"""Auditable, at-most-once local delivery for EAP.R4."""
from __future__ import annotations

import hashlib
import json
from typing import Optional

from .. import db
from . import decision, episodes, intensity, settings

LEASE_SECONDS = 30.0
CHANNELS = {0: "silent", 1: "live2d", 2: "bubble", 3: "chat", 4: "desktop_notification"}
TERMINAL = {"delivered", "failed", "cancelled", "suppressed", "expired"}


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: dict) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _public(row) -> dict:
    value = dict(row)
    value["payload"] = json.loads(value.pop("payload_json"))
    return value


def _event(conn, delivery_id, event_type, before, after, reason, now, metadata=None):
    conn.execute(
        "INSERT INTO proactive_delivery_events(id,delivery_id,event_type,from_status,to_status,"
        "reason_code,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (db.new_id(), delivery_id, event_type, before, after, reason, _json(metadata or {}), now),
    )


def _record_approach_locked(conn, episode_id: Optional[str], level: int, now: float) -> None:
    """Record one confirmed approach in the delivery transaction."""
    if not episode_id:
        return
    row = conn.execute(
        "SELECT approach_count,unanswered_pressure,status FROM contact_episodes WHERE id=?",
        (episode_id,),
    ).fetchone()
    if row is None:
        return
    count = int(row["approach_count"] or 0)
    intrusiveness = episodes.CHANNEL_INTRUSIVENESS.get(level, 1.0)
    repetition = episodes.REPETITION_FACTOR_BASE ** count
    pressure = min(5.0, float(row["unanswered_pressure"] or 0) + level * intrusiveness * repetition)
    status = "approached" if row["status"] in {"proposed", "waiting"} else row["status"]
    conn.execute(
        "UPDATE contact_episodes SET status=?,first_candidate_at=COALESCE(first_candidate_at,?),"
        "last_approach_at=?,approach_count=approach_count+1,unanswered_pressure=?,"
        "current_intensity=?,updated_at=? WHERE id=?",
        (status, now, now, pressure, level, now, episode_id),
    )


def _payload(level, result, plan):
    topic = (result.topic or "想来看看你").strip()[:240]
    if level == 1:
        return {"state": "remind", "action": plan.live2d_action or {
            "expression": "soft_smile", "motion": "lean_in"}}
    if level == 2:
        return {"state": "remind", "bubble_text": (plan.bubble_text or "（轻轻看向你）")[:240],
                "dismiss_after_ms": 5000}
    if level == 3:
        return {"content": topic}
    if level == 4:
        return {"title": "遐蝶", "body": topic}
    return {}


def _authorization(kind, source_revision, source_hash, now):
    policy = settings.effective_policy(now=now, candidate_kind=kind)
    values = policy.settings
    revision = int(values["proactive_settings_revision"])
    material = {"revision": revision, "local": values["proactive_local_delivery_enabled"],
                "desktop": values["proactive_desktop_notification_enabled"], "kind": kind,
                "blocked": list(policy.blocked_reasons), "source_revision": source_revision,
                "source_hash": source_hash}
    return policy, revision, _hash(material)


def enqueue_decision(decision_id: str, *, now: Optional[float] = None) -> dict:
    """Create the one immutable delivery record attached to a decision."""
    now = db.now() if now is None else now
    result = decision.get_decision(decision_id)
    plan = intensity.get_intensity_plan_by_decision(decision_id)
    if result is None or plan is None:
        raise ValueError("decision or intensity plan not found")
    if plan.level not in CHANNELS:
        raise ValueError("Level 5 and external proactive delivery are hard disabled")
    conn = db.connect()
    try:
        existing = conn.execute("SELECT * FROM proactive_deliveries WHERE decision_id=?",
                                (decision_id,)).fetchone()
        if existing:
            return _public(existing)
        source = conn.execute(
            "SELECT s.*,c.candidate_kind,c.episode_id FROM proactive_candidates c "
            "JOIN proactive_runtime_sources s ON s.id=c.runtime_source_id WHERE c.id=?",
            (result.candidate_id,),
        ).fetchone()
        if source is None:
            raise ValueError("runtime source not found")
    finally:
        conn.close()
    policy, revision, auth_hash = _authorization(
        source["candidate_kind"], source["source_revision"], source["source_hash"], now)
    payload = _payload(plan.level, result, plan)
    status, reason = "queued", None
    if plan.level == 0:
        status, reason = "suppressed", "decision_not_visible"
    elif policy.settings["proactive_local_delivery_enabled"] != "1":
        status, reason = "suppressed", "local_delivery_disabled"
    elif not intensity.is_level_authorized(plan.level, settings=policy.settings):
        status, reason = "suppressed", "channel_unauthorized"
    elif result.decision != decision.DecisionAction.SEND:
        status, reason = "suppressed", f"decision_{result.decision}"
    elif policy.blocked_reasons:
        status, reason = "suppressed", policy.blocked_reasons[0]
    delivery_id = db.new_id()
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR IGNORE INTO proactive_deliveries(id,decision_id,candidate_id,episode_id,"
            "session_id,level,channel,payload_json,payload_hash,authorization_revision,"
            "authorization_hash,source_revision,source_hash,status,error_code,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (delivery_id, decision_id, result.candidate_id, source["episode_id"], result.session_id,
             plan.level, CHANNELS[plan.level], _json(payload), _hash(payload), revision, auth_hash,
             source["source_revision"], source["source_hash"], status, reason, now, now),
        )
        row = conn.execute("SELECT * FROM proactive_deliveries WHERE decision_id=?",
                           (decision_id,)).fetchone()
        if row["id"] == delivery_id:
            _event(conn, delivery_id, "enqueued", None, status, reason, now)
        conn.commit()
        return _public(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def recover_stale(*, now: Optional[float] = None) -> int:
    """Retry pre-invocation claims; fail uncertain post-invocation work."""
    now = db.now() if now is None else now
    conn = db.connect()
    changed = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT * FROM proactive_deliveries WHERE status IN ('claimed','delivering') "
            "AND lease_expires_at IS NOT NULL AND lease_expires_at<=?", (now,)).fetchall()
        for row in rows:
            if row["status"] == "claimed":
                status, code = "queued", "claim_lease_expired"
                conn.execute("UPDATE proactive_deliveries SET status='queued',lease_owner=NULL,"
                             "lease_token=NULL,lease_expires_at=NULL,error_code=?,updated_at=? WHERE id=?",
                             (code, now, row["id"]))
            else:
                status, code = "failed", "delivery_confirmation_unknown"
                conn.execute("UPDATE proactive_deliveries SET status='failed',lease_expires_at=NULL,"
                             "error_code=?,updated_at=? WHERE id=?", (code, now, row["id"]))
                conn.execute("UPDATE proactive_delivery_attempts SET status='uncertain',error_code=?,"
                             "updated_at=? WHERE delivery_id=? AND status='delivering'",
                             (code, now, row["id"]))
            _event(conn, row["id"], "lease_recovered", row["status"], status, code, now)
            changed += 1
        expired = conn.execute(
            "SELECT d.id,d.status FROM proactive_deliveries d JOIN proactive_candidates c "
            "ON c.id=d.candidate_id WHERE d.status IN ('queued','claimed') "
            "AND c.expires_at IS NOT NULL AND c.expires_at<=?", (now,)).fetchall()
        for row in expired:
            conn.execute("UPDATE proactive_deliveries SET status='expired',lease_owner=NULL,"
                         "lease_token=NULL,lease_expires_at=NULL,error_code='candidate_expired',"
                         "updated_at=? WHERE id=?", (now, row["id"]))
            _event(conn, row["id"], "expired", row["status"], "expired", "candidate_expired", now)
            changed += 1
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def claim_next(consumer_id: str, *, now: Optional[float] = None):
    if not consumer_id or len(consumer_id) > 120:
        raise ValueError("invalid consumer_id")
    now = db.now() if now is None else now
    recover_stale(now=now)
    token = db.new_id()
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT d.* FROM proactive_deliveries d WHERE d.status='queued' AND EXISTS "
            "(SELECT 1 FROM proactive_runtime_sagas s WHERE s.candidate_id=d.candidate_id "
            "AND s.status='completed') ORDER BY d.created_at,d.id LIMIT 1").fetchone()
        if row is None:
            conn.commit()
            return None
        conn.execute("UPDATE proactive_deliveries SET status='claimed',lease_owner=?,lease_token=?,"
                     "lease_expires_at=?,error_code=NULL,updated_at=? WHERE id=?",
                     (consumer_id, token, now + LEASE_SECONDS, now, row["id"]))
        _event(conn, row["id"], "claimed", "queued", "claimed", None, now,
               {"consumer_id": consumer_id})
        conn.commit()
        return _public(conn.execute("SELECT * FROM proactive_deliveries WHERE id=?",
                                    (row["id"],)).fetchone())
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _preflight(delivery, now):
    from . import orchestrator
    source = orchestrator._source_for_candidate(delivery["candidate_id"])
    if source is None or not orchestrator._source_matches(source):
        return "source_invalidated"
    policy = settings.effective_policy(now=now, candidate_kind=delivery["candidate_kind"])
    if policy.settings["proactive_local_delivery_enabled"] != "1":
        return "local_delivery_disabled"
    if policy.blocked_reasons:
        return policy.blocked_reasons[0]
    if not intensity.is_level_authorized(delivery["level"], settings=policy.settings):
        return "channel_unauthorized"
    return None


def begin_delivery(delivery_id: str, consumer_id: str, lease_token: str,
                   *, now: Optional[float] = None) -> dict:
    """Commit the final gate and the sole invocation boundary."""
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT d.*,c.status candidate_status,c.expires_at candidate_expires_at,c.candidate_kind "
            "FROM proactive_deliveries d JOIN proactive_candidates c ON c.id=d.candidate_id "
            "WHERE d.id=?", (delivery_id,)).fetchone()
        if row is None:
            raise ValueError("delivery not found")
        if row["status"] in TERMINAL:
            conn.commit()
            return _public(row)
        if row["status"] != "claimed" or row["lease_owner"] != consumer_id or row["lease_token"] != lease_token:
            raise ValueError("delivery claim does not match")
        # The write reservation is acquired before this re-read.  User returns,
        # source edits, and setting writes therefore cannot cross the final gate.
        gate_error = _preflight(dict(row), now)
        revision_row = conn.execute("SELECT value FROM settings WHERE key='proactive_settings_revision'").fetchone()
        try:
            revision = int(revision_row["value"] if revision_row else 0)
        except (TypeError, ValueError):
            revision = -1
        error = gate_error
        if row["authorization_revision"] != revision:
            error = "authorization_changed"
        elif row["candidate_status"] != "approved":
            error = "candidate_not_approved"
        elif row["candidate_expires_at"] is not None and row["candidate_expires_at"] <= now:
            error = "candidate_expired"
        elif row["lease_expires_at"] is not None and row["lease_expires_at"] <= now:
            error = "claim_lease_expired"
        if error:
            status = "expired" if error in {"candidate_expired", "claim_lease_expired"} else "cancelled"
            conn.execute("UPDATE proactive_deliveries SET status=?,lease_expires_at=NULL,error_code=?,"
                         "updated_at=? WHERE id=?", (status, error, now, delivery_id))
            _event(conn, delivery_id, "final_gate_blocked", "claimed", status, error, now)
            conn.commit()
            return _public(conn.execute("SELECT * FROM proactive_deliveries WHERE id=?",
                                        (delivery_id,)).fetchone())
        if row["level"] == 3:
            message_id = db.new_id()
            conn.execute("INSERT OR IGNORE INTO messages(id,session_id,role,content,"
                         "proactive_delivery_id,created_at) VALUES(?,?,'assistant',?,?,?)",
                         (message_id, row["session_id"], json.loads(row["payload_json"])["content"],
                          delivery_id, now))
            conn.execute("INSERT INTO proactive_delivery_attempts(id,delivery_id,attempt_no,consumer_id,"
                         "lease_token,channel,status,claimed_at,invocation_started_at,confirmed_at,"
                         "created_at,updated_at) VALUES(?,?,1,?,?,?,'delivered',?,?,?,?,?)",
                         (db.new_id(), delivery_id, consumer_id, lease_token, row["channel"],
                          now, now, now, now, now))
            conn.execute("UPDATE proactive_deliveries SET status='delivered',attempt_count=1,"
                         "delivered_at=?,acknowledged_at=?,lease_expires_at=NULL,error_code=NULL,"
                         "updated_at=? WHERE id=?", (now, now, now, delivery_id))
            conn.execute("UPDATE proactive_candidates SET status='delivered',updated_at=? WHERE id=?",
                         (now, row["candidate_id"]))
            _record_approach_locked(conn, row["episode_id"], row["level"], now)
            conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, row["session_id"]))
            _event(conn, delivery_id, "delivered", "claimed", "delivered", None, now,
                   {"message_id": message_id})
            conn.commit()
            result = _public(conn.execute("SELECT * FROM proactive_deliveries WHERE id=?",
                                          (delivery_id,)).fetchone())
            result["message_id"] = message_id
            return result
        conn.execute("INSERT INTO proactive_delivery_attempts(id,delivery_id,attempt_no,consumer_id,"
                     "lease_token,channel,status,claimed_at,invocation_started_at,created_at,updated_at) "
                     "VALUES(?,?,1,?,?,?,'delivering',?,?,?,?)",
                     (db.new_id(), delivery_id, consumer_id, lease_token, row["channel"],
                      now, now, now, now))
        conn.execute("UPDATE proactive_deliveries SET status='delivering',attempt_count=1,"
                     "lease_expires_at=?,updated_at=? WHERE id=?", (now + LEASE_SECONDS, now, delivery_id))
        _event(conn, delivery_id, "invocation_started", "claimed", "delivering", None, now)
        conn.commit()
        return _public(conn.execute("SELECT * FROM proactive_deliveries WHERE id=?",
                                    (delivery_id,)).fetchone())
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def acknowledge_delivery(delivery_id: str, consumer_id: str, lease_token: str, *,
                         success: bool, error_code: Optional[str] = None,
                         now: Optional[float] = None) -> dict:
    now = db.now() if now is None else now
    safe_error = None if success else (error_code or "channel_delivery_failed")[:80]
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM proactive_deliveries WHERE id=?", (delivery_id,)).fetchone()
        if row is None:
            raise ValueError("delivery not found")
        if row["status"] in TERMINAL:
            conn.commit()
            return _public(row)
        if row["status"] != "delivering" or row["lease_owner"] != consumer_id or row["lease_token"] != lease_token:
            raise ValueError("delivery invocation does not match")
        status = "delivered" if success else "failed"
        conn.execute("UPDATE proactive_deliveries SET status=?,delivered_at=?,acknowledged_at=?,"
                     "lease_expires_at=NULL,error_code=?,updated_at=? WHERE id=?",
                     (status, now if success else None, now, safe_error, now, delivery_id))
        conn.execute("UPDATE proactive_delivery_attempts SET status=?,error_code=?,confirmed_at=?,"
                     "updated_at=? WHERE delivery_id=? AND attempt_no=1",
                     (status, safe_error, now, now, delivery_id))
        if success:
            conn.execute("UPDATE proactive_candidates SET status='delivered',updated_at=? WHERE id=?",
                         (now, row["candidate_id"]))
            _record_approach_locked(conn, row["episode_id"], row["level"], now)
        _event(conn, delivery_id, "acknowledged", "delivering", status, safe_error, now)
        conn.commit()
        return _public(conn.execute("SELECT * FROM proactive_deliveries WHERE id=?",
                                    (delivery_id,)).fetchone())
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_deliveries(limit: int = 100) -> list[dict]:
    conn = db.connect()
    try:
        result = []
        for row in conn.execute(
            "SELECT * FROM proactive_deliveries ORDER BY created_at DESC,id DESC LIMIT ?",
            (max(1, min(limit, 500)),),
        ).fetchall():
            value = dict(row)
            result.append({key: value[key] for key in (
                "id", "decision_id", "candidate_id", "episode_id", "session_id",
                "level", "channel", "status", "attempt_count", "error_code",
                "delivered_at", "acknowledged_at", "created_at", "updated_at",
            )})
        return result
    finally:
        conn.close()
