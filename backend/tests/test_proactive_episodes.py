"""EAP v0.2 ContactEpisode 与动态未回复压力测试。

覆盖：
1. 创建测试（origin_type、open_thread、source_refs、expires_at、CHECK 约束）
2. 查询测试（按 ID、活跃 episode）
3. 状态转换测试（10 值状态机、终态保护）
4. 接近记录测试（approach_count、unanswered_pressure 累积、first_candidate_at、intensity 校验）
5. 衰减测试（每小时 0.05、下限 0、无 last_approach_at 不衰减、批量）
6. 用户后续行为测试（6 类响应）
7. 过期测试
8. 沉默不影响 bond/trust 测试
9. schema：migration 53 后 schema_version = "53"，表存在
"""
import pytest

from app import db
from app.proactive import episodes
from app.proactive.episodes import (
    EpisodeStatus,
    OriginType,
    Outcome,
    UserResponseType,
)


def _setup_session(session_id: str) -> None:
    """插入测试 session，满足 contact_episodes 外键约束。"""
    now = db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (session_id, "episodes 测试", now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _cleanup(session_id: str) -> None:
    conn = db.connect()
    try:
        conn.execute("DELETE FROM contact_episodes WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM episode_relationship_delta_suggestions WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        conn.commit()
    finally:
        conn.close()


# ---------- 1. 创建测试 ----------

def test_create_episode_basic():
    """创建后状态为 proposed，approach_count=0，unanswered_pressure=0。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="测试结果", origin_type=OriginType.EXPECTED_RETURN,
        )
        assert record.session_id == session_id
        assert record.topic == "测试结果"
        assert record.origin_type == OriginType.EXPECTED_RETURN
        assert record.status == EpisodeStatus.PROPOSED
        assert record.approach_count == 0
        assert record.unanswered_pressure == 0.0
        assert record.current_intensity == 0
        assert record.first_candidate_at is None
        assert record.last_approach_at is None
        assert record.outcome is None
        assert record.expires_at is not None  # 默认 7 天后
        assert record.source_refs == {}
        assert record.protocol_version == "proactive-decision-v2"
    finally:
        _cleanup(session_id)


def test_create_episode_with_open_thread():
    """open_thread 字段正确保存。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="测试结果", origin_type=OriginType.EXPECTED_RETURN,
            open_thread="想问问测试跑得怎么样了",
        )
        assert record.open_thread == "想问问测试跑得怎么样了"
        # DB 中确认
        loaded = episodes.get_episode(record.id)
        assert loaded is not None
        assert loaded.open_thread == "想问问测试跑得怎么样了"
    finally:
        _cleanup(session_id)


def test_create_episode_with_source_refs():
    """source_refs JSON 正确保存和读取。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        refs = {
            "message_id": "msg_abc",
            "episode_id": "ep_def",
            "saga_id": "saga_xyz",
        }
        record = episodes.create_episode(
            session_id, topic="测试", origin_type=OriginType.MILESTONE,
            source_refs=refs,
        )
        assert record.source_refs == refs
        # DB 中确认 roundtrip
        loaded = episodes.get_episode(record.id)
        assert loaded is not None
        assert loaded.source_refs == refs
    finally:
        _cleanup(session_id)


def test_create_episode_invalid_origin_type():
    """无效 origin_type 抛出 ValueError。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        with pytest.raises(ValueError):
            episodes.create_episode(
                session_id, topic="x", origin_type="invalid_origin",
            )
    finally:
        _cleanup(session_id)


def test_create_episode_default_expires_at():
    """未提供 expires_at 时按默认 7 天计算。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        now = 1000000.0
        record = episodes.create_episode(
            session_id, topic="x", origin_type=OriginType.EMOTIONAL_CARE,
            now=now,
        )
        assert record.expires_at == now + episodes.DEFAULT_MAX_LIFETIME_SECONDS
        assert episodes.DEFAULT_MAX_LIFETIME_SECONDS == 7 * 24 * 3600
    finally:
        _cleanup(session_id)


# ---------- 2. 查询测试 ----------

def test_get_episode_by_id():
    """按 ID 查询。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="查", origin_type=OriginType.LIFE_SHARE,
        )
        loaded = episodes.get_episode(record.id)
        assert loaded is not None
        assert loaded.id == record.id
        assert loaded.topic == "查"
    finally:
        _cleanup(session_id)


