"""EAP v0.2 表达向量、迟滞与 ExpressionPlan 测试（spec 第 5.11 节）。

覆盖：
1. 表达向量测试（7 个）
2. 迟滞参数测试（2 个）
3. 状态转换迟滞检查测试（8 个）
4. 状态转换记录测试（4 个）
5. ExpressionPlan 禁区验证测试（4 个）
6. ExpressionPlan 创建测试（6 个）
7. 迟滞应用到向量测试（4 个）
8. 查询测试（3 个）
9. schema 测试（4 个）
10. 关键约束测试（3 个）
"""
import time

import pytest

from app import db
from app.proactive import expression as expr
from app.proactive.expression import (
    ALL_DIMENSIONS,
    DEFAULT_HYSTERESIS_PARAMS,
    DIMENSION_DESCRIPTIONS,
    EXPRESSION_ACT_DEFAULT_VECTORS,
    EXPRESSION_PLAN_ALLOWED_ADJUSTMENTS,
    EXPRESSION_PLAN_FORBIDDEN_MODIFICATIONS,
    ExpressionDimension,
    ExpressionPlan,
    ExpressionVector,
    HysteresisParams,
    StateTransition,
    apply_hysteresis_to_vector,
    create_expression_plan,
    create_expression_vector,
    create_expression_vector_for_act,
    get_expression_plan,
    get_expression_plan_by_decision,
    get_last_transition,
    list_expression_plans_by_session,
    record_state_transition,
    should_transition_state,
    validate_expression_plan_scope,
)


# ---------- 公共 fixture ----------

