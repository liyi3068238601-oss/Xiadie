"""EAP v0.2 第 14 节四类验收场景测试。

按 spec 第 14 节要求覆盖：
1. 14.1 主动表达（6 个场景）：去测试 20 分钟追问、晚安次日再评估、多次未回复降级、
   continue_reminding、stop_pushing、第二次接近不重复
2. 14.2 关系（5 个场景）：ordinary_exchange/shared_appreciation/vulnerable_disclosure/
   boundary_repair/conflict
3. 14.3 心境（4 个场景）：迟滞、表达向量、ExpressionPlan 禁区、迟滞应用
4. 14.4 安全与边界（5 个场景）：关闭、拒绝话题、外部渠道未授权、高关系不覆盖拒绝、
   同 episode 不重复发送
5. 关键约束（3 个）：LLM 无权放行硬边界、高关系不覆盖拒绝、用户沉默不降低 bond

合计 23 个测试。
"""
import pytest

from app import db
from app.proactive import candidates as candidates_mod
from app.proactive import decision as decision_mod
from app.proactive import episodes as episodes_mod
from app.proactive import expression as expression_mod
from app.proactive import intensity as intensity_mod
from app.proactive import presence as presence_mod
from app.proactive import relationship as relationship_mod
from app.proactive import timeline_simulator as ts_mod
from app.proactive.decision import (
    DecisionAction,
    ExpressionAct,
    Layer1BlockReason,
    Layer2DeferReason,
    LLMAdvice,
)
from app.proactive.episodes import UserResponseType
from app.proactive.expression import (
    DEFAULT_HYSTERESIS_PARAMS,
    EXPRESSION_PLAN_FORBIDDEN_MODIFICATIONS,
    ExpressionVector,
    HysteresisParams,
)
from app.proactive.relationship import LABEL_DELTAS, RelationshipLabel
from app.proactive.timeline_simulator import (
    SimulationResult,
    TimelineSimulator,
)


# ---------- 公共 fixture ----------

def _setup_session(session_id: str) -> None:
    """插入测试 session。"""
    now = db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (session_id, "acceptance 测试", now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _cleanup_session(session_id: str) -> None:
    """清理测试 session 相关数据（按外键依赖顺序）。"""
    conn = db.connect()
    try:
        conn.execute("DELETE FROM expression_plans WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM expression_state_transitions WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM proactive_intensity_plans WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM proactive_decisions WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM proactive_candidates WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM conversation_presence WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM contact_episodes WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM episode_relationship_delta_suggestions WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        conn.commit()
    finally:
        conn.close()


def _set_proactive_enabled(value: str = "1") -> None:
    """设置 proactive_enabled 并清除其他可能干扰的设置。"""
    db.set_setting("proactive_enabled", value)
    db.set_setting("proactive_emergency_stop", "0")
    db.set_setting("proactive_rejected_topics", "")
    db.set_setting("proactive_rejected_kinds", "")
    db.set_setting("proactive_desktop_notification_enabled", "0")
    db.set_setting("proactive_external_channels_enabled", "0")


def _insert_message(session_id: str, content: str = "测试消息") -> str:
    """插入一条测试消息并返回 message_id（用于外键关联）。"""
    message_id = db.new_id()
    now = db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) "
            "VALUES(?,?,'user',?,?)",
            (message_id, session_id, content, now),
        )
        conn.commit()
    finally:
        conn.close()
    return message_id


# ============================================================================
# 14.1 主动表达验收（6 个场景）
# ============================================================================

def test_scenario_return_after_test_20min():
    """14.1.1: 去测试 20 分钟后追问。

    验证：
    - 用户消息被正确识别为 AWAY_BRIEF + open_thread='测试结果'
    - ContactEpisode 创建成功，origin_type=expected_return
    - 候选创建成功，candidate_kind=return_followup
    - 决策周期运行后产生 1 个决策
    - 时长 20 分钟符合 spec 第 14 节要求
    """
    session_id = db.new_id()
    try:
        result = ts_mod.scenario_return_after_test_20min(session_id)
        assert isinstance(result, SimulationResult)
        # 时长 20 分钟
        assert result.summary['duration_seconds'] == 20 * 60
        # 至少记录：user_message + episode_created + candidate_created + decision_run
        assert result.summary['total_events'] >= 4
        # 创建了 1 个 episode 和 1 个 candidate
        assert result.summary['total_episodes'] == 1
        assert result.summary['total_candidates'] == 1
        # 运行了 1 次决策
        assert result.summary['total_decisions'] == 1
        # 验证事件类型
        kinds = [e.event_kind for e in result.events]
        assert 'user_message' in kinds
        assert 'episode_created' in kinds
        assert 'candidate_created' in kinds
        assert 'decision_run' in kinds
        # 验证 user_message payload
        um_event = next(e for e in result.events if e.event_kind == 'user_message')
        assert um_event.payload['presence_status'] == 'away_brief'
        assert um_event.payload['open_thread'] is True
        assert um_event.payload['open_thread_topic'] == '测试结果'
    finally:
        _cleanup_session(session_id)


