"""EAP v0.2 主动强度阶梯与 Live2D 低干扰行为测试（spec 第 5.10 节）。

覆盖：
1. 授权检查测试（4-5 个）
2. Live2D 动作构建测试（3-4 个）
3. 气泡文本构建测试（2-3 个）
4. 最低足够强度选择测试（8-10 个）
5. 强度计划创建测试（4-5 个）
6. 完整流程测试（3-4 个）
7. 查询测试（2-3 个）
8. schema 测试（2 个）
"""
import json
import time
from unittest.mock import patch

import pytest

from app import db
from app.proactive import candidates as candidates_mod
from app.proactive import decision as decision_mod
from app.proactive import intensity as intensity_mod
from app.proactive import presence as presence_mod
from app.proactive.candidates import (
    CandidateKind,
    CandidateStatus,
    ProactiveCandidate,
)
from app.proactive.decision import (
    DecisionAction,
    ExpressionAct,
    LLMAdvice,
    decide_candidate,
)
from app.proactive.intensity import (
    ALL_LEVELS,
    DEFAULT_BUBBLE_TEMPLATES,
    DEFAULT_LEVEL_AUTHORIZATION,
    LEVEL_CHANNELS,
    LEVEL_DESCRIPTIONS,
    LEVEL_NAMES,
    LIVE2D_ACTION_TEMPLATES,
    IntensityLevel,
    IntensityPlan,
    build_bubble_text,
    build_live2d_action,
    create_intensity_plan,
    get_intensity_plan,
    get_intensity_plan_by_decision,
    is_level_authorized,
    list_intensity_plans_by_session,
    plan_intensity_for_decision,
    select_minimum_sufficient_level,
)
from app.proactive.presence import (
    PresenceRecord,
    UserStatus,
)


# ---------- 公共 fixture ----------

