"""EAP v0.2 Proactive Decision v2：三层硬门 + LLM 结构化建议 + Shadow 基线。

按 spec 第 6.1 节三层硬门、第 6.3 节 5 步决策流程、第 6.5 节 LLM 职责边界、
第 5.8 节接近意愿与打扰负担模型实现。

**Shadow 模式**：本阶段决策算法和 Shadow 基线并行运行，但不真实发送消息；
decide_candidate 函数只是审计落库，不调用 main.py 发送消息。

关键约束：
- 关闭主动陪伴（settings.proactive_enabled != '1'）时，第一层硬门 PROACTIVE_DISABLED 命中，
  decision=SUPPRESS，无论 LLM 建议如何 → 0 次发送
- 第一层硬门 blocked 时 decision=SUPPRESS，LLM 无权放行（spec 第 6.1 节"LLM 无权放行"）
"""

import json
import time
from dataclasses import dataclass, field
from typing import Optional

from .. import db
from .candidates import (
    CandidateStatus,
    ProactiveCandidate,
    is_candidate_valid,
)
from .presence import (
    PresenceRecord,
    UserStatus,
    get_current_presence,
    should_block_proactive,
)
from .protocols import PROACTIVE_DECISION_V2
from .run_ledger import make_idempotency_key


# 决策动作（spec 第 6.3 节）
class DecisionAction:
    SEND = "send"            # 发送
    DEFER = "defer"          # 延后
    SUPPRESS = "suppress"    # 抑制
    ABANDON = "abandon"      # 放弃


# 6 种表达行为（spec 第 3.2 节）
class ExpressionAct:
    PLAYFUL_COMPLAINT = "playful_complaint"      # 轻微埋怨
    GENTLE_URGE = "gentle_urge"                  # 温柔催促
    FIRM_CARE = "firm_care"                      # 坚定关怀
    WORRIED_CHECKIN = "worried_checkin"          # 担心问候
    EXPECTANT_FOLLOWUP = "expectant_followup"    # 期待跟进
    QUIET_WAITING = "quiet_waiting"              # 安静等待


# 第一层硬边界原因（spec 第 6.1 节，7 项）
class Layer1BlockReason:
    PROACTIVE_DISABLED = "proactive_disabled"             # 用户关闭主动陪伴
    TOPIC_REJECTED = "topic_rejected"                     # 用户明确拒绝该话题
    KIND_REJECTED = "kind_rejected"                       # 用户明确拒绝该类型
    CHANNEL_UNAUTHORIZED = "channel_unauthorized"         # 渠道未授权
    SOURCE_INVALIDATED = "source_invalidated"             # 来源消息已删除/撤销
    ALREADY_DELIVERED = "already_delivered"               # 相同候选已投递
    EMERGENCY_STOP = "emergency_stop"                     # 应用急停/不可打断


# 第二层延后原因（spec 第 6.1 节，7 项）
class Layer2DeferReason:
    USER_BUSY = "user_busy"                       # 用户忙碌
    USER_RETURN_LATER = "user_return_later"       # 用户稍后回来
    USER_SLEEPING = "user_sleeping"               # 用户睡觉
    QUIET_HOURS = "quiet_hours"                   # 安静时段
    USER_DND = "user_dnd"                         # 勿扰
    CONVERSATION_ENDED = "conversation_ended"     # 聊天自然结束
    TIMING_NOT_RIGHT = "timing_not_right"         # 时机不合适


# 第三层动态因素（spec 第 6.1 节，6 项）
class Layer3Factor:
    TODAY_ALREADY_PROACTIVE = "today_already_proactive"     # 当天已主动过
    LAST_24H_COUNT = "last_24h_count"                       # 24 小时主动次数
    PREVIOUS_UNANSWERED = "previous_unanswered"             # 前一条主动未回复
    CONSECUTIVE_IGNORED = "consecutive_ignored"             # 连续忽略次数
    SAME_KIND_COOLDOWN = "same_kind_cooldown"               # 同类型冷却中
    TIME_SINCE_LAST_PROACTIVE = "time_since_last_proactive"  # 距上次主动时间


# 默认安静时段（24h 制）
DEFAULT_QUIET_HOURS_START = 23  # 23 点开始安静
DEFAULT_QUIET_HOURS_END = 9     # 9 点结束安静


# v0.1 旧线性公式 Shadow 基线权重（spec 第 6.3 节）
SHADOW_FORMULA_WEIGHTS = {
    "evidence_strength": 0.25,
    "open_thread_relevance": 0.20,
    "emotional_resonance": 0.15,
    "relationship_fit": 0.15,
    "contact_need_fit": 0.10,
    "timing_score": 0.10,
    "kind_priority": 0.05,
}


# 第三层因素权重的简化映射（candidate_kind → 基础 approach_drive 分）
KIND_BASE_DRIVE = {
    "emotional_care": 0.7,
    "return_followup": 0.65,
    "milestone_followup": 0.7,
    "chat_continuation": 0.5,
    "casual_greeting": 0.3,
    "life_share": 0.6,
}


# 同类型冷却窗口（秒）：相同 candidate_kind 在该窗口内视为冷却中
SAME_KIND_COOLDOWN_SECONDS = 6 * 3600  # 6 小时