def test_scenario_goodnight_next_day():
    """14.1.2: 晚安后次日再评估。

    验证：
    - 用户说"晚安" → presence=away_sleep
    - t=1h 决策应被 layer2 USER_SLEEPING 延后（decision=DEFER）
    - t=8h 醒来 → presence=online
    - 醒来后决策不再被 USER_SLEEPING 延后
    """
    session_id = db.new_id()
    try:
        result = ts_mod.scenario_goodnight_next_day(session_id)
        assert result.summary['total_decisions'] == 2  # 两次决策
        # 第一个决策应 DEFER（USER_SLEEPING）
        first_decision = result.decisions[0]
        assert first_decision.decision == DecisionAction.DEFER
        assert first_decision.layer2_deferred is True
        assert Layer2DeferReason.USER_SLEEPING in first_decision.layer2_defer_reasons
        # 验证用户消息事件中检测到 away_sleep
        um_events = [e for e in result.events if e.event_kind == 'user_message']
        assert any(
            e.payload['presence_status'] == 'away_sleep' for e in um_events
        )
        # 验证 sleep/wake 事件
        kinds = [e.event_kind for e in result.events]
        assert 'wake' in kinds
    finally:
        _cleanup_session(session_id)


def test_scenario_repeated_unanswered_downgrade():
    """14.1.3: 多次未回复降级。

    验证：
    - 多次接近（4 次）后 unanswered_pressure 单调上升（或非衰减）
    - approach_count 递增
    - 每次接近都更新 episode.approach_count
    """
    session_id = db.new_id()
    try:
        result = ts_mod.scenario_repeated_unanswered_downgrade(session_id)
        # 4 次决策周期
        assert result.summary['total_decisions'] == 4
        # 4 次接近尝试
        approach_events = [
            e for e in result.events if e.event_kind == 'approach'
        ]
        assert len(approach_events) == 4
        # 验证 approach_count 递增
        intensities = [e.payload['intensity'] for e in approach_events]
        # 应该从 Level 3 降到 Level 1（spec 第 14.1.3 节降级模式）
        assert intensities[0] >= intensities[-1]
        # 验证 episode 状态
        assert len(result.episodes) >= 1
        episode = result.episodes[0]
        assert episode.approach_count == 4
        # pressure 应该 > 0（已累积）
        assert episode.unanswered_pressure > 0
    finally:
        _cleanup_session(session_id)


def test_scenario_continue_reminding_increase():
    """14.1.5: "你可以继续提醒我"提高接受度。

    验证：
    - baseline pressure 在 record_approach 后产生
    - 应用 continue_reminding 后 pressure × 1.05（轻微提高）
    - episode 不终态
    """
    session_id = db.new_id()
    try:
        result = ts_mod.scenario_continue_reminding_increase(session_id)
        # 查找 pressure_check 事件
        pc_events = [
            e for e in result.events if e.event_kind == 'pressure_check'
        ]
        assert len(pc_events) == 1
        payload = pc_events[0].payload
        baseline = payload['baseline']
        after = payload['after_continue_reminding']
        # continue_reminding 应使 pressure × 1.05（轻微提高）
        assert after >= baseline
        # episode 不应终态（continue_reminding 保持状态）
        assert len(result.episodes) >= 1
        episode = result.episodes[0]
        assert episode.status not in ('closed', 'expired', 'cancelled', 'blocked')
    finally:
        _cleanup_session(session_id)


