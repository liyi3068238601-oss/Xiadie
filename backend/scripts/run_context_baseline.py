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
    context_window = context_budget.get_context_window(provider)
    system_tokens = context_budget.estimate_tokens(system_prompt)
    history_tokens = context_budget.count_history_tokens(history)
    available = max(512, context_window - system_tokens)
    trimmed = context_budget.trim_history(history, available)
    kept_history_tokens = context_budget.count_history_tokens(trimmed)
    estimated_input_tokens = system_tokens + kept_history_tokens
    component_tokens = {
        name: context_budget.estimate_tokens(value)
        for name, value in system_components.items()
    }
    component_tokens.update({
        "current_user_message": context_budget.estimate_tokens(history[-1]["content"]),
        "history_before": history_tokens,
        "history_kept": kept_history_tokens,
        "message_envelope": None,
        "output_reserve": 0,
    })
    return {
        "case": case,
        "provider_id": provider_id,
        "model": model,
        "context_window": context_window,
        "system_tokens": system_tokens,
        "completed_history_rounds": rounds,
        "history_tokens_before": history_tokens,
        "available_history_tokens": available,
        "history_messages_kept": len(trimmed),
        "history_tokens_kept": kept_history_tokens,
        "estimated_input_tokens": estimated_input_tokens,
        "exceeds_window": estimated_input_tokens > context_window,
        "component_tokens": component_tokens,
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