@dataclass
class LLMAdvice:
    """LLM 结构化建议（proactive-decision-v2 schema）。"""
    decision: str                  # send/defer/suppress/abandon
    intensity: Optional[int]       # 0-5
    expression_act: Optional[str]
    topic: Optional[str]
    confidence: float
    reason_codes: list
    source_refs: list


@dataclass
class Layer1Result:
    blocked: bool
    reasons: list  # Layer1BlockReason 字符串列表


@dataclass
class Layer2Result:
    deferred: bool
    reasons: list  # Layer2DeferReason 字符串列表
    next_available_window: Optional[float]


@dataclass
class Layer3Factors:
    factors: dict  # Layer3Factor 字符串 → 数值


@dataclass
class DriveAssessment:
    approach_drive: float       # 0.0~1.0
    contact_cost: float         # 0.0~1.0
    effective_drive: float
    approach_value: float
    shadow_score: float         # 旧线性公式 Shadow 基线分数


@dataclass
class ProactiveDecision:
    """proactive_decisions 表的完整记录。"""
    id: str
    candidate_id: str
    session_id: str
    decision: str
    intensity: Optional[int]
    expression_act: Optional[str]
    topic: Optional[str]
    confidence: float
    reason_codes: list
    source_refs: list
    layer1_blocked: bool
    layer1_block_reasons: list
    layer2_deferred: bool
    layer2_defer_reasons: list
    layer3_factors: dict
    approach_drive: float
    contact_cost: float
    effective_drive: float
    approach_value: float
    shadow_score: Optional[float]
    is_shadow: bool
    llm_raw_response: Optional[str]
    idempotency_key: str
    protocol_version: str
    created_at: float


def _clamp(value: float, low: float, high: float) -> float:
    """限制 value 在 [low, high] 范围内。"""
    return max(low, min(high, value))


def _is_in_quiet_hours(
    now_ts: float,
    quiet_start: int,
    quiet_end: int,
) -> bool:
    """判断当前小时是否在安静时段 [quiet_start, quiet_end) 范围内。

    支持跨午夜：如 quiet_start=23, quiet_end=9 表示 23:00~次日 9:00。
    """
    # 使用本地时间小时（与 presence 检测保持一致）
    local_hour = time.localtime(now_ts).tm_hour
    if quiet_start <= quiet_end:
        # 不跨午夜（如 13~17）
        return quiet_start <= local_hour < quiet_end
    # 跨午夜（如 23~9）
    return local_hour >= quiet_start or local_hour < quiet_end


def _compute_next_quiet_window_end(
    now_ts: float,
    quiet_end: int,
) -> float:
    """计算今天 quiet_end 点的时间戳（如已过则返回明天的）。"""
    local = time.localtime(now_ts)
    # 今天 quiet_end 点的时间戳
    today_end = time.mktime((
        local.tm_year, local.tm_mon, local.tm_mday,
        quiet_end, 0, 0, 0, 0, -1,
    ))
    if today_end > now_ts:
        return today_end
    # 已过今天的 quiet_end，返回明天的
    return today_end + 24 * 3600


def check_layer1_hard_boundary(
    candidate: ProactiveCandidate,
    *,
    now: Optional[float] = None,
    settings: Optional[dict] = None,
    presence: Optional[PresenceRecord] = None,
    recent_decisions: Optional[list] = None,
) -> Layer1Result:
    """第一层硬边界检查（spec 第 6.1 节）。

    检查项：
    - PROACTIVE_DISABLED：settings['proactive_enabled'] != '1'
    - TOPIC_REJECTED：用户对该 topic 明确拒绝（settings 中查询，本阶段简化为 settings 检查）
    - KIND_REJECTED：用户对该 candidate_kind 明确拒绝
    - CHANNEL_UNAUTHORIZED：默认主窗口已授权（proactive_enabled=1 时）；其他渠道单独检查
    - SOURCE_INVALIDATED：source_hash 与现有 candidate 重复且已 delivered
    - ALREADY_DELIVERED：相同 source_hash 已有 delivered 决策
    - EMERGENCY_STOP：settings['proactive_emergency_stop'] == '1'
    """
    now = now if now is not None else db.now()

    # 加载 settings
    if settings is None:
        settings = {}
        settings["proactive_enabled"] = db.get_setting("proactive_enabled", "1")
        settings["proactive_emergency_stop"] = db.get_setting(
            "proactive_emergency_stop", "0"
        )
        settings["proactive_rejected_topics"] = db.get_setting(
            "proactive_rejected_topics", ""
        )
        settings["proactive_rejected_kinds"] = db.get_setting(
            "proactive_rejected_kinds", ""
        )

    reasons = []

    # PROACTIVE_DISABLED：用户关闭主动陪伴
    if settings.get("proactive_enabled", "1") != "1":
        reasons.append(Layer1BlockReason.PROACTIVE_DISABLED)

    # EMERGENCY_STOP：应用急停/不可打断
    if settings.get("proactive_emergency_stop", "0") == "1":
        reasons.append(Layer1BlockReason.EMERGENCY_STOP)

    # TOPIC_REJECTED：用户明确拒绝该话题（settings 中以逗号分隔存储）
    rejected_topics_str = settings.get("proactive_rejected_topics", "")
    if rejected_topics_str:
        rejected_topics = [t.strip() for t in rejected_topics_str.split(",") if t.strip()]
        if candidate.topic in rejected_topics:
            reasons.append(Layer1BlockReason.TOPIC_REJECTED)

    # KIND_REJECTED：用户明确拒绝该 candidate_kind
    rejected_kinds_str = settings.get("proactive_rejected_kinds", "")
    if rejected_kinds_str:
        rejected_kinds = [k.strip() for k in rejected_kinds_str.split(",") if k.strip()]
        if candidate.candidate_kind in rejected_kinds:
            reasons.append(Layer1BlockReason.KIND_REJECTED)

    # CHANNEL_UNAUTHORIZED：主窗口授权逻辑（proactive_enabled=1 时主窗口已授权）
    # 本阶段简化：如 proactive_enabled=0 已在上面命中 PROACTIVE_DISABLED；
    # 其他渠道（桌面通知、外部渠道）的授权检查留给后续阶段（EAP.G）
    # 此处不强制添加 CHANNEL_UNAUTHORIZED

    # ALREADY_DELIVERED / SOURCE_INVALIDATED：
    # 相同 source_hash 的候选已投递（状态为 delivered）
    if candidate.source_hash:
        conn = db.connect()
        try:
            # 查询是否有相同 source_hash 且状态为 delivered 的其他候选
            row = conn.execute(
                "SELECT id FROM proactive_candidates "
                "WHERE source_hash = ? AND status = ? "
                "AND id != ? "
                "LIMIT 1",
                (candidate.source_hash, CandidateStatus.DELIVERED, candidate.id),
            ).fetchone()
            if row:
                reasons.append(Layer1BlockReason.ALREADY_DELIVERED)
        finally:
            conn.close()

    return Layer1Result(blocked=len(reasons) > 0, reasons=reasons)


