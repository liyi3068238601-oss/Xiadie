"""EAP v0.2 领域协议版本常量。

按 spec 第 6.5 节，EAP 注册 6 个领域协议。本模块只定义协议版本字符串，
具体 schema 和 run 账本由各子阶段实施时按需建立。

协议命名遵循现有约定：<domain>-<role>-v<N>
"""

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