def _setup_session(session_id: str) -> None:
    """插入测试 session。"""
    now = db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (session_id, "intensity 测试", now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _cleanup_session(session_id: str) -> None:
    conn = db.connect()
    try:
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


def _make_candidate(
    session_id: str,
    *,
    candidate_kind: str = CandidateKind.RETURN_FOLLOWUP,
    topic: str = "测试结果",
    source_messages=None,
    open_thread: str = "想问问测试跑得怎么样了",
    now: float = None,
) -> ProactiveCandidate:
    """创建一个测试候选。"""
    if source_messages is None:
        source_messages = [
            {"id": "m1", "role": "user", "content": "我去跑测试了"},
            {"id": "m2", "role": "assistant", "content": "好的，等你回来"},
        ]
    if now is None:
        now = db.now()
    return candidates_mod.create_candidate(
        session_id,
        candidate_kind=candidate_kind,
        topic=topic,
        source_messages=source_messages,
        open_thread=open_thread,
        now=now,
    )


def _set_proactive_enabled(value: str = "1") -> None:
    """设置 proactive_enabled 并清除其他可能干扰的设置。"""
    db.set_setting("proactive_enabled", value)
    db.set_setting("proactive_emergency_stop", "0")
    db.set_setting("proactive_rejected_topics", "")
    db.set_setting("proactive_rejected_kinds", "")


def _set_default_auth_settings() -> None:
    """重置桌面通知和外部渠道授权为默认（关闭）。"""
    db.set_setting("proactive_desktop_notification_enabled", "0")
    db.set_setting("proactive_external_channels_enabled", "0")


def _make_presence_record(
    session_id: str,
    user_status: str,
    *,
    is_active: bool = True,
    expires_at=None,
    expected_return_at=None,
) -> PresenceRecord:
    """构造一个内存中的 PresenceRecord（不落库）。"""
    now = db.now()
    return PresenceRecord(
        id=db.new_id(), session_id=session_id, user_status=user_status,
        detected_at=now, expires_at=expires_at,
        expected_return_at=expected_return_at,
        open_thread=False, open_thread_topic=None,
        source_message_id=None, priority=0, is_active=is_active,
    )


def _make_send_decision(session_id: str, *, approach_value=None, intensity=None,
                        expression_act=None) -> str:
    """创建一个 send 决策并返回 decision_id。

    通过 LLM advice 强制 SEND，并允许覆盖 approach_value（通过 patch evaluate_*）。
    """
    candidate = _make_candidate(session_id)
    advice = LLMAdvice(
        decision=DecisionAction.SEND, intensity=intensity,
        expression_act=expression_act,
        topic="t", confidence=0.8,
        reason_codes=["open_thread"], source_refs=["m1"],
    )
    online_presence = _make_presence_record(session_id, UserStatus.ONLINE)
    local_now = time.localtime(db.now())
    ts_2pm = time.mktime((
        local_now.tm_year, local_now.tm_mon, local_now.tm_mday,
        14, 0, 0, 0, 0, -1,
    ))

    if approach_value is not None:
        # 通过 patch 让 approach_value 等于指定值
        # effective_drive - contact_cost = approach_value
        # 简单做法：让 effective_drive=approach_value, contact_cost=0
        with patch.object(decision_mod, "get_current_presence", return_value=online_presence):
            with patch.object(decision_mod, "evaluate_approach_drive", return_value=approach_value):
                with patch.object(decision_mod, "evaluate_contact_cost", return_value=0.0):
                    with patch.object(decision_mod, "compute_effective_drive", return_value=approach_value):
                        decision = decide_candidate(
                            candidate.id, llm_advice=advice, now=ts_2pm,
                        )
    else:
        with patch.object(decision_mod, "get_current_presence", return_value=online_presence):
            decision = decide_candidate(
                candidate.id, llm_advice=advice, now=ts_2pm,
            )
    return decision.id


# ---------- 1. 授权检查测试 ----------

def test_is_level_authorized_default():
    """默认 settings（proactive_enabled='1', desktop=0, external=0）下 Level 0-3 True，4-5 False。"""
    db.init_db()
    _set_proactive_enabled("1")
    _set_default_auth_settings()
    try:
        settings = {
            "proactive_enabled": "1",
            "proactive_desktop_notification_enabled": "0",
            "proactive_external_channels_enabled": "0",
        }
        assert is_level_authorized(0, settings=settings) is True
        assert is_level_authorized(1, settings=settings) is True
        assert is_level_authorized(2, settings=settings) is True
        assert is_level_authorized(3, settings=settings) is True
        assert is_level_authorized(4, settings=settings) is False
        assert is_level_authorized(5, settings=settings) is False
    finally:
        _set_default_auth_settings()


def test_is_level_authorized_with_desktop_enabled():
    """Level 4 在 desktop_notification_enabled='1' 时 True。"""
    db.init_db()
    settings = {
        "proactive_enabled": "1",
        "proactive_desktop_notification_enabled": "1",
        "proactive_external_channels_enabled": "0",
    }
    assert is_level_authorized(4, settings=settings) is True
    assert is_level_authorized(5, settings=settings) is False


def test_is_level_authorized_with_external_enabled():
    """Level 5 remains hard disabled even if a legacy value says enabled."""
    db.init_db()
    settings = {
        "proactive_enabled": "1",
        "proactive_desktop_notification_enabled": "0",
        "proactive_external_channels_enabled": "1",
    }
    assert is_level_authorized(4, settings=settings) is False
    assert is_level_authorized(5, settings=settings) is False


def test_is_level_authorized_proactive_disabled():
    """proactive_enabled='0' 时 Level 1-5 全部 False，Level 0 仍 True。"""
    db.init_db()
    settings = {
        "proactive_enabled": "0",
        "proactive_desktop_notification_enabled": "1",
        "proactive_external_channels_enabled": "1",
    }
    assert is_level_authorized(0, settings=settings) is True
    assert is_level_authorized(1, settings=settings) is False
    assert is_level_authorized(2, settings=settings) is False
    assert is_level_authorized(3, settings=settings) is False
    assert is_level_authorized(4, settings=settings) is False
    assert is_level_authorized(5, settings=settings) is False


def test_is_level_authorized_invalid_level():
    """level < 0 或 > 5 抛出 ValueError。"""
    db.init_db()
    with pytest.raises(ValueError):
        is_level_authorized(-1)
    with pytest.raises(ValueError):
        is_level_authorized(6)
    with pytest.raises(ValueError):
        is_level_authorized(100)


# ---------- 2. Live2D 动作构建测试 ----------

def test_build_live2d_action_with_valid_act():
    """playful_complaint → 对应模板。"""
    action = build_live2d_action("playful_complaint")
    assert action is not None
    assert action == LIVE2D_ACTION_TEMPLATES["playful_complaint"]
    assert action["gaze"] == "sideways"
    assert action["expression"] == "pout"
    assert action["motion"] == "head_tilt"


def test_build_live2d_action_with_none():
    """expression_act=None 返回默认 quiet_waiting 模板。"""
    action = build_live2d_action(None)
    assert action is not None
    assert action == LIVE2D_ACTION_TEMPLATES["quiet_waiting"]
    assert action["gaze"] == "down"
    assert action["expression"] == "calm"
    assert action["motion"] == "idle"


def test_build_live2d_action_invalid_act():
    """无效 expression_act 返回默认 quiet_waiting 模板。"""
    action = build_live2d_action("not_a_real_act")
    assert action is not None
    assert action == LIVE2D_ACTION_TEMPLATES["quiet_waiting"]


def test_build_live2d_action_returns_copy():
    """返回的 dict 应是副本，修改不影响模板。"""
    action = build_live2d_action("gentle_urge")
    action["gaze"] = "modified"
    # 模板未被修改
    assert LIVE2D_ACTION_TEMPLATES["gentle_urge"]["gaze"] == "direct"


# ---------- 3. 气泡文本构建测试 ----------

def test_build_bubble_text_with_valid_act():
    """有效 expression_act 返回对应文本。"""
    text = build_bubble_text("gentle_urge")
    assert text == DEFAULT_BUBBLE_TEMPLATES["gentle_urge"]
    assert text == "（期待地看着你）"


def test_build_bubble_text_with_none():
    """expression_act=None 返回默认 quiet_waiting 文本。"""
    text = build_bubble_text(None)
    assert text == DEFAULT_BUBBLE_TEMPLATES["quiet_waiting"]
    assert text == "（在这里）"


def test_build_bubble_text_invalid_act():
    """无效 expression_act 返回默认 quiet_waiting 文本。"""
    text = build_bubble_text("not_a_real_act")
    assert text == DEFAULT_BUBBLE_TEMPLATES["quiet_waiting"]


# ---------- 4. 最低足够强度选择测试 ----------

def test_select_level_zero_when_negative_approach_value():
    """approach_value < 0 → Level 0。"""
    db.init_db()
    _set_proactive_enabled("1")
    _set_default_auth_settings()
    try:
        settings = {
            "proactive_enabled": "1",
            "proactive_desktop_notification_enabled": "0",
            "proactive_external_channels_enabled": "0",
        }
        level = select_minimum_sufficient_level(
            approach_value=-0.5, settings=settings,
        )
        assert level == 0
    finally:
        _set_default_auth_settings()


def test_select_level_one_when_low_approach_value_and_busy():
    """approach_value=0.05, presence.away_busy → Level 1（无文字 Live2D）。"""
    db.init_db()
    _set_proactive_enabled("1")
    _set_default_auth_settings()
    try:
        settings = {
            "proactive_enabled": "1",
            "proactive_desktop_notification_enabled": "0",
            "proactive_external_channels_enabled": "0",
        }
        session_id = db.new_id()
        _setup_session(session_id)
        try:
            presence = _make_presence_record(session_id, UserStatus.AWAY_BUSY)
            level = select_minimum_sufficient_level(
                approach_value=0.05, presence=presence, settings=settings,
            )
            assert level == 1
        finally:
            _cleanup_session(session_id)
    finally:
        _set_default_auth_settings()


def test_select_level_two_when_low_approach_value_online():
    """approach_value=0.05, presence.online → Level 2（无通知气泡）。"""
    db.init_db()
    _set_proactive_enabled("1")
    _set_default_auth_settings()
    try:
        settings = {
            "proactive_enabled": "1",
            "proactive_desktop_notification_enabled": "0",
            "proactive_external_channels_enabled": "0",
        }
        session_id = db.new_id()
        _setup_session(session_id)
        try:
            presence = _make_presence_record(session_id, UserStatus.ONLINE)
            level = select_minimum_sufficient_level(
                approach_value=0.2, presence=presence, settings=settings,
            )
            assert level == 2
        finally:
            _cleanup_session(session_id)
    finally:
        _set_default_auth_settings()


def test_select_level_one_when_quiet_waiting_state():
    """approach_value=0.2, presence.do_not_disturb → Level 1。"""
    db.init_db()
    _set_proactive_enabled("1")
    _set_default_auth_settings()
    try:
        settings = {
            "proactive_enabled": "1",
            "proactive_desktop_notification_enabled": "0",
            "proactive_external_channels_enabled": "0",
        }
        session_id = db.new_id()
        _setup_session(session_id)
        try:
            presence = _make_presence_record(session_id, UserStatus.DO_NOT_DISTURB)
            level = select_minimum_sufficient_level(
                approach_value=0.2, presence=presence, settings=settings,
            )
            assert level == 1
        finally:
            _cleanup_session(session_id)
    finally:
        _set_default_auth_settings()


def test_select_level_three_when_medium_approach_value():
    """approach_value=0.4 → Level 3。"""
    db.init_db()
    _set_proactive_enabled("1")
    _set_default_auth_settings()
    try:
        settings = {
            "proactive_enabled": "1",
            "proactive_desktop_notification_enabled": "0",
            "proactive_external_channels_enabled": "0",
        }
        level = select_minimum_sufficient_level(
            approach_value=0.4, settings=settings,
        )
        assert level == 3
    finally:
        _set_default_auth_settings()


def test_select_level_three_when_high_approach_value_unauthorized_desktop():
    """approach_value=0.8, desktop 未授权 → Level 3（不升 4）。"""
    db.init_db()
    _set_proactive_enabled("1")
    _set_default_auth_settings()
    try:
        settings = {
            "proactive_enabled": "1",
            "proactive_desktop_notification_enabled": "0",
            "proactive_external_channels_enabled": "0",
        }
        level = select_minimum_sufficient_level(
            approach_value=0.8, settings=settings,
        )
        assert level == 3
    finally:
        _set_default_auth_settings()


def test_select_level_four_when_high_approach_value_authorized():
    """approach_value=0.8, desktop 已授权 → Level 4。"""
    db.init_db()
    _set_proactive_enabled("1")
    try:
        settings = {
            "proactive_enabled": "1",
            "proactive_desktop_notification_enabled": "1",
            "proactive_external_channels_enabled": "0",
        }
        level = select_minimum_sufficient_level(
            approach_value=0.8, settings=settings,
        )
        assert level == 4
    finally:
        _set_default_auth_settings()


def test_select_level_with_llm_advice_lower():
    """LLM 建议 Level 1，本地需 Level 3 → 用 Level 1（允许降级）。"""
    db.init_db()
    _set_proactive_enabled("1")
    _set_default_auth_settings()
    try:
        settings = {
            "proactive_enabled": "1",
            "proactive_desktop_notification_enabled": "0",
            "proactive_external_channels_enabled": "0",
        }
        level = select_minimum_sufficient_level(
            approach_value=0.4, llm_advice_intensity=1, settings=settings,
        )
        assert level == 1
    finally:
        _set_default_auth_settings()


def test_select_level_with_llm_advice_higher_unauthorized():
    """LLM 建议 Level 5，未授权 → 降级到 Level 3。"""
    db.init_db()
    _set_proactive_enabled("1")
    _set_default_auth_settings()
    try:
        settings = {
            "proactive_enabled": "1",
            "proactive_desktop_notification_enabled": "0",
            "proactive_external_channels_enabled": "0",
        }
        level = select_minimum_sufficient_level(
            approach_value=0.4, llm_advice_intensity=5, settings=settings,
        )
        # LLM 建议 5 未授权 → 降级到 local_required（3）
        assert level == 3
    finally:
        _set_default_auth_settings()


def test_select_level_with_llm_advice_within_bounds():
    """LLM 建议 Level 2，本地需 Level 2 → Level 2。"""
    db.init_db()
    _set_proactive_enabled("1")
    _set_default_auth_settings()
    try:
        settings = {
            "proactive_enabled": "1",
            "proactive_desktop_notification_enabled": "0",
            "proactive_external_channels_enabled": "0",
        }
        session_id = db.new_id()
        _setup_session(session_id)
        try:
            presence = _make_presence_record(session_id, UserStatus.ONLINE)
            level = select_minimum_sufficient_level(
                approach_value=0.2, llm_advice_intensity=2,
                presence=presence, settings=settings,
            )
            assert level == 2
        finally:
            _cleanup_session(session_id)
    finally:
        _set_default_auth_settings()


def test_select_level_with_llm_advice_higher_authorized_uses_minimum():
    """LLM 建议 Level 4（已授权），本地需 Level 3 → 用 Level 3（不强制升级）。"""
    db.init_db()
    _set_proactive_enabled("1")
    try:
        settings = {
            "proactive_enabled": "1",
            "proactive_desktop_notification_enabled": "1",
            "proactive_external_channels_enabled": "0",
        }
        level = select_minimum_sufficient_level(
            approach_value=0.4, llm_advice_intensity=4, settings=settings,
        )
        # local_required=3，LLM 建议 4 > 3 → 不强制升级，用 3
        assert level == 3
    finally:
        _set_default_auth_settings()


def test_select_level_disabled_proactive_returns_zero():
    """proactive_enabled='0' 时所有级别降级到 Level 0。"""
    db.init_db()
    settings = {
        "proactive_enabled": "0",
        "proactive_desktop_notification_enabled": "1",
        "proactive_external_channels_enabled": "1",
    }
    level = select_minimum_sufficient_level(
        approach_value=0.8, llm_advice_intensity=5, settings=settings,
    )
    assert level == 0


# ---------- 5. 强度计划创建测试 ----------

def test_create_intensity_plan_basic():
    """基础创建：Level 3 计划，无 Live2D/气泡。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    _set_default_auth_settings()
    try:
        decision_id = _make_send_decision(session_id, approach_value=0.4, intensity=3)
        plan = create_intensity_plan(
            decision_id, session_id,
            level=3, approach_value=0.4,
            llm_advice_intensity=3,
            reason="测试",
        )
        assert plan.level == 3
        assert plan.channel == "chat"
        assert plan.live2d_action is None
        assert plan.bubble_text is None
        assert plan.reason == "测试"
        assert plan.protocol_version == "proactive-decision-v2"
    finally:
        _set_default_auth_settings()
        _cleanup_session(session_id)


def test_create_intensity_plan_level_1_with_live2d_action():
    """Level 1 计划填充 live2d_action。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    _set_default_auth_settings()
    try:
        decision_id = _make_send_decision(
            session_id, approach_value=0.05, intensity=1,
            expression_act=ExpressionAct.QUIET_WAITING,
        )
        plan = create_intensity_plan(
            decision_id, session_id,
            level=1, expression_act=ExpressionAct.QUIET_WAITING,
            approach_value=0.05, llm_advice_intensity=1,
        )
        assert plan.level == 1
        assert plan.channel == "live2d"
        assert plan.live2d_action is not None
        assert plan.live2d_action == LIVE2D_ACTION_TEMPLATES["quiet_waiting"]
        assert plan.bubble_text is None
    finally:
        _set_default_auth_settings()
        _cleanup_session(session_id)


def test_create_intensity_plan_level_2_with_bubble_text():
    """Level 2 计划填充 bubble_text。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    _set_default_auth_settings()
    try:
        decision_id = _make_send_decision(
            session_id, approach_value=0.2, intensity=2,
            expression_act=ExpressionAct.GENTLE_URGE,
        )
        plan = create_intensity_plan(
            decision_id, session_id,
            level=2, expression_act=ExpressionAct.GENTLE_URGE,
            approach_value=0.2, llm_advice_intensity=2,
        )
        assert plan.level == 2
        assert plan.channel == "bubble"
        assert plan.bubble_text is not None
        assert plan.bubble_text == DEFAULT_BUBBLE_TEMPLATES["gentle_urge"]
        assert plan.live2d_action is None
    finally:
        _set_default_auth_settings()
        _cleanup_session(session_id)


def test_create_intensity_plan_unauthorized_level_downgrades():
    """传 level=5 但未授权 → 实际落库 level=3（降级到 minimum_sufficient）。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    _set_default_auth_settings()
    try:
        decision_id = _make_send_decision(
            session_id, approach_value=0.4, intensity=5,
        )
        plan = create_intensity_plan(
            decision_id, session_id,
            level=5, approach_value=0.4, llm_advice_intensity=5,
        )
        # 5 未授权，minimum_sufficient=3 → 降级到 3
        assert plan.level == 3
        assert plan.channel == "chat"
    finally:
        _set_default_auth_settings()
        _cleanup_session(session_id)


def test_create_intensity_plan_level_0_no_action():
    """Level 0 计划无 live2d_action 无 bubble_text。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    _set_default_auth_settings()
    try:
        decision_id = _make_send_decision(session_id, approach_value=-0.1, intensity=0)
        plan = create_intensity_plan(
            decision_id, session_id,
            level=0, approach_value=-0.1, llm_advice_intensity=0,
        )
        assert plan.level == 0
        assert plan.channel == "silent"
        assert plan.live2d_action is None
        assert plan.bubble_text is None
    finally:
        _set_default_auth_settings()
        _cleanup_session(session_id)


def test_create_intensity_plan_invalid_level_raises():
    """无效 level 抛出 ValueError。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        with pytest.raises(ValueError):
            create_intensity_plan(
                "fake_decision_id", session_id, level=-1,
            )
        with pytest.raises(ValueError):
            create_intensity_plan(
                "fake_decision_id", session_id, level=6,
            )
    finally:
        _cleanup_session(session_id)


# ---------- 6. 完整流程测试 ----------

def test_plan_intensity_for_decision_send():
    """send 决策生成强度计划。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    _set_default_auth_settings()
    try:
        decision_id = _make_send_decision(session_id, approach_value=0.4, intensity=3)
        plan = plan_intensity_for_decision(decision_id)
        assert plan is not None
        assert plan.decision_id == decision_id
        assert plan.session_id == session_id
        assert plan.level in ALL_LEVELS
    finally:
        _set_default_auth_settings()
        _cleanup_session(session_id)


def test_plan_intensity_for_decision_non_send_returns_none():
    """defer/suppress/abandon 决策不生成强度计划。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("0")  # 触发 SUPPRESS
    try:
        candidate = _make_candidate(session_id)
        decision = decide_candidate(candidate.id)
        assert decision.decision == DecisionAction.SUPPRESS
        plan = plan_intensity_for_decision(decision.id)
        assert plan is None
    finally:
        _set_proactive_enabled("1")
        _cleanup_session(session_id)


def test_plan_intensity_for_decision_low_approach_value():
    """低 approach_value 决策生成 Level 1/2 计划。

    验证"LLM 认为不值得打断时优先 Level 1/2"。
    """
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    _set_default_auth_settings()
    try:
        # LLM 建议 Level 1（不值得打断），approach_value 较低
        decision_id = _make_send_decision(
            session_id, approach_value=0.05, intensity=1,
            expression_act=ExpressionAct.QUIET_WAITING,
        )
        plan = plan_intensity_for_decision(decision_id)
        assert plan is not None
        # 应该是 Level 1 或更低（0）
        assert plan.level <= 1
        # live2d_action 应填充
        if plan.level == 1:
            assert plan.live2d_action is not None
    finally:
        _set_default_auth_settings()
        _cleanup_session(session_id)


def test_plan_intensity_for_decision_high_approach_value():
    """高 approach_value 决策生成 Level 3+ 计划。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    _set_default_auth_settings()
    try:
        decision_id = _make_send_decision(
            session_id, approach_value=0.8, intensity=3,
        )
        plan = plan_intensity_for_decision(decision_id)
        assert plan is not None
        # 高 approach_value → Level 3 或 4
        assert plan.level >= 3
    finally:
        _set_default_auth_settings()
        _cleanup_session(session_id)


# ---------- 7. 查询测试 ----------

def test_get_intensity_plan():
    """按 ID 查询。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    _set_default_auth_settings()
    try:
        decision_id = _make_send_decision(session_id, approach_value=0.4, intensity=3)
        plan = create_intensity_plan(
            decision_id, session_id,
            level=3, approach_value=0.4, llm_advice_intensity=3,
        )
        loaded = get_intensity_plan(plan.id)
        assert loaded is not None
        assert loaded.id == plan.id
        assert loaded.level == plan.level
        assert loaded.channel == plan.channel
    finally:
        _set_default_auth_settings()
        _cleanup_session(session_id)


def test_get_intensity_plan_by_decision():
    """按 decision_id 查询。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    _set_default_auth_settings()
    try:
        decision_id = _make_send_decision(session_id, approach_value=0.4, intensity=3)
        plan = create_intensity_plan(
            decision_id, session_id,
            level=3, approach_value=0.4, llm_advice_intensity=3,
        )
        loaded = get_intensity_plan_by_decision(decision_id)
        assert loaded is not None
        assert loaded.id == plan.id
        assert loaded.decision_id == decision_id
    finally:
        _set_default_auth_settings()
        _cleanup_session(session_id)


def test_list_intensity_plans_by_session():
    """按 session 列表查询。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    _set_default_auth_settings()
    try:
        # 创建 2 个决策和强度计划
        d1 = _make_send_decision(session_id, approach_value=0.4, intensity=3)
        create_intensity_plan(
            d1, session_id, level=3, approach_value=0.4, llm_advice_intensity=3,
        )
        time.sleep(0.01)
        d2 = _make_send_decision(session_id, approach_value=0.2, intensity=2)
        create_intensity_plan(
            d2, session_id, level=2, approach_value=0.2, llm_advice_intensity=2,
        )
        plans = list_intensity_plans_by_session(session_id)
        # 至少 2 条
        assert len(plans) >= 2
        # 按 created_at 倒序
        assert plans[0].created_at >= plans[1].created_at
    finally:
        _set_default_auth_settings()
        _cleanup_session(session_id)


# ---------- 8. schema 测试 ----------

def test_schema_version_is_54():
    """migration 54 后 schema_version = '54'。"""
    db.init_db()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        assert row[0] == "81"
    finally:
        conn.close()


def test_proactive_intensity_plans_table_exists():
    """proactive_intensity_plans 表存在。"""
    db.init_db()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='proactive_intensity_plans'"
        ).fetchone()
        assert row is not None
        assert row["name"] == "proactive_intensity_plans"
    finally:
        conn.close()