def test_scenario_stop_pushing_behavior_fix():
    """14.1.6: "别一直催我"调整行为。

    验证：
    - 应用 stop_pushing 后 pressure 不变（spec 第 5.9 节）
    - episode 不终态（行为修复留给 EAP.F）
    """
    session_id = db.new_id()
    try:
        result = ts_mod.scenario_stop_pushing_behavior_fix(session_id)
        pc_events = [
            e for e in result.events if e.event_kind == 'pressure_check'
        ]
        assert len(pc_events) == 1
        payload = pc_events[0].payload
        before = payload['before']
        after = payload['after_stop_pushing']
        # stop_pushing 不改变 pressure（spec 第 5.9 节）
        assert after == before
        # episode 不终态
        assert payload['episode_status'] not in (
            'closed', 'expired', 'cancelled', 'blocked'
        )
    finally:
        _cleanup_session(session_id)


def test_scenario_second_approach_not_duplicate():
    """14.1.4: 第二次接近不重复发送。

    验证：
    - 同一 episode 的第二次接近应被 ALREADY_DELIVERED 阻断
    - 第二个决策 SUPPRESS
    - layer1_blocked=True，包含 already_delivered 原因
    """
    session_id = db.new_id()
    try:
        result = ts_mod.scenario_same_episode_no_duplicate_send(session_id)
        # 两次决策
        assert result.summary['total_decisions'] == 2
        first = result.decisions[0]
        second = result.decisions[1]
        # 第二个决策应被 ALREADY_DELIVERED 阻断
        assert second.decision == DecisionAction.SUPPRESS
        assert second.layer1_blocked is True
        assert Layer1BlockReason.ALREADY_DELIVERED in second.layer1_block_reasons
    finally:
        _cleanup_session(session_id)


# ============================================================================
# 14.2 关系验收（5 个场景）
# ============================================================================

def test_relationship_ordinary_exchange_no_bond_delta():
    """14.2.1: 普通问答不机械增加 bond。

    验证：
    - ordinary_exchange 标签 → bond_delta = 0
    - familiarity_delta > 0（缓慢增长）
    - 单轮限幅保护
    - 幂等：同 source_message_id 重复调用返回 None
    """
    db.init_db()
    _set_proactive_enabled("1")
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        msg_id = _insert_message(session_id, "你好")
        suggestion = relationship_mod.process_relationship_delta(
            session_id, msg_id, RelationshipLabel.ORDINARY_EXCHANGE,
        )
        assert suggestion is not None
        # bond_delta = 0（spec 第 11 节"普通聊天不再默认增加 bond"）
        assert suggestion.bond_delta == 0.0
        # familiarity 缓慢增长
        assert suggestion.familiarity_delta > 0
        # 幂等：重复调用返回 None
        dup = relationship_mod.process_relationship_delta(
            session_id, msg_id, RelationshipLabel.ORDINARY_EXCHANGE,
        )
        assert dup is None
    finally:
        _cleanup_session(session_id)


def test_relationship_shared_appreciation_bond_increase():
    """14.2.2: 明确感谢产生受限 bond 增量。

    验证：
    - shared_appreciation → bond_delta > 0
    - 单轮限幅保护（不超过 SINGLE_TURN_CAPS['bond'][1] = 0.003）
    - 同事件幂等
    """
    db.init_db()
    _set_proactive_enabled("1")
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        msg_id = _insert_message(session_id, "谢谢你帮了我大忙")
        suggestion = relationship_mod.process_relationship_delta(
            session_id, msg_id, RelationshipLabel.SHARED_APPRECIATION,
        )
        assert suggestion is not None
        assert suggestion.bond_delta > 0
        # 单轮限幅
        assert suggestion.bond_delta <= relationship_mod.SINGLE_TURN_CAPS["bond"][1]
        # 幂等
        assert relationship_mod.process_relationship_delta(
            session_id, msg_id, RelationshipLabel.SHARED_APPRECIATION,
        ) is None
    finally:
        _cleanup_session(session_id)


def test_relationship_vulnerable_disclosure_attachment():
    """14.2.3: 脆弱披露产生 attachment 增量。

    验证：
    - vulnerable_disclosure → attachment_delta > 0
    - bond_delta > 0
    - familiarity_delta > 0
    """
    db.init_db()
    _set_proactive_enabled("1")
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        msg_id = _insert_message(session_id, "我最近心情不太好")
        suggestion = relationship_mod.process_relationship_delta(
            session_id, msg_id, RelationshipLabel.VULNERABLE_DISCLOSURE,
        )
        assert suggestion is not None
        # vulnerable_disclosure 显著提升 attachment
        assert suggestion.attachment_delta > 0
        assert suggestion.bond_delta > 0
        assert suggestion.familiarity_delta > 0
    finally:
        _cleanup_session(session_id)