def check_layer2_defer_conditions(
    candidate: ProactiveCandidate,
    *,
    now: Optional[float] = None,
    presence: Optional[PresenceRecord] = None,
    quiet_hours_start: int = DEFAULT_QUIET_HOURS_START,
    quiet_hours_end: int = DEFAULT_QUIET_HOURS_END,
) -> Layer2Result:
    """第二层延后条件检查（spec 第 6.1 节）。

    检查项：
    - USER_BUSY：presence.user_status == away_busy
    - USER_RETURN_LATER：presence.user_status == away_brief 且未到 expected_return_at
    - USER_SLEEPING：presence.user_status == away_sleep
    - USER_DND：presence.user_status == do_not_disturb
    - CONVERSATION_ENDED：presence.user_status == ended_conversation
    - QUIET_HOURS：当前小时在 [quiet_hours_start, quiet_hours_end) 范围（支持跨午夜）
    - TIMING_NOT_RIGHT：本阶段简化，不命中
    """
    now = now if now is not None else db.now()

    # 加载 presence
    if presence is None:
        presence = get_current_presence(candidate.session_id)

    reasons = []
    next_window = None

    if presence and presence.is_active:
        status = presence.user_status
        if status == UserStatus.AWAY_BUSY:
            reasons.append(Layer2DeferReason.USER_BUSY)
            # next_window: presence.expires_at 或 now + 2h
            next_window = presence.expires_at or (now + 2 * 3600)
        elif status == UserStatus.AWAY_BRIEF:
            # 用户短暂离开，未到 expected_return_at 时延后
            if presence.expected_return_at and presence.expected_return_at > now:
                reasons.append(Layer2DeferReason.USER_RETURN_LATER)
                next_window = presence.expected_return_at
        elif status == UserStatus.AWAY_SLEEP:
            reasons.append(Layer2DeferReason.USER_SLEEPING)
            # next_window: presence.expires_at 或 now + 8h
            next_window = presence.expires_at or (now + 8 * 3600)
        elif status == UserStatus.DO_NOT_DISTURB:
            reasons.append(Layer2DeferReason.USER_DND)
        elif status == UserStatus.ENDED_CONVERSATION:
            reasons.append(Layer2DeferReason.CONVERSATION_ENDED)
        elif status == UserStatus.AWAY_EXTENDED:
            # 长时间离开按 USER_RETURN_LATER 处理
            if presence.expected_return_at and presence.expected_return_at > now:
                reasons.append(Layer2DeferReason.USER_RETURN_LATER)
                next_window = presence.expected_return_at

    # QUIET_HOURS：当前小时在安静时段范围内
    if _is_in_quiet_hours(now, quiet_hours_start, quiet_hours_end):
        reasons.append(Layer2DeferReason.QUIET_HOURS)
        if next_window is None:
            next_window = _compute_next_quiet_window_end(now, quiet_hours_end)

    return Layer2Result(
        deferred=len(reasons) > 0,
        reasons=reasons,
        next_available_window=next_window,
    )


