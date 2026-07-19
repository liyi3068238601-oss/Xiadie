"""模型上下文能力解析与硬预算规划。

本模块不读取数据库、不调用网络，也不保存消息正文。它以纯函数方式解析
provider+model 能力并为一次聊天请求生成可审计的预算计划。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

APPLICATION_CONTEXT_CEILING_TOKENS = 1_000_000
CONSERVATIVE_CONTEXT_WINDOW_TOKENS = 4_096
CONSERVATIVE_MAX_OUTPUT_TOKENS = 1_024
DEFAULT_OUTPUT_RESERVE_TOKENS = 2_048
MIN_OUTPUT_RESERVE_TOKENS = 256
MIN_SAFETY_MARGIN_TOKENS = 256
MAX_SAFETY_MARGIN_TOKENS = 8_192
MESSAGE_ENVELOPE_TOKENS = 4
REQUEST_ENVELOPE_TOKENS = 2
ESTIMATOR_VERSION = "xiadie-conservative-v1"
BUDGET_PROTOCOL_VERSION = "context-budget-v1"


@dataclass(frozen=True)
class ModelContextCapability:
    provider_id: str
    model_id: str
    declared_context_window: int
    effective_context_window: int
    max_output_tokens: int
    default_output_tokens: int
    source: str
    verified: bool

    def public_meta(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "declared_context_window": self.declared_context_window,
            "effective_context_window": self.effective_context_window,
            "max_output_tokens": self.max_output_tokens,
            "default_output_tokens": self.default_output_tokens,
            "source": self.source,
            "verified": self.verified,
        }


@dataclass(frozen=True)
class BudgetPlan:
    capability: ModelContextCapability
    messages: tuple[dict[str, str], ...]
    component_tokens: dict[str, int]
    system_breakdown_tokens: dict[str, int]
    estimated_input_tokens: int
    output_reserve_tokens: int
    safety_margin_tokens: int
    reserved_total_tokens: int
    history_budget_tokens: int
    history_tokens_before: int
    history_tokens_kept: int
    trimmed_messages: int
    trimmed_rounds: int

    def public_meta(self) -> dict[str, object]:
        return {
            "protocol_version": BUDGET_PROTOCOL_VERSION,
            "estimator_version": ESTIMATOR_VERSION,
            "estimated_input_tokens": self.estimated_input_tokens,
            "output_reserve_tokens": self.output_reserve_tokens,
            "safety_margin_tokens": self.safety_margin_tokens,
            "reserved_total_tokens": self.reserved_total_tokens,
            "context_window_tokens": self.capability.effective_context_window,
            "context_window_source": self.capability.source,
            "trimmed_messages": self.trimmed_messages,
            "trimmed_rounds": self.trimmed_rounds,
            "component_tokens": dict(self.component_tokens),
            "system_breakdown_tokens": dict(self.system_breakdown_tokens),
        }


class ContextBudgetError(ValueError):
    """受保护区无法放入模型窗口；详情只包含计数，不包含正文。"""

    def __init__(self, code: str, message: str, details: Mapping[str, object]):
        super().__init__(message)
        self.code = code
        self.details = dict(details)

    def public_detail(self) -> dict[str, object]:
        return {"code": self.code, "message": str(self), "budget": dict(self.details)}


def _capability(provider_id: str, model_id: str, context_window: int,
                max_output_tokens: int, default_output_tokens: int, *,
                source: str, verified: bool) -> ModelContextCapability:
    declared = max(1, int(context_window))
    effective = min(APPLICATION_CONTEXT_CEILING_TOKENS, declared)
    max_output = max(1, min(int(max_output_tokens), effective))
    default_output = max(1, min(int(default_output_tokens), max_output))
    return ModelContextCapability(
        provider_id=provider_id,
        model_id=model_id,
        declared_context_window=declared,
        effective_context_window=effective,
        max_output_tokens=max_output,
        default_output_tokens=default_output,
        source=source,
        verified=verified,
    )


# 这些条目把旧 Provider 级假设收窄到项目默认的具体模型。除内置 mock 外，
# 它们属于适配器保守映射，不声称是运行时探测或永久不变的供应商事实。
_ADAPTER_CAPABILITIES: dict[tuple[str, str], tuple[int, int, int]] = {
    ("deepseek", "deepseek-chat"): (65_536, 8_192, 2_048),
    ("deepseek", "deepseek-reasoner"): (65_536, 8_192, 2_048),
    ("openai", "gpt-4o-mini"): (128_000, 16_384, 4_096),
    ("openai", "gpt-4o"): (128_000, 16_384, 4_096),
    ("glm", "glm-4-flash"): (128_000, 8_192, 2_048),
    ("glm", "glm-4-plus"): (128_000, 8_192, 2_048),
    ("qwen", "qwen-plus"): (32_768, 8_192, 2_048),
    ("qwen", "qwen-turbo"): (32_768, 8_192, 2_048),
    ("kimi", "moonshot-v1-8k"): (8_192, 4_096, 1_024),
    ("siliconflow", "qwen/qwen2.5-7b-instruct"): (32_768, 4_096, 2_048),
    ("ollama", "qwen2.5:7b"): (8_192, 2_048, 1_024),
}


def parse_configured_profiles(raw: str | Mapping[str, object] | None) -> dict[str, dict]:
    if isinstance(raw, str):
        try:
            value = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return {}
    else:
        value = raw
    if not isinstance(value, Mapping):
        return {}
    profiles: dict[str, dict] = {}
    for key, item in value.items():
        if isinstance(key, str) and isinstance(item, Mapping):
            profiles[key.casefold()] = dict(item)
    return profiles


def resolve_model_context_capability(
    provider: Mapping[str, object] | None,
    model: str,
    *,
    configured_profiles: Mapping[str, object] | str | None = None,
) -> ModelContextCapability:
    provider_id = str((provider or {}).get("id") or "mock").strip().casefold()
    model_id = str(model or "xiadie-mock").strip()
    normalized_model = model_id.casefold()
    if provider_id == "mock" and normalized_model == "xiadie-mock":
        return _capability(
            provider_id, model_id, 8_192, 2_048, 1_024,
            source="verified", verified=True,
        )

    adapter = _ADAPTER_CAPABILITIES.get((provider_id, normalized_model))
    if adapter:
        return _capability(
            provider_id, model_id, *adapter, source="adapter", verified=False,
        )

    configured = parse_configured_profiles(configured_profiles)
    item = configured.get(f"{provider_id}/{normalized_model}")
    if item is None and isinstance(provider, Mapping):
        direct_window = provider.get("context_window")
        if direct_window is not None:
            item = {
                "context_window": direct_window,
                "max_output_tokens": provider.get("max_output_tokens"),
                "default_output_tokens": provider.get("default_output_tokens"),
            }
    if item:
        try:
            context_window = int(item["context_window"])
            max_output = int(item.get("max_output_tokens") or CONSERVATIVE_MAX_OUTPUT_TOKENS)
            default_output = int(item.get("default_output_tokens") or max_output)
        except (KeyError, TypeError, ValueError):
            item = None
        else:
            if context_window > 0 and max_output > 0 and default_output > 0:
                return _capability(
                    provider_id, model_id, context_window, max_output, default_output,
                    source="configured", verified=False,
                )

    return _capability(
        provider_id,
        model_id,
        CONSERVATIVE_CONTEXT_WINDOW_TOKENS,
        CONSERVATIVE_MAX_OUTPUT_TOKENS,
        min(DEFAULT_OUTPUT_RESERVE_TOKENS, CONSERVATIVE_MAX_OUTPUT_TOKENS),
        source="conservative_fallback",
        verified=False,
    )


def estimate_tokens(text: str) -> int:
    """无需外部 tokenizer 的统一保守估算器。"""
    if not text:
        return 0
    cjk = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", text))
    words = len(re.findall(r"[A-Za-z0-9_]+", text))
    punctuation = len(re.findall(r"[^\s\u3400-\u4dbf\u4e00-\u9fffA-Za-z0-9_]", text))
    return cjk + words + (punctuation + 3) // 4


def estimate_message_envelope(message_count: int) -> int:
    return REQUEST_ENVELOPE_TOKENS + max(0, int(message_count)) * MESSAGE_ENVELOPE_TOKENS


def count_history_tokens(history: Sequence[Mapping[str, object]]) -> int:
    return sum(estimate_tokens(str(message.get("content") or "")) for message in history)


def _normalize_history(history: Sequence[Mapping[str, object]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for message in history:
        item = dict(message)
        normalized.append({
            "role": str(item.get("role") or ""),
            "content": str(item.get("content") or ""),
        })
    return normalized


def _history_turns(history: Sequence[Mapping[str, object]]) -> list[list[dict[str, str]]]:
    turns: list[list[dict[str, str]]] = []
    current_user: dict[str, str] | None = None
    for message in _normalize_history(history):
        if message["role"] == "user":
            # 未配对的旧 user 不构成完整轮次；较新的 user 取代它。
            current_user = message
        elif message["role"] == "assistant" and current_user is not None:
            turns.append([current_user, message])
            current_user = None
    return turns


def trim_history(history: Sequence[Mapping[str, object]], max_tokens: int, *,
                 keep_min_rounds: int = 0) -> list[dict[str, str]]:
    """保留不超过预算的连续最近完整轮次；keep_min_rounds 仅兼容旧调用。"""
    del keep_min_rounds
    budget = max(0, int(max_tokens))
    kept: list[list[dict[str, str]]] = []
    used = 0
    for turn in reversed(_history_turns(history)):
        turn_tokens = count_history_tokens(turn)
        if used + turn_tokens > budget:
            break
        kept.insert(0, turn)
        used += turn_tokens
    return [message for turn in kept for message in turn]


def _safety_margin(context_window: int) -> int:
    proportional = max(MIN_SAFETY_MARGIN_TOKENS, context_window // 100)
    return min(MAX_SAFETY_MARGIN_TOKENS, proportional)


def _budget_error(capability: ModelContextCapability, *, system_tokens: int,
                  current_user_tokens: int, message_envelope_tokens: int,
                  output_reserve_tokens: int, safety_margin_tokens: int) -> ContextBudgetError:
    reserved = (
        system_tokens + current_user_tokens + message_envelope_tokens
        + output_reserve_tokens + safety_margin_tokens
    )
    return ContextBudgetError(
        "context_protected_region_exceeds_window",
        "当前消息与必要规则无法放入所选模型的上下文窗口，请缩短输入或切换更大模型。",
        {
            "protocol_version": BUDGET_PROTOCOL_VERSION,
            "estimator_version": ESTIMATOR_VERSION,
            "context_window_tokens": capability.effective_context_window,
            "context_window_source": capability.source,
            "system_tokens": system_tokens,
            "current_user_tokens": current_user_tokens,
            "message_envelope_tokens": message_envelope_tokens,
            "output_reserve_tokens": output_reserve_tokens,
            "safety_margin_tokens": safety_margin_tokens,
            "reserved_total_tokens": reserved,
        },
    )


def build_budget_plan(
    *,
    system_prompt: str,
    history: Sequence[Mapping[str, object]],
    capability: ModelContextCapability,
    system_components: Mapping[str, str] | None = None,
    output_reserve_tokens: int | None = None,
) -> BudgetPlan:
    normalized = _normalize_history(history)
    if not normalized or normalized[-1]["role"] != "user":
        raise ContextBudgetError(
            "context_current_user_missing",
            "无法确定当前用户消息，已在调用模型前停止。",
            {
                "protocol_version": BUDGET_PROTOCOL_VERSION,
                "context_window_tokens": capability.effective_context_window,
                "history_message_count": len(normalized),
            },
        )

    current_user = normalized[-1]
    prior_history = normalized[:-1]
    system_tokens = estimate_tokens(system_prompt)
    current_user_tokens = estimate_tokens(current_user["content"])
    output_reserve = output_reserve_tokens or capability.default_output_tokens
    minimum_output = min(MIN_OUTPUT_RESERVE_TOKENS, capability.max_output_tokens)
    output_reserve = max(
        minimum_output,
        min(int(output_reserve), capability.max_output_tokens),
    )
    safety_margin = _safety_margin(capability.effective_context_window)
    protected_envelope = estimate_message_envelope(2)
    protected_total = (
        system_tokens + current_user_tokens + protected_envelope
        + output_reserve + safety_margin
    )
    if protected_total > capability.effective_context_window:
        raise _budget_error(
            capability,
            system_tokens=system_tokens,
            current_user_tokens=current_user_tokens,
            message_envelope_tokens=protected_envelope,
            output_reserve_tokens=output_reserve,
            safety_margin_tokens=safety_margin,
        )

    history_budget = capability.effective_context_window - protected_total
    turns = _history_turns(prior_history)
    kept_turns: list[list[dict[str, str]]] = []
    kept_history_tokens = 0
    for turn in reversed(turns):
        turn_content_tokens = count_history_tokens(turn)
        turn_envelope_tokens = len(turn) * MESSAGE_ENVELOPE_TOKENS
        if kept_history_tokens + turn_content_tokens + turn_envelope_tokens > history_budget:
            break
        kept_turns.insert(0, turn)
        kept_history_tokens += turn_content_tokens + turn_envelope_tokens

    kept_history = [message for turn in kept_turns for message in turn]
    messages = (
        {"role": "system", "content": system_prompt},
        *kept_history,
        current_user,
    )
    message_envelope = estimate_message_envelope(len(messages))
    kept_content_tokens = count_history_tokens(kept_history)
    estimated_input = system_tokens + current_user_tokens + kept_content_tokens + message_envelope
    reserved_total = estimated_input + output_reserve + safety_margin
    if reserved_total > capability.effective_context_window:  # pragma: no cover - invariant guard
        raise ContextBudgetError(
            "context_budget_invariant_failed",
            "上下文预算内部校验失败，已在调用模型前停止。",
            {
                "protocol_version": BUDGET_PROTOCOL_VERSION,
                "context_window_tokens": capability.effective_context_window,
                "reserved_total_tokens": reserved_total,
            },
        )

    breakdown = {
        name: estimate_tokens(text)
        for name, text in (system_components or {}).items()
    }
    attributed = sum(breakdown.values())
    breakdown["prompt_structure_and_unattributed"] = max(0, system_tokens - attributed)
    components = {
        "system_prompt": system_tokens,
        "current_user_message": current_user_tokens,
        "recent_raw_turns": kept_content_tokens,
        "rolling_summary": 0,
        "cross_session_recall": 0,
        "message_envelope": message_envelope,
        "safety_margin": safety_margin,
        "output_reserve": output_reserve,
    }
    return BudgetPlan(
        capability=capability,
        messages=messages,
        component_tokens=components,
        system_breakdown_tokens=breakdown,
        estimated_input_tokens=estimated_input,
        output_reserve_tokens=output_reserve,
        safety_margin_tokens=safety_margin,
        reserved_total_tokens=reserved_total,
        history_budget_tokens=history_budget,
        history_tokens_before=count_history_tokens(prior_history),
        history_tokens_kept=kept_content_tokens,
        trimmed_messages=len(prior_history) - len(kept_history),
        trimmed_rounds=len(turns) - len(kept_turns),
    )


def get_context_window(provider: Mapping[str, object] | None, model: str = "") -> int:
    """兼容旧调用；新代码应读取完整 ModelContextCapability。"""
    return resolve_model_context_capability(provider, model).effective_context_window