def _setup_session(session_id: str) -> None:
    """插入测试 session。"""
    now = db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (session_id, "expression 测试", now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _cleanup_session(session_id: str) -> None:
    conn = db.connect()
    try:
        conn.execute("DELETE FROM expression_plans WHERE session_id=?", (session_id,))
        conn.execute(
            "DELETE FROM expression_state_transitions WHERE session_id=?",
            (session_id,),
        )
        conn.execute("DELETE FROM proactive_intensity_plans WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM proactive_decisions WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM proactive_candidates WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM conversation_presence WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM contact_episodes WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        conn.commit()
    finally:
        conn.close()


def _make_fake_decision(session_id: str, *, unique_suffix: str = "") -> str:
    """直接插入一条 minimal proactive_decisions 行用于 FK 引用。

    返回 decision_id。绕过 candidates/decision 模块的完整流程，仅供测试 FK。
    """
    candidate_id = db.new_id()
    decision_id = db.new_id()
    now = db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO proactive_candidates"
            " (id, session_id, candidate_kind, topic, source_refs, open_thread,"
            "  source_hash, status, protocol_version, created_at, updated_at)"
            " VALUES (?, ?, 'chat_continuation', ?, '{}', NULL, '', 'approved',"
            "  'proactive-decision-v2', ?, ?)",
            (candidate_id, session_id, f"测试{unique_suffix}", now, now),
        )
        conn.execute(
            "INSERT INTO proactive_decisions"
            " (id, candidate_id, session_id, decision, intensity, expression_act,"
            "  topic, confidence, reason_codes, source_refs,"
            "  layer1_blocked, layer1_block_reasons,"
            "  layer2_deferred, layer2_defer_reasons, layer3_factors,"
            "  approach_drive, contact_cost, effective_drive, approach_value,"
            "  shadow_score, is_shadow, llm_raw_response, idempotency_key,"
            "  protocol_version, created_at)"
            " VALUES (?, ?, ?, 'send', NULL, NULL, '', 0.0, '[]', '[]',"
            "  0, '[]', 0, '[]', '{}', 0.0, 0.0, 0.0, 0.0, NULL, 0, NULL, ?, ?, ?)",
            (decision_id, candidate_id, session_id,
             db.new_id(), 'proactive-decision-v2', now),
        )
        conn.commit()
    finally:
        conn.close()
    return decision_id


# ---------- 1. 表达向量测试 ----------

def test_create_expression_vector_default():
    """默认全 0.5。"""
    v = create_expression_vector()
    assert v.warmth == 0.5
    assert v.playfulness == 0.5
    assert v.directness == 0.5
    assert v.concern == 0.5
    assert v.initiative == 0.5
    assert v.restraint == 0.5
    assert v.energy == 0.5


def test_create_expression_vector_with_values():
    """自定义值。"""
    v = create_expression_vector(
        warmth=0.8, playfulness=0.3, directness=0.6,
        concern=0.7, initiative=0.4, restraint=0.9, energy=0.2,
    )
    assert v.warmth == 0.8
    assert v.playfulness == 0.3
    assert v.directness == 0.6
    assert v.concern == 0.7
    assert v.initiative == 0.4
    assert v.restraint == 0.9
    assert v.energy == 0.2


def test_create_expression_vector_clamps_out_of_range():
    """传入 1.5 和 -0.5 → clamp 到 1.0 和 0.0。"""
    v = create_expression_vector(
        warmth=1.5, playfulness=-0.5, directness=2.0,
        concern=-1.0, initiative=0.5, restraint=0.5, energy=0.5,
    )
    assert v.warmth == 1.0
    assert v.playfulness == 0.0
    assert v.directness == 1.0
    assert v.concern == 0.0
    assert v.initiative == 0.5
    assert v.restraint == 0.5
    assert v.energy == 0.5


def test_expression_vector_to_dict():
    """to_dict 返回 7 个键。"""
    v = ExpressionVector(warmth=0.1, playfulness=0.2, directness=0.3,
                          concern=0.4, initiative=0.5, restraint=0.6, energy=0.7)
    d = v.to_dict()
    assert d == {
        'warmth': 0.1, 'playfulness': 0.2, 'directness': 0.3,
        'concern': 0.4, 'initiative': 0.5, 'restraint': 0.6, 'energy': 0.7,
    }


def test_expression_vector_from_dict():
    """from_dict 构造，缺失维度使用默认 0.5。"""
    d = {'warmth': 0.8, 'playfulness': 0.3}
    v = ExpressionVector.from_dict(d)
    assert v.warmth == 0.8
    assert v.playfulness == 0.3
    assert v.directness == 0.5  # 默认
    assert v.concern == 0.5
    assert v.initiative == 0.5
    assert v.restraint == 0.5
    assert v.energy == 0.5


def test_create_expression_vector_for_act_playful_complaint():
    """playful_complaint 返回对应默认向量。"""
    v = create_expression_vector_for_act('playful_complaint')
    expected = EXPRESSION_ACT_DEFAULT_VECTORS['playful_complaint']
    assert v.warmth == expected['warmth']
    assert v.playfulness == expected['playfulness']
    assert v.directness == expected['directness']
    assert v.concern == expected['concern']
    assert v.initiative == expected['initiative']
    assert v.restraint == expected['restraint']
    assert v.energy == expected['energy']
    # 验证 playful_complaint 默认向量关键特征：playfulness 高、restraint 低
    assert v.playfulness == 0.8
    assert v.restraint == 0.4


def test_create_expression_vector_for_act_invalid():
    """无效 act 返回全 0.5 默认向量。"""
    v = create_expression_vector_for_act('not_a_real_act')
    assert v.warmth == 0.5
    assert v.playfulness == 0.5
    assert v.directness == 0.5
    assert v.concern == 0.5
    assert v.initiative == 0.5
    assert v.restraint == 0.5
    assert v.energy == 0.5


# ---------- 2. 迟滞参数测试 ----------

def test_default_hysteresis_params():
    """DEFAULT_HYSTERESIS_PARAMS 默认值正确。"""
    assert DEFAULT_HYSTERESIS_PARAMS['minimum_state_duration'] == 30.0
    assert DEFAULT_HYSTERESIS_PARAMS['hysteresis_margin'] == 0.1
    assert DEFAULT_HYSTERESIS_PARAMS['transition_momentum'] == 0.5


def test_hysteresis_params_custom():
    """HysteresisParams 自定义值。"""
    h = HysteresisParams(
        minimum_state_duration=60.0,
        hysteresis_margin=0.2,
        transition_momentum=0.7,
    )
    assert h.minimum_state_duration == 60.0
    assert h.hysteresis_margin == 0.2
    assert h.transition_momentum == 0.7


# ---------- 3. 状态转换迟滞检查测试 ----------

def test_should_transition_state_no_last_transition():
    """无上次转换时间，允许（无 minimum_state_duration 检查）。"""
    h = HysteresisParams(minimum_state_duration=30.0, hysteresis_margin=0.1,
                          transition_momentum=0.5)
    # 无 last_transition_at，时间检查跳过
    should, reason = should_transition_state(
        current_value=0.7, target_value=0.8,
        threshold=0.5, hysteresis=h,
        last_transition_at=None, now=1000.0,
    )
    assert should is True
    assert reason == 'ok'


def test_should_transition_state_minimum_duration_not_met():
    """时间不足，拒绝。"""
    h = HysteresisParams(minimum_state_duration=30.0, hysteresis_margin=0.1,
                          transition_momentum=0.5)
    # 上次转换在 10 秒前，minimum_state_duration=30，effective_duration=30/0.5=60
    should, reason = should_transition_state(
        current_value=0.7, target_value=0.8,
        threshold=0.5, hysteresis=h,
        last_transition_at=1000.0, now=1010.0,
    )
    assert should is False
    assert reason == 'minimum_state_duration_not_met'


def test_should_transition_state_minimum_duration_met():
    """时间足够，允许。"""
    h = HysteresisParams(minimum_state_duration=30.0, hysteresis_margin=0.1,
                          transition_momentum=0.5)
    # 上次转换在 100 秒前，effective_duration=60，elapsed=100 > 60，通过
    should, reason = should_transition_state(
        current_value=0.7, target_value=0.8,
        threshold=0.5, hysteresis=h,
        last_transition_at=1000.0, now=1100.0,
    )
    assert should is True
    assert reason == 'ok'


def test_should_transition_state_hysteresis_margin_not_met():
    """值在 margin 内，拒绝。

    target > threshold 但 current < threshold + margin
    """
    h = HysteresisParams(minimum_state_duration=30.0, hysteresis_margin=0.1,
                          transition_momentum=0.5)
    # threshold=0.5, margin=0.1，current=0.55（在 0.5~0.6 之间）
    # target=0.8 > 0.5，需要 current >= 0.5 + 0.1 = 0.6
    should, reason = should_transition_state(
        current_value=0.55, target_value=0.8,
        threshold=0.5, hysteresis=h,
        last_transition_at=None, now=1000.0,
    )
    assert should is False
    assert reason == 'hysteresis_margin_not_met'


def test_should_transition_state_hysteresis_margin_met():
    """值超过 margin，允许。"""
    h = HysteresisParams(minimum_state_duration=30.0, hysteresis_margin=0.1,
                          transition_momentum=0.5)
    # current=0.7 >= 0.5 + 0.1 = 0.6，通过
    should, reason = should_transition_state(
        current_value=0.7, target_value=0.8,
        threshold=0.5, hysteresis=h,
        last_transition_at=None, now=1000.0,
    )
    assert should is True
    assert reason == 'ok'


def test_should_transition_state_low_momentum_extends_duration():
    """动量低时延长所需时间。

    momentum=0.2 → effective_duration = 30/0.2 = 150 秒
    """
    h = HysteresisParams(minimum_state_duration=30.0, hysteresis_margin=0.1,
                          transition_momentum=0.2)
    # elapsed=60 秒，effective_duration=150，60 < 150 → 拒绝
    should, reason = should_transition_state(
        current_value=0.7, target_value=0.8,
        threshold=0.5, hysteresis=h,
        last_transition_at=1000.0, now=1060.0,
    )
    assert should is False
    assert reason == 'minimum_state_duration_not_met'

    # elapsed=200 秒 > 150 → 允许
    should, reason = should_transition_state(
        current_value=0.7, target_value=0.8,
        threshold=0.5, hysteresis=h,
        last_transition_at=1000.0, now=1200.0,
    )
    assert should is True
    assert reason == 'ok'


def test_should_transition_state_high_momentum_shortens_duration():
    """动量高时缩短所需时间。

    momentum=1.0 → effective_duration = 30/1.0 = 30 秒
    """
    h = HysteresisParams(minimum_state_duration=30.0, hysteresis_margin=0.1,
                          transition_momentum=1.0)
    # elapsed=40 秒，effective_duration=30，40 > 30 → 允许
    should, reason = should_transition_state(
        current_value=0.7, target_value=0.8,
        threshold=0.5, hysteresis=h,
        last_transition_at=1000.0, now=1040.0,
    )
    assert should is True
    assert reason == 'ok'

    # elapsed=20 秒 < 30 → 拒绝
    should, reason = should_transition_state(
        current_value=0.7, target_value=0.8,
        threshold=0.5, hysteresis=h,
        last_transition_at=1000.0, now=1020.0,
    )
    assert should is False
    assert reason == 'minimum_state_duration_not_met'


def test_should_transition_state_threshold_boundary_no_frequent_jump():
    """阈值附近不频繁跳变：刚转换后短时间内再次转换应被拒绝。"""
    h = HysteresisParams(minimum_state_duration=30.0, hysteresis_margin=0.1,
                          transition_momentum=0.5)
    # 第一次转换：current=0.7 已超 margin，允许
    should1, _ = should_transition_state(
        current_value=0.7, target_value=0.8,
        threshold=0.5, hysteresis=h,
        last_transition_at=1000.0, now=1100.0,  # 100 秒后
    )
    assert should1 is True

    # 5 秒后再次转换：时间不足，拒绝（避免频繁跳变）
    should2, reason2 = should_transition_state(
        current_value=0.65, target_value=0.75,
        threshold=0.5, hysteresis=h,
        last_transition_at=1100.0, now=1105.0,  # 5 秒后
    )
    assert should2 is False
    assert reason2 == 'minimum_state_duration_not_met'


# ---------- 4. 状态转换记录测试 ----------

def test_record_state_transition_basic():
    """基础记录。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        t = record_state_transition(
            session_id,
            state_kind='mood_cluster',
            from_state='calm', to_state='warm',
            from_value=0.5, to_value=0.7,
            transition_at=1000.0,
        )
        assert t.id is not None
        assert t.session_id == session_id
        assert t.state_kind == 'mood_cluster'
        assert t.from_state == 'calm'
        assert t.to_state == 'warm'
        assert t.from_value == 0.5
        assert t.to_value == 0.7
        assert t.transition_at == 1000.0
        assert t.hysteresis_applied is False
        assert t.rejection_reason is None
    finally:
        _cleanup_session(session_id)


def test_record_state_transition_with_hysteresis_applied():
    """记录因迟滞被拒绝的转换。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        t = record_state_transition(
            session_id,
            state_kind='guardedness_level',
            from_state='low', to_state='medium',
            transition_at=1000.0,
            hysteresis_applied=True,
            rejection_reason='minimum_state_duration_not_met',
        )
        assert t.hysteresis_applied is True
        assert t.rejection_reason == 'minimum_state_duration_not_met'
    finally:
        _cleanup_session(session_id)


def test_get_last_transition():
    """获取最近一次转换。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        # 记录两条转换
        record_state_transition(
            session_id, state_kind='mood_cluster',
            from_state='a', to_state='b',
            transition_at=1000.0,
        )
        record_state_transition(
            session_id, state_kind='mood_cluster',
            from_state='b', to_state='c',
            transition_at=2000.0,
        )
        # 获取最近一条
        last = get_last_transition(session_id, state_kind='mood_cluster')
        assert last is not None
        assert last.from_state == 'b'
        assert last.to_state == 'c'
        assert last.transition_at == 2000.0
    finally:
        _cleanup_session(session_id)


def test_get_last_transition_none():
    """无转换记录返回 None。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        last = get_last_transition(session_id, state_kind='mood_cluster')
        assert last is None
    finally:
        _cleanup_session(session_id)


# ---------- 5. ExpressionPlan 禁区验证测试 ----------

def test_validate_expression_plan_scope_all_false():
    """默认全 False 时通过。"""
    is_valid, violations = validate_expression_plan_scope()
    assert is_valid is True
    assert violations == []


def test_validate_expression_plan_scope_facts_violation():
    """modifies_facts=True 时违规。"""
    is_valid, violations = validate_expression_plan_scope(modifies_facts=True)
    assert is_valid is False
    assert 'facts' in violations


def test_validate_expression_plan_scope_safety_violation():
    """modifies_safety=True 时违规。"""
    is_valid, violations = validate_expression_plan_scope(modifies_safety=True)
    assert is_valid is False
    assert 'safety' in violations


def test_validate_expression_plan_scope_multiple_violations():
    """多禁区违规返回多个 violation。"""
    is_valid, violations = validate_expression_plan_scope(
        modifies_facts=True,
        modifies_tool_results=True,
        modifies_user_boundary=True,
    )
    assert is_valid is False
    assert len(violations) == 3
    assert 'facts' in violations
    assert 'tool_results' in violations
    assert 'user_boundary' in violations


# ---------- 6. ExpressionPlan 创建测试 ----------

def test_create_expression_plan_basic():
    """基础创建：默认向量 + 默认迟滞。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        plan = create_expression_plan(
            session_id, expression_act='quiet_waiting',
        )
        assert plan.id is not None
        assert plan.session_id == session_id
        assert plan.decision_id is None
        assert plan.intensity_plan_id is None
        # 默认向量来自 quiet_waiting
        expected = EXPRESSION_ACT_DEFAULT_VECTORS['quiet_waiting']
        assert plan.vector.warmth == expected['warmth']
        assert plan.vector.restraint == expected['restraint']
        # 默认迟滞参数
        assert plan.hysteresis.minimum_state_duration == 30.0
        assert plan.hysteresis.hysteresis_margin == 0.1
        assert plan.hysteresis.transition_momentum == 0.5
        # 默认作用范围
        assert plan.adjusts_tone is True
        assert plan.adjusts_length is True
        assert plan.adjusts_directness is True
        assert plan.adjusts_live2d_intensity is True
        assert plan.adjusts_voice_prosody is False
        # 禁区全部 False
        assert plan.modifies_facts is False
        assert plan.modifies_safety is False
        assert plan.modifies_tool_results is False
        assert plan.modifies_permissions is False
        assert plan.modifies_user_boundary is False
        # 协议版本
        assert plan.protocol_version == 'expression-plan-v1'
    finally:
        _cleanup_session(session_id)


def test_create_expression_plan_with_act_default_vector():
    """根据 act 自动填充向量。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        plan = create_expression_plan(
            session_id, expression_act='firm_care',
        )
        expected = EXPRESSION_ACT_DEFAULT_VECTORS['firm_care']
        assert plan.vector.concern == expected['concern']  # 0.9
        assert plan.vector.warmth == expected['warmth']
        assert plan.expression_act == 'firm_care'
    finally:
        _cleanup_session(session_id)


def test_create_expression_plan_with_custom_vector():
    """自定义向量被保留。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        v = ExpressionVector(
            warmth=0.9, playfulness=0.1, directness=0.8,
            concern=0.7, initiative=0.6, restraint=0.4, energy=0.5,
        )
        plan = create_expression_plan(
            session_id, vector=v,
        )
        assert plan.vector.warmth == 0.9
        assert plan.vector.playfulness == 0.1
        assert plan.vector.directness == 0.8
    finally:
        _cleanup_session(session_id)


def test_create_expression_plan_with_source_messages():
    """source_hash 正确计算。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        msgs = [
            {"id": "m1", "role": "user", "content": "我去跑测试了"},
            {"id": "m2", "role": "assistant", "content": "好的，等你回来"},
        ]
        plan = create_expression_plan(
            session_id, source_messages=msgs,
        )
        # source_hash 应为 64 字符 hex
        assert len(plan.source_hash) == 64
        assert plan.source_hash != ''
    finally:
        _cleanup_session(session_id)


def test_create_expression_plan_idempotency():
    """相同 (session_id, decision_id) 重复调用幂等。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        decision_id = _make_fake_decision(session_id, unique_suffix="idem")
        plan1 = create_expression_plan(
            session_id, decision_id=decision_id,
            expression_act='gentle_urge',
        )
        plan2 = create_expression_plan(
            session_id, decision_id=decision_id,
            expression_act='gentle_urge',
        )
        # 幂等：返回同一 plan
        assert plan1.id == plan2.id
        assert plan1.idempotency_key == plan2.idempotency_key
    finally:
        _cleanup_session(session_id)


def test_create_expression_plan_default_hysteresis():
    """未提供 hysteresis 时使用 DEFAULT_HYSTERESIS_PARAMS。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        plan = create_expression_plan(session_id)
        assert plan.hysteresis.minimum_state_duration == \
            DEFAULT_HYSTERESIS_PARAMS['minimum_state_duration']
        assert plan.hysteresis.hysteresis_margin == \
            DEFAULT_HYSTERESIS_PARAMS['hysteresis_margin']
        assert plan.hysteresis.transition_momentum == \
            DEFAULT_HYSTERESIS_PARAMS['transition_momentum']
    finally:
        _cleanup_session(session_id)


def test_create_expression_plan_forbidden_modification_raises():
    """传入 modifies_facts=True 时抛出 ValueError。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        with pytest.raises(ValueError, match="禁区"):
            create_expression_plan(
                session_id, modifies_facts=True,
            )
        with pytest.raises(ValueError, match="禁区"):
            create_expression_plan(
                session_id, modifies_safety=True,
            )
    finally:
        _cleanup_session(session_id)


# ---------- 7. 迟滞应用到向量测试 ----------

def test_apply_hysteresis_to_vector_all_pass():
    """所有维度通过迟滞检查（无 last_transition_at）。"""
    db.init_db()
    h = HysteresisParams(minimum_state_duration=30.0, hysteresis_margin=0.1,
                          transition_momentum=0.5)
    current = ExpressionVector(warmth=0.7, playfulness=0.6, directness=0.7,
                                concern=0.7, initiative=0.7, restraint=0.3,
                                energy=0.7)
    target = ExpressionVector(warmth=0.8, playfulness=0.7, directness=0.8,
                              concern=0.8, initiative=0.8, restraint=0.2,
                              energy=0.8)
    final, results = apply_hysteresis_to_vector(
        current, target, hysteresis=h,
        last_transition_at=None, now=1000.0,
    )
    # 所有维度应通过（无 last_transition_at）
    for dim in ALL_DIMENSIONS:
        should, reason = results[dim]
        assert should is True
        assert reason == 'ok'
    # final 向量等于 target
    assert final.warmth == 0.8
    assert final.energy == 0.8


def test_apply_hysteresis_to_vector_some_blocked():
    """部分维度被迟滞拒绝（时间不足）。"""
    h = HysteresisParams(minimum_state_duration=30.0, hysteresis_margin=0.1,
                          transition_momentum=0.5)
    # effective_duration = 30/0.5 = 60，elapsed=10 → 时间不足
    current = ExpressionVector(warmth=0.7, playfulness=0.6, directness=0.7,
                                concern=0.7, initiative=0.7, restraint=0.3,
                                energy=0.7)
    target = ExpressionVector(warmth=0.8, playfulness=0.7, directness=0.8,
                              concern=0.8, initiative=0.8, restraint=0.2,
                              energy=0.8)
    final, results = apply_hysteresis_to_vector(
        current, target, hysteresis=h,
        last_transition_at=1000.0, now=1010.0,
    )
    # 所有维度都应被拒绝（minimum_state_duration_not_met）
    for dim in ALL_DIMENSIONS:
        should, reason = results[dim]
        assert should is False
        assert reason == 'minimum_state_duration_not_met'
    # final 向量保留 current
    assert final.warmth == 0.7
    assert final.playfulness == 0.6


def test_apply_hysteresis_to_vector_no_last_transition():
    """无 last_transition_at 时全部通过时间检查。"""
    h = HysteresisParams(minimum_state_duration=30.0, hysteresis_margin=0.1,
                          transition_momentum=0.5)
    current = ExpressionVector()
    target = ExpressionVector(warmth=0.8, playfulness=0.8, directness=0.8,
                              concern=0.8, initiative=0.8, restraint=0.2,
                              energy=0.8)
    final, results = apply_hysteresis_to_vector(
        current, target, hysteresis=h,
        last_transition_at=None, now=1000.0,
    )
    # 无 last_transition_at，时间检查跳过；但 margin 检查仍生效
    # current=0.5，target=0.8 > 0.5，需要 current >= 0.5 + 0.1 = 0.6
    # current=0.5 < 0.6 → 拒绝（hysteresis_margin_not_met）
    for dim in ALL_DIMENSIONS:
        should, reason = results[dim]
        if dim == 'restraint':
            # target=0.2 < 0.5，向下转换，需要 current <= 0.5 - 0.1 = 0.4
            # current=0.5 > 0.4 → 拒绝
            assert should is False
            assert reason == 'hysteresis_margin_not_met'
        else:
            # 向上转换：current=0.5 < 0.6 → 拒绝
            assert should is False
            assert reason == 'hysteresis_margin_not_met'


def test_apply_hysteresis_to_vector_returns_per_dimension_results():
    """返回 per_dimension_results 含 7 维。"""
    h = HysteresisParams()
    current = ExpressionVector()
    target = ExpressionVector()
    final, results = apply_hysteresis_to_vector(
        current, target, hysteresis=h,
        last_transition_at=None, now=1000.0,
    )
    assert len(results) == 7
    for dim in ALL_DIMENSIONS:
        assert dim in results
        should, reason = results[dim]
        assert isinstance(should, bool)
        assert isinstance(reason, str)


# ---------- 8. 查询测试 ----------

def test_get_expression_plan():
    """按 ID 查询。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        plan = create_expression_plan(
            session_id, expression_act='worried_checkin',
        )
        loaded = get_expression_plan(plan.id)
        assert loaded is not None
        assert loaded.id == plan.id
        assert loaded.expression_act == 'worried_checkin'
        # 验证 7 维向量
        assert loaded.vector.warmth == plan.vector.warmth
        assert loaded.vector.concern == plan.vector.concern
    finally:
        _cleanup_session(session_id)


def test_get_expression_plan_by_decision():
    """按 decision_id 查询。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        decision_id = _make_fake_decision(session_id, unique_suffix="gbd")
        plan = create_expression_plan(
            session_id, decision_id=decision_id,
            expression_act='gentle_urge',
        )
        loaded = get_expression_plan_by_decision(decision_id)
        assert loaded is not None
        assert loaded.id == plan.id
        assert loaded.decision_id == decision_id
    finally:
        _cleanup_session(session_id)


def test_list_expression_plans_by_session():
    """按 session 列表查询。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        # 创建 3 个 plan（需要不同的 decision_id 避免幂等冲突）
        d1 = _make_fake_decision(session_id, unique_suffix="l1")
        create_expression_plan(
            session_id, decision_id=d1, expression_act='quiet_waiting',
        )
        time.sleep(0.01)
        d2 = _make_fake_decision(session_id, unique_suffix="l2")
        create_expression_plan(
            session_id, decision_id=d2, expression_act='gentle_urge',
        )
        time.sleep(0.01)
        d3 = _make_fake_decision(session_id, unique_suffix="l3")
        create_expression_plan(
            session_id, decision_id=d3, expression_act='firm_care',
        )
        plans = list_expression_plans_by_session(session_id)
        assert len(plans) >= 3
        # 按 created_at 倒序
        assert plans[0].created_at >= plans[1].created_at
        assert plans[1].created_at >= plans[2].created_at
    finally:
        _cleanup_session(session_id)


# ---------- 9. schema 测试 ----------

def test_schema_version_is_54():
    """migration 54 后 schema_version = '54'。"""
    db.init_db()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        assert row[0] == "59"
    finally:
        conn.close()


def test_expression_plans_table_exists():
    """expression_plans 表存在。"""
    db.init_db()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='expression_plans'"
        ).fetchone()
        assert row is not None
        assert row["name"] == "expression_plans"
    finally:
        conn.close()