def compute_layer3_factors(
    candidate: ProactiveCandidate,
    *,
    now: Optional[float] = None,
    recent_decisions: Optional[list] = None,
    episode=None,
) -> Layer3Factors:
    """第三层动态考虑因素（spec 第 6.1 节）。

    计算项（不阻断，只影响 cost）：
    - TODAY_ALREADY_PROACTIVE: 0/1
    - LAST_24H_COUNT: 整数
    - PREVIOUS_UNANSWERED: 0/1（前一条主动尚未回复）
    - CONSECUTIVE_IGNORED: 整数
    - SAME_KIND_COOLDOWN: 0/1（同类型冷却中）
    - TIME_SINCE_LAST_PROACTIVE: 秒数
    """
    now = now if now is not None else db.now()

    if recent_decisions is None:
        # 默认查最近 24h 的决策
        since = now - 24 * 3600
        recent_decisions = list_recent_decisions(since=since, limit=200)

    # 过滤掉非 send 决策，只统计实际"发送"决策
    sent_decisions = [d for d in recent_decisions if d.decision == DecisionAction.SEND]
    last_24h_count = len(sent_decisions)

    # TODAY_ALREADY_PROACTIVE：今天是否已有 send 决策
    local_today = time.localtime(now).tm_mday
    today_already = 0
    for d in sent_decisions:
        if time.localtime(d.created_at).tm_mday == local_today:
            today_already = 1
            break

    # TIME_SINCE_LAST_PROACTIVE：距上次主动的时间（秒）
    if sent_decisions:
        last_created = max(d.created_at for d in sent_decisions)
        time_since = max(0.0, now - last_created)
    else:
        time_since = -1.0  # 表示从未主动过

    # PREVIOUS_UNANSWERED：前一条主动尚未回复（episode 存在且未 responded 时为 1）
    previous_unanswered = 0
    if episode is not None:
        # EpisodeStatus.RESPONDED 是终态之一，未达到即视为未回复
        if hasattr(episode, "status") and episode.status not in (
            "responded", "closed", "expired", "cancelled", "blocked",
        ):
            previous_unanswered = 1
    else:
        # 没有 episode 时，使用 sent_decisions 中最新一条的 candidate 是否已 delivered 来判断
        # 简化：如有 sent_decisions 且最新 candidate 未 delivered（即未投递成功/未回复），记为 1
        if sent_decisions:
            # 已有的 sent_decisions 假设若无 delivered 标记则视为 unanswered
            # 此处不查库（避免循环），交由调用方传入 episode 时精确判断
            previous_unanswered = 0  # 默认不标记，避免误判

    # CONSECUTIVE_IGNORED：连续忽略次数（episode.approach_count - 已回复次数）
    # 简化：如 episode 存在，取 episode.approach_count；否则取 sent_decisions 中
    # 同 session 的 send 决策数（粗略估计）
    if episode is not None and hasattr(episode, "approach_count"):
        consecutive_ignored = episode.approach_count
    else:
        # 取本 session 的 send 决策数（粗略）
        same_session_sent = [
            d for d in sent_decisions if d.session_id == candidate.session_id
        ]
        consecutive_ignored = len(same_session_sent)

    # SAME_KIND_COOLDOWN：同类型在冷却窗口内有 send 决策
    same_kind_cooldown = 0
    for d in sent_decisions:
        if d.created_at < now - SAME_KIND_COOLDOWN_SECONDS:
            continue
        # 需要查 candidate.candidate_kind；本阶段简化为查库
        # 这里直接通过 candidate_id 反查（避免复杂关联）
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT candidate_kind FROM proactive_candidates WHERE id=?",
                (d.candidate_id,),
            ).fetchone()
            if row and row["candidate_kind"] == candidate.candidate_kind:
                same_kind_cooldown = 1
                break
        finally:
            conn.close()

    factors = {
        Layer3Factor.TODAY_ALREADY_PROACTIVE: today_already,
        Layer3Factor.LAST_24H_COUNT: last_24h_count,
        Layer3Factor.PREVIOUS_UNANSWERED: previous_unanswered,
        Layer3Factor.CONSECUTIVE_IGNORED: consecutive_ignored,
        Layer3Factor.SAME_KIND_COOLDOWN: same_kind_cooldown,
        Layer3Factor.TIME_SINCE_LAST_PROACTIVE: time_since,
    }
    return Layer3Factors(factors=factors)


def evaluate_approach_drive(
    candidate: ProactiveCandidate,
    *,
    episode=None,
    contact_need: float = 0.5,
    mood_valence: float = 0.0,
    relationship_bond: float = 0.5,
) -> float:
    """评估接近意愿 approach_drive（spec 第 5.8 节）。

    简化实现（0.0~1.0）：
    - candidate_kind 基础分
    - contact_need 加成
    - mood_valence 微调
    - relationship_bond 加成
    - episode.unanswered_pressure 减成
    - clamp 到 [0.0, 1.0]
    """
    base = KIND_BASE_DRIVE.get(candidate.candidate_kind, 0.5)
    drive = base
    drive += contact_need * 0.2
    drive += mood_valence * 0.05
    drive += relationship_bond * 0.1

    if episode is not None and hasattr(episode, "unanswered_pressure"):
        drive -= episode.unanswered_pressure * 0.3

    return _clamp(drive, 0.0, 1.0)


