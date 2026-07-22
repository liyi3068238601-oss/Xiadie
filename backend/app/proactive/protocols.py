"""EAP v0.2 领域协议版本常量。

按 spec 第 6.5 节，EAP 注册 6 个领域协议。本模块只定义协议版本字符串，
具体 schema 和 run 账本由各子阶段实施时按需建立。

协议命名遵循现有约定：<domain>-<role>-v<N>
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

# Conversation Presence v2：用户在线状态、离开原因、open_thread
# 与 affect-observer-v1 的 user_status 4 值枚举互补，扩展为 8 值
CONVERSATION_PRESENCE_V2 = "conversation-presence-v2"

# User Affect Observation v1：用户情绪观察（区别于 affect-observer-v1 的遐蝶自身情绪）
USER_AFFECT_OBSERVATION_V1 = "user-affect-observation-v1"

# Relationship Meaning v1：关系意义标签（ordinary_exchange 等 9 种）
RELATIONSHIP_MEANING_V1 = "relationship-meaning-v1"

# Proactive Decision v2：主动决策建议（替代 v0.1 的线性总分）
PROACTIVE_DECISION_V2 = "proactive-decision-v2"

# Expression Plan v1：表达向量（warmth 等 7 维）与迟滞参数
EXPRESSION_PLAN_V1 = "expression-plan-v1"

# Proactive Feedback v1：用户反馈（少一点这种消息、别用这种语气等）
PROACTIVE_FEEDBACK_V1 = "proactive-feedback-v1"

ALL_PROTOCOLS = (
    CONVERSATION_PRESENCE_V2,
    USER_AFFECT_OBSERVATION_V1,
    RELATIONSHIP_MEANING_V1,
    PROACTIVE_DECISION_V2,
    EXPRESSION_PLAN_V1,
    PROACTIVE_FEEDBACK_V1,
)

class ProtocolStatus(str, Enum):
    IMPLEMENTED = "IMPLEMENTED"
    DRAFT = "DRAFT"
    PLACEHOLDER = "PLACEHOLDER"


@dataclass(frozen=True)
class ProtocolDefinition:
    name: str
    version: int
    status: ProtocolStatus
    validator: Optional[Callable]
    compatibility: str


def _validate_user_affect(*args, **kwargs):
    from .schemas import validate_user_affect
    return validate_user_affect(*args, **kwargs)


def _validate_feedback(*args, **kwargs):
    from .schemas import validate_proactive_feedback
    return validate_proactive_feedback(*args, **kwargs)


def _validate_relationship(*args, **kwargs):
    from .schemas import validate_relationship_meaning
    return validate_relationship_meaning(*args, **kwargs)


PROTOCOL_REGISTRY = {
    CONVERSATION_PRESENCE_V2: ProtocolDefinition(
        CONVERSATION_PRESENCE_V2, 2, ProtocolStatus.IMPLEMENTED, None,
        "Frozen eight-value user_status contract; incompatible changes require v3.",
    ),
    USER_AFFECT_OBSERVATION_V1: ProtocolDefinition(
        USER_AFFECT_OBSERVATION_V1, 1, ProtocolStatus.IMPLEMENTED, _validate_user_affect,
        "Grounded schema and Companion Cognition result repository are active since EAP.R2.",
    ),
    RELATIONSHIP_MEANING_V1: ProtocolDefinition(
        RELATIONSHIP_MEANING_V1, 1, ProtocolStatus.IMPLEMENTED, _validate_relationship,
        "Grounded validator and atomic apply/revoke repository are active since EAP.R2.",
    ),
    PROACTIVE_DECISION_V2: ProtocolDefinition(
        PROACTIVE_DECISION_V2, 2, ProtocolStatus.IMPLEMENTED, None,
        "Existing decision records remain authoritative; DecisionRun is an adapter target.",
    ),
    EXPRESSION_PLAN_V1: ProtocolDefinition(
        EXPRESSION_PLAN_V1, 1, ProtocolStatus.DRAFT, None,
        "No executable validator or repository until EAP.R4.",
    ),
    PROACTIVE_FEEDBACK_V1: ProtocolDefinition(
        PROACTIVE_FEEDBACK_V1, 1, ProtocolStatus.IMPLEMENTED, _validate_feedback,
        "Delivered feedback is persisted and grounded to one local delivery in EAP.R5.",
    ),
}


def get_protocol(name: str) -> ProtocolDefinition:
    try:
        return PROTOCOL_REGISTRY[name]
    except KeyError as exc:
        raise ValueError(f"unknown protocol: {name}") from exc
