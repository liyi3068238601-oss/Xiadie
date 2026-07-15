"""Episode 标题/摘要的受限模型调用与安全回退。"""
from __future__ import annotations

import json

from . import db, episode_summary, episodes, llm

MAX_INPUT_CHARS = 16_000


def get_model_config() -> dict:
    try:
        value = json.loads(db.get_setting("episode_summary_model", '{"mode":"current"}'))
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
        raise ValueError("Episode 摘要模型模式无效")
    if mode == "dedicated":
        provider = _load_provider(provider_id)
        if not _provider_available(provider) or not model:
            raise ValueError("请选择可用的真实供应商与 Episode 摘要模型")
        models = json.loads(provider.get("models") or "[]")
        if model not in models:
            raise ValueError("Episode 摘要模型不在该供应商的模型列表中")
        value = {"mode": mode, "provider_id": provider_id, "model": model}
    else:
        value = {"mode": "current"}
    db.set_setting("episode_summary_model", json.dumps(value, ensure_ascii=False))
    return get_model_config()


async def enrich_candidates(candidates: list[dict]) -> dict:
    result = {"validated": 0, "fallback": 0, "skipped": 0}
    for item in candidates[:20]:
        status = await enrich_candidate(str(item.get("id") or ""))
        result[status] += 1
    return result


async def enrich_candidate(candidate_id: str) -> str:
    candidate = episodes.get_candidate(candidate_id)
    if not candidate or candidate["status"] != "pending":
        return "skipped"
    fragment_ids = [fragment["id"] for fragment in candidate["fragments"]]
    expected_source_hash = episode_summary.source_hash(candidate["fragments"])
    conn = db.connect()
    try:
        entity_names = episodes.shared_entity_names(conn, fragment_ids)
    finally:
        conn.close()
    provider, model = _resolve_model()
    provider_id = provider.get("id") if provider else None
    if not _provider_available(provider) or not model:
        episodes.record_summary_fallback(
            candidate_id, "summary_model_unavailable", provider_id=provider_id, model=model
        )
        return "fallback"
    messages = episode_summary.build_messages(
        fragments=candidate["fragments"], entity_names=entity_names
    )
    input_chars = sum(len(message.get("content", "")) for message in messages)
    if input_chars > MAX_INPUT_CHARS:
        episodes.record_summary_fallback(
            candidate_id, "summary_input_too_large", provider_id=provider_id, model=model
        )
        return "fallback"
    completions = []
    repair_attempted = False
    try:
        completion = await llm.complete_json(
            provider, model, messages, max_tokens=llm.JSON_COMPLETION_MAX_TOKENS
        )
        completions.append(completion)
        try:
            episode_summary.parse_and_validate(
                completion["text"], fragments=candidate["fragments"], entity_names=entity_names
            )
            selected = completion["text"]
        except episode_summary.EpisodeSummaryValidationError as exc:
            if exc.code not in ("invalid_json", "invalid_type", "schema_invalid"):
                raise
            repair_attempted = True
            repaired = await llm.complete_json(
                provider, model, episode_summary.build_repair_messages(completion["text"]),
                max_tokens=llm.JSON_COMPLETION_MAX_TOKENS,
            )
            completions.append(repaired)
            selected = repaired["text"]
        prompt_tokens, completion_tokens = _token_totals(completions)
        updated = episodes.apply_model_summary(
            candidate_id, selected, provider_id=provider_id or "", model=model,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            repair_attempted=repair_attempted, expected_source_hash=expected_source_hash,
        )
        return "validated" if updated else "skipped"
    except episode_summary.EpisodeSummaryValidationError as exc:
        error_code = exc.code
    except llm.LLMError:
        error_code = "summary_model_call_failed"
    except Exception:  # noqa: BLE001 - 模型摘要不能破坏安全回退
        error_code = "summary_internal_error"
    prompt_tokens, completion_tokens = _token_totals(completions)
    episodes.record_summary_fallback(
        candidate_id, error_code, provider_id=provider_id, model=model,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        repair_attempted=repair_attempted,
    )
    return "fallback"


def _resolve_model() -> tuple[dict | None, str]:
    config = get_model_config()
    if config["mode"] == "dedicated":
        return _load_provider(config["provider_id"]), config["model"] or ""
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
