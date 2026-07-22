"""Recoverable, shadow-only proactive runtime orchestration (EAP.R3).

The orchestrator owns candidate creation and evaluation.  It never delivers a
user-visible action; R4 is the only stage allowed to add a delivery ledger.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
from typing import Optional

from .. import db
from . import candidates, decision, episodes, expression, intensity, life_adapter, presence, settings
from .run_ledger import compute_source_hash

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "proactive-orchestrator-v1"
LEASE_SECONDS = 60.0
RETRY_SECONDS = 30.0
POLL_SECONDS = 30.0

SOURCE_EXPECTED_RETURN = "expected_return"
SOURCE_EMOTIONAL_CARE = "emotional_care"
SOURCE_EPISODE_MILESTONE = "episode_milestone"
SOURCE_SAGA_MILESTONE = "saga_milestone"
SOURCE_CASUAL_GREETING = "casual_greeting"
SOURCE_LIFE_SEED = "life_seed"
MILESTONE_CURSOR_KEY = "proactive_milestone_cursor"
MILESTONE_CURSOR_BACKUP_KEY = "proactive_milestone_cursor_backup"

_worker_task: asyncio.Task | None = None
_wake_event: asyncio.Event | None = None
_stop_event: asyncio.Event | None = None


def _hash(value: dict) -> str:
    return compute_source_hash([value])


def enqueue_source(
    *, session_id: str, source_kind: str, source_ref_id: str,
    source_revision: str, source_hash: str, payload: dict,
    due_at: float, expires_at: float, now: Optional[float] = None,
) -> dict:
    if len(source_hash) != 64:
        raise ValueError("source_hash must be a 64-character digest")
    if expires_at <= due_at:
        raise ValueError("expires_at must be later than due_at")
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO proactive_runtime_sources("
            "id,session_id,source_kind,source_ref_id,source_revision,source_hash,"
            "payload_json,due_at,expires_at,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,'queued',?,?)",
            (db.new_id(), session_id, source_kind, source_ref_id, source_revision,
             source_hash, json.dumps(payload, ensure_ascii=False, sort_keys=True),
             due_at, expires_at, now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM proactive_runtime_sources WHERE source_kind=? "
            "AND source_ref_id=? AND source_revision=?",
            (source_kind, source_ref_id, source_revision),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def enqueue_after_chat(
    *, session_id: str, user_message_id: str, assistant_message_id: str,
    now: Optional[float] = None,
) -> list[dict]:
    """Schedule bounded expected-return and greeting sources after a real turn."""
    now = db.now() if now is None else now
    queued: list[dict] = []
    current = presence.get_current_presence(session_id)
    if current and current.open_thread and current.source_message_id == user_message_id:
        snapshot = _presence_snapshot(current.id)
        if snapshot:
            due = current.expected_return_at or now + 15 * 60
            queued.append(enqueue_source(
                session_id=session_id, source_kind=SOURCE_EXPECTED_RETURN,
                source_ref_id=current.id, source_revision=snapshot[0], source_hash=snapshot[1],
                payload={
                    "topic": current.open_thread_topic or "unfinished conversation",
                    "open_thread": current.open_thread_topic,
                    "origin_type": episodes.OriginType.EXPECTED_RETURN,
                    "candidate_kind": candidates.CandidateKind.RETURN_FOLLOWUP,
                },
                due_at=due, expires_at=due + 24 * 3600, now=now,
            ))

    snapshot = _message_snapshot(assistant_message_id)
    if snapshot:
        due = now + 8 * 3600
        queued.append(enqueue_source(
            session_id=session_id, source_kind=SOURCE_CASUAL_GREETING,
            source_ref_id=assistant_message_id, source_revision=snapshot[0],
            source_hash=snapshot[1],
            payload={
                "topic": "light check-in", "open_thread": None,
                "origin_type": episodes.OriginType.LIFE_SHARE,
                "candidate_kind": candidates.CandidateKind.CASUAL_GREETING,
            },
            due_at=due, expires_at=due + 16 * 3600, now=now,
        ))
    wake_worker()
    return queued


def enqueue_emotional_care(
    *, run_id: str, session_id: str, state: str, confidence: float,
    now: Optional[float] = None,
) -> Optional[dict]:
    if state not in {"low", "frustrated", "overwhelmed"} or confidence < 0.6:
        return None
    now = db.now() if now is None else now
    snapshot = _cognition_snapshot(run_id)
    if not snapshot:
        return None
    record = enqueue_source(
        session_id=session_id, source_kind=SOURCE_EMOTIONAL_CARE,
        source_ref_id=run_id, source_revision=snapshot[0], source_hash=snapshot[1],
        payload={
            "topic": "emotional care", "open_thread": None,
            "origin_type": episodes.OriginType.EMOTIONAL_CARE,
            "candidate_kind": candidates.CandidateKind.EMOTIONAL_CARE,
        },
        due_at=now + 15 * 60, expires_at=now + 24 * 3600, now=now,
    )
    wake_worker()
    return record


def enqueue_memory_milestone(
    *, session_id: str, source_type: str, source_id: str,
    due_at: Optional[float] = None, now: Optional[float] = None,
) -> Optional[dict]:
    """Event adapter for Episode/Saga owners; it does not invent domain rows."""
    if source_type not in {SOURCE_EPISODE_MILESTONE, SOURCE_SAGA_MILESTONE}:
        raise ValueError("source_type must be episode_milestone or saga_milestone")
    now = db.now() if now is None else now
    snapshot = _memory_snapshot(source_type, source_id)
    if not snapshot:
        return None
    due = now if due_at is None else due_at
    record = enqueue_source(
        session_id=session_id, source_kind=source_type, source_ref_id=source_id,
        source_revision=snapshot[0], source_hash=snapshot[1],
        payload={
            "topic": snapshot[2], "open_thread": None,
            "origin_type": episodes.OriginType.MILESTONE,
            "candidate_kind": candidates.CandidateKind.MILESTONE_FOLLOWUP,
        },
        due_at=due, expires_at=due + 7 * 24 * 3600, now=now,
    )
    wake_worker()
    return record


def enqueue_life_seed_fixture(
    *, session_id: str, seed_id: str, due_at: Optional[float] = None,
    now: Optional[float] = None,
) -> Optional[dict]:
    """R3 fixture/adapter: a LIFE seed may create only an EAP source."""
    now = db.now() if now is None else now
    snapshot = _life_snapshot(seed_id)
    if not snapshot:
        return None
    due = now if due_at is None else due_at
    record = enqueue_source(
        session_id=session_id, source_kind=SOURCE_LIFE_SEED, source_ref_id=seed_id,
        source_revision=snapshot[0], source_hash=snapshot[1],
        payload={
            "topic": snapshot[2], "open_thread": None,
            "origin_type": snapshot[3],
            "candidate_kind": candidates.CandidateKind.LIFE_SHARE,
        },
        due_at=due, expires_at=due + 7 * 24 * 3600, now=now,
    )
    wake_worker()
    return record


def handle_user_message(session_id: str, *, now: Optional[float] = None) -> int:
    """A real user return closes the same active ContactEpisode before evaluation."""
    now = db.now() if now is None else now
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE proactive_runtime_sources SET status='skipped',result_code='user_returned',"
            "lease_owner=NULL,lease_expires_at=NULL,updated_at=? "
            "WHERE session_id=? AND status IN ('queued','claimed')", (now, session_id),
        )
        placeholders = ",".join("?" * len(episodes.ACTIVE_STATUSES))
        active = conn.execute(
            f"SELECT * FROM contact_episodes WHERE session_id=? "
            f"AND status IN ({placeholders}) ORDER BY updated_at DESC LIMIT 1",
            (session_id, *episodes.ACTIVE_STATUSES),
        ).fetchone()
        if not active:
            conn.commit()
            return 0
        conn.execute(
            "UPDATE contact_episodes SET unanswered_pressure=?,status='responded',"
            "outcome='replied',updated_at=? WHERE id=? AND status IN "
            "('proposed','waiting','approached','deferred','quiet_waiting')",
            (max(0.0, active["unanswered_pressure"] * 0.4), now, active["id"]),
        )
        conn.execute(
            "UPDATE proactive_candidates SET status='abandoned',updated_at=? "
            "WHERE episode_id=? AND status IN ('pending','evaluating','deferred','approved')",
            (now, active["id"]),
        )
        conn.execute(
            "UPDATE proactive_runtime_sources SET status='skipped',result_code='user_returned',"
            "lease_owner=NULL,lease_expires_at=NULL,updated_at=? "
            "WHERE candidate_id IN (SELECT id FROM proactive_candidates WHERE episode_id=?) "
            "AND status IN ('queued','claimed')",
            (now, active["id"]),
        )
        conn.execute(
            "UPDATE proactive_runtime_sagas SET status='skipped',error_code='user_returned',"
            "updated_at=? WHERE candidate_id IN "
            "(SELECT id FROM proactive_candidates WHERE episode_id=?) "
            "AND status IN ('claimed','recovery_pending')", (now, active["id"]),
        )
        deliveries = conn.execute(
            "SELECT id,status FROM proactive_deliveries WHERE candidate_id IN "
            "(SELECT id FROM proactive_candidates WHERE episode_id=?) "
            "AND status IN ('queued','claimed')", (active["id"],),
        ).fetchall()
        for delivery_row in deliveries:
            conn.execute(
                "UPDATE proactive_deliveries SET status='cancelled',lease_owner=NULL,lease_token=NULL,"
                "lease_expires_at=NULL,error_code='user_returned',updated_at=? WHERE id=?",
                (now, delivery_row["id"]),
            )
            conn.execute(
                "INSERT INTO proactive_delivery_events(id,delivery_id,event_type,from_status,to_status,"
                "reason_code,metadata_json,created_at) VALUES(?,?,?,?,'cancelled','user_returned','{}',?)",
                (db.new_id(), delivery_row["id"], "user_returned", delivery_row["status"], now),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return 1


def process_due(*, now: Optional[float] = None, limit: int = 20, worker_id: Optional[str] = None) -> int:
    """Process local work; database-busy is conservative, programming errors propagate."""
    now = db.now() if now is None else now
    worker_id = worker_id or f"orchestrator-{db.new_id()}"
    discover_memory_milestones(now=now)
    _recover(now)
    processed = 0
    try:
        for _ in range(max(1, limit)):
            source = _claim_source(worker_id, now)
            if not source:
                break
            _materialize_source(source, now)
            processed += 1
        for _ in range(max(1, limit)):
            candidate = _claim_candidate(worker_id, now)
            if not candidate:
                break
            _evaluate_candidate(candidate, worker_id, now)
            processed += 1
    except sqlite3.OperationalError:
        logger.warning("proactive_orchestrator_database_busy", exc_info=True)
    return processed


def discover_memory_milestones(*, now: Optional[float] = None) -> int:
    """Advance a durable local cursor over newly completed Episode/Saga milestones."""
    now = db.now() if now is None else now
    raw_cursor = db.get_setting(MILESTONE_CURSOR_KEY, "")
    if not raw_cursor:
        # Installation watermark: do not retroactively contact users about old memories.
        encoded = _encode_milestone_cursor(now)
        _save_milestone_cursor(encoded, encoded)
        return 0
    last_valid_raw = raw_cursor
    try:
        cursor = _decode_milestone_cursor(raw_cursor)
    except ValueError:
        logger.warning("proactive_milestone_cursor_invalid")
        backup = db.get_setting(MILESTONE_CURSOR_BACKUP_KEY, "")
        try:
            cursor = _decode_milestone_cursor(backup)
            last_valid_raw = backup
        except ValueError:
            logger.error("proactive_milestone_cursor_backup_invalid")
            return 0
    conn = db.connect()
    try:
        episode_rows = conn.execute(
            "SELECT DISTINCT e.id,f.source_session_id FROM memory_episodes e "
            "JOIN json_each(e.source_fragment_ids_json) j "
            "JOIN memory_fragments f ON f.id=j.value "
            "WHERE e.status='completed' AND e.significance>=7 "
            "AND e.updated_at>? AND e.updated_at<=? AND f.source_session_id IS NOT NULL",
            (cursor, now),
        ).fetchall()
        saga_rows = conn.execute(
            "SELECT DISTINCT s.id,f.source_session_id FROM memory_sagas s "
            "JOIN memory_saga_episodes se ON se.saga_id=s.id AND se.removed_at IS NULL "
            "JOIN memory_episodes e ON e.id=se.episode_id "
            "JOIN json_each(e.source_fragment_ids_json) j "
            "JOIN memory_fragments f ON f.id=j.value "
            "WHERE s.status='completed' AND s.significance>=7 "
            "AND s.updated_at>? AND s.updated_at<=? AND f.source_session_id IS NOT NULL",
            (cursor, now),
        ).fetchall()
    finally:
        conn.close()
    queued = 0
    for row in episode_rows:
        queued += int(enqueue_memory_milestone(
            session_id=row["source_session_id"], source_type=SOURCE_EPISODE_MILESTONE,
            source_id=row["id"], now=now,
        ) is not None)
    for row in saga_rows:
        queued += int(enqueue_memory_milestone(
            session_id=row["source_session_id"], source_type=SOURCE_SAGA_MILESTONE,
            source_id=row["id"], now=now,
        ) is not None)
    encoded = _encode_milestone_cursor(now)
    _save_milestone_cursor(encoded, last_valid_raw)
    return queued


def _encode_milestone_cursor(at: float) -> str:
    value = f"{float(at):.6f}"
    checksum = hashlib.sha256(f"v1:{value}".encode("utf-8")).hexdigest()
    return json.dumps({"version": 1, "at": value, "checksum": checksum}, sort_keys=True)


def _decode_milestone_cursor(raw: str) -> float:
    if not raw:
        raise ValueError("empty milestone cursor")
    try:
        return float(raw)  # migration compatibility with the R3 numeric cursor
    except ValueError:
        pass
    try:
        payload = json.loads(raw)
        if payload.get("version") != 1:
            raise ValueError("unsupported milestone cursor version")
        value = str(payload["at"])
        expected = hashlib.sha256(f"v1:{value}".encode("utf-8")).hexdigest()
        if payload.get("checksum") != expected:
            raise ValueError("milestone cursor checksum mismatch")
        return float(value)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("malformed milestone cursor") from exc


def _save_milestone_cursor(primary: str, backup: str) -> None:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (MILESTONE_CURSOR_BACKUP_KEY, backup),
        )
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (MILESTONE_CURSOR_KEY, primary),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _claim_source(worker_id: str, now: float):
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM proactive_runtime_sources WHERE status='queued' "
            "AND due_at<=? AND expires_at>? ORDER BY due_at,id LIMIT 1", (now, now),
        ).fetchone()
        if not row:
            conn.commit()
            return None
        changed = conn.execute(
            "UPDATE proactive_runtime_sources SET status='claimed',lease_owner=?,"
            "lease_expires_at=?,updated_at=? WHERE id=? AND status='queued'",
            (worker_id, now + LEASE_SECONDS, now, row["id"]),
        ).rowcount
        claimed = conn.execute(
            "SELECT * FROM proactive_runtime_sources WHERE id=?", (row["id"],),
        ).fetchone() if changed else None
        conn.commit()
        return dict(claimed) if claimed else None
    finally:
        conn.close()


def _materialize_source(source: dict, now: float) -> None:
    snapshot = _current_snapshot(source["source_kind"], source["source_ref_id"])
    if not snapshot or snapshot[0] != source["source_revision"] or snapshot[1] != source["source_hash"]:
        _finish_source(source["id"], "skipped", "source_invalidated", now)
        return
    try:
        payload = json.loads(source["payload_json"])
        for key in ("topic", "origin_type", "candidate_kind"):
            if not isinstance(payload[key], str) or not payload[key].strip():
                raise ValueError(f"invalid payload field: {key}")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        logger.error("proactive_source_payload_invalid source_id=%s", source["id"], exc_info=True)
        _finish_source(source["id"], "skipped", "source_payload_invalid", now)
        return
    try:
        refs = {
            "runtime_source_id": source["id"], "source_kind": source["source_kind"],
            "source_ref_id": source["source_ref_id"],
            "source_revision": source["source_revision"],
        }
        episode, candidate = _existing_materialization(source["id"])
        if episode is None:
            episode = episodes.create_episode(
                source["session_id"], topic=payload["topic"],
                origin_type=payload["origin_type"], open_thread=payload.get("open_thread"),
                source_refs=refs, expires_at=source["expires_at"], now=now,
            )
        if candidate is None:
            candidate = candidates.create_candidate(
                source["session_id"], candidate_kind=payload["candidate_kind"],
                topic=payload["topic"], episode_id=episode.id,
                source_refs=refs, open_thread=payload.get("open_thread"),
                source_messages=[{"source_hash": source["source_hash"]}],
                expires_at=source["expires_at"], now=now,
            )
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE proactive_candidates SET source_hash=?,source_revision=?,due_at=?,"
                "runtime_source_id=? WHERE id=?",
                (source["source_hash"], source["source_revision"], now, source["id"], candidate.id),
            )
            changed = conn.execute(
                "UPDATE proactive_runtime_sources SET status='processed',candidate_id=?,"
                "result_code='candidate_created',lease_owner=NULL,lease_expires_at=NULL,updated_at=? "
                "WHERE id=? AND status='claimed'",
                (candidate.id, now, source["id"]),
            ).rowcount
            if changed != 1:
                conn.execute(
                    "UPDATE proactive_candidates SET status='abandoned',updated_at=? WHERE id=?",
                    (now, candidate.id),
                )
                conn.execute(
                    "UPDATE contact_episodes SET status='cancelled',outcome='cancelled',updated_at=? "
                    "WHERE id=? AND status NOT IN ('closed','expired','cancelled','blocked')",
                    (now, episode.id),
                )
            conn.commit()
        finally:
            conn.close()
        if changed != 1:
            return
        if (source["source_kind"] == SOURCE_LIFE_SEED
                and life_adapter.get_seed(source["source_ref_id"]).consumed_at is None):
            life_adapter.consume_seed(source["source_ref_id"], episode_id=episode.id,
                                      candidate_id=candidate.id, now=now)
    except sqlite3.OperationalError:  # transient database failure: retry idempotently
        logger.exception("proactive_source_materialization_failed source_id=%s", source["id"])
        _finish_source(source["id"], "queued", "materialization_failed", now,
                       due_at=now + RETRY_SECONDS)
    except ValueError:
        logger.exception("proactive_source_domain_invalid source_id=%s", source["id"])
        _finish_source(source["id"], "skipped", "source_domain_invalid", now)


def _claim_candidate(worker_id: str, now: float):
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM proactive_candidates c WHERE c.status IN ('pending','deferred') "
            "AND COALESCE(c.due_at,c.created_at)<=? AND (c.expires_at IS NULL OR c.expires_at>?) "
            "AND NOT EXISTS (SELECT 1 FROM proactive_candidate_claims l "
            "WHERE l.candidate_id=c.id AND l.lease_expires_at>?) "
            "AND NOT EXISTS (SELECT 1 FROM proactive_runtime_sagas s "
            "WHERE s.candidate_id=c.id AND s.status IN ('completed','skipped')) "
            "ORDER BY COALESCE(c.due_at,c.created_at),c.id LIMIT 1", (now, now, now),
        ).fetchone()
        if not row:
            conn.commit()
            return None
        conn.execute("DELETE FROM proactive_candidate_claims WHERE candidate_id=?", (row["id"],))
        conn.execute(
            "INSERT INTO proactive_candidate_claims(candidate_id,source_revision,lease_owner,"
            "lease_expires_at,claimed_at,updated_at) VALUES(?,?,?,?,?,?)",
            (row["id"], row["source_revision"], worker_id, now + LEASE_SECONDS, now, now),
        )
        conn.execute(
            "UPDATE proactive_candidates SET status='evaluating',updated_at=? "
            "WHERE id=? AND status IN ('pending','deferred')", (now, row["id"]),
        )
        conn.execute(
            "INSERT OR IGNORE INTO proactive_runtime_sagas(candidate_id,source_revision,status,"
            "attempt_count,max_attempts,created_at,updated_at) VALUES(?,?,'claimed',0,3,?,?)",
            (row["id"], row["source_revision"], now, now),
        )
        conn.commit()
        return candidates.get_candidate(row["id"])
    finally:
        conn.close()


def _evaluate_candidate(candidate, worker_id: str, now: float) -> None:
    source = _source_for_candidate(candidate.id)
    before = _gate_snapshot(candidate, now)
    if not source or not _source_matches(source):
        _skip_candidate(candidate.id, worker_id, "source_invalidated", before, now)
        return
    try:
        # There is no model call in R3.  This second snapshot occupies the post-advice
        # hard-gate boundary and makes later LLM advice insertion safe.
        after = _gate_snapshot(candidate, now)
        if not _source_matches(source):
            _skip_candidate(candidate.id, worker_id, "source_invalidated_after_advice", before, now)
            return
        local_delivery = settings.load_settings()["proactive_local_delivery_enabled"] == "1"
        result = decision.decide_candidate(candidate.id, now=now, is_shadow=not local_delivery)
        plan = intensity.get_intensity_plan_by_decision(result.id)
        if plan is None:
            level = intensity.select_minimum_sufficient_level(
                approach_value=result.approach_value,
                expression_act=result.expression_act,
                llm_advice_intensity=result.intensity,
            )
            plan = intensity.create_intensity_plan(
                result.id, result.session_id, level=level,
                expression_act=result.expression_act,
                approach_value=result.approach_value,
                reason=("local delivery plan" if local_delivery else "shadow orchestration; no delivery"),
                now=now,
            )
        expression_plan = expression.get_expression_plan_by_decision(result.id)
        if expression_plan is None:
            expression_plan = expression.create_expression_plan(
                result.session_id, decision_id=result.id, intensity_plan_id=plan.id,
                expression_act=result.expression_act, now=now,
            )
        if local_delivery:
            # Enqueue before the saga becomes claimable.  A crash is recoverable via
            # the decision-unique ledger row, while consumers require saga=completed.
            from . import delivery
            delivery.enqueue_decision(result.id, now=now)
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE proactive_runtime_sagas SET decision_id=?,intensity_plan_id=?,"
                "expression_plan_id=?,status='completed',attempt_count=attempt_count+1,"
                "gate_before_json=?,gate_after_json=?,error_code=NULL,updated_at=? "
                "WHERE candidate_id=?",
                (result.id, plan.id, expression_plan.id,
                 json.dumps(before, sort_keys=True), json.dumps(after, sort_keys=True),
                 now, candidate.id),
            )
            conn.execute(
                "DELETE FROM proactive_candidate_claims WHERE candidate_id=? AND lease_owner=?",
                (candidate.id, worker_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("proactive_candidate_evaluation_failed candidate_id=%s", candidate.id)
        _recover_candidate(candidate.id, worker_id, now)


def _gate_snapshot(candidate, now: float) -> dict:
    hard = decision.check_layer1_hard_boundary(candidate, now=now)
    deferred = decision.check_layer2_defer_conditions(candidate, now=now)
    return {
        "blocked": hard.blocked, "block_reasons": list(hard.reasons),
        "deferred": deferred.deferred, "defer_reasons": list(deferred.reasons),
    }


def _recover(now: float) -> None:
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE proactive_runtime_sources SET status='queued',lease_owner=NULL,"
            "lease_expires_at=NULL,updated_at=? WHERE status='claimed' AND lease_expires_at<=?",
            (now, now),
        )
        rows = conn.execute(
            "SELECT candidate_id FROM proactive_candidate_claims WHERE lease_expires_at<=?", (now,),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE proactive_candidates SET status='pending',due_at=?,updated_at=? "
                "WHERE id=? AND status='evaluating'", (now, now, row["candidate_id"]),
            )
        conn.execute("DELETE FROM proactive_candidate_claims WHERE lease_expires_at<=?", (now,))
        conn.execute(
            "UPDATE proactive_runtime_sources SET status='expired',result_code='source_expired',"
            "updated_at=? WHERE status IN ('queued','claimed') AND expires_at<=?", (now, now),
        )
        conn.execute(
            "UPDATE proactive_candidates SET status='abandoned',updated_at=? "
            "WHERE status IN ('pending','evaluating','deferred','approved') "
            "AND expires_at IS NOT NULL AND expires_at<=?", (now, now),
        )
        conn.commit()
    finally:
        conn.close()
    episodes.expire_episodes(now=now)


def _recover_candidate(candidate_id: str, worker_id: str, now: float) -> None:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT s.attempt_count,s.max_attempts,c.status candidate_status "
            "FROM proactive_runtime_sagas s JOIN proactive_candidates c ON c.id=s.candidate_id "
            "WHERE s.candidate_id=?",
            (candidate_id,),
        ).fetchone()
        attempts = (row["attempt_count"] if row else 0) + 1
        exhausted = row is not None and attempts >= row["max_attempts"]
        externally_stopped = row is not None and row["candidate_status"] in {
            candidates.CandidateStatus.ABANDONED, candidates.CandidateStatus.SUPPRESSED,
        }
        conn.execute(
            "UPDATE proactive_runtime_sagas SET status=?,attempt_count=?,next_attempt_at=?,"
            "error_code='orchestration_failed',updated_at=? WHERE candidate_id=?",
            ("skipped" if exhausted or externally_stopped else "recovery_pending", attempts,
             None if exhausted or externally_stopped else now + RETRY_SECONDS, now, candidate_id),
        )
        conn.execute(
            "UPDATE proactive_candidates SET status=?,due_at=?,updated_at=? "
            "WHERE id=? AND status='evaluating'",
            ("suppressed" if exhausted else "pending", now + RETRY_SECONDS, now, candidate_id),
        )
        conn.execute(
            "DELETE FROM proactive_candidate_claims WHERE candidate_id=? AND lease_owner=?",
            (candidate_id, worker_id),
        )
        conn.commit()
    finally:
        conn.close()


def _skip_candidate(candidate_id: str, worker_id: str, code: str, before: dict, now: float) -> None:
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE proactive_candidates SET status='abandoned',updated_at=? WHERE id=?", (now, candidate_id),
        )
        conn.execute(
            "UPDATE proactive_runtime_sagas SET status='skipped',attempt_count=attempt_count+1,"
            "error_code=?,gate_before_json=?,updated_at=? WHERE candidate_id=?",
            (code, json.dumps(before, sort_keys=True), now, candidate_id),
        )
        conn.execute(
            "DELETE FROM proactive_candidate_claims WHERE candidate_id=? AND lease_owner=?",
            (candidate_id, worker_id),
        )
        conn.commit()
    finally:
        conn.close()


def _finish_source(source_id: str, status: str, code: str, now: float, due_at: float | None = None) -> None:
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE proactive_runtime_sources SET status=?,result_code=?,due_at=COALESCE(?,due_at),"
            "lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE id=?",
            (status, code, due_at, now, source_id),
        )
        conn.commit()
    finally:
        conn.close()


def _source_for_candidate(candidate_id: str):
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT s.* FROM proactive_runtime_sources s JOIN proactive_candidates c "
            "ON c.runtime_source_id=s.id WHERE c.id=?", (candidate_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _existing_materialization(source_id: str):
    conn = db.connect()
    try:
        episode_row = conn.execute(
            "SELECT id FROM contact_episodes "
            "WHERE json_extract(source_refs,'$.runtime_source_id')=? "
            "ORDER BY created_at LIMIT 1", (source_id,),
        ).fetchone()
        candidate_row = conn.execute(
            "SELECT id FROM proactive_candidates WHERE runtime_source_id=? "
            "OR json_extract(source_refs,'$.runtime_source_id')=? "
            "ORDER BY created_at LIMIT 1", (source_id, source_id),
        ).fetchone()
    finally:
        conn.close()
    return (
        episodes.get_episode(episode_row["id"]) if episode_row else None,
        candidates.get_candidate(candidate_row["id"]) if candidate_row else None,
    )


def _source_matches(source: dict) -> bool:
    current = _current_snapshot(source["source_kind"], source["source_ref_id"])
    return bool(current and current[0] == source["source_revision"] and current[1] == source["source_hash"])


def _current_snapshot(kind: str, ref_id: str):
    if kind == SOURCE_EXPECTED_RETURN:
        return _presence_snapshot(ref_id)
    if kind == SOURCE_EMOTIONAL_CARE:
        return _cognition_snapshot(ref_id)
    if kind in {SOURCE_EPISODE_MILESTONE, SOURCE_SAGA_MILESTONE}:
        return _memory_snapshot(kind, ref_id)
    if kind == SOURCE_CASUAL_GREETING:
        return _message_snapshot(ref_id)
    if kind == SOURCE_LIFE_SEED:
        return _life_snapshot(ref_id)
    return None


def _message_snapshot(message_id: str):
    conn = db.connect()
    try:
        row = conn.execute("SELECT id,role,content FROM messages WHERE id=?", (message_id,)).fetchone()
        if not row:
            return None
        value = {"id": row["id"], "role": row["role"], "content": row["content"]}
        digest = _hash(value)
        return digest, digest, row["content"]
    finally:
        conn.close()


def _presence_snapshot(presence_id: str):
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id,session_id,user_status,expected_return_at,open_thread,"
            "open_thread_topic,source_message_id,is_active FROM conversation_presence WHERE id=?",
            (presence_id,),
        ).fetchone()
        if not row or not row["is_active"] or not row["open_thread"]:
            return None
        value = dict(row)
        digest = _hash(value)
        return digest, digest, row["open_thread_topic"] or "unfinished conversation"
    finally:
        conn.close()


def _cognition_snapshot(run_id: str):
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT source_user_message_id,source_assistant_message_id,source_revision,source_hash "
            "FROM companion_cognition_results WHERE run_id=?", (run_id,),
        ).fetchone()
        if not row or not row["source_user_message_id"] or not row["source_assistant_message_id"]:
            return None
        messages = conn.execute(
            "SELECT id,role,content FROM messages WHERE id IN (?,?) ORDER BY role DESC",
            (row["source_user_message_id"], row["source_assistant_message_id"]),
        ).fetchall()
        if len(messages) != 2:
            return None
        digest = compute_source_hash([dict(item) for item in messages])
        if digest != row["source_hash"]:
            return None
        return row["source_revision"], row["source_hash"], "emotional care"
    finally:
        conn.close()


def _memory_snapshot(kind: str, source_id: str):
    table = "memory_episodes" if kind == SOURCE_EPISODE_MILESTONE else "memory_sagas"
    conn = db.connect()
    try:
        row = conn.execute(
            f"SELECT id,title,status,significance,source_hash,updated_at FROM {table} WHERE id=?",
            (source_id,),
        ).fetchone()
        if not row or row["status"] == "tombstone" or row["significance"] < 7:
            return None
        value = dict(row)
        digest = _hash(value)
        return digest, digest, row["title"]
    finally:
        conn.close()


def _life_snapshot(seed_id: str):
    seed = life_adapter.get_seed(seed_id)
    if not seed or seed.rejected_at is not None:
        return None
    value = {
        "id": seed.id, "source_event_type": seed.source_event_type,
        "source_event_id": seed.source_event_id,
        "summary": seed.source_event_summary, "topic": seed.topic,
        "origin_type": seed.origin_type, "source_revision": seed.source_revision,
        "source_hash": seed.source_hash,
    }
    digest = _hash(value)
    return seed.source_revision or digest, digest, seed.topic, seed.origin_type


def list_runtime_sources(limit: int = 100) -> list[dict]:
    conn = db.connect()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM proactive_runtime_sources ORDER BY created_at DESC LIMIT ?", (limit,),
        ).fetchall()]
    finally:
        conn.close()


def list_runtime_sagas(limit: int = 100) -> list[dict]:
    conn = db.connect()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM proactive_runtime_sagas ORDER BY created_at DESC LIMIT ?", (limit,),
        ).fetchall()]
    finally:
        conn.close()


async def start_worker() -> None:
    global _worker_task, _wake_event, _stop_event
    if _worker_task and not _worker_task.done():
        return
    _wake_event = asyncio.Event()
    _stop_event = asyncio.Event()
    _worker_task = asyncio.create_task(_worker_loop(), name="proactive-orchestrator")
    wake_worker()


async def stop_worker() -> None:
    global _worker_task
    if not _worker_task:
        return
    assert _stop_event is not None
    _stop_event.set()
    wake_worker()
    await _worker_task
    _worker_task = None


def wake_worker() -> None:
    if _wake_event is not None:
        _wake_event.set()


async def _worker_loop() -> None:
    assert _wake_event is not None and _stop_event is not None
    while not _stop_event.is_set():
        try:
            process_due()
        except Exception:
            logger.exception("proactive_orchestrator_cycle_failed")
        _wake_event.clear()
        try:
            await asyncio.wait_for(_wake_event.wait(), timeout=POLL_SECONDS)
        except asyncio.TimeoutError:
            pass
