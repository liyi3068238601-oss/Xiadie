"""CTX.0：固定临时上下文预算器的现状和已知失败基线。"""

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


def test_current_window_selection_uses_provider_id_not_model():
    small_model = {"id": "openai", "model": "configured-small-model"}
    large_model = {"id": "openai", "model": "configured-large-model"}

    assert context_budget.get_context_window(small_model) == 128_000
    assert context_budget.get_context_window(large_model) == 128_000


def test_current_estimator_is_the_shared_knowledge_estimator():
    # 记录 import 尾部覆盖后的真实运行状态，CTX.1 将移除重复定义。
    assert context_budget.estimate_tokens.__module__ == "app.knowledge_context"


@pytest.mark.xfail(
    strict=True,
    reason="CTX.0 baseline: keep_min_rounds can exceed the supplied hard token budget",
)
def test_trimmed_history_never_exceeds_budget():
    budget = 1
    trimmed = context_budget.trim_history(_rounds(8), budget, keep_min_rounds=4)

    assert context_budget.count_history_tokens(trimmed) <= budget

@pytest.mark.xfail(
    strict=True,
    reason="CTX.0 baseline: an oversized protected tail is returned instead of failing closed",
)
def test_oversized_recent_round_fails_closed():
    budget = 32
    trimmed = context_budget.trim_history(_rounds(1, units=256), budget)

    assert context_budget.count_history_tokens(trimmed) <= budget
