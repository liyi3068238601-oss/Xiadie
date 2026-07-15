"""旁观观察器调用编排：幂等、审计、失败隔离；阶段 2.2 不应用候选。"""
from __future__ import annotations

import json

from .. import db, llm
from . import observer

MAX_INPUT_CHARS = 20_000
MAX_ATTEMPTS = 3
FIRST_RETRY_DELAY_SECONDS = 5 * 60
RUNNING_STALE_SECONDS = 2 * 60


async def observe_turn(
    *,
    provider: dict | None,
    model: str,
    session_id: str,
    user_message_id: str,
    assistant_message_id: str,
    user_text: str,
    assistant_text: str,
    current_state: dict,
    persona_summary: str,
) -> dict:
    """安全入口：任何观察错误都转换为状态结果，绝不向聊天链抛出。"""
    try:
        recover_stale_runs()
        return await _observe_turn(
            provider=provider,
            model=model,
            session_id=session_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            user_text=user_text,
            assistant_text=assistant_text,
            current_state=current_state,
            persona_summary=persona_summary,
        )
    except Exception:  # noqa: BLE001 - 失败域必须与聊天完全隔离
        return {"status": "unlogged_failure", "error_code": "observer_internal_error"}


async def _observe_turn(**context) -> dict:
    idempotency_key = f"{observer.PROTOCOL_VERSION}:{context['assistant_message_id']}"
    existing = _get_by_key(idempotency_key)
    if existing:
        return _public(existing)

    provider = context["provider"]
    provider_id = provider.get("id") if provider else None
    if provider is None or provider_id == "mock" or not provider.get("base_url"):
        row = _insert_initial(
            context,
            idempotency_key=idempotency_key,
            provider_id=provider_id,
            status="skipped",
            error_code="observer_model_unavailable",
            input_chars=0,
        )
        return _public(row)

    messages = observer.build_messages(
        user_text=context["user_text"],
        assistant_text=context["assistant_text"],
        current_state=context["current_state"],
        persona_summary=context["persona_summary"],
    )
    input_chars = sum(len(item.get("content", "")) for item in messages)
    if input_chars > MAX_INPUT_CHARS:
        row = _insert_initial(
            context,
            idempotency_key=idempotency_key,
            provider_id=provider_id,
            status="skipped",
            error_code="observer_input_too_large",
            input_chars=input_chars,
        )
        return _public(row)

    row = _insert_initial(
        context,
        idempotency_key=idempotency_key,
        provider_id=provider_id,
        status="running",
        error_code=None,
        input_chars=input_chars,
    )
    claimed = row.pop("_claimed", False)
    if not claimed or row["status"] != "running":
        return _public(row)

    try:
        completion = await llm.complete_json(
            provider,
            context["model"],
            messages,
            max_tokens=llm.JSON_COMPLETION_MAX_TOKENS,
        )
        candidate = observer.parse_and_validate(
            completion["text"],
            user_text=context["user_text"],
            assistant_text=context["assistant_text"],
        )
    except observer.ObserverValidationError as exc:
        return _mark_recovery(
            row["id"], exc.code,
            output_chars=len(completion["text"]),
            prompt_tokens=completion.get("prompt_tokens"),
            completion_tokens=completion.get("completion_tokens"),
        )
    except llm.LLMError:
        return _mark_recovery(row["id"], "model_call_failed")
    except Exception:  # noqa: BLE001
        return _mark_recovery(row["id"], "observer_internal_error")

    conn = db.connect()
    try:
        now = db.now()
        conn.execute(
            "UPDATE affect_observer_runs SET status='candidate', candidate_json=?,"
            " warnings_json=?, error_code=NULL, next_attempt_at=NULL, output_chars=?,"
            " prompt_tokens=?, completion_tokens=?, updated_at=? WHERE id=? AND status='running'",
            (
                json.dumps(candidate, ensure_ascii=False),
                json.dumps(candidate["warnings"], ensure_ascii=False),
                len(completion["text"]), completion.get("prompt_tokens"),
                completion.get("completion_tokens"), now, row["id"],
            ),
        )
        conn.commit()
        return _public(_get(conn, row["id"]))
    finally:
        conn.close()