def test_get_episode_not_found():
    """查询不存在的 ID 返回 None。"""
    db.init_db()
    assert episodes.get_episode("nonexistent-id-xyz") is None


def test_get_active_episode_for_session():
    """获取会话当前活跃的 ContactEpisode。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        # 创建多个 episode，验证返回最新 updated_at 的活跃 episode
        first = episodes.create_episode(
            session_id, topic="t1", origin_type=OriginType.EXPECTED_RETURN,
            now=1000.0,
        )
        # 第二个 episode 较晚创建，updated_at 较大
        second = episodes.create_episode(
            session_id, topic="t2", origin_type=OriginType.EMOTIONAL_CARE,
            now=2000.0,
        )
        active = episodes.get_active_episode_for_session(session_id)
        assert active is not None
        assert active.id == second.id
    finally:
        _cleanup(session_id)


def test_get_active_episode_for_session_excludes_terminal():
    """终态 episode 不返回。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t1", origin_type=OriginType.EXPECTED_RETURN,
        )
        # 转为终态 closed
        episodes.transition_status(
            record.id, EpisodeStatus.CLOSED, outcome=Outcome.CANCELLED,
        )
        assert episodes.get_active_episode_for_session(session_id) is None
    finally:
        _cleanup(session_id)


def test_list_active_episodes():
    """列出所有活跃 episode。"""
    db.init_db()
    session_id_1 = db.new_id()
    session_id_2 = db.new_id()
    _setup_session(session_id_1)
    _setup_session(session_id_2)
    try:
        # 先清理可能存在的活跃 episode
        conn = db.connect()
        try:
            conn.execute("DELETE FROM contact_episodes WHERE session_id IN (?, ?)",
                         (session_id_1, session_id_2))
            conn.commit()
        finally:
            conn.close()

        episodes.create_episode(
            session_id_1, topic="t1", origin_type=OriginType.EXPECTED_RETURN,
        )
        episodes.create_episode(
            session_id_2, topic="t2", origin_type=OriginType.EMOTIONAL_CARE,
        )
        active_list = episodes.list_active_episodes()
        assert len(active_list) >= 2
        # 包含两个 session 的活跃 episode
        session_ids = {e.session_id for e in active_list}
        assert session_id_1 in session_ids
        assert session_id_2 in session_ids
    finally:
        _cleanup(session_id_1)
        _cleanup(session_id_2)


# ---------- 3. 状态转换测试 ----------

def test_transition_status_proposed_to_waiting():
    """proposed → waiting。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        updated = episodes.transition_status(record.id, EpisodeStatus.WAITING)
        assert updated.status == EpisodeStatus.WAITING
        assert updated.id == record.id
    finally:
        _cleanup(session_id)


def test_transition_status_approached_to_quiet_waiting():
    """approached → quiet_waiting。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        # 第一次接近自动转为 approached
        episodes.record_approach(record.id, intensity=3)
        # 显式转为 quiet_waiting
        updated = episodes.transition_status(record.id, EpisodeStatus.QUIET_WAITING)
        assert updated.status == EpisodeStatus.QUIET_WAITING
    finally:
        _cleanup(session_id)


def test_transition_status_to_closed_with_outcome():
    """转为 closed 同时设置 outcome。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        updated = episodes.transition_status(
            record.id, EpisodeStatus.CLOSED, outcome=Outcome.REPLIED,
        )
        assert updated.status == EpisodeStatus.CLOSED
        assert updated.outcome == Outcome.REPLIED
    finally:
        _cleanup(session_id)


def test_transition_status_terminal_cannot_transition():
    """终态再转换应抛出 ValueError。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        episodes.transition_status(
            record.id, EpisodeStatus.CLOSED, outcome=Outcome.CANCELLED,
        )
        with pytest.raises(ValueError):
            episodes.transition_status(record.id, EpisodeStatus.WAITING)
    finally:
        _cleanup(session_id)


