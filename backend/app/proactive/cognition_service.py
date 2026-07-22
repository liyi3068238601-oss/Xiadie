"""Recoverable background worker for grounded user affect and relationship meaning."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress

from .. import db, llm
from ..affect import observer_service
from ..persona import OBSERVER_PERSONA_SUMMARY
from . import cognition, relationship
from .run_ledger import (
    RunStatus, compute_source_hash, create_or_get_run, get_run, make_idempotency_key,
    transition_run,
)

logger = logging.getLogger(__name__)

TASK_KIND = "companion_cognition"
MAX_INPUT_CHARS = 12_000
MAX_ATTEMPTS = 3
FIRST_RETRY_DELAY_SECONDS = 5 * 60
RUNNING_STALE_SECONDS = 2 * 60
WORKER_IDLE_SECONDS = 30

_worker_task: asyncio.Task | None = None
_wake_event: asyncio.Event | None = None


def enqueue_turn(
    *, chat_provider: dict | None, chat_model: str, session_id: str,
    user_message_id: str, assistant_message_id: str,
) -> dict:
    try:
        context = _load_messages(session_id, user_message_id, assistant_message_id)
        if not context:
            return {"status": "unlogged_failure", "error_code": "cognition_source_unavailable"}
        provider, model = observer_service._resolve_model(chat_provider, chat_model)
        provider_id = provider.get("id") if provider else None
        source_hash = _context_hash(user_message_id, assistant_message_id, context)
        revision = source_hash
        key = make_idempotency_key(cognition.PROTOCOL_VERSION, assistant_message_id, revision)
        run, _ = create_or_get_run(
            task_kind=TASK_KIND, protocol_version=cognition.PROTOCOL_VERSION,
            source_type="conversation_turn",
            source_id=f"{user_message_id}|{assistant_message_id}",
            source_revision=revision, source_hash=source_hash, idempotency_key=key,
            max_attempts=MAX_ATTEMPTS, provider_id=provider_id, model_id=model,
        )
        wake_worker()
        return _public(run)
    except Exception:  # noqa: BLE001 - cognition must never break a completed chat
        return {"status": "unlogged_failure", "error_code": "cognition_enqueue_failed"}


async def start_worker() -> None:
    global _worker_task, _wake_event
    if _worker_task and not _worker_task.done():
        return
    _wake_event = asyncio.Event()
    _worker_task = asyncio.create_task(_worker_loop(), name="xiadie-companion-cognition")


async def stop_worker() -> None:
    global _worker_task, _wake_event
    task = _worker_task
    _worker_task = None
    _wake_event = None
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def wake_worker() -> None:
    if _wake_event:
        _wake_event.set()


async def _worker_loop() -> None:
    while True:
        if _wake_event:
            _wake_event.clear()
        try:
            processed = await process_due(limit=5)
        except Exception:  # noqa: BLE001
            processed = 0
        if processed:
            continue
        try:
            if _wake_event:
                await asyncio.wait_for(_wake_event.wait(), timeout=WORKER_IDLE_SECONDS)
            else:
                await asyncio.sleep(WORKER_IDLE_SECONDS)
        except asyncio.TimeoutError:
            pass


async def process_due(limit: int = 5) -> int:
    recover_stale_runs()
    relationship.revoke_invalidated_suggestions()
    count = 0
    for _ in range(max(1, min(int(limit), 20))):
        run = _claim_next()
        if not run:
            break
        await _process_claimed(run)
        count += 1
    return count


def recover_stale_runs() -> int:
    conn = db.connect()
    try:
        now = db.now()
        rows = conn.execute(
            "SELECT id FROM decision_runs WHERE task_kind=? AND status='running' AND updated_at<?",
            (TASK_KIND, now - RUNNING_STALE_SECONDS),
        ).fetchall()
    finally:
        conn.close()
    count = 0
    for row in rows:
        try:
            transition_run(
                row["id"], RunStatus.RECOVERY_PENDING,
                error_code="cognition_interrupted", next_attempt_at=now, now=now,
            )
            count += 1
        except ValueError:
            pass
    return count


def _claim_next():
    conn = db.connect()
    try:
        now = db.now()
        rows = conn.execute(
            "SELECT id FROM decision_runs WHERE task_kind=? AND "
            "(status='queued' OR (status='recovery_pending' AND next_attempt_at<=?)) "
            "AND attempt_count<max_attempts ORDER BY created_at LIMIT 5",
            (TASK_KIND, now),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        try:
            return transition_run(row["id"], RunStatus.RUNNING, now=now)
        except ValueError:
            continue
    return None


async def _process_claimed(run) -> None:
    ids = run.source_id.split("|", 1)
    if len(ids) != 2:
        transition_run(run.id, RunStatus.SKIPPED, error_code="cognition_source_invalid")
        return
    user_id, assistant_id = ids
    context = _load_messages_by_ids(user_id, assistant_id)
    if not context:
        transition_run(run.id, RunStatus.SKIPPED, error_code="cognition_source_unavailable")
        return
    current_hash = _context_hash(user_id, assistant_id, context)
    if current_hash != run.source_hash or current_hash != run.source_revision:
        transition_run(run.id, RunStatus.SKIPPED, error_code="cognition_source_changed")
        return
    provider = observer_service._load_provider(run.provider_id)
    available = bool(
        provider and provider["id"] != "mock" and provider.get("enabled")
        and provider.get("base_url") and run.model_id
    )
    messages = cognition.build_messages(
        user_text=context["user_text"], assistant_text=context["assistant_text"],
        persona_summary=OBSERVER_PERSONA_SUMMARY,
    )
    input_chars = sum(len(item["content"]) for item in messages)
    if not available or input_chars > MAX_INPUT_CHARS:
        error = "cognition_model_unavailable" if not available else "cognition_input_too_large"
        _apply_result(run, context, cognition.unknown_fallback(), error_code=error,
                      input_chars=input_chars)
        return
    try:
        completion = await llm.complete_json(
            provider, run.model_id, messages, max_tokens=llm.JSON_COMPLETION_MAX_TOKENS,
        )
        result = cognition.parse_and_validate(
            completion["text"], user_text=context["user_text"],
            assistant_text=context["assistant_text"],
        )
    except Exception as exc:  # validation/model failures share bounded retry policy
        code = getattr(exc, "code", "cognition_model_failed")
        _mark_failure(run, code, context=context, input_chars=input_chars)
        return
    _apply_result(
        run, context, result, input_chars=input_chars,
        latency_ms=completion.get("latency_ms"),
        input_tokens=completion.get("prompt_tokens"),
        output_tokens=completion.get("completion_tokens"),
    )


def _mark_failure(run, error_code: str, *, context: dict, input_chars: int) -> None:
    if run.attempt_count >= run.max_attempts:
        _apply_result(
            run, context, cognition.unknown_fallback(), error_code=error_code,
            input_chars=input_chars, warnings=["conservative_fallback_after_exhaustion"],
        )
        return
    delay = FIRST_RETRY_DELAY_SECONDS * (2 ** max(0, run.attempt_count - 1))
    transition_run(
        run.id, RunStatus.RECOVERY_PENDING, error_code=error_code,
        next_attempt_at=db.now() + delay,
    )


def _apply_result(
    run, context: dict, result: dict, *, error_code: str | None = None,
    input_chars: int = 0, latency_ms: int | None = None,
    input_tokens: int | None = None, output_tokens: int | None = None,
    warnings: list[str] | None = None,
) -> None:
    meaning = result["relationship_meaning"]
    suggestion = relationship.process_relationship_delta(
        context["session_id"], context["user_id"], meaning["label"],
        source_assistant_message_id=context["assistant_id"],
        evidence=meaning["evidence"], reason=meaning["reason"],
        confidence=meaning["confidence"],
    )
    if suggestion is None:
        suggestion = relationship.get_suggestion_by_source_message(
            context["user_id"], run.source_revision,
        )
    if suggestion and suggestion.status == "proposed":
        suggestion = relationship.apply_suggestion(suggestion.id)
    conn = db.connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO companion_cognition_results("
            "run_id,session_id,source_user_message_id,source_assistant_message_id,"
            "source_revision,source_hash,user_affect_json,relationship_label,"
            "relationship_suggestion_id,protocol_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (run.id, context["session_id"], context["user_id"], context["assistant_id"],
             run.source_revision, run.source_hash,
             json.dumps(result["user_affect"], ensure_ascii=False), meaning["label"],
             suggestion.id if suggestion else None, cognition.PROTOCOL_VERSION, db.now()),
        )
        conn.commit()
    finally:
        conn.close()
    try:
        # Local import avoids a module cycle: cognition is a source producer,
        # while the orchestrator owns every candidate and decision.
        from . import orchestrator
        affect = result["user_affect"]
        orchestrator.enqueue_emotional_care(
            run_id=run.id, session_id=context["session_id"],
            state=affect["state"], confidence=affect["confidence"],
        )
    except Exception:  # noqa: BLE001 - cognition application remains authoritative
        logger.exception("cognition_proactive_source_enqueue_failed run_id=%s", run.id)
    warning_values = list(warnings or [])
    if error_code:
        warning_values.append(error_code)
    transition_run(
        run.id, RunStatus.APPLIED, error_code=error_code, latency_ms=latency_ms,
        input_tokens=input_tokens, output_tokens=output_tokens, warnings=warning_values,
    )


def _load_messages(session_id: str, user_id: str, assistant_id: str) -> dict | None:
    context = _load_messages_by_ids(user_id, assistant_id)
    if not context or context["session_id"] != session_id:
        return None
    return context


def _load_messages_by_ids(user_id: str, assistant_id: str) -> dict | None:
    conn = db.connect()
    try:
        user = conn.execute(
            "SELECT id,session_id,role,content FROM messages WHERE id=?", (user_id,),
        ).fetchone()
        assistant = conn.execute(
            "SELECT id,session_id,role,content FROM messages WHERE id=?", (assistant_id,),
        ).fetchone()
        if (
            not user or not assistant or user["role"] != "user"
            or assistant["role"] != "assistant" or user["session_id"] != assistant["session_id"]
        ):
            return None
        return {
            "session_id": user["session_id"], "user_id": user["id"],
            "assistant_id": assistant["id"], "user_text": user["content"],
            "assistant_text": assistant["content"],
        }
    finally:
        conn.close()


def _context_hash(user_id: str, assistant_id: str, context: dict) -> str:
    return compute_source_hash([
        {"id": user_id, "role": "user", "content": context["user_text"]},
        {"id": assistant_id, "role": "assistant", "content": context["assistant_text"]},
    ])


def list_runs(limit: int = 50) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM decision_runs WHERE task_kind=? ORDER BY created_at DESC LIMIT ?",
            (TASK_KIND, max(1, min(int(limit), 200))),
        ).fetchall()
    finally:
        conn.close()
    return [_public(get_run(row["id"]), include_result=True) for row in rows]


def _public(run, *, include_result: bool = False) -> dict:
    result = {
        "id": run.id, "status": run.status, "error_code": run.error_code,
        "attempt_count": run.attempt_count, "max_attempts": run.max_attempts,
        "next_attempt_at": run.next_attempt_at, "protocol_version": run.protocol_version,
    }
    if include_result:
        source_ids = run.source_id.split("|", 1)
        result.update({
            "source_session_id": None,
            "source_user_message_id": source_ids[0] if len(source_ids) == 2 else None,
            "source_assistant_message_id": source_ids[1] if len(source_ids) == 2 else None,
            "source_revision": run.source_revision,
            "provider_id": run.provider_id,
            "model": run.model_id,
        })
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT session_id,user_affect_json,relationship_label,relationship_suggestion_id "
                "FROM companion_cognition_results WHERE run_id=?", (run.id,),
            ).fetchone()
        finally:
            conn.close()
        result["result"] = ({
            "user_affect": json.loads(row["user_affect_json"]),
            "relationship_label": row["relationship_label"],
            "relationship_suggestion_id": row["relationship_suggestion_id"],
        } if row else None)
        if row:
            result["source_session_id"] = row["session_id"]
        elif len(source_ids) == 2:
            context = _load_messages_by_ids(source_ids[0], source_ids[1])
            result["source_session_id"] = context["session_id"] if context else None
    return result
