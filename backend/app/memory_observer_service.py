"""自主记忆观察器的后台调用、有限恢复与原子正式写入。"""
from __future__ import annotations

import asyncio
import json
import re
import time
from contextlib import suppress

from . import (
    companion_state, db, episode_consolidator, llm, memory,
    memory_observer as observer, memory_writer,
)
from .persona import OBSERVER_PERSONA_SUMMARY

MAX_INPUT_CHARS = 16_000
MAX_ATTEMPTS = 3
FIRST_RETRY_DELAY_SECONDS = 5 * 60
RUNNING_STALE_SECONDS = 2 * 60
WORKER_IDLE_SECONDS = 30
_USER_CONFIRM_PATTERNS = (
    re.compile(r"我决定"),
    re.compile(r"以后(?:就)?按(?:这个|那个|文档|资料|方案|建议|规范|计划)"),
    re.compile(r"(?:就)?照(?:这个|那个|文档|资料|方案|建议|规范|计划|你说的).{0,8}(?:做|办|执行|来|走)"),
    re.compile(r"(?:就)?按(?:这个|那个|文档|资料|方案|建议|规范|计划|你说的).{0,8}(?:做|办|执行|来|走)"),
    re.compile(r"(?:采用|接受|采纳).{0,12}(?:文档|资料|方案|建议|配置)"),
    re.compile(r"(?:好[，,、\s]*)?(?:就)?这么定了"),
)
LEGACY_FALLBACK_ERROR_CODES = frozenset({
    "model_call_failed", "observer_model_timeout", "invalid_json", "schema_invalid",
    "invalid_type", "output_too_large",
})

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
        enabled = db.get_setting("memory_enabled", db.DEFAULT_MEMORY_ENABLED) == "1"
        available = bool(provider and provider_id != "mock" and provider.get("base_url"))
        status = "queued" if enabled and available else "skipped"
        error_code = (
            None if status == "queued"
            else "memory_disabled" if not enabled else "observer_model_unavailable"
        )
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
            "(status IN ('queued','validated')"
            " OR (status='recovery_pending' AND next_attempt_at<=?))"
            " AND (attempt_count < max_attempts OR candidate_json IS NOT NULL)"
            " ORDER BY created_at LIMIT 1",
            (now,),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        has_stored_candidate = bool(row["candidate_json"])
        attempt = row["attempt_count"] if has_stored_candidate else row["attempt_count"] + 1
        conn.execute(
            "UPDATE memory_observer_runs SET status='running',attempt_count=?,"
            " last_attempt_at=?,updated_at=? WHERE id=?",
            (attempt, now, now, row["id"]),
        )
        conn.commit()
        result = dict(row)
        result.update({
            "attempt_count": attempt, "status": "running",
            "has_stored_candidate": has_stored_candidate,
        })
        return result
    finally:
        conn.close()


async def _process_claimed(row: dict) -> None:
    context = _load_context(row)
    if not context:
        _finish_without_retry(row, "observer_source_unavailable", "skipped")
        return
    knowledge_meta = context.get("knowledge_meta") or {}
    knowledge_used = bool(knowledge_meta.get("knowledge_used"))
    user_has_confirmed = knowledge_used and _detect_user_confirmation(context["user_text"])
    knowledge_guard = {
        "knowledge_used": knowledge_used,
        "user_confirmed": user_has_confirmed,
        "source_user_message_id": row["source_user_message_id"],
    }
    if row.get("has_stored_candidate"):
        try:
            candidate = json.loads(row["candidate_json"])
        except (TypeError, ValueError):
            _mark_failure(
                row, "stored_candidate_invalid", input_chars=row.get("input_chars", 0),
                completions=[], latency_ms=row.get("latency_ms") or 0,
                repair_attempted=bool(row.get("repair_attempted")),
            )
            return
        _apply_candidate(
            row, candidate, [], row.get("input_chars", 0), row.get("latency_ms") or 0,
            bool(row.get("repair_attempted")), stored_audit=True,
            knowledge_guard=knowledge_guard,
        )
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
            knowledge_meta=context.get("knowledge_meta"),
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
    _apply_candidate(
        row, candidate, completions, input_chars, _elapsed_ms(started), repair_attempted,
        knowledge_guard=knowledge_guard,
    )


