"""EAP v0.2 领域协议版本常量测试。"""
from app.proactive import protocols


def test_six_protocols_defined():
    """spec 第 6.5 节要求注册 6 个领域协议。"""
    assert len(protocols.ALL_PROTOCOLS) == 6


def test_protocol_naming_convention():
    """所有协议遵循 <domain>-<role>-v<N> 命名约定。"""
    for p in protocols.ALL_PROTOCOLS:
        # 至少包含一个 "-v" 版本后缀
        assert "-v" in p, f"协议 {p} 缺少版本后缀"
        assert p.endswith(("-v1", "-v2")), f"协议 {p} 版本后缀应为 -v1 或 -v2"


def test_protocol_values_match_spec():
    """协议版本字符串与 spec 第 6.5 节一致。"""
    assert protocols.CONVERSATION_PRESENCE_V2 == "conversation-presence-v2"
    assert protocols.USER_AFFECT_OBSERVATION_V1 == "user-affect-observation-v1"
    assert protocols.RELATIONSHIP_MEANING_V1 == "relationship-meaning-v1"
    assert protocols.PROACTIVE_DECISION_V2 == "proactive-decision-v2"
    assert protocols.EXPRESSION_PLAN_V1 == "expression-plan-v1"
    assert protocols.PROACTIVE_FEEDBACK_V1 == "proactive-feedback-v1"


def test_no_duplicate_protocols():
    """6 个协议互不重复。"""
    assert len(set(protocols.ALL_PROTOCOLS)) == 6


def test_registry_covers_all_protocols_and_exposes_lifecycle_metadata():
    assert set(protocols.PROTOCOL_REGISTRY) == set(protocols.ALL_PROTOCOLS)
    for name in protocols.ALL_PROTOCOLS:
        definition = protocols.get_protocol(name)
        assert definition.name == name
        assert definition.version in {1, 2}
        assert definition.status in protocols.ProtocolStatus
        assert definition.compatibility


def test_registry_tracks_implemented_runtime_and_feedback_protocols():
    affect = protocols.get_protocol(protocols.USER_AFFECT_OBSERVATION_V1)
    relationship = protocols.get_protocol(protocols.RELATIONSHIP_MEANING_V1)
    feedback = protocols.get_protocol(protocols.PROACTIVE_FEEDBACK_V1)
    expression = protocols.get_protocol(protocols.EXPRESSION_PLAN_V1)
    assert affect.status is protocols.ProtocolStatus.IMPLEMENTED
    assert relationship.status is protocols.ProtocolStatus.IMPLEMENTED
    assert feedback.status is protocols.ProtocolStatus.IMPLEMENTED
    assert expression.status is protocols.ProtocolStatus.IMPLEMENTED
    assert callable(affect.validator)
    assert callable(relationship.validator)
    assert callable(feedback.validator)


def test_protocols_do_not_collide_with_existing():
    """EAP 协议不与现有 13 个协议冲突。"""
    existing = {
        "context-budget-v1",
        "conversation-summary-v1",
        "context-package-v1",
        "affect-observer-v1",
        "memory-observer-v1",
        "episode-summary-v1",
        "saga-summary-v1",
        "knowledge-recall-decision-v1",
        "knowledge-transmission-grant-v1",
        "knowledge-search-v2",
        "knowledge-recall-eval-v3",
        "knowledge-recall-report-v1",
    }
    for p in protocols.ALL_PROTOCOLS:
        assert p not in existing, f"EAP 协议 {p} 与现有协议冲突"
