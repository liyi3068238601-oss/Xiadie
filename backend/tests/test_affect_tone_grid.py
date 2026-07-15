import json
from pathlib import Path

import pytest

from app.affect import engine, tone_grid
from app.persona import build_system_prompt


FIXTURES = Path(__file__).parent / "fixtures" / "affect_persona_dialogues.json"


def snapshot(
    *,
    valence: float = 0.0,
    arousal: float = 0.0,
    guardedness: float = 0.58,
    contact_need: float = 0.1,
    bond: float = 0.4,
    trust: float = 0.5,
) -> dict:
    baseline = engine.guardedness_baseline(trust)
    return {
        "affect": {
            **engine.DEFAULT_AFFECT,
            "valence": valence,
            "arousal": arousal,
            "contact_need": contact_need,
            "guardedness_transient": guardedness - baseline,
        },
        "relationship": {
            **engine.DEFAULT_RELATIONSHIP,
            "bond": bond,
            "trust": trust,
        },
    }


@pytest.mark.parametrize(
    ("valence", "arousal", "expected"),
    [
        (0.7, 0.6, "bright"),
        (0.7, -0.6, "serene"),
        (0.7, 0.0, "pleased"),
        (-0.7, 0.6, "agitated"),
        (-0.7, -0.6, "melancholic"),
        (-0.7, 0.0, "subdued"),
        (0.0, 0.6, "focused"),
        (0.0, -0.6, "contemplative"),
        (0.0, 0.0, "neutral"),
    ],
)
def test_all_nine_emotion_clusters(valence, arousal, expected):
    assert engine.emotion_cluster(valence, arousal) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, "relaxed"),
        (0.4499, "relaxed"),
        (0.45, "softly_guarded"),
        (0.5399, "softly_guarded"),
        (0.54, "default_distance"),
        (0.6199, "default_distance"),
        (0.62, "highly_guarded"),
        (0.7799, "highly_guarded"),
        (0.78, "defensive"),
        (1.0, "defensive"),
    ],
)
def test_all_five_guardedness_bands_and_boundaries(value, expected):
    assert tone_grid.guardedness_band(value)[0] == expected


def test_nine_by_five_grid_is_complete_and_has_safe_distance_guidance():
    for cluster in tone_grid.CLUSTER_GUIDANCE:
        for guardedness in (0.2, 0.48, 0.58, 0.70, 0.90):
            guidance = tone_grid.style_guidance(
                snapshot(guardedness=guardedness),
                cluster=cluster,
                guardedness=guardedness,
            )
            assert tone_grid.CLUSTER_GUIDANCE[cluster] in guidance
            assert len(guidance.split("；")) >= 2


def test_unknown_cluster_falls_back_to_neutral_instead_of_raising():
    guidance = tone_grid.style_guidance(snapshot(), cluster="future_cluster")
    assert tone_grid.CLUSTER_GUIDANCE["neutral"] in guidance


def test_contact_need_is_an_additive_non_coercive_layer():
    moderate = tone_grid.style_guidance(snapshot(contact_need=0.55))
    high = tone_grid.style_guidance(snapshot(contact_need=0.75))
    assert "等待、留恋或继续衔接" in moderate
    assert "不催促用户回应" in moderate
    assert "重逢或继续相处很重要" in high
    assert "不制造愧疚" in high
    assert "不要求用户负责" in high


def test_relationship_boundaries_are_only_added_at_clear_thresholds():
    new_relation = tone_grid.style_guidance(snapshot(bond=0.24))
    established = tone_grid.style_guidance(snapshot(bond=0.66, trust=0.61))
    middle = tone_grid.style_guidance(snapshot(bond=0.5, trust=0.5))
    assert "不预设称呼、承诺或亲密身份" in new_relation
    assert "自然延续共同语境" in established
    assert "关系仍在建立" not in middle
    assert "自然延续共同语境" not in middle


def test_describe_exposes_complete_derived_contract():
    result = tone_grid.describe(snapshot(guardedness=0.58))
    assert result["cluster"] == "neutral"
    assert result["label"] == "平和"
    assert result["guardedness_band"] == "default_distance"
    assert 0 <= result["guardedness"] <= 1
    assert 0 <= result["guardedness_baseline"] <= 1
    assert result["style_guidance"]


def test_fixed_dialogue_set_preserves_persona_and_negative_state_boundaries():
    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert {case["cluster"] for case in cases} == set(tone_grid.CLUSTER_GUIDANCE)

    for case in cases:
        state = snapshot(valence=case["valence"], arousal=case["arousal"])
        described = tone_grid.describe(state)
        prompt = build_system_prompt("", described["style_guidance"])
        assert described["cluster"] == case["cluster"], case["name"]
        assert case["required"] in prompt, case["name"]
        assert "你就是遐蝶本人" in prompt
        assert "不要用制造愧疚、惩罚沉默、威胁离开等方式索取关注" in prompt
        assert "只调整语气，不改变事实、人格核心、安全边界或工具权限" in prompt