def list_runs(limit: int = 50) -> list[dict]:
    recover_stale_runs()
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM affect_observer_runs ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
        return [_public(dict(row), include_candidate=True) for row in rows]
    finally:
        conn.close()


def recover_stale_runs() -> int:
    """把进程中断遗留的 running 任务转为可重试状态，不执行网络调用。"""
    conn = db.connect()
    try:
        now = db.now()
        cursor = conn.execute(
            "UPDATE affect_observer_runs SET status='recovery_pending',"
            " error_code='observer_interrupted', next_attempt_at=?, updated_at=?"
            " WHERE status='running' AND updated_at < ?",
            (now + FIRST_RETRY_DELAY_SECONDS, now, now - RUNNING_STALE_SECONDS),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def _insert_initial(
    context: dict,
    *,
    idempotency_key: str,
    provider_id: str | None,
    status: str,
    error_code: str | None,
    input_chars: int,
) -> dict:
    conn = db.connect()
    try:
        now = db.now()
        new_id = db.new_id()
        cursor = conn.execute(
            "INSERT OR IGNORE INTO affect_observer_runs("
            "id,idempotency_key,source_session_id,source_user_message_id,"
            "source_assistant_message_id,provider_id,model,status,error_code,attempt_count,"
            "max_attempts,input_chars,protocol_version,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,1,?,?,?,?,?)",
            (
                new_id, idempotency_key, context["session_id"],
                context["user_message_id"], context["assistant_message_id"], provider_id,
                context["model"], status, error_code, MAX_ATTEMPTS, input_chars,
                observer.PROTOCOL_VERSION, now, now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM affect_observer_runs WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        result = dict(row)
        result["_claimed"] = cursor.rowcount == 1 and result["id"] == new_id
        return result
    finally:
        conn.close()


def _mark_recovery(
    run_id: str,
    error_code: str,
    *,
    output_chars: int = 0,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> dict:
    conn = db.connect()
    try:
        now = db.now()
        conn.execute(
            "UPDATE affect_observer_runs SET status='recovery_pending', error_code=?,"
            " candidate_json=NULL, warnings_json='[]', next_attempt_at=?, output_chars=?,"
            " prompt_tokens=?, completion_tokens=?, updated_at=?"
            " WHERE id=?",
            (
                error_code, now + FIRST_RETRY_DELAY_SECONDS, output_chars,
                prompt_tokens, completion_tokens, now, run_id,
            ),
        )
        conn.commit()
        return _public(_get(conn, run_id))
    finally:
        conn.close()


def _get_by_key(key: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM affect_observer_runs WHERE idempotency_key=?", (key,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _get(conn, run_id: str) -> dict:
    return dict(conn.execute("SELECT * FROM affect_observer_runs WHERE id=?", (run_id,)).fetchone())


def _public(row: dict, *, include_candidate: bool = False) -> dict:
    result = {
        "id": row["id"],
        "status": row["status"],
        "error_code": row.get("error_code"),
        "warnings": json.loads(row.get("warnings_json") or "[]"),
        "attempt_count": row["attempt_count"],
        "max_attempts": row["max_attempts"],
        "next_attempt_at": row.get("next_attempt_at"),
        "protocol_version": row["protocol_version"],
    }
    if include_candidate:
        result.update({
            "source_session_id": row["source_session_id"],
            "source_user_message_id": row["source_user_message_id"],
            "source_assistant_message_id": row["source_assistant_message_id"],
            "provider_id": row.get("provider_id"),
            "model": row["model"],
            "candidate": json.loads(row["candidate_json"]) if row.get("candidate_json") else None,
            "input_chars": row["input_chars"],
            "output_chars": row["output_chars"],
            "prompt_tokens": row.get("prompt_tokens"),
            "completion_tokens": row.get("completion_tokens"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })
    return result