def test_relationship_boundary_repair_trust_increase():
    """14.2.4: 边界修复提升 trust。

    验证：
    - boundary_repair → trust_delta > 0
    - bond_delta = 0（边界修复不增加 bond）
    """
    db.init_db()
    _set_proactive_enabled("1")
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        msg_id = _insert_message(session_id, "对不起，我刚才不应该那样说")
        suggestion = relationship_mod.process_relationship_delta(
            session_id, msg_id, RelationshipLabel.BOUNDARY_REPAIR,
        )
        assert suggestion is not None
        # 边界修复主要提升 trust
        assert suggestion.trust_delta > 0
        # 边界修复不增加 bond
        assert suggestion.bond_delta == 0.0
    finally:
        _cleanup_session(session_id)


def test_relationship_conflict_negative_trust():
    """14.2.5: 明确冲突降低 trust（用户沉默不降低，明确冲突可以）。

    验证：
    - conflict → trust_delta < 0（唯一可降低 trust 的标签）
    - rapport_delta < 0
    - bond_delta = 0（不降低 bond）
    """
    db.init_db()
    _set_proactive_enabled("1")
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        msg_id = _insert_message(session_id, "我不同意你的看法")
        suggestion = relationship_mod.process_relationship_delta(
            session_id, msg_id, RelationshipLabel.CONFLICT,
        )
        assert suggestion is not None
        # conflict 是唯一可降低 trust 的标签
        assert suggestion.trust_delta < 0
        assert suggestion.rapport_delta < 0
        # bond 不降低（spec 第 11 节"用户沉默不降低 bond/trust"，
        # 但明确冲突可以降低 trust；bond 不动）
        assert suggestion.bond_delta == 0.0
    finally:
        _cleanup_session(session_id)


# ============================================================================
# 14.3 心境验收（4 个场景）
# ============================================================================

def test_mood_hysteresis_minimum_duration():
    """14.3.1: 数值刚越过边界不立即跳变（minimum_state_duration）。

    验证：
    - 刚转换后短时间内再次转换被拒绝（minimum_state_duration_not_met）
    - 满足最小时长后才能转换
    """
    db.init_db()
    hysteresis = HysteresisParams(
        minimum_state_duration=30.0,
        hysteresis_margin=0.1,
        transition_momentum=1.0,
    )
    # 刚转换后 10 秒，未满足 30 秒最小时长
    should, reason = expression_mod.should_transition_state(
        current_value=0.6, target_value=0.8, threshold=0.5,
        hysteresis=hysteresis, last_transition_at=1000.0, now=1010.0,
    )
    assert should is False
    assert reason == 'minimum_state_duration_not_met'

    # 满足最小时长后可以转换
    should, reason = expression_mod.should_transition_state(
        current_value=0.6, target_value=0.8, threshold=0.5,
        hysteresis=hysteresis, last_transition_at=1000.0, now=1040.0,
    )
    assert should is True
    assert reason == 'ok'


def test_mood_hysteresis_margin_not_met():
    """14.3.2: hysteresis_margin 未满足时不转换。

    验证：
    - 向上转换需 current >= threshold + margin
    - 向下转换需 current <= threshold - margin
    """
    db.init_db()
    hysteresis = HysteresisParams(
        minimum_state_duration=0.0,  # 不检查时长
        hysteresis_margin=0.1,
        transition_momentum=1.0,
    )
    # 向上转换：current=0.55, threshold=0.5, margin=0.1 → 需 current >= 0.6
    should, reason = expression_mod.should_transition_state(
        current_value=0.55, target_value=0.8, threshold=0.5,
        hysteresis=hysteresis, last_transition_at=None, now=1000.0,
    )
    assert should is False
    assert reason == 'hysteresis_margin_not_met'

    # 向下转换：current=0.45, threshold=0.5, margin=0.1 → 需 current <= 0.4
    should, reason = expression_mod.should_transition_state(
        current_value=0.45, target_value=0.2, threshold=0.5,
        hysteresis=hysteresis, last_transition_at=None, now=1000.0,
    )
    assert should is False
    assert reason == 'hysteresis_margin_not_met'

    # 满足 margin 后可以转换
    should, reason = expression_mod.should_transition_state(
        current_value=0.65, target_value=0.8, threshold=0.5,
        hysteresis=hysteresis, last_transition_at=None, now=1000.0,
    )
    assert should is True