def _apply_candidate(
    row: dict, candidate: dict, completions: list[dict], input_chars: int,
    latency_ms: int, repair_attempted: bool, stored_audit: bool = False,
    knowledge_guard: dict | None = None,
) -> None:
    """把净化候选、Fragment、实体、事件和 applied 状态放进同一事务。"""
    # 来源标签来自不可信模型。只有服务端确认“本轮用了知识 + 用户明确采纳 +
    # 当前用户消息是证据”时，才允许 user_confirmed_fact 进入正式写入层。
    guard = knowledge_guard or {}
    if guard.get("user_confirmed") and candidate.get("items"):
        for item in candidate["items"]:
            if item.get("observation_source") == "knowledge_reference":
                item["observation_source"] = "user_confirmed_fact"
    if stored_audit:
        prompt_tokens = row.get("prompt_tokens")
        completion_tokens = row.get("completion_tokens")
        output_chars = row.get("output_chars", 0)
    else:
        prompt_tokens, completion_tokens = _token_totals(completions)
        output_chars = sum(len(item.get("text") or "") for item in completions)
    conn = db.connect()
    apply_error: str | None = None
    fragment_ids: list[str] = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        fragment_ids = memory_writer.apply_observation_in_transaction(
            conn, run=row, candidate=candidate,
            knowledge_guard=guard,
            audit={
                "input_chars": input_chars, "output_chars": output_chars,
                "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                "latency_ms": latency_ms, "repair_attempted": repair_attempted,
            },
        )
        conn.commit()
    except memory_writer.MemoryApplyError as exc:
        conn.rollback()
        apply_error = exc.code
    except Exception:  # noqa: BLE001 - 任一步失败都回滚，再单独标记恢复
        conn.rollback()
        apply_error = "observer_apply_failed"
    finally:
        conn.close()
    if apply_error:
        _mark_failure(
            row, apply_error, input_chars=input_chars, completions=completions,
            latency_ms=latency_ms, repair_attempted=repair_attempted,
            audit_override=(
                {
                    "output_chars": output_chars, "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                }
                if stored_audit else None
            ),
        )
        return
    try:
        episode_consolidator.enqueue_for_fragments(
            fragment_ids, request_key=f"memory-observer:{row['id']}"
        )
    except Exception:  # noqa: BLE001 - Episode 调度不能改变已提交的观察结果
        pass


def _mark_failure(
    row: dict, error_code: str, *, input_chars: int, completions: list[dict],
    latency_ms: int, repair_attempted: bool, audit_override: dict | None = None,
) -> None:
    attempt = row["attempt_count"]
    exhausted = attempt >= row["max_attempts"]
    delay = FIRST_RETRY_DELAY_SECONDS * (2 ** max(0, attempt - 1))
    if audit_override:
        prompt_tokens = audit_override.get("prompt_tokens")
        completion_tokens = audit_override.get("completion_tokens")
        output_chars = audit_override.get("output_chars", 0)
    else:
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
    if exhausted and error_code in LEGACY_FALLBACK_ERROR_CODES:
        _maybe_create_legacy_fallback(row)


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
    if error_code == "observer_model_unavailable":
        _maybe_create_legacy_fallback(row)


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


def _maybe_create_legacy_fallback(row: dict) -> None:
    """真实观察路径不可用或耗尽时，才使用旧关键词候选作为保守兜底。"""
    if db.get_setting("memory_enabled", db.DEFAULT_MEMORY_ENABLED) != "1":
        return
    conn = db.connect()
    try:
        source = conn.execute(
            "SELECT content FROM messages WHERE id=? AND session_id=? AND role='user'",
            (row["source_user_message_id"], row["source_session_id"]),
        ).fetchone()
    finally:
        conn.close()
    if not source:
        return
    try:
        memory.maybe_create_candidate(
            source["content"], row["source_session_id"], row["source_user_message_id"]
        )
    except Exception:  # noqa: BLE001 - 兜底失败也不能破坏 worker
        return


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
        knowledge_meta = _load_knowledge_meta(conn, row["source_assistant_message_id"])
        return {
            "user_text": user["content"], "messages": messages,
            "knowledge_meta": knowledge_meta,
        }
    finally:
        conn.close()


def _load_knowledge_meta(conn, assistant_message_id: str) -> dict:
    """只返回本轮知识使用的元数据，不复制知识正文到观察器输入中。"""
    retrieval = conn.execute(
        "SELECT injected_count, trigger_reason FROM knowledge_chat_retrievals "
        "WHERE assistant_message_id = ?", (assistant_message_id,)
    ).fetchone()
    knowledge_used = bool(retrieval and retrieval["injected_count"] > 0)
    meta: dict = {
        "knowledge_used": knowledge_used,
        "trigger_reason": retrieval["trigger_reason"] if retrieval else None,
        "citations": [],
    }
    if knowledge_used:
        citation_rows = conn.execute(
            "SELECT citation_key, document_id, chunk_id, original_name,"
            " heading_path_json, content_sha256"
            " FROM knowledge_message_citations WHERE assistant_message_id = ?"
            " ORDER BY citation_key",
            (assistant_message_id,),
        ).fetchall()
        meta["citations"] = [
            {
                "citation_key": row["citation_key"],
                "document_id": row["document_id"],
                "chunk_id": row["chunk_id"],
                "original_name": row["original_name"],
                "heading_path": json.loads(row["heading_path_json"] or "[]"),
                "content_sha256": row["content_sha256"],
            }
            for row in citation_rows
        ]
    return meta


def _detect_user_confirmation(user_text: str) -> bool:
    """检查用户消息是否包含明确采纳知识资料为自身决定的表达。"""
    text = user_text.strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in _USER_CONFIRM_PATTERNS)


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


def get_run_result(run_id: str) -> dict | None:
    """供聊天页短轮询的最小结果，不暴露候选正文、理由或来源内容。"""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id,status,error_code,created_fragment_ids_json FROM memory_observer_runs"
            " WHERE id=?", (run_id,),
        ).fetchone()
        if not row:
            return None
        created_ids = json.loads(row["created_fragment_ids_json"] or "[]")
        remembered_count = 0
        if created_ids:
            placeholders = ",".join("?" for _ in created_ids)
            remembered_count = conn.execute(
                f"SELECT COUNT(*) FROM memory_fragments WHERE id IN ({placeholders})"
                " AND status='active' AND enabled=1",
                created_ids,
            ).fetchone()[0]
        return {
            "id": row["id"], "status": row["status"],
            "error_code": row["error_code"], "created_count": len(created_ids),
            "remembered_count": remembered_count,
        }
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
            "applied_fragment_ids": json.loads(
                row.get("applied_fragment_ids_json") or "[]"
            ),
            "created_fragment_ids": json.loads(
                row.get("created_fragment_ids_json") or "[]"
            ),
            "applied_at": row.get("applied_at"),
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
