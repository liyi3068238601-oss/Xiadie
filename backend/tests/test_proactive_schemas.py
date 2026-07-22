import pytest

from app.proactive import schemas
from app.proactive.protocols import (
    PROACTIVE_FEEDBACK_V1, RELATIONSHIP_MEANING_V1, USER_AFFECT_OBSERVATION_V1,
)


def test_user_affect_schema_accepts_grounded_user_evidence():
    result = schemas.validate_user_affect({
        "protocol_version": USER_AFFECT_OBSERVATION_V1,
        "state": "frustrated",
        "needs": ["listen"],
        "evidence": [{"quote": "这件事让我很烦"}],
        "confidence": 0.9,
        "reason": "explicit wording",
    }, user_text="今天这件事让我很烦")
    assert result.state == "frustrated"


def test_user_affect_schema_rejects_extra_fields_and_invented_evidence():
    payload = {
        "protocol_version": USER_AFFECT_OBSERVATION_V1,
        "state": "low", "needs": [], "evidence": [{"quote": "并不存在"}],
        "confidence": 0.8, "reason": "test",
    }
    with pytest.raises(schemas.ProtocolValidationError) as exc:
        schemas.validate_user_affect(payload, user_text="原始消息")
    assert exc.value.code == "evidence_not_found"
    payload["diagnosis"] = "depression"
    with pytest.raises(schemas.ProtocolValidationError) as exc:
        schemas.validate_user_affect(payload, user_text="并不存在")
    assert exc.value.code == "schema_invalid"


def test_feedback_schema_requires_delivery_and_user_quote():
    result = schemas.validate_proactive_feedback({
        "protocol_version": PROACTIVE_FEEDBACK_V1,
        "feedback_kind": "too_frequent",
        "delivery_id": "delivery-1",
        "evidence": [{"quote": "少发一点"}],
        "target_topic": None, "target_kind": "casual_greeting", "confidence": 1.0,
    }, user_text="以后少发一点")
    assert result.feedback_kind == "too_frequent"


def test_schema_error_never_contains_raw_user_text():
    secret = "private-user-text-123"
    with pytest.raises(schemas.ProtocolValidationError) as exc:
        schemas.validate_proactive_feedback("not-json", user_text=secret)
    assert secret not in str(exc.value)


@pytest.mark.parametrize("label,evidence,user_text,assistant_text", [
    ("ordinary_exchange", [], "天气如何", "今天晴朗"),
    ("shared_appreciation", [{"speaker": "user", "quote": "谢谢你"}], "谢谢你", "不客气"),
    ("reliable_help", [{"speaker": "assistant", "quote": "问题已经修好"}], "解决了吗", "问题已经修好"),
    ("shared_success", [{"speaker": "user", "quote": "我们成功了"}], "我们成功了", "太好了"),
    ("vulnerable_disclosure", [{"speaker": "user", "quote": "我有点害怕"}], "我有点害怕", "我在"),
    ("boundary_respected", [{"speaker": "assistant", "quote": "我会尊重你的边界"}], "请停下", "我会尊重你的边界"),
    ("boundary_repair", [{"speaker": "assistant", "quote": "对不起，我会改正"}], "刚才让我不舒服", "对不起，我会改正"),
    ("reunion", [{"speaker": "user", "quote": "我回来了"}], "我回来了", "欢迎回来"),
    ("conflict", [{"speaker": "user", "quote": "你越界了"}], "你越界了", "对不起"),
])
def test_all_nine_relationship_meanings_are_grounded(
    label, evidence, user_text, assistant_text,
):
    result = schemas.validate_relationship_meaning({
        "protocol_version": RELATIONSHIP_MEANING_V1, "label": label,
        "evidence": evidence, "confidence": 0.9, "reason": "test",
    }, user_text=user_text, assistant_text=assistant_text)
    assert result.label == label


def test_negative_relationship_change_requires_explicit_user_boundary_evidence():
    with pytest.raises(schemas.ProtocolValidationError) as exc:
        schemas.validate_relationship_meaning({
            "protocol_version": RELATIONSHIP_MEANING_V1, "label": "conflict",
            "evidence": [{"speaker": "assistant", "quote": "发生冲突"}],
            "confidence": 0.9, "reason": "model claim",
        }, user_text="普通消息", assistant_text="发生冲突")
    assert exc.value.code == "conflict_without_boundary_evidence"