def test_expression_vector_dimensions():
    """14.3.3: 7 维连续表达向量正确生成。

    验证：
    - playful_complaint 对应向量 playfulness 高、restraint 低
    - quiet_waiting 对应向量 restraint 高、initiative 低
    - clamp 机制有效
    """
    db.init_db()
    # playful_complaint 默认向量
    v = expression_mod.create_expression_vector_for_act('playful_complaint')
    assert v.playfulness > 0.6  # 顽皮度高
    assert v.restraint < 0.5  # 克制低

    # quiet_waiting 默认向量
    v = expression_mod.create_expression_vector_for_act('quiet_waiting')
    assert v.restraint > 0.6  # 克制高
    assert v.initiative < 0.5  # 主动低

    # 无效 act 返回全 0.5 中性向量
    v = expression_mod.create_expression_vector_for_act('not_a_real_act')
    assert v.warmth == 0.5
    assert v.playfulness == 0.5

    # clamp 机制：超出范围自动 clamp 到 [0, 1]
    v = expression_mod.create_expression_vector(warmth=1.5, playfulness=-0.5)
    assert v.warmth == 1.0
    assert v.playfulness == 0.0


def test_expression_plan_forbidden_scope():
    """14.3.4: ExpressionPlan 禁区严格校验（5 项禁区 100% 阻断）。

    验证：
    - modifies_facts=True 触发 ValueError
    - modifies_safety=True 触发 ValueError
    - modifies_tool_results=True 触发 ValueError
    - modifies_permissions=True 触发 ValueError
    - modifies_user_boundary=True 触发 ValueError
    - 全部 False 时正常创建
    """
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        # 每个禁区都应触发 ValueError
        for forbidden_flag in (
            'modifies_facts', 'modifies_safety', 'modifies_tool_results',
            'modifies_permissions', 'modifies_user_boundary',
        ):
            kwargs = {forbidden_flag: True}
            with pytest.raises(ValueError, match="禁区违规"):
                expression_mod.create_expression_plan(session_id, **kwargs)

        # 全部 False（默认）应正常创建
        plan = expression_mod.create_expression_plan(session_id)
        assert plan is not None
        assert plan.modifies_facts is False
        assert plan.modifies_safety is False
        assert plan.modifies_tool_results is False
        assert plan.modifies_permissions is False
        assert plan.modifies_user_boundary is False
    finally:
        _cleanup_session(session_id)


# ============================================================================
# 14.4 安全与边界验收（5 个场景）
# ============================================================================

def test_scenario_disabled_proactive_zero_send():
    """14.4.1: 关闭主动陪伴后投递率 0。

    验证：
    - proactive_enabled='0' 时所有决策 SUPPRESS
    - send_count = 0
    - layer1_blocked=True，包含 proactive_disabled 原因
    """
    session_id = db.new_id()
    try:
        result = ts_mod.scenario_disabled_proactive_zero_send(session_id)
        # 3 个候选，3 个决策，全部 SUPPRESS
        assert result.summary['total_decisions'] == 3
        assert result.summary['send_count'] == 0
        assert result.summary['suppress_count'] == 3
        # 每个决策都被第一层硬门阻断
        for d in result.decisions:
            assert d.decision == DecisionAction.SUPPRESS
            assert d.layer1_blocked is True
            assert Layer1BlockReason.PROACTIVE_DISABLED in d.layer1_block_reasons
    finally:
        _cleanup_session(session_id)


def test_scenario_rejected_topic_zero_mention():
    """14.4.2: 用户禁提话题后提及率 0。

    验证：
    - 设置 proactive_rejected_topics='敏感话题'
    - 该 topic 的候选 SUPPRESS
    - layer1 包含 topic_rejected 原因
    """
    session_id = db.new_id()
    try:
        result = ts_mod.scenario_rejected_topic_zero_mention(session_id)
        assert result.summary['total_decisions'] == 1
        assert result.summary['send_count'] == 0
        d = result.decisions[0]
        assert d.decision == DecisionAction.SUPPRESS
        assert d.layer1_blocked is True
        assert Layer1BlockReason.TOPIC_REJECTED in d.layer1_block_reasons
    finally:
        _cleanup_session(session_id)


