"""无模型依赖的确定性心境引擎。

相同状态、相同经过分钟数和相同互动文本会得到相同结果，便于回放和调参。
"""
from __future__ import annotations

import copy
import math

ALGORITHM_VERSION = "affect-v1"
MAX_ELAPSED_MINUTES = 7 * 24 * 60
STEP_MINUTES = 5.0

DEFAULT_AFFECT = {
    "contact_need": 0.05,
    "guardedness_transient": 0.0,
    "valence": 0.08,
    "arousal": -0.12,
    "immersion": 0.12,
    "activity_type": None,
    "activity_label": None,
    "activity_started_at": None,
    "last_user_message_at": None,
    "last_tick_at": 0.0,
    "updated_at": 0.0,
}

DEFAULT_RELATIONSHIP = {
    "bond": 0.12,
    "trust": 0.25,
    "interaction_count": 0,
    "updated_at": 0.0,
}

APPRECIATION_HINTS = ("谢谢你", "感谢你", "辛苦了", "做得好", "帮大忙", "很厉害")


def guardedness_baseline(trust: float) -> float:
    return clamp(0.68 - clamp(trust, 0, 1) * 0.22, 0.42, 0.68)


def guardedness(snapshot: dict) -> float:
    affect = snapshot["affect"]
    return clamp(
        guardedness_baseline(snapshot["relationship"]["trust"])
        + affect["guardedness_transient"],
        0,
        1,
    )


def valence_factor(value: float) -> float:
    """联系需求的连续非单调 valence 调制函数。"""
    value = clamp(value, -1, 1)
    if value >= 0:
        return 0.90 - 0.10 * value
    if value >= -0.30:
        return 0.90 + 0.50 * abs(value)
    if value >= -0.60:
        return 1.05 - 0.50 * (abs(value) - 0.30)
    return 0.50 + (value + 1.00)


def emotion_cluster(valence: float, arousal: float) -> str:
    if abs(valence) < 0.15:
        if arousal >= 0.15:
            return "focused"
        if arousal <= -0.15:
            return "contemplative"
        return "neutral"
    if valence >= 0.15:
        if arousal >= 0.30:
            return "bright"
        if arousal <= -0.30:
            return "serene"
        return "pleased"
    if arousal >= 0.30:
        return "agitated"
    if arousal <= -0.30:
        return "melancholic"
    return "subdued"


def advance(snapshot: dict, minutes: float) -> dict:
    """以至多五分钟的小步推进，最长一次处理七天。"""
    result = copy.deepcopy(snapshot)
    remaining = clamp(float(minutes), 0, MAX_ELAPSED_MINUTES)
    while remaining > 1e-9:
        step = min(STEP_MINUTES, remaining)
        _advance_step(result, step)
        remaining -= step
    return normalize(result)


def _advance_step(snapshot: dict, minutes: float) -> None:
    affect = snapshot["affect"]
    relation = snapshot["relationship"]
    c = affect["contact_need"]
    g = guardedness(snapshot)

    acceleration = math.pow(1 + c, 1.2)
    immersion_factor = max(0.25, 1 - affect["immersion"] * 0.75)
    bond_factor = 1 + min(relation["bond"], 1) * 0.20
    rate = 0.00042 * acceleration * immersion_factor * bond_factor
    rate *= valence_factor(affect["valence"])
    affect["contact_need"] += rate * minutes

    # 短期克制偏移回归 0；联系需求较高时保护性距离上升，极高时则难以继续维持。
    transient = affect["guardedness_transient"]
    transient = move_toward(transient, 0.0, 0.0005 * minutes)
    if c >= 0.45:
        transient += 0.00015 * minutes
    if c >= 0.75:
        transient -= 0.00030 * minutes
    affect["guardedness_transient"] = transient

    affect["valence"] = move_toward(affect["valence"], 0.05, 0.0006 * minutes)
    if c >= 0.55:
        affect["valence"] -= 0.00008 * c * minutes

    arousal = move_toward(affect["arousal"], -0.12, 0.0008 * minutes)
    if c >= 0.45:
        arousal += 0.00025 * minutes
    if c >= 0.55 and g >= 0.62:
        arousal += 0.00015 * minutes
    affect["arousal"] = arousal

    affect["immersion"] = max(0.0, affect["immersion"] - 0.003 * minutes)
    if affect["immersion"] <= 0.01:
        affect["immersion"] = 0.0
        affect["activity_type"] = None
        affect["activity_label"] = None
        affect["activity_started_at"] = None


def apply_fallback_interaction(snapshot: dict, user_text: str) -> dict:
    """无观察模型时的保守互动变化，不从一般负面技术词推断情绪。"""
    result = copy.deepcopy(snapshot)
    affect = result["affect"]
    relation = result["relationship"]
    text = user_text.strip()
    appreciated = any(hint in text for hint in APPRECIATION_HINTS)

    affect["contact_need"] = 0.03
    affect["immersion"] += min(0.20, 0.035 + len(text) / 1600)
    affect["arousal"] += min(0.06, len(text) / 4000)
    if appreciated:
        affect["guardedness_transient"] -= 0.02
        affect["valence"] += 0.04

    relation["interaction_count"] += 1
    relation["bond"] += 0.001 + (0.001 if appreciated else 0)
    if appreciated:
        relation["trust"] += 0.001
    return normalize(result)


def signals(snapshot: dict) -> list[dict]:
    affect = snapshot["affect"]
    c = affect["contact_need"]
    g = guardedness(snapshot)
    result: list[dict] = []
    if 0.30 <= c < 0.55:
        result.append({"action": "observation", "urgency": (c - 0.30) / 0.25})
    elif 0.55 <= c < 0.75:
        if g >= 0.62 and affect["immersion"] < 0.20:
            result.append({"action": "find_activity", "reason": "guardedness"})
        else:
            result.append({"action": "consider_contact", "urgency": (c - 0.55) / 0.20})
    elif c >= 0.75:
        result.append({"action": "contact", "urgency": min(1.0, (c - 0.70) / 0.30)})
    return result


def normalize(snapshot: dict) -> dict:
    affect = snapshot["affect"]
    relation = snapshot["relationship"]
    affect["contact_need"] = clamp(affect["contact_need"], 0, 1)
    affect["guardedness_transient"] = clamp(affect["guardedness_transient"], -0.25, 0.25)
    affect["valence"] = clamp(affect["valence"], -1, 1)
    affect["arousal"] = clamp(affect["arousal"], -1, 1)
    affect["immersion"] = clamp(affect["immersion"], 0, 1)
    relation["bond"] = clamp(relation["bond"], 0, 1)
    relation["trust"] = clamp(relation["trust"], 0, 1)
    relation["interaction_count"] = max(0, int(relation["interaction_count"]))
    return snapshot


def move_toward(value: float, target: float, distance: float) -> float:
    if value < target:
        return min(target, value + distance)
    if value > target:
        return max(target, value - distance)
    return value


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