def test_proactive_intensity_plans_has_6_levels_check():
    """CHECK 约束允许 level 0~5，拒绝其他值。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        # 先创建一个 candidate + decision
        decision_id = _make_send_decision(session_id, approach_value=0.4, intensity=3)
        conn = db.connect()
        try:
            now = db.now()
            # 验证 0~5 都可插入
            for level in range(6):
                conn.execute(
                    "INSERT INTO proactive_intensity_plans"
                    " (id, decision_id, session_id, level, channel,"
                    "  is_minimum_sufficient, live2d_action, bubble_text,"
                    "  reason, protocol_version, created_at)"
                    " VALUES (?, ?, ?, ?, ?, 1, NULL, NULL, '', 'proactive-decision-v2', ?)",
                    (db.new_id(), decision_id, session_id, level,
                     intensity_mod.LEVEL_CHANNELS[level], now),
                )
                conn.commit()
            # 无效 level 应被拒绝
            with pytest.raises(Exception):
                conn.execute(
                    "INSERT INTO proactive_intensity_plans"
                    " (id, decision_id, session_id, level, channel,"
                    "  is_minimum_sufficient, live2d_action, bubble_text,"
                    "  reason, protocol_version, created_at)"
                    " VALUES (?, ?, ?, ?, ?, 1, NULL, NULL, '', 'proactive-decision-v2', ?)",
                    (db.new_id(), decision_id, session_id, 6,
                     "external", now),
                )
        finally:
            conn.close()
    finally:
        _cleanup_session(session_id)


def test_proactive_intensity_plans_has_6_channels_check():
    """CHECK 约束允许 6 种 channel，拒绝其他值。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        decision_id = _make_send_decision(session_id, approach_value=0.4, intensity=3)
        conn = db.connect()
        try:
            now = db.now()
            # 无效 channel 应被拒绝
            with pytest.raises(Exception):
                conn.execute(
                    "INSERT INTO proactive_intensity_plans"
                    " (id, decision_id, session_id, level, channel,"
                    "  is_minimum_sufficient, live2d_action, bubble_text,"
                    "  reason, protocol_version, created_at)"
                    " VALUES (?, ?, ?, ?, ?, 1, NULL, NULL, '', 'proactive-decision-v2', ?)",
                    (db.new_id(), decision_id, session_id, 3,
                     "invalid_channel", now),
                )
        finally:
            conn.close()
    finally:
        _cleanup_session(session_id)