def test_expression_state_transitions_table_exists():
    """expression_state_transitions 表存在。"""
    db.init_db()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='expression_state_transitions'"
        ).fetchone()
        assert row is not None
        assert row["name"] == "expression_state_transitions"
    finally:
        conn.close()


def test_expression_plans_7_dimensions_check():
    """CHECK 约束验证 7 维向量每维 0~1。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        conn = db.connect()
        try:
            now = db.now()
            # 验证 0~1 都可插入
            for v in (0.0, 0.5, 1.0):
                conn.execute(
                    "INSERT INTO expression_plans"
                    " (id, session_id, warmth, playfulness, directness,"
                    "  concern, initiative, restraint, energy,"
                    "  minimum_state_duration, hysteresis_margin, transition_momentum,"
                    "  adjusts_tone, adjusts_length, adjusts_directness,"
                    "  adjusts_live2d_intensity, adjusts_voice_prosody,"
                    "  modifies_facts, modifies_safety, modifies_tool_results,"
                    "  modifies_permissions, modifies_user_boundary,"
                    "  source_hash, idempotency_key, protocol_version, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 30.0, 0.1, 0.5, 1, 1, 1, 1, 0,"
                    "  0, 0, 0, 0, 0, '', ?, 'expression-plan-v1', ?)",
                    (db.new_id(), session_id, v, v, v, v, v, v, v,
                     db.new_id(), now),
                )
                conn.commit()
            # 越界值应被拒绝
            for bad in (1.5, -0.1, 2.0):
                with pytest.raises(Exception):
                    conn.execute(
                        "INSERT INTO expression_plans"
                        " (id, session_id, warmth, playfulness, directness,"
                        "  concern, initiative, restraint, energy,"
                        "  minimum_state_duration, hysteresis_margin, transition_momentum,"
                        "  adjusts_tone, adjusts_length, adjusts_directness,"
                        "  adjusts_live2d_intensity, adjusts_voice_prosody,"
                        "  modifies_facts, modifies_safety, modifies_tool_results,"
                        "  modifies_permissions, modifies_user_boundary,"
                        "  source_hash, idempotency_key, protocol_version, created_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 30.0, 0.1, 0.5, 1, 1, 1, 1, 0,"
                        "  0, 0, 0, 0, 0, '', ?, 'expression-plan-v1', ?)",
                        (db.new_id(), session_id, bad, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5,
                         db.new_id(), now),
                    )
        finally:
            conn.close()
    finally:
        _cleanup_session(session_id)


# ---------- 10. 关键约束测试 ----------

def test_expression_plan_does_not_modify_facts():
    """ExpressionPlan 不影响事实（modifies_facts 永远 False）。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        plan = create_expression_plan(session_id)
        assert plan.modifies_facts is False
        # 落库的记录也应该是 False
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT modifies_facts, modifies_safety, modifies_tool_results,"
                "       modifies_permissions, modifies_user_boundary "
                "FROM expression_plans WHERE id=?",
                (plan.id,),
            ).fetchone()
            assert row["modifies_facts"] == 0
            assert row["modifies_safety"] == 0
            assert row["modifies_tool_results"] == 0
            assert row["modifies_permissions"] == 0
            assert row["modifies_user_boundary"] == 0
        finally:
            conn.close()
    finally:
        _cleanup_session(session_id)