def test_transition_status_invalid_status():
    """无效状态抛出 ValueError。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        with pytest.raises(ValueError):
            episodes.transition_status(record.id, "invalid_status")
    finally:
        _cleanup(session_id)


# ---------- 4. 接近记录测试 ----------

def test_record_approach_increments_count():
    """approach_count 递增。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        assert record.approach_count == 0
        updated = episodes.record_approach(record.id, intensity=3)
        assert updated.approach_count == 1
        updated = episodes.record_approach(record.id, intensity=3)
        assert updated.approach_count == 2
    finally:
        _cleanup(session_id)


def test_record_approach_accumulates_pressure():
    """unanswered_pressure 累积（多次接近）。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        # intensity=3, channel_intrusiveness 默认 = 0.6 (Level 3)
        # delta = 3 * 0.6 * 1.0 = 1.8
        updated = episodes.record_approach(record.id, intensity=3)
        assert updated.unanswered_pressure == pytest.approx(1.8)
        # 再一次：1.8 + 1.8 = 3.6
        updated = episodes.record_approach(record.id, intensity=3)
        assert updated.unanswered_pressure == pytest.approx(3.6)
    finally:
        _cleanup(session_id)


def test_record_approach_sets_first_candidate_at():
    """第一次接近设置 first_candidate_at。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        assert record.first_candidate_at is None
        now = 1000000.0
        updated = episodes.record_approach(record.id, intensity=2, now=now)
        assert updated.first_candidate_at == now
        # 第二次接近不改变 first_candidate_at
        updated = episodes.record_approach(record.id, intensity=2, now=now + 100)
        assert updated.first_candidate_at == now
    finally:
        _cleanup(session_id)


def test_record_approach_updates_last_approach_at():
    """更新 last_approach_at。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        t1 = 1000000.0
        updated = episodes.record_approach(record.id, intensity=2, now=t1)
        assert updated.last_approach_at == t1
        t2 = t1 + 3600
        updated = episodes.record_approach(record.id, intensity=2, now=t2)
        assert updated.last_approach_at == t2
    finally:
        _cleanup(session_id)


def test_record_approach_updates_current_intensity():
    """更新 current_intensity。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        assert record.current_intensity == 0
        updated = episodes.record_approach(record.id, intensity=4)
        assert updated.current_intensity == 4
        updated = episodes.record_approach(record.id, intensity=2)
        assert updated.current_intensity == 2
    finally:
        _cleanup(session_id)


def test_record_approach_invalid_intensity():
    """intensity < 0 或 > 5 抛出 ValueError。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        with pytest.raises(ValueError):
            episodes.record_approach(record.id, intensity=-1)
        with pytest.raises(ValueError):
            episodes.record_approach(record.id, intensity=6)
    finally:
        _cleanup(session_id)


def test_record_approach_default_channel_intrusiveness():
    """未提供时按 intensity 从 CHANNEL_INTRUSIVENESS 取。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        # intensity=3, default intrusiveness=0.6 → delta = 3 * 0.6 = 1.8
        updated = episodes.record_approach(record.id, intensity=3)
        assert updated.unanswered_pressure == pytest.approx(1.8)
        # intensity=5, default intrusiveness=1.0 → delta = 5 * 1.0 = 5.0
        record2 = episodes.create_episode(
            session_id, topic="t2", origin_type=OriginType.EMOTIONAL_CARE,
        )
        updated = episodes.record_approach(record2.id, intensity=5)
        assert updated.unanswered_pressure == pytest.approx(5.0)
    finally:
        _cleanup(session_id)


def test_record_approach_with_repetition_factor():
    """重复程度因子生效。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        # intensity=3, intrusiveness=0.6, repetition=2.0 → 3 * 0.6 * 2.0 = 3.6
        updated = episodes.record_approach(
            record.id, intensity=3, repetition_factor=2.0,
        )
        assert updated.unanswered_pressure == pytest.approx(3.6)
    finally:
        _cleanup(session_id)


def test_record_approach_transitions_proposed_to_approached():
    """proposed → approached（自动转换）。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        assert record.status == EpisodeStatus.PROPOSED
        updated = episodes.record_approach(record.id, intensity=2)
        assert updated.status == EpisodeStatus.APPROACHED
    finally:
        _cleanup(session_id)


# ---------- 5. 衰减测试 ----------

