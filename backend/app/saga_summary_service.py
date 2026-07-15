"""Saga 候选摘要的受限模型调用、一次结构修复与安全回退。"""
from __future__ import annotations

import json

from . import db, llm, saga_summary, sagas

MAX_INPUT_CHARS = 24_000


def get_model_config() -> dict:
    try:
        value = json.loads(db.get_setting("saga_summary_model", '{"mode":"current"}'))
    except (ValueError, TypeError):
        value = {"mode": "current"}
    if value.get("mode") != "dedicated":
        return {"mode": "current", "provider_id": None, "model": None}
    return {
        "mode": "dedicated", "provider_id": value.get("provider_id"),
        "model": value.get("model"),
    }


async def enrich_candidates(candidates: list[dict]) -> dict:
    result = {"validated": 0, "fallback": 0, "skipped": 0}
    for item in candidates[:20]:
        status = await enrich_candidate(str(item.get("id") or ""))
        result[status] += 1
    return result


async def enrich_candidate(candidate_id: str) -> str:
    candidate = sagas.get_group_candidate(candidate_id)
    if not candidate or candidate["status"] != "qualified":
        return "skipped"
    episodes = candidate["episodes"]
    entity_names = candidate["shared_entity_names"]
    try:
        saga_summary.validate_source_chain(episodes)
    except saga_summary.SagaSummaryValidationError as exc:
        sagas.record_summary_rejection(candidate_id, exc.code)
        return "skipped"
    expected_source_hash = saga_summary.source_hash(episodes)
    provider, model = _resolve_model()
    provider_id = provider.get("id") if provider else None
    if not _provider_available(provider) or not model:
        return _fallback(
            candidate_id, "summary_model_unavailable", provider_id=provider_id, model=model
        )
    messages = saga_summary.build_messages(episodes=episodes, entity_names=entity_names)
    if sum(len(message.get("content", "")) for message in messages) > MAX_INPUT_CHARS:
        return _fallback(
            candidate_id, "summary_input_too_large", provider_id=provider_id, model=model
        )
    completions: list[dict] = []
    repair_attempted = False
    try:
        completion = await llm.complete_json(
            provider, model, messages, max_tokens=llm.JSON_COMPLETION_MAX_TOKENS
        )
        completions.append(completion)
        try:
            saga_summary.parse_and_validate(
                completion["text"], episodes=episodes, entity_names=entity_names
            )
            selected = completion["text"]
        except saga_summary.SagaSummaryValidationError as exc:
            if exc.code not in {"invalid_json", "invalid_type", "schema_invalid"}:
                raise
            repair_attempted = True
            repaired = await llm.complete_json(
                provider, model, saga_summary.build_repair_messages(completion["text"]),
                max_tokens=llm.JSON_COMPLETION_MAX_TOKENS,
            )
            completions.append(repaired)
            selected = repaired["text"]
        prompt_tokens, completion_tokens = _token_totals(completions)
        updated = sagas.apply_model_summary(
            candidate_id, selected, provider_id=provider_id or "", model=model,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            repair_attempted=repair_attempted, expected_source_hash=expected_source_hash,
        )
        return "validated" if updated else "skipped"
    except saga_summary.SagaSummaryValidationError as exc:
        error_code = exc.code
    except llm.LLMError:
        error_code = "summary_model_call_failed"
    except Exception:  # noqa: BLE001 - 摘要失败不能影响候选与聊天
        error_code = "summary_internal_error"
    prompt_tokens, completion_tokens = _token_totals(completions)
    return _fallback(
        candidate_id, error_code, provider_id=provider_id, model=model,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        repair_attempted=repair_attempted,
    )


def _fallback(
    candidate_id: str, error_code: str, *, provider_id: str | None,
    model: str | None, prompt_tokens: int | None = None,
    completion_tokens: int | None = None, repair_attempted: bool = False,
) -> str:
    try:
        updated = sagas.record_summary_fallback(
            candidate_id, error_code, provider_id=provider_id, model=model,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            repair_attempted=repair_attempted,
        )
        return "fallback" if updated else "skipped"
    except saga_summary.SagaSummaryValidationError as exc:
        sagas.record_summary_rejection(candidate_id, exc.code)
        return "skipped"
    except Exception:  # noqa: BLE001 - 审计失败也不能传播到后台 worker
        try:
            sagas.record_summary_rejection(candidate_id, "summary_fallback_failed")
        except Exception:  # noqa: BLE001
            pass
        return "skipped"


def _resolve_model() -> tuple[dict | None, str]:
    config = get_model_config()
    if config["mode"] == "dedicated":
        return _load_provider(config["provider_id"]), str(config.get("model") or "")
    try:
        current = json.loads(db.get_setting("current_model", "{}") or "{}")
    except (ValueError, TypeError):
        current = {}
    provider_id = current.get("provider_id", "mock")
    return _load_provider(provider_id), str(current.get("model") or "xiadie-mock")


def _load_provider(provider_id: str | None) -> dict | None:
    if not provider_id:
        return None
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _provider_available(provider: dict | None) -> bool:
    return bool(
        provider and provider.get("id") != "mock" and provider.get("enabled")
        and provider.get("base_url")
    )


def _token_totals(completions: list[dict]) -> tuple[int | None, int | None]:
    def total(key: str) -> int | None:
        values = [item.get(key) for item in completions]
        numeric = [int(value) for value in values if isinstance(value, (int, float))]
        return sum(numeric) if numeric else None
    return total("prompt_tokens"), total("completion_tokens")