def evaluate_contact_cost(
    candidate: ProactiveCandidate,
    *,
    now: Optional[float] = None,
    presence: Optional[PresenceRecord] = None,
    episode=None,
    layer3_factors: Optional[Layer3Factors] = None,
) -> float:
    """评估打扰负担 contact_cost（spec 第 5.8 节）。

    简化实现（0.0~1.0）：
    - 基础分 0.2
    - presence.busy/dnd/sleep 加 0.4
    - presence.away_brief/extended 加 0.2
    - layer3_factors.PREVIOUS_UNANSWERED 加 0.2
    - layer3_factors.CONSECUTIVE_IGNORED * 0.1
    - layer3_factors.LAST_24H_COUNT * 0.05
    - layer3_factors.SAME_KIND_COOLDOWN 加 0.15
    - episode.unanswered_pressure * 0.4
    - clamp 到 [0.0, 1.0]
    """
    now = now if now is not None else db.now()

    if presence is None:
        presence = get_current_presence(candidate.session_id)

    cost = 0.2

    if presence and presence.is_active:
        status = presence.user_status
        if status in (
            UserStatus.AWAY_BUSY,
            UserStatus.AWAY_SLEEP,
            UserStatus.DO_NOT_DISTURB,
        ):
            cost += 0.4
        elif status in (UserStatus.AWAY_BRIEF, UserStatus.AWAY_EXTENDED):
            cost += 0.2

    if layer3_factors is not None:
        factors = layer3_factors.factors
        if factors.get(Layer3Factor.PREVIOUS_UNANSWERED, 0):
            cost += 0.2
        consecutive = factors.get(Layer3Factor.CONSECUTIVE_IGNORED, 0)
        if isinstance(consecutive, (int, float)) and consecutive > 0:
            cost += consecutive * 0.1
        last_24h = factors.get(Layer3Factor.LAST_24H_COUNT, 0)
        if isinstance(last_24h, (int, float)) and last_24h > 0:
            cost += last_24h * 0.05
        if factors.get(Layer3Factor.SAME_KIND_COOLDOWN, 0):
            cost += 0.15

    if episode is not None and hasattr(episode, "unanswered_pressure"):
        cost += episode.unanswered_pressure * 0.4

    return _clamp(cost, 0.0, 1.0)


def compute_effective_drive(
    approach_drive: float,
    *,
    relationship_modulation: float = 1.0,
    mood_modulation: float = 1.0,
) -> float:
    """effective_drive = approach_drive × relationship_modulation × mood_modulation。

    relationship_modulation: 0.8~1.2（高关系稍增，低关系稍减）
    mood_modulation: 0.8~1.2（好心情稍增）
    """
    return _clamp(approach_drive * relationship_modulation * mood_modulation, 0.0, 1.0)


def compute_approach_value(effective_drive: float, contact_cost: float) -> float:
    """approach_value = effective_drive - contact_cost"""
    return effective_drive - contact_cost


def compute_shadow_score(
    candidate: ProactiveCandidate,
    *,
    evidence_strength: float = 0.5,
    open_thread_relevance: float = 0.5,
    emotional_resonance: float = 0.5,
    relationship_fit: float = 0.5,
    contact_need_fit: float = 0.5,
    timing_score: float = 0.5,
    kind_priority: float = 0.5,
) -> float:
    """v0.1 旧线性公式 Shadow 基线（spec 第 6.3 节）。

    score = evidence_strength*0.25 + open_thread_relevance*0.20 + emotional_resonance*0.15
          + relationship_fit*0.15 + contact_need_fit*0.10 + timing_score*0.10 + kind_priority*0.05
    """
    score = (
        evidence_strength * SHADOW_FORMULA_WEIGHTS["evidence_strength"]
        + open_thread_relevance * SHADOW_FORMULA_WEIGHTS["open_thread_relevance"]
        + emotional_resonance * SHADOW_FORMULA_WEIGHTS["emotional_resonance"]
        + relationship_fit * SHADOW_FORMULA_WEIGHTS["relationship_fit"]
        + contact_need_fit * SHADOW_FORMULA_WEIGHTS["contact_need_fit"]
        + timing_score * SHADOW_FORMULA_WEIGHTS["timing_score"]
        + kind_priority * SHADOW_FORMULA_WEIGHTS["kind_priority"]
    )
    return score


# LLM advice 解析 ===========================================================

# intensity 字符串到整数的映射
_INTENSITY_MAP = {
    "level_0": 0,
    "level_1": 1,
    "level_2": 2,
    "level_3": 3,
    "level_4": 4,
    "level_5": 5,
}

# 合法 decision 值
_VALID_DECISIONS = {
    DecisionAction.SEND,
    DecisionAction.DEFER,
    DecisionAction.SUPPRESS,
    DecisionAction.ABANDON,
}

# 合法 expression_act 值
_VALID_EXPRESSION_ACTS = {
    ExpressionAct.PLAYFUL_COMPLAINT,
    ExpressionAct.GENTLE_URGE,
    ExpressionAct.FIRM_CARE,
    ExpressionAct.WORRIED_CHECKIN,
    ExpressionAct.EXPECTANT_FOLLOWUP,
    ExpressionAct.QUIET_WAITING,
}