def test_decay_pressure_reduces_over_time():
    """衰减 unanswered_pressure。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        # 先接近一次产生 pressure = 1.8
        # last_approach_at = 1000
        episodes.record_approach(record.id, intensity=3, now=1000.0)
        # 经过 10 小时后衰减
        # new_pressure = 1.8 - 10 * 0.05 = 1.3
        updated = episodes.decay_pressure(record.id, hours_elapsed=10.0, now=1000.0 + 36000)
        assert updated.unanswered_pressure == pytest.approx(1.3)
    finally:
        _cleanup(session_id)


def test_decay_pressure_floor_zero():
    """衰减不低于 0。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        # 接近产生较小 pressure = 0.3 (intensity=1, intrusiveness=0.3)
        episodes.record_approach(record.id, intensity=1, now=1000.0)
        # 经过 100 小时后衰减应到底
        # 0.3 - 100 * 0.05 = -4.7 → 钳为 0
        updated = episodes.decay_pressure(record.id, hours_elapsed=100.0, now=1000.0 + 360000)
        assert updated.unanswered_pressure == 0.0
    finally:
        _cleanup(session_id)


def test_decay_pressure_no_last_approach_at():
    """未接近过时不衰减。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        # 未接近过，调用 decay 不改变
        updated = episodes.decay_pressure(record.id, now=100000.0)
        assert updated.unanswered_pressure == 0.0
        assert updated.last_approach_at is None
    finally:
        _cleanup(session_id)


def test_decay_all_pressures_batch():
    """批量衰减所有活跃 episode 的 unanswered_pressure。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        # 清理现有活跃 episode
        conn = db.connect()
        try:
            conn.execute("DELETE FROM contact_episodes WHERE session_id=?", (session_id,))
            conn.commit()
        finally:
            conn.close()

        record1 = episodes.create_episode(
            session_id, topic="t1", origin_type=OriginType.EXPECTED_RETURN,
        )
        record2 = episodes.create_episode(
            session_id, topic="t2", origin_type=OriginType.EMOTIONAL_CARE,
        )
        # 都接近一次，pressure 都为 1.8（intensity=3, intrusiveness=0.6）
        episodes.record_approach(record1.id, intensity=3, now=1000.0)
        episodes.record_approach(record2.id, intensity=3, now=1000.0)
        # 批量衰减 10 小时
        affected = episodes.decay_all_pressures(now=1000.0 + 36000)
        assert affected == 2
        # 验证
        loaded1 = episodes.get_episode(record1.id)
        loaded2 = episodes.get_episode(record2.id)
        assert loaded1.unanswered_pressure == pytest.approx(1.3)
        assert loaded2.unanswered_pressure == pytest.approx(1.3)
    finally:
        _cleanup(session_id)


# ---------- 6. 用户后续行为测试 ----------

def test_apply_user_response_positive():
    """positive：pressure 快速降低，status=responded，outcome=replied。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        # 接近一次 pressure = 1.8
        episodes.record_approach(record.id, intensity=3)
        # positive: 1.8 * 0.1 = 0.18
        updated = episodes.apply_user_response(record.id, UserResponseType.POSITIVE)
        assert updated.unanswered_pressure == pytest.approx(0.18)
        assert updated.status == EpisodeStatus.RESPONDED
        assert updated.outcome == Outcome.REPLIED
    finally:
        _cleanup(session_id)


def test_apply_user_response_normal():
    """normal：pressure 适度降低，status=responded。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        episodes.record_approach(record.id, intensity=3)  # pressure=1.8
        # normal: 1.8 * 0.4 = 0.72
        updated = episodes.apply_user_response(record.id, UserResponseType.NORMAL)
        assert updated.unanswered_pressure == pytest.approx(0.72)
        assert updated.status == EpisodeStatus.RESPONDED
        assert updated.outcome == Outcome.REPLIED
    finally:
        _cleanup(session_id)


def test_apply_user_response_was_busy():
    """was_busy：pressure 降低但状态保持。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        episodes.record_approach(record.id, intensity=3)  # pressure=1.8
        # was_busy: 1.8 * 0.6 = 1.08
        updated = episodes.apply_user_response(record.id, UserResponseType.WAS_BUSY)
        assert updated.unanswered_pressure == pytest.approx(1.08)
        # 状态保持（approached）
        assert updated.status == EpisodeStatus.APPROACHED
        assert updated.outcome is None
    finally:
        _cleanup(session_id)


def test_apply_user_response_continue_reminding():
    """continue_reminding：pressure 轻微提高。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        episodes.record_approach(record.id, intensity=3)  # pressure=1.8
        # continue_reminding: 1.8 * 1.05 = 1.89
        updated = episodes.apply_user_response(record.id, UserResponseType.CONTINUE_REMINDING)
        assert updated.unanswered_pressure == pytest.approx(1.89)
        assert updated.status == EpisodeStatus.APPROACHED
    finally:
        _cleanup(session_id)