def test_expression_plan_does_not_modify_safety():
    """ExpressionPlan 不影响安全结论。

    传入 modifies_safety=True 应抛出 ValueError。
    """
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        with pytest.raises(ValueError):
            create_expression_plan(session_id, modifies_safety=True)
        # 落库的记录应为 0 条
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM expression_plans WHERE session_id=?",
                (session_id,),
            ).fetchone()
            assert row["c"] == 0
        finally:
            conn.close()
    finally:
        _cleanup_session(session_id)


def test_threshold_no_frequent_jump_integration():
    """阈值附近多次小变化不导致频繁跳变（集成测试）。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        h = HysteresisParams(minimum_state_duration=30.0, hysteresis_margin=0.1,
                              transition_momentum=0.5)
        # 模拟阈值附近多次小变化：current=0.51~0.55，target=0.8
        # 第一次转换（1000 秒）：current=0.55 < 0.5+0.1=0.6 → 拒绝
        last_t = 1000.0
        # 第一次转换尝试
        should1, _ = should_transition_state(
            current_value=0.55, target_value=0.8,
            threshold=0.5, hysteresis=h,
            last_transition_at=None, now=last_t,
        )
        assert should1 is False  # margin 不够

        # 假设 current 升到 0.65，可以转换
        should2, _ = should_transition_state(
            current_value=0.65, target_value=0.8,
            threshold=0.5, hysteresis=h,
            last_transition_at=None, now=last_t,
        )
        assert should2 is True

        # 转换后 last_transition_at = 1000
        # 5 秒后再次尝试转换（current 略微变化到 0.66）
        should3, reason3 = should_transition_state(
            current_value=0.66, target_value=0.85,
            threshold=0.5, hysteresis=h,
            last_transition_at=1000.0, now=1005.0,
        )
        # 时间不足（effective_duration=60, elapsed=5），拒绝
        assert should3 is False
        assert reason3 == 'minimum_state_duration_not_met'

        # 70 秒后再次尝试
        should4, _ = should_transition_state(
            current_value=0.7, target_value=0.85,
            threshold=0.5, hysteresis=h,
            last_transition_at=1000.0, now=1070.0,
        )
        # elapsed=70 > 60，margin 满足 → 允许
        assert should4 is True
    finally:
        _cleanup_session(session_id)


# ---------- 11. 常量完整性测试 ----------

def test_all_dimensions_count():
    """ALL_DIMENSIONS 7 维。"""
    assert len(ALL_DIMENSIONS) == 7
    assert ExpressionDimension.WARMTH in ALL_DIMENSIONS
    assert ExpressionDimension.PLAYFULNESS in ALL_DIMENSIONS
    assert ExpressionDimension.DIRECTNESS in ALL_DIMENSIONS
    assert ExpressionDimension.CONCERN in ALL_DIMENSIONS
    assert ExpressionDimension.INITIATIVE in ALL_DIMENSIONS
    assert ExpressionDimension.RESTRAINT in ALL_DIMENSIONS
    assert ExpressionDimension.ENERGY in ALL_DIMENSIONS


def test_dimension_descriptions_complete():
    """DIMENSION_DESCRIPTIONS 覆盖 7 维。"""
    for dim in ALL_DIMENSIONS:
        assert dim in DIMENSION_DESCRIPTIONS
        assert isinstance(DIMENSION_DESCRIPTIONS[dim], str)
        assert DIMENSION_DESCRIPTIONS[dim]  # 非空


def test_expression_act_default_vectors_complete():
    """EXPRESSION_ACT_DEFAULT_VECTORS 覆盖 6 种 ExpressionAct。"""
    expected_acts = {
        'playful_complaint', 'gentle_urge', 'firm_care',
        'worried_checkin', 'expectant_followup', 'quiet_waiting',
    }
    assert set(EXPRESSION_ACT_DEFAULT_VECTORS.keys()) == expected_acts
    for act, vec_dict in EXPRESSION_ACT_DEFAULT_VECTORS.items():
        # 每个向量必须含 7 维
        assert set(vec_dict.keys()) == set(ALL_DIMENSIONS)
        for dim, v in vec_dict.items():
            assert 0.0 <= v <= 1.0


def test_expression_plan_scope_constants():
    """作用范围与禁区常量均为 5 项。"""
    assert len(EXPRESSION_PLAN_ALLOWED_ADJUSTMENTS) == 5
    assert len(EXPRESSION_PLAN_FORBIDDEN_MODIFICATIONS) == 5
    # 5 项作用范围
    assert 'tone' in EXPRESSION_PLAN_ALLOWED_ADJUSTMENTS
    assert 'length' in EXPRESSION_PLAN_ALLOWED_ADJUSTMENTS
    assert 'directness' in EXPRESSION_PLAN_ALLOWED_ADJUSTMENTS
    assert 'live2d_intensity' in EXPRESSION_PLAN_ALLOWED_ADJUSTMENTS
    assert 'voice_prosody' in EXPRESSION_PLAN_ALLOWED_ADJUSTMENTS
    # 5 项禁区
    assert 'facts' in EXPRESSION_PLAN_FORBIDDEN_MODIFICATIONS
    assert 'safety' in EXPRESSION_PLAN_FORBIDDEN_MODIFICATIONS
    assert 'tool_results' in EXPRESSION_PLAN_FORBIDDEN_MODIFICATIONS
    assert 'permissions' in EXPRESSION_PLAN_FORBIDDEN_MODIFICATIONS
    assert 'user_boundary' in EXPRESSION_PLAN_FORBIDDEN_MODIFICATIONS