def parse_llm_advice(llm_raw_response: str) -> LLMAdvice:
    """解析 LLM 结构化输出 JSON（spec 第 6.5 节 schema）。

    如解析失败返回默认 SUPPRESS advice。

    JSON schema:
    {
      "decision": "send | defer | suppress | abandon",
      "intensity": "level_0 | level_1 | level_2 | level_3 | level_4 | level_5",
      "expression_act": "playful_complaint | ...",
      "topic": "话题摘要",
      "confidence": 0.0,
      "reason_codes": [...],
      "source_refs": [...]
    }

    注意：intensity 字段在 JSON 中是字符串 "level_0"~"level_5"，需要转为整数 0~5。
    """
    if not llm_raw_response:
        return _default_advice()

    try:
        data = json.loads(llm_raw_response)
    except (ValueError, TypeError):
        return _default_advice()

    if not isinstance(data, dict):
        return _default_advice()

    # 校验 decision
    decision = data.get("decision")
    if decision not in _VALID_DECISIONS:
        return _default_advice()

    # 解析 intensity
    intensity_raw = data.get("intensity")
    intensity = None
    if isinstance(intensity_raw, str):
        intensity = _INTENSITY_MAP.get(intensity_raw)
    elif isinstance(intensity_raw, int) and 0 <= intensity_raw <= 5:
        intensity = intensity_raw

    # 校验 expression_act
    expression_act = data.get("expression_act")
    if expression_act is not None and expression_act not in _VALID_EXPRESSION_ACTS:
        expression_act = None

    # 校验 confidence
    confidence = data.get("confidence", 0.0)
    if not isinstance(confidence, (int, float)):
        confidence = 0.0
    confidence = _clamp(float(confidence), 0.0, 1.0)

    # reason_codes 和 source_refs 必须是 list
    reason_codes = data.get("reason_codes", [])
    if not isinstance(reason_codes, list):
        reason_codes = []
    source_refs = data.get("source_refs", [])
    if not isinstance(source_refs, list):
        source_refs = []

    topic = data.get("topic")
    if topic is not None and not isinstance(topic, str):
        topic = None

    return LLMAdvice(
        decision=decision,
        intensity=intensity,
        expression_act=expression_act,
        topic=topic,
        confidence=confidence,
        reason_codes=reason_codes,
        source_refs=source_refs,
    )


def _default_advice() -> LLMAdvice:
    """返回默认 SUPPRESS advice（解析失败时使用）。"""
    return LLMAdvice(
        decision=DecisionAction.SUPPRESS,
        intensity=None,
        expression_act=None,
        topic=None,
        confidence=0.0,
        reason_codes=["parse_failed"],
        source_refs=[],
    )


# 主决策函数 ================================================================

