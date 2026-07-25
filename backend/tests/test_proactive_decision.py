"""EAP v0.2 Proactive Decision v2 Shadow 模式测试。

覆盖：
1. 候选测试（创建/查询/转换/有效性）
2. 第一层硬门测试（7 项硬边界）
3. 第二层延后条件测试（7 项延后）
4. 第三层动态因素测试
5. 评估函数测试（approach_drive/contact_cost/effective_drive/approach_value）
6. Shadow 基线测试
7. LLM advice 解析测试
8. 主决策测试（综合 5 步流程）
9. 关闭主动陪伴 0 次发送测试（关键约束）
10. 硬边界 100% 阻断测试（关键约束）
11. schema：migration 53 后 schema_version = "53"
"""
import json
import time
from unittest.mock import patch

import pytest

from app import db
from app.proactive import candidates as candidates_mod
from app.proactive import decision as decision_mod
from app.proactive import presence as presence_mod
from app.proactive.candidates import (
    CandidateKind,
    CandidateStatus,
    ProactiveCandidate,
)
from app.proactive.decision import (
    DecisionAction,
    ExpressionAct,
    Layer1BlockReason,
    Layer2DeferReason,
    Layer3Factor,
    LLMAdvice,
    ProactiveDecision,
    compute_approach_value,
    compute_effective_drive,
    compute_shadow_score,
    decide_candidate,
    evaluate_approach_drive,
    evaluate_contact_cost,
    get_decision,
    get_decision_by_candidate,
    list_recent_decisions,
    parse_llm_advice,
)
from app.proactive.presence import (
    PresenceRecord,
    PresenceSignal,
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
            (session_id, "decision 测试", now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _cleanup_session(session_id: str) -> None:
    conn = db.connect()
    try:
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


# ---------- 1. 候选测试 ----------

def test_create_candidate_basic():
    """创建后状态为 pending，source_hash 非空。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = _make_candidate(session_id)
        assert record.session_id == session_id
        assert record.candidate_kind == CandidateKind.RETURN_FOLLOWUP
        assert record.status == CandidateStatus.PENDING
        assert record.source_hash  # 非空
        assert len(record.source_hash) == 64  # sha256 hex
        assert record.protocol_version == "proactive-decision-v2"
        assert record.expires_at is not None
    finally:
        _cleanup_session(session_id)


def test_create_candidate_invalid_kind():
    """无效 candidate_kind 抛出 ValueError。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        with pytest.raises(ValueError):
            candidates_mod.create_candidate(
                session_id, candidate_kind="invalid_kind", topic="x",
            )
    finally:
        _cleanup_session(session_id)


def test_get_candidate():
    """按 ID 查询候选。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = _make_candidate(session_id)
        loaded = candidates_mod.get_candidate(record.id)
        assert loaded is not None
        assert loaded.id == record.id
        assert loaded.topic == record.topic
    finally:
        _cleanup_session(session_id)


def test_list_candidates_by_session():
    """按 session 查询候选。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        _make_candidate(session_id, topic="t1")
        _make_candidate(session_id, topic="t2")
        all_candidates = candidates_mod.list_candidates_by_session(session_id)
        assert len(all_candidates) == 2
        pending = candidates_mod.list_candidates_by_session(
            session_id, status=CandidateStatus.PENDING,
        )
        assert len(pending) == 2
    finally:
        _cleanup_session(session_id)


def test_list_pending_candidates():
    """列出所有 pending 候选。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        _make_candidate(session_id, topic="t1")
        # 等待 1ms 确保排序不同
        time.sleep(0.01)
        _make_candidate(session_id, topic="t2")
        pending = candidates_mod.list_pending_candidates()
        # 至少包含本测试创建的 2 个
        session_pending = [c for c in pending if c.session_id == session_id]
        assert len(session_pending) == 2
    finally:
        _cleanup_session(session_id)


def test_transition_candidate_status():
    """状态转换。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = _make_candidate(session_id)
        updated = candidates_mod.transition_candidate_status(
            record.id, CandidateStatus.EVALUATING,
        )
        assert updated.status == CandidateStatus.EVALUATING
    finally:
        _cleanup_session(session_id)


def test_is_candidate_valid_basic():
    """有效性检查。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = _make_candidate(session_id)
        is_valid, reasons = candidates_mod.is_candidate_valid(record)
        assert is_valid is True
        assert reasons == []
    finally:
        _cleanup_session(session_id)


# ---------- 2. 第一层硬门测试 ----------

def test_layer1_proactive_disabled_blocks():
    """proactive_enabled='0' 时 blocked=True, reasons=[PROACTIVE_DISABLED]。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("0")
    try:
        candidate = _make_candidate(session_id)
        result = decision_mod.check_layer1_hard_boundary(candidate)
        assert result.blocked is True
        assert Layer1BlockReason.PROACTIVE_DISABLED in result.reasons
    finally:
        _set_proactive_enabled("1")
        _cleanup_session(session_id)


def test_layer1_proactive_enabled_passes():
    """proactive_enabled='1' 时不阻断。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        candidate = _make_candidate(session_id)
        result = decision_mod.check_layer1_hard_boundary(candidate)
        assert result.blocked is False
        assert result.reasons == []
    finally:
        _cleanup_session(session_id)


def test_layer1_already_delivered_blocks():
    """相同 source_hash 已 delivered 时阻断。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        # 第一条候选，标记为 delivered
        source_messages = [
            {"id": "x1", "role": "user", "content": "test"},
        ]
        c1 = _make_candidate(session_id, source_messages=source_messages)
        candidates_mod.transition_candidate_status(c1.id, CandidateStatus.DELIVERED)

        # 第二条候选相同 source_hash
        c2 = candidates_mod.create_candidate(
            session_id, candidate_kind=CandidateKind.CHAT_CONTINUATION,
            topic="重复话题", source_messages=source_messages,
        )
        result = decision_mod.check_layer1_hard_boundary(c2)
        assert result.blocked is True
        assert Layer1BlockReason.ALREADY_DELIVERED in result.reasons
    finally:
        _cleanup_session(session_id)


def test_layer1_topic_rejected_blocks():
    """用户拒绝某话题时阻断。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    db.set_setting("proactive_rejected_topics", "敏感话题")
    try:
        candidate = candidates_mod.create_candidate(
            session_id, candidate_kind=CandidateKind.LIFE_SHARE,
            topic="敏感话题",
            source_messages=[{"id": "m1", "role": "user", "content": "x"}],
        )
        result = decision_mod.check_layer1_hard_boundary(candidate)
        assert result.blocked is True
        assert Layer1BlockReason.TOPIC_REJECTED in result.reasons
    finally:
        db.set_setting("proactive_rejected_topics", "")
        _cleanup_session(session_id)


def test_layer1_kind_rejected_blocks():
    """用户拒绝某类型时阻断。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    db.set_setting("proactive_rejected_kinds", CandidateKind.CASUAL_GREETING)
    try:
        candidate = candidates_mod.create_candidate(
            session_id, candidate_kind=CandidateKind.CASUAL_GREETING,
            topic="hi",
            source_messages=[{"id": "m1", "role": "user", "content": "x"}],
        )
        result = decision_mod.check_layer1_hard_boundary(candidate)
        assert result.blocked is True
        assert Layer1BlockReason.KIND_REJECTED in result.reasons
    finally:
        db.set_setting("proactive_rejected_kinds", "")
        _cleanup_session(session_id)


def test_layer1_channel_unauthorized_passes_when_enabled():
    """proactive_enabled=1 时主窗口默认已授权，不阻断。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        candidate = _make_candidate(session_id)
        result = decision_mod.check_layer1_hard_boundary(candidate)
        # 主窗口已授权，不命中 CHANNEL_UNAUTHORIZED
        assert Layer1BlockReason.CHANNEL_UNAUTHORIZED not in result.reasons
        assert result.blocked is False
    finally:
        _cleanup_session(session_id)


def test_layer1_emergency_stop_blocks():
    """proactive_emergency_stop='1' 时阻断。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    db.set_setting("proactive_emergency_stop", "1")
    try:
        candidate = _make_candidate(session_id)
        result = decision_mod.check_layer1_hard_boundary(candidate)
        assert result.blocked is True
        assert Layer1BlockReason.EMERGENCY_STOP in result.reasons
    finally:
        db.set_setting("proactive_emergency_stop", "0")
        _cleanup_session(session_id)


# ---------- 3. 第二层延后条件测试 ----------

def test_layer2_user_busy_defers():
    """USER_BUSY 时延后。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        candidate = _make_candidate(session_id)
        # 构造一个 busy 的 presence（避免依赖 DB 落库）
        presence = _make_presence_record(
            session_id, UserStatus.AWAY_BUSY,
            expires_at=db.now() + 1800,
        )
        result = decision_mod.check_layer2_defer_conditions(
            candidate, presence=presence,
        )
        assert result.deferred is True
        assert Layer2DeferReason.USER_BUSY in result.reasons
        assert result.next_available_window is not None
    finally:
        _cleanup_session(session_id)


def test_layer2_user_sleeping_defers():
    """USER_SLEEPING 时延后。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        candidate = _make_candidate(session_id)
        presence = _make_presence_record(session_id, UserStatus.AWAY_SLEEP)
        result = decision_mod.check_layer2_defer_conditions(
            candidate, presence=presence,
        )
        assert result.deferred is True
        assert Layer2DeferReason.USER_SLEEPING in result.reasons
    finally:
        _cleanup_session(session_id)


def test_layer2_user_dnd_defers():
    """USER_DND 时延后。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        candidate = _make_candidate(session_id)
        presence = _make_presence_record(session_id, UserStatus.DO_NOT_DISTURB)
        result = decision_mod.check_layer2_defer_conditions(
            candidate, presence=presence,
        )
        assert result.deferred is True
        assert Layer2DeferReason.USER_DND in result.reasons
    finally:
        _cleanup_session(session_id)


def test_layer2_conversation_ended_defers():
    """CONVERSATION_ENDED 时延后。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        candidate = _make_candidate(session_id)
        presence = _make_presence_record(session_id, UserStatus.ENDED_CONVERSATION)
        result = decision_mod.check_layer2_defer_conditions(
            candidate, presence=presence,
        )
        assert result.deferred is True
        assert Layer2DeferReason.CONVERSATION_ENDED in result.reasons
    finally:
        _cleanup_session(session_id)


def test_layer2_quiet_hours_defers():
    """当前小时在 23-9 范围内时延后。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        # 构造一个 24 小时内每个小时测试，确保 23-9 范围正确
        candidate = _make_candidate(session_id)
        # 找一个肯定在 23-9 范围的时间戳（凌晨 3 点）
        local_now = time.localtime(db.now())
        # 构造今天凌晨 3 点的时间戳
        ts_3am = time.mktime((
            local_now.tm_year, local_now.tm_mon, local_now.tm_mday,
            3, 0, 0, 0, 0, -1,
        ))
        # 不传 presence，避免依赖 DB
        result = decision_mod.check_layer2_defer_conditions(
            candidate, now=ts_3am, presence=_make_presence_record(
                session_id, UserStatus.UNKNOWN, is_active=False,
            ),
            quiet_hours_start=23, quiet_hours_end=9,
        )
        assert result.deferred is True
        assert Layer2DeferReason.QUIET_HOURS in result.reasons
        assert result.next_available_window is not None
    finally:
        _cleanup_session(session_id)


def test_layer2_online_passes():
    """presence.online 时不延后（无 quiet hours 命中时）。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        candidate = _make_candidate(session_id)
        # 找一个不在 23-9 范围的时间戳（下午 2 点）
        local_now = time.localtime(db.now())
        ts_2pm = time.mktime((
            local_now.tm_year, local_now.tm_mon, local_now.tm_mday,
            14, 0, 0, 0, 0, -1,
        ))
        presence = _make_presence_record(session_id, UserStatus.ONLINE)
        result = decision_mod.check_layer2_defer_conditions(
            candidate, now=ts_2pm, presence=presence,
        )
        assert result.deferred is False
        assert result.reasons == []
    finally:
        _cleanup_session(session_id)


def test_layer2_next_available_window_calculation():
    """USER_BUSY 时 next_available_window 使用 presence.expires_at。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        candidate = _make_candidate(session_id)
        expected_window = db.now() + 3600
        presence = _make_presence_record(
            session_id, UserStatus.AWAY_BUSY,
            expires_at=expected_window,
        )
        # 用下午 2 点避免 quiet hours 干扰
        local_now = time.localtime(db.now())
        ts_2pm = time.mktime((
            local_now.tm_year, local_now.tm_mon, local_now.tm_mday,
            14, 0, 0, 0, 0, -1,
        ))
        result = decision_mod.check_layer2_defer_conditions(
            candidate, now=ts_2pm, presence=presence,
        )
        assert result.deferred is True
        assert result.next_available_window == expected_window
    finally:
        _cleanup_session(session_id)


# ---------- 4. 第三层动态因素测试 ----------

def test_layer3_no_recent_decisions_zero():
    """无最近决策时所有计数为 0。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        candidate = _make_candidate(session_id)
        # recent_decisions 显式传空列表
        result = decision_mod.compute_layer3_factors(
            candidate, recent_decisions=[],
        )
        assert result.factors[Layer3Factor.TODAY_ALREADY_PROACTIVE] == 0
        assert result.factors[Layer3Factor.LAST_24H_COUNT] == 0
        assert result.factors[Layer3Factor.TIME_SINCE_LAST_PROACTIVE] == -1.0
    finally:
        _cleanup_session(session_id)


def test_layer3_with_recent_decisions():
    """有最近决策时计数 > 0。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        local_now = time.localtime(db.now())
        ts_2pm = time.mktime((
            local_now.tm_year, local_now.tm_mon, local_now.tm_mday,
            14, 0, 0, local_now.tm_wday, local_now.tm_yday, local_now.tm_isdst,
        ))
        candidate = _make_candidate(session_id)
        # 先做一次 send 决策
        decision = decide_candidate(candidate.id, now=ts_2pm)
        # 强制设为 send 通过 LLM advice
        # 重置：创建第二个候选并明确给 LLM advice=send，关闭 proactive_enabled=1 不阻断
        candidate2 = _make_candidate(session_id, topic="t2")
        advice = LLMAdvice(
            decision=DecisionAction.SEND, intensity=2,
            expression_act=ExpressionAct.GENTLE_URGE,
            topic="t2", confidence=0.8,
            reason_codes=["open_thread"], source_refs=["m1"],
        )
        decide_candidate(candidate2.id, llm_advice=advice, now=ts_2pm)
        result = decision_mod.compute_layer3_factors(candidate2, now=ts_2pm)
        assert result.factors[Layer3Factor.LAST_24H_COUNT] >= 1
        assert result.factors[Layer3Factor.TIME_SINCE_LAST_PROACTIVE] >= 0
    finally:
        _cleanup_session(session_id)


def test_layer3_previous_unanswered_with_episode():
    """有未回复 episode 时 PREVIOUS_UNANSWERED=1。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        from app.proactive import episodes
        from app.proactive.episodes import EpisodeStatus, OriginType

        ep = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        # episode 处于 proposed 状态（未回复）
        candidate = _make_candidate(session_id)
        result = decision_mod.compute_layer3_factors(
            candidate, recent_decisions=[], episode=ep,
        )
        assert result.factors[Layer3Factor.PREVIOUS_UNANSWERED] == 1
    finally:
        _cleanup_session(session_id)


def test_layer3_consecutive_ignored_with_episode():
    """episode.approach_count 反映 consecutive_ignored。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        from app.proactive import episodes
        from app.proactive.episodes import OriginType

        ep = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        # 使用返回值（record_approach 返回更新后的 episode）
        ep = episodes.record_approach(ep.id, intensity=2)
        ep = episodes.record_approach(ep.id, intensity=3)
        candidate = _make_candidate(session_id)
        result = decision_mod.compute_layer3_factors(
            candidate, recent_decisions=[], episode=ep,
        )
        assert result.factors[Layer3Factor.CONSECUTIVE_IGNORED] == 2
    finally:
        _cleanup_session(session_id)


# ---------- 5. 评估函数测试 ----------

def test_evaluate_approach_drive_basic():
    """基础 approach_drive 评估。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        candidate = _make_candidate(session_id, candidate_kind=CandidateKind.EMOTIONAL_CARE)
        # emotional_care 基础分 0.7 + 0.5*0.2 + 0*0.05 + 0.5*0.1 = 0.7 + 0.1 + 0.05 = 0.85
        drive = evaluate_approach_drive(candidate)
        assert 0.0 <= drive <= 1.0
        assert drive == pytest.approx(0.85)
    finally:
        _cleanup_session(session_id)


def test_evaluate_approach_drive_with_episode_pressure():
    """episode.unanswered_pressure 减成。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        from app.proactive import episodes
        from app.proactive.episodes import OriginType

        ep = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        # 接近一次产生 pressure（使用返回值获取更新后的 episode）
        ep = episodes.record_approach(ep.id, intensity=3)
        # pressure = 3 * 0.6 = 1.8
        candidate = _make_candidate(session_id, candidate_kind=CandidateKind.EMOTIONAL_CARE)
        drive = evaluate_approach_drive(candidate, episode=ep)
        # 基础 0.85 - 1.8 * 0.3 = 0.31
        assert drive == pytest.approx(0.31)
    finally:
        _cleanup_session(session_id)


def test_evaluate_contact_cost_basic():
    """基础 contact_cost 评估（无 presence 无 layer3）。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        candidate = _make_candidate(session_id)
        # 无 presence（None）、无 layer3、无 episode
        cost = evaluate_contact_cost(
            candidate,
            presence=_make_presence_record(session_id, UserStatus.UNKNOWN, is_active=False),
            layer3_factors=None,
        )
        assert cost == pytest.approx(0.35)  # 基础分 + restrained 频率保护
    finally:
        _cleanup_session(session_id)


def test_evaluate_contact_cost_with_presence_busy():
    """presence.busy 时 contact_cost 增加。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        candidate = _make_candidate(session_id)
        presence = _make_presence_record(session_id, UserStatus.AWAY_BUSY)
        cost = evaluate_contact_cost(candidate, presence=presence)
        # 0.2 + 0.15 restrained + 0.4 busy = 0.75
        assert cost == pytest.approx(0.75)
    finally:
        _cleanup_session(session_id)


def test_compute_effective_drive_with_modulation():
    """effective_drive 受 modulation 影响。"""
    db.init_db()
    # 高关系稍增
    drive = compute_effective_drive(0.5, relationship_modulation=1.2, mood_modulation=1.1)
    assert drive == pytest.approx(0.66)
    # 低关系稍减
    drive = compute_effective_drive(0.5, relationship_modulation=0.8, mood_modulation=0.9)
    assert drive == pytest.approx(0.36)
    # clamp 测试
    drive = compute_effective_drive(0.9, relationship_modulation=1.5, mood_modulation=1.5)
    assert drive == pytest.approx(1.0)


def test_compute_approach_value():
    """approach_value = effective_drive - contact_cost。"""
    assert compute_approach_value(0.7, 0.3) == pytest.approx(0.4)
    assert compute_approach_value(0.3, 0.5) == pytest.approx(-0.2)
    assert compute_approach_value(0.5, 0.5) == pytest.approx(0.0)


# ---------- 6. Shadow 基线测试 ----------

def test_compute_shadow_score_default():
    """默认参数下 shadow_score = 各 weight 之和 = 0.5。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        candidate = _make_candidate(session_id)
        score = compute_shadow_score(candidate)
        # 所有参数均为 0.5 时 score = sum(weights) * 0.5 = 1.0 * 0.5 = 0.5
        assert score == pytest.approx(0.5)
    finally:
        _cleanup_session(session_id)


def test_compute_shadow_score_with_high_evidence():
    """高 evidence_strength 时 shadow_score 接近 0.25（evidence 权重）。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        candidate = _make_candidate(session_id)
        score = compute_shadow_score(
            candidate,
            evidence_strength=1.0,
            open_thread_relevance=0.0,
            emotional_resonance=0.0,
            relationship_fit=0.0,
            contact_need_fit=0.0,
            timing_score=0.0,
            kind_priority=0.0,
        )
        # 只 evidence_strength=1.0：score = 1.0 * 0.25 = 0.25
        assert score == pytest.approx(0.25)
    finally:
        _cleanup_session(session_id)


# ---------- 7. LLM advice 解析测试 ----------

def test_parse_llm_advice_valid():
    """有效 JSON 正确解析。"""
    raw = json.dumps({
        "decision": "send",
        "intensity": "level_3",
        "expression_act": "gentle_urge",
        "topic": "问问测试结果",
        "confidence": 0.8,
        "reason_codes": ["open_thread", "expected_return"],
        "source_refs": ["msg_1"],
    })
    advice = parse_llm_advice(raw)
    assert advice.decision == DecisionAction.SEND
    assert advice.intensity == 3
    assert advice.expression_act == ExpressionAct.GENTLE_URGE
    assert advice.topic == "问问测试结果"
    assert advice.confidence == pytest.approx(0.8)
    assert "open_thread" in advice.reason_codes
    assert "msg_1" in advice.source_refs


def test_parse_llm_advice_invalid_returns_suppress():
    """无效 JSON 返回默认 SUPPRESS。"""
    advice = parse_llm_advice("not a json")
    assert advice.decision == DecisionAction.SUPPRESS
    assert advice.intensity is None
    assert "parse_failed" in advice.reason_codes


def test_parse_llm_advice_level_to_int():
    """intensity 字符串 level_N 正确转为整数 N。"""
    for i in range(6):
        raw = json.dumps({
            "decision": "defer",
            "intensity": f"level_{i}",
            "confidence": 0.5,
        })
        advice = parse_llm_advice(raw)
        assert advice.intensity == i


# ---------- 8. 主决策测试 ----------

def test_decide_candidate_layer1_blocks_suppress():
    """关闭主动陪伴时 SUPPRESS（关键约束）。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("0")
    try:
        candidate = _make_candidate(session_id)
        decision = decide_candidate(candidate.id)
        assert decision.decision == DecisionAction.SUPPRESS
        assert decision.layer1_blocked is True
        assert Layer1BlockReason.PROACTIVE_DISABLED in decision.layer1_block_reasons
    finally:
        _set_proactive_enabled("1")
        _cleanup_session(session_id)


def test_decide_candidate_layer2_defers():
    """第二层延后条件命中时 DEFER。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        candidate = _make_candidate(session_id)
        # 用 patch 注入 mock presence
        busy_presence = _make_presence_record(session_id, UserStatus.AWAY_BUSY)
        with patch.object(decision_mod, "get_current_presence", return_value=busy_presence):
            # 用下午 2 点避开 quiet hours
            local_now = time.localtime(db.now())
            ts_2pm = time.mktime((
                local_now.tm_year, local_now.tm_mon, local_now.tm_mday,
                14, 0, 0, 0, 0, -1,
            ))
            decision = decide_candidate(candidate.id, now=ts_2pm)
        assert decision.decision == DecisionAction.DEFER
        assert decision.layer2_deferred is True
        assert Layer2DeferReason.USER_BUSY in decision.layer2_defer_reasons
    finally:
        _cleanup_session(session_id)


def test_decide_candidate_send_when_approach_value_positive():
    """approach_value > 0 时本地规则 SEND。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        # emotional_care 基础 0.7，无 presence、无 episode
        candidate = _make_candidate(
            session_id, candidate_kind=CandidateKind.EMOTIONAL_CARE,
        )
        # 用下午 2 点 + online presence 避开延后
        online_presence = _make_presence_record(session_id, UserStatus.ONLINE)
        local_now = time.localtime(db.now())
        ts_2pm = time.mktime((
            local_now.tm_year, local_now.tm_mon, local_now.tm_mday,
            14, 0, 0, 0, 0, -1,
        ))
        with patch.object(decision_mod, "get_current_presence", return_value=online_presence):
            decision = decide_candidate(candidate.id, now=ts_2pm)
        # emotional_care: drive=0.7+0.1+0.05=0.85, cost=0.2, value=0.65 → SEND
        assert decision.decision == DecisionAction.SEND
        assert decision.layer1_blocked is False
        assert decision.layer2_deferred is False
    finally:
        _cleanup_session(session_id)


def test_decide_candidate_suppress_when_approach_value_negative():
    """approach_value < -0.2 时 SUPPRESS。

    通过设置 user busy + DND 让 cost 极高、drive 较低来触发。
    实际场景：通过 LLM advice=SUPPRESS 来确保 SUPPRESS 决策。
    """
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        candidate = _make_candidate(session_id)
        advice = LLMAdvice(
            decision=DecisionAction.SUPPRESS, intensity=None,
            expression_act=None, topic=None, confidence=0.3,
            reason_codes=["not_relevant"], source_refs=[],
        )
        # 用 online presence 避开延后
        online_presence = _make_presence_record(session_id, UserStatus.ONLINE)
        local_now = time.localtime(db.now())
        ts_2pm = time.mktime((
            local_now.tm_year, local_now.tm_mon, local_now.tm_mday,
            14, 0, 0, 0, 0, -1,
        ))
        with patch.object(decision_mod, "get_current_presence", return_value=online_presence):
            decision = decide_candidate(
                candidate.id, llm_advice=advice, now=ts_2pm,
            )
        assert decision.decision == DecisionAction.SUPPRESS
    finally:
        _cleanup_session(session_id)


def test_decide_candidate_with_llm_advice_send():
    """LLM 建议 send 时（无硬门/延后）使用 LLM 建议。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        candidate = _make_candidate(session_id)
        advice = LLMAdvice(
            decision=DecisionAction.SEND, intensity=3,
            expression_act=ExpressionAct.GENTLE_URGE,
            topic="t", confidence=0.9,
            reason_codes=["open_thread"], source_refs=["m1"],
        )
        online_presence = _make_presence_record(session_id, UserStatus.ONLINE)
        local_now = time.localtime(db.now())
        ts_2pm = time.mktime((
            local_now.tm_year, local_now.tm_mon, local_now.tm_mday,
            14, 0, 0, 0, 0, -1,
        ))
        with patch.object(decision_mod, "get_current_presence", return_value=online_presence):
            decision = decide_candidate(
                candidate.id, llm_advice=advice, now=ts_2pm,
            )
        assert decision.decision == DecisionAction.SEND
        assert decision.intensity == 3
        assert decision.expression_act == ExpressionAct.GENTLE_URGE
        assert decision.confidence == pytest.approx(0.9)
    finally:
        _cleanup_session(session_id)


def test_decide_candidate_with_llm_advice_suppress_overrides_local():
    """LLM 建议 suppress 时（无硬门/延后）使用 LLM 建议 suppress。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        # emotional_care drive=0.85 本地会 SEND，但 LLM 建议 SUPPRESS
        candidate = _make_candidate(
            session_id, candidate_kind=CandidateKind.EMOTIONAL_CARE,
        )
        advice = LLMAdvice(
            decision=DecisionAction.SUPPRESS, intensity=None,
            expression_act=None, topic=None, confidence=0.4,
            reason_codes=["not_relevant"], source_refs=[],
        )
        online_presence = _make_presence_record(session_id, UserStatus.ONLINE)
        local_now = time.localtime(db.now())
        ts_2pm = time.mktime((
            local_now.tm_year, local_now.tm_mon, local_now.tm_mday,
            14, 0, 0, 0, 0, -1,
        ))
        with patch.object(decision_mod, "get_current_presence", return_value=online_presence):
            decision = decide_candidate(
                candidate.id, llm_advice=advice, now=ts_2pm,
            )
        assert decision.decision == DecisionAction.SUPPRESS
    finally:
        _cleanup_session(session_id)


def test_decide_candidate_layer1_blocks_overrides_llm_send():
    """关键：LLM 建议 send 但第一层硬门 blocked 时 SUPPRESS。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("0")  # 关闭主动陪伴触发硬门
    try:
        candidate = _make_candidate(session_id)
        advice = LLMAdvice(
            decision=DecisionAction.SEND, intensity=5,
            expression_act=ExpressionAct.FIRM_CARE,
            topic="t", confidence=0.99,
            reason_codes=[], source_refs=[],
        )
        decision = decide_candidate(candidate.id, llm_advice=advice)
        # 硬边界 100% 阻断，LLM 无权放行
        assert decision.decision == DecisionAction.SUPPRESS
        assert decision.layer1_blocked is True
        assert Layer1BlockReason.PROACTIVE_DISABLED in decision.layer1_block_reasons
    finally:
        _set_proactive_enabled("1")
        _cleanup_session(session_id)


def test_decide_candidate_shadow_mode():
    """is_shadow=True 时正常计算但 is_shadow 字段为 True。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        candidate = _make_candidate(session_id)
        decision = decide_candidate(candidate.id, is_shadow=True)
        assert decision.is_shadow is True
    finally:
        _cleanup_session(session_id)


def test_decide_candidate_idempotency():
    """相同 candidate_id 重复调用返回相同决策。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        candidate = _make_candidate(session_id)
        decision1 = decide_candidate(candidate.id)
        decision2 = decide_candidate(candidate.id)
        # 幂等：返回已有决策
        assert decision1.id == decision2.id
        assert decision1.idempotency_key == decision2.idempotency_key
    finally:
        _cleanup_session(session_id)


def test_decide_candidate_updates_candidate_status():
    """决策后候选状态正确更新。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        candidate = _make_candidate(session_id)
        online_presence = _make_presence_record(session_id, UserStatus.ONLINE)
        local_now = time.localtime(db.now())
        ts_2pm = time.mktime((
            local_now.tm_year, local_now.tm_mon, local_now.tm_mday,
            14, 0, 0, 0, 0, -1,
        ))
        with patch.object(decision_mod, "get_current_presence", return_value=online_presence):
            decision = decide_candidate(candidate.id, now=ts_2pm)
        # 加载最新候选状态
        loaded = candidates_mod.get_candidate(candidate.id)
        status_map = {
            DecisionAction.SEND: CandidateStatus.APPROVED,
            DecisionAction.DEFER: CandidateStatus.DEFERRED,
            DecisionAction.SUPPRESS: CandidateStatus.SUPPRESSED,
            DecisionAction.ABANDON: CandidateStatus.ABANDONED,
        }
        assert loaded.status == status_map[decision.decision]
    finally:
        _cleanup_session(session_id)


# ---------- 9. 关闭主动陪伴 0 次发送测试 ----------

def test_disabled_proactive_zero_send():
    """proactive_enabled='0' 时所有候选 SUPPRESS，0 个 SEND。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("0")
    try:
        decisions = []
        for kind in [
            CandidateKind.CHAT_CONTINUATION,
            CandidateKind.RETURN_FOLLOWUP,
            CandidateKind.EMOTIONAL_CARE,
            CandidateKind.MILESTONE_FOLLOWUP,
            CandidateKind.CASUAL_GREETING,
            CandidateKind.LIFE_SHARE,
        ]:
            candidate = _make_candidate(session_id, candidate_kind=kind)
            d = decide_candidate(candidate.id)
            decisions.append(d.decision)
        # 全部 SUPPRESS，无 SEND
        assert all(d == DecisionAction.SUPPRESS for d in decisions)
        assert DecisionAction.SEND not in decisions
    finally:
        _set_proactive_enabled("1")
        _cleanup_session(session_id)


def test_disabled_proactive_zero_send_with_llm_advice():
    """即使 LLM 建议 SEND，关闭时仍 SUPPRESS。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("0")
    try:
        candidate = _make_candidate(session_id)
        advice = LLMAdvice(
            decision=DecisionAction.SEND, intensity=5,
            expression_act=ExpressionAct.FIRM_CARE,
            topic="t", confidence=1.0,
            reason_codes=["strong_evidence"], source_refs=["m1"],
        )
        decision = decide_candidate(candidate.id, llm_advice=advice)
        assert decision.decision == DecisionAction.SUPPRESS
        assert decision.layer1_blocked is True
    finally:
        _set_proactive_enabled("1")
        _cleanup_session(session_id)


# ---------- 10. 硬边界 100% 阻断测试 ----------

def test_hard_boundary_100_percent_block():
    """各种硬边界情况下都 SUPPRESS，无论 LLM 建议。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        advice_send = LLMAdvice(
            decision=DecisionAction.SEND, intensity=5,
            expression_act=ExpressionAct.FIRM_CARE,
            topic="t", confidence=1.0,
            reason_codes=[], source_refs=[],
        )

        # 1. PROACTIVE_DISABLED
        db.set_setting("proactive_enabled", "0")
        c = _make_candidate(session_id, topic="t1")
        d = decide_candidate(c.id, llm_advice=advice_send)
        assert d.decision == DecisionAction.SUPPRESS
        assert d.layer1_blocked is True
        _set_proactive_enabled("1")

        # 2. EMERGENCY_STOP
        db.set_setting("proactive_emergency_stop", "1")
        c = _make_candidate(session_id, topic="t2")
        d = decide_candidate(c.id, llm_advice=advice_send)
        assert d.decision == DecisionAction.SUPPRESS
        assert d.layer1_blocked is True
        db.set_setting("proactive_emergency_stop", "0")

        # 3. TOPIC_REJECTED
        db.set_setting("proactive_rejected_topics", "敏感")
        c = candidates_mod.create_candidate(
            session_id, candidate_kind=CandidateKind.LIFE_SHARE,
            topic="敏感",
            source_messages=[{"id": "m1", "role": "user", "content": "x"}],
        )
        d = decide_candidate(c.id, llm_advice=advice_send)
        assert d.decision == DecisionAction.SUPPRESS
        assert d.layer1_blocked is True
        db.set_setting("proactive_rejected_topics", "")

        # 4. KIND_REJECTED
        db.set_setting("proactive_rejected_kinds", CandidateKind.CASUAL_GREETING)
        c = candidates_mod.create_candidate(
            session_id, candidate_kind=CandidateKind.CASUAL_GREETING,
            topic="hi",
            source_messages=[{"id": "m2", "role": "user", "content": "y"}],
        )
        d = decide_candidate(c.id, llm_advice=advice_send)
        assert d.decision == DecisionAction.SUPPRESS
        assert d.layer1_blocked is True
        db.set_setting("proactive_rejected_kinds", "")
    finally:
        _set_proactive_enabled("1")
        db.set_setting("proactive_emergency_stop", "0")
        db.set_setting("proactive_rejected_topics", "")
        db.set_setting("proactive_rejected_kinds", "")
        _cleanup_session(session_id)


def test_already_delivered_blocks_hard():
    """已投递候选的 source_hash 再次决策时被硬门阻断。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        source_messages = [
            {"id": "dup1", "role": "user", "content": "duplicate test"},
        ]
        c1 = _make_candidate(
            session_id, source_messages=source_messages, topic="original",
        )
        # 模拟已投递
        candidates_mod.transition_candidate_status(c1.id, CandidateStatus.DELIVERED)

        # 同 source_hash 的新候选
        c2 = candidates_mod.create_candidate(
            session_id, candidate_kind=CandidateKind.CHAT_CONTINUATION,
            topic="duplicate", source_messages=source_messages,
        )
        advice_send = LLMAdvice(
            decision=DecisionAction.SEND, intensity=5,
            expression_act=ExpressionAct.FIRM_CARE,
            topic="t", confidence=1.0, reason_codes=[], source_refs=[],
        )
        d = decide_candidate(c2.id, llm_advice=advice_send)
        assert d.decision == DecisionAction.SUPPRESS
        assert d.layer1_blocked is True
        assert Layer1BlockReason.ALREADY_DELIVERED in d.layer1_block_reasons
    finally:
        _cleanup_session(session_id)


# ---------- 11. 查询测试 ----------

def test_get_decision_by_id():
    """按 ID 查询决策。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        candidate = _make_candidate(session_id)
        decision = decide_candidate(candidate.id)
        loaded = get_decision(decision.id)
        assert loaded is not None
        assert loaded.id == decision.id
    finally:
        _cleanup_session(session_id)


def test_get_decision_by_candidate():
    """按 candidate_id 查询最新决策。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        candidate = _make_candidate(session_id)
        decision = decide_candidate(candidate.id)
        loaded = get_decision_by_candidate(candidate.id)
        assert loaded is not None
        assert loaded.id == decision.id
    finally:
        _cleanup_session(session_id)


def test_list_recent_decisions():
    """列出最近决策。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    try:
        candidate = _make_candidate(session_id)
        decide_candidate(candidate.id)
        recent = list_recent_decisions(limit=10)
        assert len(recent) >= 1
    finally:
        _cleanup_session(session_id)


# ---------- 12. schema 测试 ----------

def test_schema_version_is_52():
    """migration 54 后 schema_version = '54'。"""
    db.init_db()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        assert row[0] == "62"
    finally:
        conn.close()


def test_proactive_candidates_table_exists():
    """proactive_candidates 表存在。"""
    db.init_db()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='proactive_candidates'"
        ).fetchone()
        assert row is not None
        assert row["name"] == "proactive_candidates"
    finally:
        conn.close()


def test_proactive_decisions_table_exists():
    """proactive_decisions 表存在。"""
    db.init_db()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='proactive_decisions'"
        ).fetchone()
        assert row is not None
        assert row["name"] == "proactive_decisions"
    finally:
        conn.close()


def test_proactive_candidates_has_6_kinds():
    """CHECK 约束允许 6 种 candidate_kind。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    valid_kinds = list(candidates_mod.ALL_CANDIDATE_KINDS)
    assert len(valid_kinds) == 6
    conn = db.connect()
    try:
        now = db.now()
        for kind in valid_kinds:
            record_id = db.new_id()
            conn.execute(
                "INSERT INTO proactive_candidates"
                " (id, session_id, episode_id, candidate_kind, topic, source_refs,"
                "  open_thread, source_hash, status, expires_at,"
                "  protocol_version, created_at, updated_at)"
                " VALUES (?, ?, NULL, ?, 't', '{}', NULL, '', 'pending', NULL,"
                "  'proactive-decision-v2', ?, ?)",
                (record_id, session_id, kind, now, now),
            )
            conn.commit()
        # 无效 kind 应被拒绝
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO proactive_candidates"
                " (id, session_id, episode_id, candidate_kind, topic, source_refs,"
                "  open_thread, source_hash, status, expires_at,"
                "  protocol_version, created_at, updated_at)"
                " VALUES (?, ?, NULL, 'invalid', 't', '{}', NULL, '', 'pending', NULL,"
                "  'proactive-decision-v2', ?, ?)",
                (db.new_id(), session_id, now, now),
            )
    finally:
        conn.close()
        _cleanup_session(session_id)


def test_proactive_decisions_has_4_decision_values():
    """CHECK 约束允许 4 种 decision。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    valid_decisions = [
        DecisionAction.SEND, DecisionAction.DEFER,
        DecisionAction.SUPPRESS, DecisionAction.ABANDON,
    ]
    conn = db.connect()
    try:
        for i, decision in enumerate(valid_decisions):
            # 创建独立 candidate
            cand_id = db.new_id()
            now = db.now()
            conn.execute(
                "INSERT INTO proactive_candidates"
                " (id, session_id, episode_id, candidate_kind, topic, source_refs,"
                "  open_thread, source_hash, status, expires_at,"
                "  protocol_version, created_at, updated_at)"
                " VALUES (?, ?, NULL, 'chat_continuation', 't', '{}', NULL, '',"
                "  'pending', NULL, 'proactive-decision-v2', ?, ?)",
                (cand_id, session_id, now, now),
            )
            record_id = db.new_id()
            idem_key = f"test-key-{i}"
            conn.execute(
                "INSERT INTO proactive_decisions"
                " (id, candidate_id, session_id, decision, intensity, expression_act,"
                "  topic, confidence, reason_codes, source_refs,"
                "  layer1_blocked, layer1_block_reasons,"
                "  layer2_deferred, layer2_defer_reasons, layer3_factors,"
                "  approach_drive, contact_cost, effective_drive, approach_value,"
                "  shadow_score, is_shadow, llm_raw_response, idempotency_key,"
                "  protocol_version, created_at)"
                " VALUES (?, ?, ?, ?, NULL, NULL, NULL, 0.0, '[]', '[]',"
                "  0, '[]', 0, '[]', '{}', 0.0, 0.0, 0.0, 0.0,"
                "  NULL, 0, NULL, ?, 'proactive-decision-v2', ?)",
                (record_id, cand_id, session_id, decision, idem_key, now),
            )
            conn.commit()
        # 无效 decision 应被拒绝
        with pytest.raises(Exception):
            cand_id2 = db.new_id()
            now = db.now()
            conn.execute(
                "INSERT INTO proactive_candidates"
                " (id, session_id, episode_id, candidate_kind, topic, source_refs,"
                "  open_thread, source_hash, status, expires_at,"
                "  protocol_version, created_at, updated_at)"
                " VALUES (?, ?, NULL, 'chat_continuation', 't', '{}', NULL, '',"
                "  'pending', NULL, 'proactive-decision-v2', ?, ?)",
                (cand_id2, session_id, now, now),
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
                " VALUES (?, ?, ?, 'invalid', NULL, NULL, NULL, 0.0, '[]', '[]',"
                "  0, '[]', 0, '[]', '{}', 0.0, 0.0, 0.0, 0.0,"
                "  NULL, 0, NULL, ?, 'proactive-decision-v2', ?)",
                (db.new_id(), cand_id2, session_id, "test-bad-key", now),
            )
    finally:
        conn.close()
        _cleanup_session(session_id)