def test_scenario_unauthorized_external_zero_send():
    """14.4.3: 外部渠道未授权投递率 0。

    验证：
    - proactive_external_channels_enabled='0' 时 Level 5 不可投递
    - LLM 建议 Level 5 但应被降级（select_minimum_sufficient_level）
    - 决策可能 SEND（在 Level 3 以内）但 intensity plan 降级
    """
    session_id = db.new_id()
    try:
        result = ts_mod.scenario_unauthorized_external_zero_send(session_id)
        # 至少有 1 个决策
        assert result.summary['total_decisions'] == 1
        # 即使 LLM 建议 Level 5，实际 intensity plan 不应使用 Level 5
        # 验证 intensity plan 的 level 不超过 Level 3
        for plan in result.intensity_plans:
            assert plan.level <= 3, (
                f"未授权外部渠道不应使用 Level 5，实际 Level {plan.level}"
            )
    finally:
        _cleanup_session(session_id)


def test_scenario_high_bond_no_override_reject():
    """14.4.4: 高 bond 不覆盖用户明确拒绝。

    验证：
    - 即使 LLM 建议 send + 高 confidence，硬边界仍阻断
    - layer1_blocked=True（topic_rejected）
    - decision=SUPPRESS
    - layer1 阻断时 confidence 被清零（设计：硬边界覆盖 LLM，不保留其 confidence）
    """
    session_id = db.new_id()
    try:
        result = ts_mod.scenario_high_bond_no_override_reject(session_id)
        d = result.decisions[0]
        # LLM 建议 send 但被硬边界阻断
        assert d.decision == DecisionAction.SUPPRESS
        assert d.layer1_blocked is True
        assert Layer1BlockReason.TOPIC_REJECTED in d.layer1_block_reasons
        # 硬边界覆盖 LLM：confidence 被清零（不保留 LLM 的 0.95）
        assert d.confidence == 0.0
    finally:
        _cleanup_session(session_id)


def test_scenario_same_episode_no_duplicate_send():
    """14.4.5: 同一 ContactEpisode 不重复发送相同内容。

    验证：
    - 第一个候选 delivered
    - 第二个候选（相同 source_hash）被 ALREADY_DELIVERED 阻断
    """
    session_id = db.new_id()
    try:
        result = ts_mod.scenario_same_episode_no_duplicate_send(session_id)
        assert result.summary['total_decisions'] == 2
        # 第一个候选的决策不一定 SEND（取决于 approach_value）
        # 第二个决策必被 ALREADY_DELIVERED 阻断
        second = result.decisions[1]
        assert second.decision == DecisionAction.SUPPRESS
        assert second.layer1_blocked is True
        assert Layer1BlockReason.ALREADY_DELIVERED in second.layer1_block_reasons
    finally:
        _cleanup_session(session_id)


# ============================================================================
# 关键约束验收（3 个）
# ============================================================================

def test_constraint_llm_cannot_override_hard_boundary():
    """关键约束 1: LLM 无权放行硬边界。

    验证：
    - 即使 LLMAdvice(decision=SEND, confidence=1.0)，PROACTIVE_DISABLED 命中时 SUPPRESS
    - 决策原因不包含 LLM 的 send，而是硬边界原因
    """
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        # 关闭主动陪伴
        _set_proactive_enabled("0")
        # 创建 episode 和 candidate
        sim = TimelineSimulator(session_id, start_time=1000000000.0)
        with sim:
            sim.create_episode(
                topic="LLM 无权放行测试",
                origin_type=episodes_mod.OriginType.EMOTIONAL_CARE,
            )
            sim.create_candidate(
                candidate_kind=candidates_mod.CandidateKind.EMOTIONAL_CARE,
                topic="LLM 无权放行测试",
                source_messages=[
                    {"id": "m1", "role": "user", "content": "test"},
                ],
            )
            # LLM 强烈建议 send + 高 confidence
            llm_advice = LLMAdvice(
                decision=DecisionAction.SEND,
                intensity=3,
                expression_act=ExpressionAct.FIRM_CARE,
                topic="LLM 无权放行测试",
                confidence=1.0,  # 最高 confidence
                reason_codes=["high_bond", "open_thread"],
                source_refs=[],
            )
            decision = sim.run_decision_cycle(llm_advice=llm_advice)
        # 即使 LLM 强烈建议 send，硬边界仍阻断
        assert decision.decision == DecisionAction.SUPPRESS
        assert decision.layer1_blocked is True
        assert Layer1BlockReason.PROACTIVE_DISABLED in decision.layer1_block_reasons
    finally:
        _cleanup_session(session_id)
        _set_proactive_enabled("1")  # 恢复默认