# ---------- 9. 常量完整性测试 ----------

def test_all_levels_tuple():
    """ALL_LEVELS 是 0~5 的元组。"""
    assert ALL_LEVELS == (0, 1, 2, 3, 4, 5)


def test_level_names_and_channels_match():
    """LEVEL_NAMES 与 LEVEL_CHANNELS 一致，且覆盖 0~5。"""
    assert LEVEL_NAMES == LEVEL_CHANNELS
    for level in range(6):
        assert level in LEVEL_NAMES
    assert LEVEL_NAMES[0] == "silent"
    assert LEVEL_NAMES[1] == "live2d"
    assert LEVEL_NAMES[2] == "bubble"
    assert LEVEL_NAMES[3] == "chat"
    assert LEVEL_NAMES[4] == "desktop_notification"
    assert LEVEL_NAMES[5] == "external"


def test_level_descriptions_count():
    """LEVEL_DESCRIPTIONS 覆盖 0~5。"""
    for level in range(6):
        assert level in LEVEL_DESCRIPTIONS
        assert isinstance(LEVEL_DESCRIPTIONS[level], str)
        assert LEVEL_DESCRIPTIONS[level]  # 非空


def test_default_level_authorization():
    """DEFAULT_LEVEL_AUTHORIZATION 默认值正确。"""
    assert DEFAULT_LEVEL_AUTHORIZATION[0] is True
    assert DEFAULT_LEVEL_AUTHORIZATION[1] is True
    assert DEFAULT_LEVEL_AUTHORIZATION[2] is True
    assert DEFAULT_LEVEL_AUTHORIZATION[3] is True
    assert DEFAULT_LEVEL_AUTHORIZATION[4] is False
    assert DEFAULT_LEVEL_AUTHORIZATION[5] is False


def test_live2d_action_templates_complete():
    """LIVE2D_ACTION_TEMPLATES 覆盖 6 种 ExpressionAct。"""
    expected_acts = {
        "playful_complaint", "gentle_urge", "firm_care",
        "worried_checkin", "expectant_followup", "quiet_waiting",
    }
    assert set(LIVE2D_ACTION_TEMPLATES.keys()) == expected_acts
    for act, template in LIVE2D_ACTION_TEMPLATES.items():
        assert "gaze" in template
        assert "expression" in template
        assert "motion" in template


def test_default_bubble_templates_complete():
    """DEFAULT_BUBBLE_TEMPLATES 覆盖 6 种 ExpressionAct。"""
    expected_acts = {
        "playful_complaint", "gentle_urge", "firm_care",
        "worried_checkin", "expectant_followup", "quiet_waiting",
    }
    assert set(DEFAULT_BUBBLE_TEMPLATES.keys()) == expected_acts
    for act, text in DEFAULT_BUBBLE_TEMPLATES.items():
        assert isinstance(text, str)
        assert text  # 非空
