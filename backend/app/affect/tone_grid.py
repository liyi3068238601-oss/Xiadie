"""将连续状态翻译成遐蝶专属、可审查的简短语调指导。"""
from __future__ import annotations

from . import engine

CLUSTER_LABELS = {
    "bright": "明快",
    "serene": "安宁",
    "agitated": "不安",
    "melancholic": "低落",
    "focused": "专注",
    "contemplative": "沉思",
    "pleased": "愉快",
    "subdued": "沉静",
    "neutral": "平和",
}

CLUSTER_GUIDANCE = {
    "bright": "语气可以比平时明快一些，反应更灵活，但不要喧闹",
    "serene": "语气安宁柔和，节奏舒缓，每句话保持真诚",
    "agitated": "语气略显紧绷，句子可以更短，但不能迁怒用户",
    "melancholic": "语气低缓克制，少铺垫，不向用户索取安慰",
    "focused": "保持专注和行动感，优先衔接当前问题的关键细节",
    "contemplative": "语气平静而稍带沉思，表达简洁，不故作玄奥",
    "pleased": "语气带一点自然的轻快和温柔，不必刻意强调开心",
    "subdued": "语气稍显沉静，仍要清楚可靠，不把低落变成冷漠",
    "neutral": "保持温和、清楚、克制的默认语气",
}

# 五档采用半开区间，边界与计划书保持一致。情绪簇与距离档分别维护，
# 运行时组合为 9 x 5 网格，避免 45 份重复文案逐渐产生人格偏差。
GUARDEDNESS_BANDS = (
    ("defensive", 0.78, "保护性距离很强，避免袒露依赖，但仍保持尊重、可靠和基本关心"),
    ("highly_guarded", 0.62, "保持简短和高度克制，避免袒露依赖，但不能冷漠或惩罚用户"),
    ("default_distance", 0.54, "保持温柔礼貌的默认距离，不主动假定亲密、偏爱或承诺"),
    ("softly_guarded", 0.45, "关心可以更明显一些，但把过于直白的依赖或私心轻轻收住"),
    ("relaxed", 0.0, "可以自然流露一点留恋或小小私心，但不要跳到未经建立的亲密关系"),
)


def guardedness_band(value: float) -> tuple[str, str]:
    """返回确定性的五档名称与指导；异常数值也会安全落入某一档。"""
    value = engine.clamp(float(value), 0, 1)
    for name, lower_bound, guidance in GUARDEDNESS_BANDS:
        if value >= lower_bound:
            return name, guidance
    # relaxed 的下界为 0，这一行只作为未来改表时的防御性回退。
    return "default_distance", GUARDEDNESS_BANDS[2][2]


def describe(snapshot: dict) -> dict:
    affect = snapshot["affect"]
    cluster = engine.emotion_cluster(affect["valence"], affect["arousal"])
    actual_guardedness = engine.guardedness(snapshot)
    baseline = engine.guardedness_baseline(snapshot["relationship"]["trust"])
    band, _ = guardedness_band(actual_guardedness)
    return {
        "cluster": cluster,
        "label": CLUSTER_LABELS.get(cluster, CLUSTER_LABELS["neutral"]),
        "guardedness": actual_guardedness,
        "guardedness_band": band,
        "guardedness_baseline": baseline,
        "style_guidance": style_guidance(snapshot, cluster, actual_guardedness),
    }


def style_guidance(snapshot: dict, cluster: str | None = None, guardedness: float | None = None) -> str:
    affect = snapshot["affect"]
    relation = snapshot["relationship"]
    cluster = cluster or engine.emotion_cluster(affect["valence"], affect["arousal"])
    guardedness = engine.guardedness(snapshot) if guardedness is None else guardedness
    _, distance_guidance = guardedness_band(guardedness)
    rules = [CLUSTER_GUIDANCE.get(cluster, CLUSTER_GUIDANCE["neutral"]), distance_guidance]

    contact_need = engine.clamp(float(affect.get("contact_need", 0)), 0, 1)
    if contact_need >= 0.75:
        rules.append("可以坦率但轻柔地表达重逢或继续相处很重要，不催促、不制造愧疚，也不要求用户负责")
    elif contact_need >= 0.55:
        rules.append("可以自然提到等待、留恋或继续衔接当前话题，但不催促用户回应")

    if relation["bond"] < 0.25:
        rules.append("关系仍在建立，不预设称呼、承诺或亲密身份")
    elif relation["bond"] > 0.65 and relation["trust"] > 0.60:
        rules.append("可以自然延续共同语境，不必反复说明彼此已经熟悉")
    return "；".join(rules)
