"""输出 CTX.0 合成上下文基线；只输出计数和状态，不输出消息正文。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import context_budget  # noqa: E402


def _history(rounds: int, units: int) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for _ in range(rounds):
        messages.extend([
            {"role": "user", "content": "用户消息" * units},
            {"role": "assistant", "content": "遐蝶回复" * units},
        ])
    messages.append({"role": "user", "content": "当前问题" * units})
    return messages


def measure_case(*, case: str, provider_id: str, model: str, rounds: int,
                 message_units: int, system_units: int) -> dict[str, object]:
    provider = {"id": provider_id, "model": model}
    history = _history(rounds, message_units)
    system_components = {
        "persona_and_safety": "人格规则" * system_units,
        "memory_digest": "长期记忆" * (system_units // 4),
        "affect_guidance": "情绪引导" * (system_units // 8),
        "lore_digest": "背景设定" * (system_units // 4),
        "knowledge_block": "知识引用" * (system_units // 4),
    }
    system_prompt = "\n".join(system_components.values())
    capability = context_budget.resolve_model_context_capability(provider, model)
    base = {
        "case": case,
        "provider_id": provider_id,
        "model": model,
        "context_window": capability.effective_context_window,
        "context_window_source": capability.source,
    }
    try:
        plan = context_budget.build_budget_plan(
            system_prompt=system_prompt,
            history=history,
            capability=capability,
            system_components=system_components,
        )
    except context_budget.ContextBudgetError as error:
        return {
            **base,
            "outcome": "fail_closed",
            "error_code": error.code,
            "exceeds_window": False,
            "request_constructed": False,
            "budget": error.details,
        }
    return {
        **base,
        "outcome": "planned",
        "completed_history_rounds": rounds,
        "history_messages_kept": len(plan.messages) - 2,
        "estimated_input_tokens": plan.estimated_input_tokens,
        "reserved_total_tokens": plan.reserved_total_tokens,
        "output_reserve_tokens": plan.output_reserve_tokens,
        "trimmed_messages": plan.trimmed_messages,
        "trimmed_rounds": plan.trimmed_rounds,
        "exceeds_window": plan.reserved_total_tokens > capability.effective_context_window,
        "request_constructed": True,
        "component_tokens": plan.component_tokens,
    }


def main() -> None:
    cases = [
        measure_case(
            case="short-mock", provider_id="mock", model="xiadie-mock",
            rounds=4, message_units=16, system_units=128,
        ),
        measure_case(
            case="medium-custom", provider_id="custom", model="user-model-128k",
            rounds=20, message_units=32, system_units=256,
        ),
        measure_case(
            case="long-openai", provider_id="openai", model="configured-32k-model",
            rounds=100, message_units=64, system_units=512,
        ),
        measure_case(
            case="oversized-system", provider_id="custom", model="user-model",
            rounds=8, message_units=32, system_units=1_024,
        ),
    ]
    payload = {
        "protocol": "context-baseline-v1",
        "contains_message_content": False,
        "estimator_module": context_budget.estimate_tokens.__module__,
        "cases": cases,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