def test_apply_user_response_stop_pushing():
    """stop_pushing：pressure 不变，状态保持。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        episodes.record_approach(record.id, intensity=3)  # pressure=1.8
        # stop_pushing: 1.8 不变
        updated = episodes.apply_user_response(record.id, UserResponseType.STOP_PUSHING)
        assert updated.unanswered_pressure == pytest.approx(1.8)
        assert updated.status == EpisodeStatus.APPROACHED
    finally:
        _cleanup(session_id)


def test_apply_user_response_explicit_reject():
    """explicit_reject：status=blocked，outcome=rejected。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        episodes.record_approach(record.id, intensity=3)  # pressure=1.8
        updated = episodes.apply_user_response(record.id, UserResponseType.EXPLICIT_REJECT)
        assert updated.status == EpisodeStatus.BLOCKED
        assert updated.outcome == Outcome.REJECTED
        # pressure 保持
        assert updated.unanswered_pressure == pytest.approx(1.8)
    finally:
        _cleanup(session_id)


def test_apply_user_response_invalid_type():
    """无效 response_type 抛出 ValueError。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        with pytest.raises(ValueError):
            episodes.apply_user_response(record.id, "invalid_response")
    finally:
        _cleanup(session_id)


# ---------- 7. 过期测试 ----------

def test_expire_episodes_marks_expired():
    """批量过期超过 expires_at 的活跃 episode。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        # 清理现有活跃 episode
        conn = db.connect()
        try:
            conn.execute("DELETE FROM contact_episodes WHERE session_id=?", (session_id,))
            conn.commit()
        finally:
            conn.close()

        # 创建一个 expires_at 在过去的 episode
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
            now=1000.0,
        )
        # 验证 expires_at = 1000 + 7*24*3600
        # 用 now = 1000 + 7*24*3600 + 1 触发过期
        future_now = 1000.0 + episodes.DEFAULT_MAX_LIFETIME_SECONDS + 1
        affected = episodes.expire_episodes(now=future_now)
        assert affected >= 1
        loaded = episodes.get_episode(record.id)
        assert loaded.status == EpisodeStatus.EXPIRED
        assert loaded.outcome == Outcome.EXPIRED
    finally:
        _cleanup(session_id)


