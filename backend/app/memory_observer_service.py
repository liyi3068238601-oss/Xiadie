"""自主记忆观察器的后台调用与只读审计。

B.2 的终点只能是 validated：本模块不会写 memory_fragments、实体或记忆事件。
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress

from . import companion_state, db, llm, memory, memory_observer as observer
from .persona import OBSERVER_PERSONA_SUMMARY

MAX_INPUT_CHARS = 16_000
MAX_ATTEMPTS = 3
FIRST_RETRY_DELAY_SECONDS = 5 * 60
RUNNING_STALE_SECONDS = 2 * 60
WORKER_IDLE_SECONDS = 30

_worker_task: asyncio.Task | None = None
_wake_event: asyncio.Event | None = None


def get_model_config() -> dict:
    try:
        value = json.loads(db.get_setting("memory_observer_model", '{"mode":"current"}'))
    except (ValueError, TypeError):
        value = {"mode": "current"}
    if value.get("mode") != "dedicated":
        return {"mode": "current", "provider_id": None, "model": None}
    return {
        "mode": "dedicated",
        "provider_id": value.get("provider_id"),
        "model": value.get("model"),
    }


def set_model_config(mode: str, provider_id: str | None, model: str | None) -> dict:
    if mode not in ("current", "dedicated"):
        raise ValueError("记忆观察模型模式无效")
    if mode == "dedicated":
        provider = _load_provider(provider_id)
        if not provider or provider["id"] == "mock" or not provider.get("enabled") or not model:
            raise ValueError("请选择可用的真实供应商与记忆观察模型")
        models = json.loads(provider.get("models") or "[]")
        if model not in models:
            raise ValueError("记忆观察模型不在该供应商的模型列表中")
        value = {"mode": mode, "provider_id": provider_id, "model": model}
    else:
        value = {"mode": "current"}
    db.set_setting("memory_observer_model", json.dumps(value, ensure_ascii=False))
    return get_model_config()


def enqueue_turn(
    *,
    chat_provider: dict | None,
    chat_model: str,
    session_id: str,
    user_message_id: str,
    assistant_message_id: str,
) -> dict:
    """聊天热路径只插入一行；所有失败均被隔离，不向 SSE 抛出。"""
    try:
        provider, model = _resolve_model(chat_provider, chat_model)
        provider_id = provider.get("id") if provider else None
        available = bool(provider and provider_id != "mock" and provider.get("base_url"))
        status = "queued" if available else "skipped"
        error_code = None if available else "observer_model_unavailable"
        key = f"{observer.PROTOCOL_VERSION}:{assistant_message_id}"
        conn = db.connect()
        try:
            now = db.now()
            conn.execute(
                "INSERT OR IGNORE INTO memory_observer_runs("
                "id,idempotency_key,source_session_id,source_user_message_id,"
                "source_assistant_message_id,provider_id,model,status,error_code,attempt_count,"
                "max_attempts,next_attempt_at,protocol_version,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,0,?,?,?,?,?)",
                (
                    db.new_id(), key, session_id, user_message_id, assistant_message_id,
                    provider_id, model, status, error_code, MAX_ATTEMPTS,
                    now if status == "queued" else None, observer.PROTOCOL_VERSION, now, now,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM memory_observer_runs WHERE idempotency_key=?", (key,)
            ).fetchone()
        finally:
            conn.close()
        if status == "queued":
            wake_worker()
        return _public(dict(row))
    except Exception:  # noqa: BLE001 - 观察器不能破坏聊天完成事件
        return {"status": "unlogged_failure", "error_code": "observer_enqueue_failed"}


async def start_worker() -> None:
    global _worker_task, _wake_event
    if _worker_task and not _worker_task.done():
        return
    _wake_event = asyncio.Event()
    _worker_task = asyncio.create_task(_worker_loop(), name="xiadie-memory-observer")


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
        except Exception:  # noqa: BLE001 - 后台循环必须能自愈
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
    count = 0
    for _ in range(max(1, min(int(limit), 20))):
        row = _claim_next()
        if not row:
            break
        await _process_claimed(row)
        count += 1
    return count


def recover_stale_runs() -> int:
    conn = db.connect()
    try:
        now = db.now()
        cursor = conn.execute(
            "UPDATE memory_observer_runs SET status='recovery_pending',"
            " error_code='observer_interrupted',next_attempt_at=?,updated_at=?"
            " WHERE status='running' AND updated_at < ?",
            (now, now, now - RUNNING_STALE_SECONDS),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def _claim_next() -> dict | None:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = db.now()
        row = conn.execute(
            "SELECT * FROM memory_observer_runs WHERE "
            "(status='queued' OR (status='recovery_pending' AND next_attempt_at<=?))"
            " AND attempt_count < max_attempts ORDER BY created_at LIMIT 1",
            (now,),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        attempt = row["attempt_count"] + 1
        conn.execute(
            "UPDATE memory_observer_runs SET status='running',attempt_count=?,"
            " last_attempt_at=?,updated_at=? WHERE id=?",
            (attempt, now, now, row["id"]),
        )
        conn.commit()
        result = dict(row)
        result.update({"attempt_count": attempt, "status": "running"})
        return result
    finally:
        conn.close()


async def _process_claimed(row: dict) -> None:
    context = _load_context(row)
    if not context:
        _finish_without_retry(row, "observer_source_unavailable", "skipped")
        return
    provider = _load_provider(row.get("provider_id"))
    if not provider or provider["id"] == "mock" or not provider.get("enabled") or not provider.get("base_url"):
        _finish_without_retry(row, "observer_model_unavailable", "skipped")
        return

    try:
        state = companion_state.get_state(persist_advance=False)
        cluster = (state.get("derived") or {}).get("cluster")
        related = memory.search_memories(context["user_text"], limit=8)
        prompt = observer.build_messages(
            messages=context["messages"], persona_summary=OBSERVER_PERSONA_SUMMARY,
            emotion_cluster=cluster, related_memories=related,
        )
    except Exception:  # noqa: BLE001 - 上下文读取失败也必须离开 running
        _mark_failure(
            row, "observer_context_failed", input_chars=0, completions=[], latency_ms=0,
            repair_attempted=bool(row.get("repair_attempted")),
        )
        return
    input_chars = sum(len(item.get("content", "")) for item in prompt)
    if input_chars > MAX_INPUT_CHARS:
        _finish_without_retry(row, "observer_input_too_large", "skipped", input_chars=input_chars)
        return

    started = time.perf_counter()
    completions: list[dict] = []
    repair_attempted = bool(row.get("repair_attempted"))
    try:
        completion = await llm.complete_json(provider, row["model"], prompt)
        completions.append(completion)
        try:
            candidate = observer.parse_and_validate(
                completion["text"], messages=context["messages"]
            )
        except observer.MemoryObserverValidationError as exc:
            if exc.code not in ("invalid_json", "schema_invalid") or repair_attempted:
                raise
            repair_attempted = True
            _record_repair_attempt(row["id"])
            repair_prompt = observer.build_repair_messages(completion["text"])
            repaired = await llm.complete_json(provider, row["model"], repair_prompt)
            completions.append(repaired)
            candidate = observer.parse_and_validate(
                repaired["text"], messages=context["messages"]
            )
            candidate["warnings"].append({"code": "model_json_repair_used"})
    except observer.MemoryObserverValidationError as exc:
        _mark_failure(
            row, exc.code, input_chars=input_chars, completions=completions,
            latency_ms=_elapsed_ms(started), repair_attempted=repair_attempted,
        )
        return
    except llm.LLMError as exc:
        error_code = getattr(exc, "code", None) or "model_call_failed"
        _mark_failure(
            row, error_code, input_chars=input_chars, completions=completions,
            latency_ms=_elapsed_ms(started), repair_attempted=repair_attempted,
        )
        return
    except Exception:  # noqa: BLE001
        _mark_failure(
            row, "observer_internal_error", input_chars=input_chars, completions=completions,
            latency_ms=_elapsed_ms(started), repair_attempted=repair_attempted,
        )
        return
    _store_validated(
        row, candidate, completions, input_chars, _elapsed_ms(started), repair_attempted
    )


def _store_validated(
    row: dict, candidate: dict, completions: list[dict], input_chars: int,
    latency_ms: int, repair_attempted: bool,
) -> None:
    """原子保存净化候选和审计指标；刻意没有任何 Fragment 写入。"""
    prompt_tokens, completion_tokens = _token_totals(completions)
    output_chars = sum(len(item.get("text") or "") for item in completions)
    conn = db.connect()
    audit_failed = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT status FROM memory_observer_runs WHERE id=?", (row["id"],)
        ).fetchone()
        if not current or current["status"] != "running":
            conn.rollback()
            return
        conn.execute(
            "UPDATE memory_observer_runs SET status='validated',candidate_json=?,warnings_json=?,"
            " error_code=NULL,next_attempt_at=NULL,input_chars=?,output_chars=?,prompt_tokens=?,"
            " completion_tokens=?,latency_ms=?,repair_attempted=?,updated_at=? WHERE id=?",
            (
                json.dumps(candidate, ensure_ascii=False),
                json.dumps(candidate.get("warnings") or [], ensure_ascii=False),
                input_chars, output_chars, prompt_tokens, completion_tokens, latency_ms,
                1 if repair_attempted else 0, db.now(), row["id"],
            ),
        )
        conn.commit()
    except Exception:  # noqa: BLE001 - 审计写入失败转恢复态，不遗留 running
        conn.rollback()
        audit_failed = True
    finally:
        conn.close()
    if audit_failed:
        _mark_failure(
            row, "observer_audit_failed", input_chars=input_chars, completions=completions,
            latency_ms=latency_ms, repair_attempted=repair_attempted,
        )


def _mark_failure(
    row: dict, error_code: str, *, input_chars: int, completions: list[dict],
    latency_ms: int, repair_attempted: bool,
) -> None:
    attempt = row["attempt_count"]
    exhausted = attempt >= row["max_attempts"]
    delay = FIRST_RETRY_DELAY_SECONDS * (2 ** max(0, attempt - 1))
    prompt_tokens, completion_tokens = _token_totals(completions)
    output_chars = sum(len(item.get("text") or "") for item in completions)
    conn = db.connect()
    try:
        now = db.now()
        conn.execute(
            "UPDATE memory_observer_runs SET status=?,error_code=?,candidate_json=NULL,"
            " warnings_json='[]',next_attempt_at=?,input_chars=?,output_chars=?,prompt_tokens=?,"
            " completion_tokens=?,latency_ms=?,repair_attempted=?,updated_at=? WHERE id=?",
            (
                "exhausted" if exhausted else "recovery_pending", error_code,
                None if exhausted else now + delay, input_chars, output_chars, prompt_tokens,
                completion_tokens, latency_ms, 1 if repair_attempted else 0, now, row["id"],
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _finish_without_retry(
    row: dict, error_code: str, status: str, *, input_chars: int = 0
) -> None:
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_observer_runs SET status=?,error_code=?,next_attempt_at=NULL,"
            " input_chars=?,updated_at=? WHERE id=?",
            (status, error_code, input_chars, db.now(), row["id"]),
        )
        conn.commit()
    finally:
        conn.close()


def _record_repair_attempt(run_id: str) -> None:
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_observer_runs SET repair_attempted=1,updated_at=? WHERE id=?",
            (db.now(), run_id),
        )
        conn.commit()
    finally:
        conn.close()


def _load_context(row: dict) -> dict | None:
    conn = db.connect()
    try:
        source = conn.execute(
            "SELECT created_at FROM messages WHERE id=? AND session_id=? AND role='assistant'",
            (row["source_assistant_message_id"], row["source_session_id"]),
        ).fetchone()
        user = conn.execute(
            "SELECT content FROM messages WHERE id=? AND session_id=? AND role='user'",
            (row["source_user_message_id"], row["source_session_id"]),
        ).fetchone()
        if not source or not user:
            return None
        rows = conn.execute(
            "SELECT id,role,content FROM messages WHERE session_id=? AND created_at<=?"
            " AND role IN ('user','assistant') ORDER BY created_at DESC,rowid DESC LIMIT 8",
            (row["source_session_id"], source["created_at"]),
        ).fetchall()
        messages = [dict(item) for item in reversed(rows)]
        if not any(item["id"] == row["source_assistant_message_id"] for item in messages):
            return None
        return {"user_text": user["content"], "messages": messages}
    finally:
        conn.close()


def _resolve_model(chat_provider: dict | None, chat_model: str) -> tuple[dict | None, str]:
    config = get_model_config()
    if config["mode"] == "dedicated":
        return _load_provider(config["provider_id"]), config["model"] or ""
    return chat_provider, chat_model


def _load_provider(provider_id: str | None) -> dict | None:
    if not provider_id:
        return None
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_runs(limit: int = 50) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM memory_observer_runs ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
        return [_public(dict(row), include_candidate=True) for row in rows]
    finally:
        conn.close()


def _public(row: dict, *, include_candidate: bool = False) -> dict:
    result = {
        "id": row["id"], "status": row["status"], "error_code": row.get("error_code"),
        "attempt_count": row["attempt_count"], "max_attempts": row["max_attempts"],
        "next_attempt_at": row.get("next_attempt_at"),
        "protocol_version": row["protocol_version"],
    }
    if include_candidate:
        result.update({
            "source_session_id": row["source_session_id"],
            "source_user_message_id": row["source_user_message_id"],
            "source_assistant_message_id": row["source_assistant_message_id"],
            "provider_id": row.get("provider_id"), "model": row["model"],
            "candidate": json.loads(row["candidate_json"]) if row.get("candidate_json") else None,
            "warnings": json.loads(row.get("warnings_json") or "[]"),
            "input_chars": row["input_chars"], "output_chars": row["output_chars"],
            "prompt_tokens": row.get("prompt_tokens"),
            "completion_tokens": row.get("completion_tokens"),
            "latency_ms": row.get("latency_ms"),
            "repair_attempted": bool(row.get("repair_attempted")),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        })
    return result


def _token_totals(completions: list[dict]) -> tuple[int | None, int | None]:
    def total(key: str) -> int | None:
        values = [item.get(key) for item in completions]
        known = [value for value in values if isinstance(value, int) and not isinstance(value, bool)]
        return sum(known) if known else None
    return total("prompt_tokens"), total("completion_tokens")


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
