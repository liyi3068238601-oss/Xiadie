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


def describe(snapshot: dict) -> dict:
    affect = snapshot["affect"]
    cluster = engine.emotion_cluster(affect["valence"], affect["arousal"])
    actual_guardedness = engine.guardedness(snapshot)
    baseline = engine.guardedness_baseline(snapshot["relationship"]["trust"])
    return {
        "cluster": cluster,
        "label": CLUSTER_LABELS[cluster],
        "guardedness": actual_guardedness,
        "guardedness_baseline": baseline,
        "style_guidance": style_guidance(snapshot, cluster, actual_guardedness),
    }


def style_guidance(snapshot: dict, cluster: str | None = None, guardedness: float | None = None) -> str:
    affect = snapshot["affect"]
    relation = snapshot["relationship"]
    cluster = cluster or engine.emotion_cluster(affect["valence"], affect["arousal"])
    guardedness = engine.guardedness(snapshot) if guardedness is None else guardedness
    rules = [CLUSTER_GUIDANCE[cluster]]

    if guardedness >= 0.78:
        rules.append("保护性距离很强，避免袒露依赖，但仍保持尊重和关心")
    elif guardedness >= 0.62:
        rules.append("比平时更克制，关心可以表达，但把过于直白的话轻轻收住")
    elif guardedness < 0.45:
        rules.append("可以自然流露一点留恋或小小私心，但不要跳到未经建立的亲密关系")

    if relation["bond"] < 0.25:
        rules.append("关系仍在建立，不预设称呼、承诺或亲密身份")
    elif relation["bond"] > 0.65 and relation["trust"] > 0.60:
        rules.append("可以自然延续共同语境，不必反复说明彼此已经熟悉")
    return "；".join(rules)