def decide_candidate(
    candidate_id: str,
    *,
    llm_advice: Optional[LLMAdvice] = None,
    llm_raw_response: Optional[str] = None,
    now: Optional[float] = None,
    is_shadow: bool = False,
) -> ProactiveDecision:
    """主决策函数（spec 第 6.3 节 5 步流程）。

    流程：
    1. 加载 candidate
    2. 第一层硬门检查 → 如 blocked，decision=SUPPRESS
    3. 第二层延后条件检查 → 如 deferred，decision=DEFER
    4. 第三层动态因素计算
    5. 评估 approach_drive, contact_cost, effective_drive, approach_value
    6. 解析 llm_advice（如有）
    7. 综合决策（硬门 > LLM > 本地规则）
    8. 计算 shadow_score
    9. 落库到 proactive_decisions 表
    10. 更新 candidate.status
    11. 返回 ProactiveDecision

    is_shadow=True 时：decision 仍按算法计算，但 is_shadow 字段标记为 True
    关键约束：关闭主动陪伴时第一层硬门 PROACTIVE_DISABLED 命中，decision=SUPPRESS，无论 LLM 建议如何
    """
    now = now if now is not None else db.now()

    # 1. 加载 candidate
    candidate = _get_candidate_or_raise(candidate_id)

    # 2. 第一层硬门检查
    layer1 = check_layer1_hard_boundary(candidate, now=now)

    # 3. 第二层延后条件检查
    layer2 = check_layer2_defer_conditions(candidate, now=now)

    # 4. 第三层动态因素
    layer3 = compute_layer3_factors(candidate, now=now)

    # 5. 评估 approach_drive, contact_cost, effective_drive, approach_value
    approach_drive = evaluate_approach_drive(candidate, episode=None)
    contact_cost = evaluate_contact_cost(
        candidate, now=now, layer3_factors=layer3,
    )
    effective_drive = compute_effective_drive(approach_drive)
    approach_value = compute_approach_value(effective_drive, contact_cost)

    # 6. 解析 LLM advice（如有）
    if llm_advice is None and llm_raw_response is not None:
        llm_advice = parse_llm_advice(llm_raw_response)

    # 7. 综合决策
    if layer1.blocked:
        # 关键约束：第一层硬门 blocked → SUPPRESS（LLM 无权放行）
        decision = DecisionAction.SUPPRESS
        intensity = None
        expression_act = None
        topic = candidate.topic
        confidence = 0.0
        reason_codes = list(layer1.reasons)
        source_refs = []
    elif layer2.deferred:
        # 第二层延后 → DEFER（除非 LLM 强烈建议 SUPPRESS/ABANDON）
        if llm_advice is not None and llm_advice.decision == DecisionAction.SUPPRESS:
            decision = DecisionAction.SUPPRESS
        elif llm_advice is not None and llm_advice.decision == DecisionAction.ABANDON:
            decision = DecisionAction.ABANDON
        else:
            decision = DecisionAction.DEFER
        intensity = llm_advice.intensity if llm_advice else None
        expression_act = llm_advice.expression_act if llm_advice else None
        topic = (llm_advice.topic if llm_advice else None) or candidate.topic
        confidence = llm_advice.confidence if llm_advice else 0.0
        reason_codes = list(layer2.reasons)
        if llm_advice and llm_advice.reason_codes:
            reason_codes.extend(llm_advice.reason_codes)
        source_refs = llm_advice.source_refs if llm_advice else []
    elif llm_advice is not None:
        # 第三层以下：按 LLM 建议
        decision = llm_advice.decision
        intensity = llm_advice.intensity
        expression_act = llm_advice.expression_act
        topic = llm_advice.topic or candidate.topic
        confidence = llm_advice.confidence
        reason_codes = list(llm_advice.reason_codes)
        source_refs = list(llm_advice.source_refs)
    else:
        # 本地规则：approach_value > 0 → SEND；> -0.2 → DEFER；否则 SUPPRESS
        if approach_value > 0:
            decision = DecisionAction.SEND
        elif approach_value > -0.2:
            decision = DecisionAction.DEFER
        else:
            decision = DecisionAction.SUPPRESS
        intensity = None
        expression_act = None
        topic = candidate.topic
        confidence = _clamp(approach_value, 0.0, 1.0)
        reason_codes = [
            f"approach_drive={approach_drive:.3f}",
            f"contact_cost={contact_cost:.3f}",
            f"approach_value={approach_value:.3f}",
        ]
        source_refs = []

    # 8. 计算 shadow_score（旧线性公式 Shadow 基线）
    # 简化：使用 candidate 字段和 layer3 factors 映射到各 weight 维度
    evidence_strength = 1.0 if candidate.source_hash else 0.3
    open_thread_relevance = 0.7 if candidate.open_thread else 0.3
    emotional_resonance = 0.6 if candidate.candidate_kind == "emotional_care" else 0.4
    relationship_fit = 0.5  # 默认中性
    contact_need_fit = 0.5
    timing_score = _clamp(1.0 - contact_cost, 0.0, 1.0)
    kind_priority_map = {
        "emotional_care": 0.9,
        "milestone_followup": 0.85,
        "return_followup": 0.8,
        "life_share": 0.7,
        "chat_continuation": 0.5,
        "casual_greeting": 0.3,
    }
    kind_priority = kind_priority_map.get(candidate.candidate_kind, 0.5)
    shadow_score = compute_shadow_score(
        candidate,
        evidence_strength=evidence_strength,
        open_thread_relevance=open_thread_relevance,
        emotional_resonance=emotional_resonance,
        relationship_fit=relationship_fit,
        contact_need_fit=contact_need_fit,
        timing_score=timing_score,
        kind_priority=kind_priority,
    )

    # 9. 落库到 proactive_decisions 表
    # 幂等检查：相同 candidate_id 已有决策则返回已有决策
    idempotency_key = make_idempotency_key(PROACTIVE_DECISION_V2, candidate_id)
    existing = _get_decision_by_idempotency_key(idempotency_key)
    if existing is not None:
        # 幂等：返回已有决策
        return existing

    record_id = db.new_id()
    layer3_factors_json = json.dumps(layer3.factors, ensure_ascii=False, sort_keys=True)
    reason_codes_json = json.dumps(reason_codes, ensure_ascii=False)
    source_refs_json = json.dumps(source_refs, ensure_ascii=False)
    layer1_reasons_json = json.dumps(layer1.reasons, ensure_ascii=False)
    layer2_reasons_json = json.dumps(layer2.reasons, ensure_ascii=False)

    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO proactive_decisions"
            " (id, candidate_id, session_id, decision, intensity, expression_act,"
            "  topic, confidence, reason_codes, source_refs,"
            "  layer1_blocked, layer1_block_reasons,"
            "  layer2_deferred, layer2_defer_reasons, layer3_factors,"
            "  approach_drive, contact_cost, effective_drive, approach_value,"
            "  shadow_score, is_shadow, llm_raw_response, idempotency_key,"
            "  protocol_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record_id, candidate_id, candidate.session_id,
                decision, intensity, expression_act, topic,
                confidence, reason_codes_json, source_refs_json,
                1 if layer1.blocked else 0, layer1_reasons_json,
                1 if layer2.deferred else 0, layer2_reasons_json,
                layer3_factors_json,
                approach_drive, contact_cost, effective_drive, approach_value,
                shadow_score, 1 if is_shadow else 0, llm_raw_response,
                idempotency_key, PROACTIVE_DECISION_V2, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # 10. 更新 candidate.status：SEND→approved；DEFER→deferred；SUPPRESS→suppressed；ABANDON→abandoned
    status_map = {
        DecisionAction.SEND: CandidateStatus.APPROVED,
        DecisionAction.DEFER: CandidateStatus.DEFERRED,
        DecisionAction.SUPPRESS: CandidateStatus.SUPPRESSED,
        DecisionAction.ABANDON: CandidateStatus.ABANDONED,
    }
    new_status = status_map.get(decision, CandidateStatus.SUPPRESSED)
    # 终态保护：如候选已在终态则不转换（避免 ValueError）
    if candidate.status not in (
        CandidateStatus.SUPPRESSED,
        CandidateStatus.ABANDONED,
        CandidateStatus.DELIVERED,
    ):
        try:
            _transition_candidate_status_internal(candidate_id, new_status, now=now)
        except ValueError:
            # 候选已被并发修改，保留决策记录但不再尝试转换
            pass

    return ProactiveDecision(
        id=record_id, candidate_id=candidate_id, session_id=candidate.session_id,
        decision=decision, intensity=intensity, expression_act=expression_act,
        topic=topic, confidence=confidence,
        reason_codes=reason_codes, source_refs=source_refs,
        layer1_blocked=layer1.blocked, layer1_block_reasons=layer1.reasons,
        layer2_deferred=layer2.deferred, layer2_defer_reasons=layer2.reasons,
        layer3_factors=layer3.factors,
        approach_drive=approach_drive, contact_cost=contact_cost,
        effective_drive=effective_drive, approach_value=approach_value,
        shadow_score=shadow_score, is_shadow=is_shadow,
        llm_raw_response=llm_raw_response,
        idempotency_key=idempotency_key,
        protocol_version=PROACTIVE_DECISION_V2, created_at=now,
    )