def test_expire_episodes_excludes_terminal():
    """终态 episode 不会再被过期（已不在 ACTIVE_STATUSES）。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        # 创建一个已 closed 的 episode
        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
            now=1000.0,
        )
        episodes.transition_status(
            record.id, EpisodeStatus.CLOSED, outcome=Outcome.CANCELLED,
        )
        future_now = 1000.0 + episodes.DEFAULT_MAX_LIFETIME_SECONDS + 1
        affected = episodes.expire_episodes(now=future_now)
        # 不应影响已 closed 的 episode
        assert affected == 0
        loaded = episodes.get_episode(record.id)
        assert loaded.status == EpisodeStatus.CLOSED  # 状态不变
        assert loaded.outcome == Outcome.CANCELLED
    finally:
        _cleanup(session_id)


# ---------- 8. 沉默不降低 bond/trust 测试 ----------

def test_silence_does_not_affect_relationship():
    """用户沉默（不调用 apply_user_response）不会自动产生 relationship delta 建议。

    验证：创建 episode + record_approach + decay_pressure（无 apply_user_response），
    检查 episode_relationship_delta_suggestions 表无新记录。
    """
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        # 先记录当前 delta 建议数量
        conn = db.connect()
        try:
            before = conn.execute(
                "SELECT COUNT(*) FROM episode_relationship_delta_suggestions WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
        finally:
            conn.close()

        record = episodes.create_episode(
            session_id, topic="t", origin_type=OriginType.EXPECTED_RETURN,
        )
        episodes.record_approach(record.id, intensity=3)  # 接近一次
        episodes.decay_pressure(record.id, hours_elapsed=10.0)  # 衰减

        # 不调用 apply_user_response（用户沉默）

        # 验证没有产生新的 relationship delta 建议
        conn = db.connect()
        try:
            after = conn.execute(
                "SELECT COUNT(*) FROM episode_relationship_delta_suggestions WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
        finally:
            conn.close()
        assert after == before, "沉默不应自动产生 relationship delta 建议"

        # episode 状态未进入 responded/blocked（仍然在 approached）
        loaded = episodes.get_episode(record.id)
        assert loaded.status == EpisodeStatus.APPROACHED
        assert loaded.outcome is None
    finally:
        _cleanup(session_id)


# ---------- 9. schema 测试 ----------

def test_schema_version_is_52():
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


def test_contact_episodes_table_exists():
    """contact_episodes 表存在。"""
    db.init_db()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='contact_episodes'"
        ).fetchone()
        assert row is not None
        assert row["name"] == "contact_episodes"
    finally:
        conn.close()


def test_contact_episodes_has_10_status_values():
    """CHECK 约束允许 10 种 status。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    valid_statuses = list(episodes.ALL_STATUSES)
    assert len(valid_statuses) == 10
    conn = db.connect()
    try:
        for status in valid_statuses:
            record_id = db.new_id()
            now = db.now()
            conn.execute(
                "INSERT INTO contact_episodes"
                " (id, session_id, topic, origin_type, source_refs, open_thread,"
                "  first_candidate_at, last_approach_at, approach_count,"
                "  unanswered_pressure, current_intensity, status, expires_at,"
                "  outcome, protocol_version, created_at, updated_at)"
                " VALUES (?, ?, 't', 'expected_return', '{}', NULL, NULL, NULL, 0,"
                "  0.0, 0, ?, NULL, NULL, 'proactive-decision-v2', ?, ?)",
                (record_id, session_id, status, now, now),
            )
            conn.commit()
        # 无效状态应被拒绝
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO contact_episodes"
                " (id, session_id, topic, origin_type, source_refs, open_thread,"
                "  first_candidate_at, last_approach_at, approach_count,"
                "  unanswered_pressure, current_intensity, status, expires_at,"
                "  outcome, protocol_version, created_at, updated_at)"
                " VALUES (?, ?, 't', 'expected_return', '{}', NULL, NULL, NULL, 0,"
                "  0.0, 0, 'invalid_status', NULL, NULL, 'proactive-decision-v2', ?, ?)",
                (db.new_id(), session_id, now, now),
            )
    finally:
        conn.close()
        _cleanup(session_id)


def test_contact_episodes_has_4_origin_types():
    """CHECK 约束允许 4 种 origin_type。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    valid_origins = [
        OriginType.EXPECTED_RETURN,
        OriginType.EMOTIONAL_CARE,
        OriginType.MILESTONE,
        OriginType.LIFE_SHARE,
    ]
    conn = db.connect()
    try:
        for ot in valid_origins:
            record_id = db.new_id()
            now = db.now()
            conn.execute(
                "INSERT INTO contact_episodes"
                " (id, session_id, topic, origin_type, source_refs, open_thread,"
                "  first_candidate_at, last_approach_at, approach_count,"
                "  unanswered_pressure, current_intensity, status, expires_at,"
                "  outcome, protocol_version, created_at, updated_at)"
                " VALUES (?, ?, 't', ?, '{}', NULL, NULL, NULL, 0,"
                "  0.0, 0, 'proposed', NULL, NULL, 'proactive-decision-v2', ?, ?)",
                (record_id, session_id, ot, now, now),
            )
            conn.commit()
        # 无效 origin_type 应被拒绝
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO contact_episodes"
                " (id, session_id, topic, origin_type, source_refs, open_thread,"
                "  first_candidate_at, last_approach_at, approach_count,"
                "  unanswered_pressure, current_intensity, status, expires_at,"
                "  outcome, protocol_version, created_at, updated_at)"
                " VALUES (?, ?, 't', 'invalid', '{}', NULL, NULL, NULL, 0,"
                "  0.0, 0, 'proposed', NULL, NULL, 'proactive-decision-v2', ?, ?)",
                (db.new_id(), session_id, now, now),
            )
    finally:
        conn.close()
        _cleanup(session_id)