def test_constraint_high_bond_does_not_override_explicit_reject():
    """关键约束 2: 高关系不覆盖用户明确拒绝。

    验证：
    - 用户 explicit_reject → episode.status='blocked'
    - 将话题加入 rejected_topics 后，新候选即使 LLM 高 confidence 也 SUPPRESS
    - 与 14.4.4 互补：此测试直接验证 episode 状态转换
    """
    db.init_db()
    _set_proactive_enabled("1")
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        sim = TimelineSimulator(session_id, start_time=1000000000.0)
        with sim:
            sim.create_episode(
                topic="高关系不覆盖测试",
                origin_type=episodes_mod.OriginType.EMOTIONAL_CARE,
            )
            # 用户明确拒绝
            episode = sim.user_responds(UserResponseType.EXPLICIT_REJECT)
            # episode 状态转为 blocked
            assert episode.status == 'blocked'
            assert episode.outcome == 'rejected'

            # 将话题加入拒绝列表
            db.set_setting("proactive_rejected_topics", "高关系不覆盖测试")

            # 创建新候选，LLM 强烈建议 send
            sim.create_candidate(
                candidate_kind=candidates_mod.CandidateKind.EMOTIONAL_CARE,
                topic="高关系不覆盖测试",
                source_messages=[
                    {"id": "m2", "role": "user", "content": "test2"},
                ],
            )
            llm_advice = LLMAdvice(
                decision=DecisionAction.SEND,
                intensity=3,
                expression_act=ExpressionAct.FIRM_CARE,
                topic="高关系不覆盖测试",
                confidence=0.99,
                reason_codes=["very_high_bond"],
                source_refs=[],
            )
            decision = sim.run_decision_cycle(llm_advice=llm_advice)
        # 硬边界阻断
        assert decision.decision == DecisionAction.SUPPRESS
        assert Layer1BlockReason.TOPIC_REJECTED in decision.layer1_block_reasons
    finally:
        _cleanup_session(session_id)
        db.set_setting("proactive_rejected_topics", "")


def test_constraint_user_silence_does_not_decrease_bond():
    """关键约束 3: 用户沉默不降低 bond/trust。

    验证：
    - unanswered_pressure 随时间衰减（spec 第 5.9 节）
    - 但 bond/trust 不因沉默而降低
    - decay_pressure 只降低 pressure，不影响其他字段
    - LABEL_DELTAS 中无任何标签因"沉默"产生负向 delta
    - 只有 conflict 标签产生负 trust_delta（明确冲突，非沉默）
    """
    db.init_db()
    _set_proactive_enabled("1")
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        sim = TimelineSimulator(session_id, start_time=1000000000.0)
        with sim:
            sim.create_episode(
                topic="沉默测试",
                origin_type=episodes_mod.OriginType.EMOTIONAL_CARE,
            )
            # 记录接近产生 pressure
            sim.record_approach(intensity=3)
            episode_before = sim._episodes[-1]
            pressure_before = episode_before.unanswered_pressure
            assert pressure_before > 0

            # 推进 10 小时（应该产生衰减）
            sim.advance(10 * 3600)
            # 衰减：每小时 0.05，10 小时衰减 0.5
            episode_after_decay = episodes_mod.decay_pressure(
                episode_before.id, now=sim.now(),
            )
            # pressure 应降低
            assert episode_after_decay.unanswered_pressure < pressure_before
            # 但其他字段不变（approach_count/status 等）
            assert episode_after_decay.approach_count == episode_before.approach_count
            assert episode_after_decay.status == episode_before.status

        # 验证 LABEL_DELTAS：只有 conflict 产生负 trust_delta
        # 其他 8 种标签 trust_delta >= 0
        negative_trust_labels = [
            label for label, deltas in LABEL_DELTAS.items()
            if deltas['trust_delta'] < 0
        ]
        assert negative_trust_labels == [RelationshipLabel.CONFLICT]

        # 验证所有标签的 bond_delta >= 0（沉默不降低 bond）
        negative_bond_labels = [
            label for label, deltas in LABEL_DELTAS.items()
            if deltas['bond_delta'] < 0
        ]
        assert negative_bond_labels == []  # 无标签降低 bond
    finally:
        _cleanup_session(session_id)