def _get_candidate_or_raise(candidate_id: str) -> ProactiveCandidate:
    """加载候选，不存在则抛 ValueError。"""
    # 延迟导入避免循环依赖
    from .candidates import get_candidate
    candidate = get_candidate(candidate_id)
    if candidate is None:
        raise ValueError(f"candidate not found: {candidate_id}")
    return candidate


def _transition_candidate_status_internal(
    candidate_id: str, new_status: str, *, now: Optional[float] = None,
) -> None:
    """内部：转换候选状态（不返回新对象，避免与 candidates 模块耦合）。"""
    from .candidates import transition_candidate_status
    transition_candidate_status(candidate_id, new_status, now=now)


def _get_decision_by_idempotency_key(idempotency_key: str) -> Optional[ProactiveDecision]:
    """按 idempotency_key 查询决策（幂等检查）。"""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM proactive_decisions WHERE idempotency_key=? "
            "ORDER BY created_at DESC LIMIT 1",
            (idempotency_key,),
        ).fetchone()
        if not row:
            return None
        return _row_to_decision(row)
    finally:
        conn.close()


def get_decision(decision_id: str) -> Optional[ProactiveDecision]:
    """按 ID 查询决策。"""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM proactive_decisions WHERE id=?",
            (decision_id,),
        ).fetchone()
        if not row:
            return None
        return _row_to_decision(row)
    finally:
        conn.close()


def get_decision_by_candidate(candidate_id: str) -> Optional[ProactiveDecision]:
    """按 candidate_id 查询最新一条决策。"""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM proactive_decisions WHERE candidate_id=? "
            "ORDER BY created_at DESC LIMIT 1",
            (candidate_id,),
        ).fetchone()
        if not row:
            return None
        return _row_to_decision(row)
    finally:
        conn.close()


def list_recent_decisions(*, since: Optional[float] = None, limit: int = 100) -> list:
    """列出最近的决策（可按 since 过滤）。"""
    conn = db.connect()
    try:
        if since is not None:
            rows = conn.execute(
                "SELECT * FROM proactive_decisions "
                "WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM proactive_decisions "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_decision(row) for row in rows]
    finally:
        conn.close()


def _parse_json_list(raw: Optional[str]) -> list:
    """从 JSON 字符串解析 list（None 或空 → []）。"""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def _parse_json_dict(raw: Optional[str]) -> dict:
    """从 JSON 字符串解析 dict（None 或空 → {}）。"""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _row_to_decision(row) -> ProactiveDecision:
    """内部：从 sqlite3.Row 构造 ProactiveDecision。"""
    return ProactiveDecision(
        id=row["id"], candidate_id=row["candidate_id"], session_id=row["session_id"],
        decision=row["decision"], intensity=row["intensity"],
        expression_act=row["expression_act"], topic=row["topic"],
        confidence=row["confidence"],
        reason_codes=_parse_json_list(row["reason_codes"]),
        source_refs=_parse_json_list(row["source_refs"]),
        layer1_blocked=bool(row["layer1_blocked"]),
        layer1_block_reasons=_parse_json_list(row["layer1_block_reasons"]),
        layer2_deferred=bool(row["layer2_deferred"]),
        layer2_defer_reasons=_parse_json_list(row["layer2_defer_reasons"]),
        layer3_factors=_parse_json_dict(row["layer3_factors"]),
        approach_drive=row["approach_drive"],
        contact_cost=row["contact_cost"],
        effective_drive=row["effective_drive"],
        approach_value=row["approach_value"],
        shadow_score=row["shadow_score"],
        is_shadow=bool(row["is_shadow"]),
        llm_raw_response=row["llm_raw_response"],
        idempotency_key=row["idempotency_key"],
        protocol_version=row["protocol_version"],
        created_at=row["created_at"],
    )
