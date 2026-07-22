import pytest

from app.proactive import schemas
from app.proactive.protocols import PROACTIVE_FEEDBACK_V1, USER_AFFECT_OBSERVATION_V1


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
