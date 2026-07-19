"""CTX.1：关闭 CTX.0 固定的上下文预算失败基线。"""

import pytest

from app import context_budget


def _rounds(count: int, *, units: int = 32) -> list[dict[str, str]]:
    history: list[dict[str, str]] = []
    for _ in range(count):
        history.extend([
            {"role": "user", "content": "用户消息" * units},
            {"role": "assistant", "content": "遐蝶回复" * units},
        ])
    return history


def test_window_selection_uses_provider_and_model():
    configured = {
        "custom/small-model": {
            "context_window": 8_192,
            "max_output_tokens": 1_024,
        },
        "custom/large-model": {
            "context_window": 128_000,
            "max_output_tokens": 8_192,
        },
    }

    small = context_budget.resolve_model_context_capability(
        {"id": "custom"}, "small-model", configured_profiles=configured,
    )
    large = context_budget.resolve_model_context_capability(
        {"id": "custom"}, "large-model", configured_profiles=configured,
    )

    assert small.effective_context_window == 8_192
    assert large.effective_context_window == 128_000
    assert small.source == large.source == "configured"


def test_context_budget_owns_the_shared_estimator():
    from app import knowledge_context

    assert context_budget.estimate_tokens.__module__ == "app.context_budget"
    assert knowledge_context.estimate_tokens is context_budget.estimate_tokens


def test_trimmed_history_never_exceeds_budget():
    budget = 1
    trimmed = context_budget.trim_history(_rounds(8), budget, keep_min_rounds=4)

    assert context_budget.count_history_tokens(trimmed) <= budget


def test_oversized_recent_round_fails_closed():
    capability = context_budget.resolve_model_context_capability(None, "xiadie-mock")
    history = [{"role": "user", "content": "当前问题" * 4_096}]

    with pytest.raises(context_budget.ContextBudgetError) as caught:
        context_budget.build_budget_plan(
            system_prompt="必要规则",
            history=history,
            capability=capability,
        )

    assert caught.value.code == "context_protected_region_exceeds_window"
