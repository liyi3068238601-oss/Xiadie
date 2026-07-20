"""CTX.3 受约束会话摘要后台服务；失败永不阻塞聊天。"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress

from . import conversation_summaries as ledger, db, llm, secret_store
from . import conversation_summary_protocol as protocol

WORKER_IDLE_SECONDS = 30
FULL_REBUILD_INTERVAL = 5
_worker_task: asyncio.Task | None = None
_wake_event: asyncio.Event | None = None


def get_model_config() -> dict:
    try:
        raw = json.loads(db.get_setting(
            "conversation_summary_model", '{"mode":"current","allow_remote_history":false}',
        ))
    except (TypeError, ValueError):
        raw = {"mode": "current", "allow_remote_history": False}
    result = {
        "mode": "dedicated" if raw.get("mode") == "dedicated" else "current",
        "provider_id": raw.get("provider_id") if raw.get("mode") == "dedicated" else None,
        "model": raw.get("model") if raw.get("mode") == "dedicated" else None,
        "allow_remote_history": bool(raw.get("allow_remote_history", False)),
    }
    if result["mode"] == "current":
        try:
            current = json.loads(db.get_setting("current_model", "{}") or "{}")
        except (TypeError, ValueError):
            current = {}
        resolved_provider_id = str(current.get("provider_id") or "mock")
        provider = _load_provider(resolved_provider_id)
        result["resolved_provider_id"] = resolved_provider_id
        result["resolved_model"] = str(current.get("model") or "xiadie-mock")
    else:
        provider = _load_provider(result["provider_id"])
        result["resolved_provider_id"] = result["provider_id"]
        result["resolved_model"] = result["model"]
    result["execution_location"] = str((provider or {}).get("execution_location") or "unknown")
    result["location_revision"] = max(1, int((provider or {}).get("location_revision") or 1))
    return result


def set_model_config(*, mode: str, provider_id: str | None = None,
                     model: str | None = None, allow_remote_history: bool = False) -> dict:
    if mode not in {"current", "dedicated"}:
        raise ValueError("摘要模型模式无效")
    value: dict = {"mode": mode, "allow_remote_history": bool(allow_remote_history)}
    if mode == "dedicated":
        provider = _load_provider(provider_id)
        models = json.loads(provider.get("models") or "[]") if provider else []
        if not provider or not provider.get("enabled") or not model or model not in models:
            raise ValueError("请选择已启用的摘要模型")
        value.update(provider_id=provider_id, model=model)
    db.set_setting("conversation_summary_model", json.dumps(value, ensure_ascii=False))
    return get_model_config()


def enqueue_after_chat(*, session_id: str, chat_provider: dict | None, chat_model: str) -> dict:
    """热路径只建账并唤醒 worker；任何异常都被隔离。"""
    try:
        provider, model = _resolve_model(chat_provider, chat_model)
        active = ledger.active_revision_internal(session_id)
        revision = int(active.get("revision") or 0) if active else 0
        mode = generation_mode_for_revision(revision)
        location = str((provider or {}).get("execution_location") or "unknown")
        location_revision = max(1, int((provider or {}).get("location_revision") or 1))
        allowed = get_model_config()["allow_remote_history"]
        generation_key = ":".join((
            str((provider or {}).get("id") or "unavailable"), model or "",
            location, str(location_revision), "1" if allowed else "0", mode,
            str((active or {}).get("id") or "none"),
        ))
        run = ledger.enqueue(session_id, binding={
            "provider_id": (provider or {}).get("id"), "model": model,
            "provider_location": location,
            "provider_location_revision": location_revision,
            "remote_history_allowed": allowed,
            "generation_mode": mode,
            "base_revision_id": (active or {}).get("id"),
            "generation_key": generation_key,
        })
        wake_worker()
        return {"id": run["id"], "status": run["status"], "error_code": run.get("error_code")}
    except Exception as exc:  # noqa: BLE001 - 摘要不能破坏已完成聊天
        return {"status": "unlogged_failure", "error_code": "summary_enqueue_failed",
                "diagnostic_type": type(exc).__name__}


def rebuild(session_id: str) -> dict:
    """Enqueue a fresh full rebuild while the last valid summary stays active."""
    config = get_model_config()
    provider = _load_provider(str(config.get("resolved_provider_id") or ""))
    model = str(config.get("resolved_model") or "")
    location = str((provider or {}).get("execution_location") or "unknown")
    location_revision = max(1, int((provider or {}).get("location_revision") or 1))
    allowed = bool(config.get("allow_remote_history"))
    run = ledger.enqueue(session_id, binding={
        "provider_id": (provider or {}).get("id"), "model": model,
        "provider_location": location,
        "provider_location_revision": location_revision,
        "remote_history_allowed": allowed,
        "generation_mode": "full", "base_revision_id": None,
        "generation_key": f"manual-full:{db.new_id()}",
    })
    wake_worker()
    return {"run": {"id": run["id"], "status": run["status"],
                    "error_code": run.get("error_code")}}


async def start_worker() -> None:
    global _worker_task, _wake_event
    if _worker_task and not _worker_task.done():
        return
    _wake_event = asyncio.Event()
    _worker_task = asyncio.create_task(_worker_loop(), name="xiadie-conversation-summary")


async def stop_worker() -> None:
    global _worker_task, _wake_event
    task, _worker_task, _wake_event = _worker_task, None, None
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
            processed = await process_due(limit=3)
        except Exception:  # noqa: BLE001 - 后台循环必须自愈
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


async def process_due(limit: int = 3) -> int:
    ledger.recover_stale_runs()
    count = 0
    for _ in range(max(1, min(int(limit), 10))):
        run = ledger.claim_next()
        if not run:
            break
        await _process(run)
        count += 1
    return count


async def _process(run: dict) -> None:
    provider = _load_provider(run.get("provider_id"))
    if not _provider_binding_valid(run, provider):
        ledger.fail_run(run["id"], run["lease_token"], "summary_provider_policy_changed", retryable=False)
        return
    if not provider or provider.get("id") == "mock" or not provider.get("enabled") or not provider.get("base_url"):
        ledger.fail_run(run["id"], run["lease_token"], "summary_model_unavailable", retryable=False)
        return
    if run.get("provider_location") != "local" and not run.get("remote_history_allowed"):
        ledger.fail_run(run["id"], run["lease_token"], "summary_remote_history_not_authorized", retryable=False)
        return
    try:
        source = ledger.load_claimed_source(run["id"], run["lease_token"])
    except ledger.ConversationSummaryError as exc:
        ledger.fail_run(run["id"], run["lease_token"], exc.code, retryable=False)
        return
    generation_source, previous = _generation_source(run, source)
    prompt_messages, safe_source, _sanitization = protocol.build_messages(messages=generation_source)
    input_chars = sum(len(item["content"]) for item in prompt_messages)
    completions: list[dict] = []
    started = time.perf_counter()
    repair_attempted = False
    try:
        first = await llm.complete_json(provider, str(run.get("model") or ""), prompt_messages)
        completions.append(first)
        try:
            result = protocol.parse_and_validate(first["text"], messages=safe_source)
        except protocol.SummaryProtocolError as first_error:
            if first_error.code not in {"invalid_json", "schema_invalid", "invalid_type"}:
                raise
            repair_attempted = True
            repaired = await llm.complete_json(
                provider, str(run.get("model") or ""),
                protocol.build_repair_messages(first["text"]),
            )
            completions.append(repaired)
            result = protocol.parse_and_validate(repaired["text"], messages=safe_source)
        result = _merge_incremental(previous, result) if previous else result
        prompt_tokens, completion_tokens = _token_totals(completions)
        output_chars = sum(len(item.get("text") or "") for item in completions)
        ledger.activate_result(
            run["id"], run["lease_token"], result,
            provider_id=provider["id"], model=str(run.get("model") or ""),
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            input_chars=input_chars, output_chars=output_chars,
            latency_ms=_elapsed_ms(started), repair_attempted=repair_attempted,
        )
    except protocol.SummaryProtocolError as exc:
        _record_failure_metrics(run, completions, input_chars, started, repair_attempted)
        ledger.fail_run(run["id"], run["lease_token"], exc.code, retryable=False)
    except llm.LLMError as exc:
        _record_failure_metrics(run, completions, input_chars, started, repair_attempted)
        ledger.fail_run(
            run["id"], run["lease_token"], exc.code or "summary_model_call_failed",
            retryable=True,
        )
    except ledger.ConversationSummaryError:
        return
    except Exception:  # noqa: BLE001
        _record_failure_metrics(run, completions, input_chars, started, repair_attempted)
        ledger.fail_run(run["id"], run["lease_token"], "summary_generation_failed", retryable=True)


def _generation_source(run: dict, source: list[dict]) -> tuple[list[dict], dict | None]:
    if run.get("generation_mode") != "incremental" or not run.get("base_revision_id"):
        return source, None
    previous = ledger.active_revision_internal(run["session_id"])
    if not previous or previous["id"] != run["base_revision_id"]:
        return source, None
    positions = {item["id"]: index for index, item in enumerate(source)}
    end = positions.get(previous["source_end_message_id"])
    if end is None or end + 1 >= len(source):
        return source, None
    prior_by_message: dict[str, list[str]] = {}
    for field in ("decisions", "corrections", "open_threads", "entity_refs"):
        for claim in previous.get(field, []):
            ids = claim.get("message_ids") or []
            if ids:
                prior_by_message.setdefault(str(ids[0]), []).append(str(claim.get("text") or ""))
    prior_claims = [
        {"id": message_id, "role": "user", "content": "\n".join(dict.fromkeys(texts))}
        for message_id, texts in prior_by_message.items()
    ]
    return prior_claims + source[end + 1:], previous


def _merge_incremental(previous: dict, current: dict) -> dict:
    superseded = {
        mid for correction in current["corrections"]
        for mid in correction.get("supersedes_message_ids", [])
    }
    def merge(field: str) -> list[dict]:
        old = [item for item in previous.get(field, []) if not (set(item.get("message_ids", [])) & superseded)]
        values = old + current[field]
        seen, result = set(), []
        for item in values:
            key = (item.get("text"), tuple(item.get("message_ids", [])))
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result[-12:]
    merged = {field: merge(field) for field in (
        "decisions", "corrections", "open_threads", "entity_refs",
    )}
    if current.get("corrections"):
        # 旧 summary_text 可能仍含被纠正状态；有纠正时只从当前有效结构重建，
        # 不能让已过滤的旧决定通过自由文本继续存活。
        parts = [current["summary_text"]]
        parts.extend(item["text"] for field in ("decisions", "open_threads") for item in merged[field])
        continuity = "；".join(dict.fromkeys(part.strip("；。") for part in parts if part)) + "。"
    else:
        continuity = (str(previous.get("summary_text") or "").rstrip("。") + "；" +
                      str(current["summary_text"]).lstrip("；")).strip("；")
    merged["summary_text"] = continuity[-protocol.MAX_SUMMARY_CHARS:]
    merged["continuity"] = current.get("continuity", [])
    return merged


def _resolve_model(chat_provider: dict | None, chat_model: str) -> tuple[dict | None, str]:
    config = get_model_config()
    if config["mode"] == "dedicated":
        return _load_provider(config["provider_id"]), str(config["model"] or "")
    return chat_provider, chat_model


def generation_mode_for_revision(active_revision: int) -> str:
    revision = max(0, int(active_revision))
    return "full" if revision == 0 or revision % FULL_REBUILD_INTERVAL == 0 else "incremental"


def _load_provider(provider_id: str | None) -> dict | None:
    if not provider_id:
        return None
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
        if not row:
            return None
        provider = dict(row)
        provider["api_key"] = secret_store.get_store().retrieve(f"provider:{provider_id}") or provider.get("api_key") or ""
        return provider
    finally:
        conn.close()


def _provider_binding_valid(run: dict, provider: dict | None) -> bool:
    return bool(provider and provider.get("id") == run.get("provider_id")
                and str(provider.get("execution_location") or "unknown") == run.get("provider_location")
                and max(1, int(provider.get("location_revision") or 1))
                == int(run.get("provider_location_revision") or 1))


def _token_totals(completions: list[dict]) -> tuple[int | None, int | None]:
    def total(key: str) -> int | None:
        values = [item.get(key) for item in completions]
        known = [value for value in values if isinstance(value, int) and not isinstance(value, bool)]
        return sum(known) if known else None
    return total("prompt_tokens"), total("completion_tokens")


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _record_failure_metrics(run: dict, completions: list[dict], input_chars: int,
                            started: float, repair_attempted: bool) -> None:
    try:
        prompt_tokens, completion_tokens = _token_totals(completions)
        ledger.record_attempt_metrics(
            run["id"], run["lease_token"], input_chars=input_chars,
            output_chars=sum(len(item.get("text") or "") for item in completions),
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            latency_ms=_elapsed_ms(started), repair_attempted=repair_attempted,
        )
    except Exception:  # noqa: BLE001 - 指标失败不能覆盖原始失败码
        pass
