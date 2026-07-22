"""EAP v0.2 确定性时间线模拟器测试（spec 第 14 节支撑模块）。

覆盖 TimelineSimulator 自身的功能：
1. 初始状态与时间控制（2 个）
2. mock 控制（1 个）
3. 事件调度（1 个）
4. 用户行为模拟（3 个）
5. 异常场景模拟（1 个）
6. 结果汇总（1 个）
7. 场景常量（1 个）

合计 10 个测试。
"""
import pytest

from app import db
from app.proactive import candidates as candidates_mod
from app.proactive import episodes as episodes_mod
from app.proactive import presence as presence_mod
from app.proactive import timeline_simulator as ts_mod
from app.proactive.timeline_simulator import (
    DEFAULT_TICK_SECONDS,
    SCENARIO_DURATION_LONG,
    SCENARIO_DURATION_SHORT,
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
            (session_id, "sim 测试", now, now),
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


# ---------- 1. 初始状态与时间控制 ----------

def test_simulator_initial_state():
    """模拟器初始状态：current_time=start_time，事件/决策/episode 列表为空。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        sim = TimelineSimulator(session_id, start_time=1000000000.0)
        assert sim.now() == 1000000000.0
        assert sim._events == []
        assert sim._scheduled_events == []
        assert sim._decisions == []
        assert sim._intensity_plans == []
        assert sim._expression_plans == []
        assert sim._episodes == []
        assert sim._candidates == []
        assert sim._mocking_active is False
    finally:
        _cleanup_session(session_id)


def test_simulator_advance_time():
    """advance 推进模拟时间；负数抛 ValueError；0 不变化。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        sim = TimelineSimulator(session_id, start_time=1000000000.0)
        assert sim.now() == 1000000000.0
        sim.advance(60)
        assert sim.now() == 1000000060.0
        sim.advance(3600)
        assert sim.now() == 1000003660.0
        # 负数应抛出 ValueError
        with pytest.raises(ValueError):
            sim.advance(-1)
        # 推进 0 不变化
        sim.advance(0)
        assert sim.now() == 1000003660.0
    finally:
        _cleanup_session(session_id)


# ---------- 2. mock 控制 ----------

def test_simulator_mock_control():
    """start/stop mocking 控制 db.now() 返回模拟时间；with 语句自动管理。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        sim = TimelineSimulator(session_id, start_time=2000000000.0)
        # 未启动 mocking 时，db.now() 返回 wall clock，与模拟时间不同
        assert db.now() != sim.now()

        sim.start_mocking()
        assert sim._mocking_active is True
        # 启动后 db.now() 返回模拟时间
        assert db.now() == sim.now()
        sim.advance(120)
        assert db.now() == sim.now()
        assert db.now() == 2000000120.0

        sim.stop_mocking()
        assert sim._mocking_active is False
        # 停止后 db.now() 恢复 wall clock
        assert db.now() != sim.now()

        # 重复 start/stop 不出错（幂等）
        sim.start_mocking()
        sim.start_mocking()  # 重复 start
        assert sim._mocking_active is True
        sim.stop_mocking()
        sim.stop_mocking()  # 重复 stop
        assert sim._mocking_active is False

        # with 语句上下文管理
        sim2 = TimelineSimulator(session_id, start_time=3000000000.0)
        with sim2:
            assert sim2._mocking_active is True
            assert db.now() == sim2.now()
            sim2.advance(60)
            assert db.now() == 3000000060.0
        assert sim2._mocking_active is False
    finally:
        _cleanup_session(session_id)


# ---------- 3. 事件调度 ----------

def test_simulator_schedule_event():
    """schedule_event 在 advance 后由 run_pending_events 触发；负 delay 抛 ValueError。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        sim = TimelineSimulator(session_id, start_time=1000000000.0)
        with sim:
            # 负 delay 抛出 ValueError
            with pytest.raises(ValueError):
                sim.schedule_event(-1, 'test', {}, '')

            sim.schedule_event(60, 'test_event', {'key': 'value'}, '测试事件')
            # 未到期
            sim.advance(30)
            sim.run_pending_events()
            assert len(sim._events) == 0

            # 到期
            sim.advance(30)
            sim.run_pending_events()
            assert len(sim._events) == 1
            assert sim._events[0].event_kind == 'test_event'
            assert sim._events[0].payload == {'key': 'value'}
            assert sim._events[0].description == '测试事件'
            assert sim._events[0].timestamp == 1000000060.0

            # 调度事件已从 _scheduled_events 移除
            assert len(sim._scheduled_events) == 0
            # 再次 run_pending_events 不会重复触发
            sim.run_pending_events()
            assert len(sim._events) == 1
    finally:
        _cleanup_session(session_id)


# ---------- 4. 用户行为模拟 ----------

def test_simulator_user_message_updates_presence():
    """user_message 触发 presence 检测，记录 user_message 事件。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        sim = TimelineSimulator(session_id, start_time=1000000000.0)
        with sim:
            # "我去跑测试" 匹配 AWAY_BRIEF + open_thread
            record = sim.user_message("我去跑一下测试")
            assert record.user_status == presence_mod.UserStatus.AWAY_BRIEF
            assert record.open_thread is True
            assert record.open_thread_topic == "测试结果"
            assert record.is_active is True

            # 事件已记录
            assert len(sim._events) == 1
            assert sim._events[0].event_kind == 'user_message'
            assert sim._events[0].payload['text'] == "我去跑一下测试"
            assert sim._events[0].payload['presence_status'] == 'away_brief'
            assert sim._events[0].payload['open_thread'] is True

            # delay 参数推进时间
            sim.user_message("我回来了", delay=120)
            assert sim.now() == 1000000120.0
    finally:
        _cleanup_session(session_id)


def test_simulator_user_responds_updates_episode():
    """user_responds 将响应应用到最新 episode，更新状态与 pressure。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        sim = TimelineSimulator(session_id, start_time=1000000000.0)
        with sim:
            sim.create_episode(
                topic="测试响应",
                origin_type=episodes_mod.OriginType.EMOTIONAL_CARE,
            )
            # 记录一次接近产生 baseline pressure
            sim.record_approach(intensity=3)
            baseline = sim._episodes[-1].unanswered_pressure
            assert baseline > 0  # 累积 ΔP = 3 × 0.6 × 1.0 = 1.8

            # 用户积极回应 → pressure 大幅降低（×0.1），状态→responded，outcome→replied
            sim.user_responds(episodes_mod.UserResponseType.POSITIVE)
            episode = sim._episodes[-1]
            assert episode.status == 'responded'
            assert episode.outcome == 'replied'
            assert episode.unanswered_pressure < baseline

            # episode_id 为空且无 episode 时抛出 ValueError
            sim2 = TimelineSimulator(session_id)
            with sim2:
                with pytest.raises(ValueError):
                    sim2.user_responds(episodes_mod.UserResponseType.NORMAL)
    finally:
        _cleanup_session(session_id)


def test_simulator_go_sleep_and_wake():
    """go_sleep 设置 AWAY_SLEEP，wake_up 设置 ONLINE。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        sim = TimelineSimulator(session_id, start_time=1000000000.0)
        with sim:
            sim.go_sleep(duration=8 * 3600)
            record = presence_mod.get_current_presence(session_id)
            assert record.user_status == 'away_sleep'
            assert record.is_active is True

            sim.wake_up()
            record = presence_mod.get_current_presence(session_id)
            assert record.user_status == 'online'
            assert record.is_active is True

            # 事件记录
            kinds = [e.event_kind for e in sim._events]
            assert 'sleep' in kinds
            assert 'wake' in kinds
    finally:
        _cleanup_session(session_id)


# ---------- 5. 异常场景模拟 ----------

def test_simulator_exception_scenarios():
    """clock_rollback/crash_recovery/network_disconnect 三类异常场景记录正确事件。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        sim = TimelineSimulator(session_id, start_time=1000000000.0)
        with sim:
            # 时钟回拨
            sim.advance(7200)
            assert sim.now() == 1000007200.0
            sim.simulate_clock_rollback(rollback_seconds=3600)
            assert sim.now() == 1000003600.0
            assert sim._events[-1].event_kind == 'clock_rollback'
            assert sim._events[-1].payload['rollback_seconds'] == 3600

            # 时钟回拨不低于 start_time
            sim.simulate_clock_rollback(rollback_seconds=10**12)
            assert sim.now() == 1000000000.0

            # 崩溃恢复
            sim.simulate_crash_recovery()
            assert sim._events[-1].event_kind == 'crash_recovery'

            # 网络断开
            sim.simulate_network_disconnect(duration=600)
            assert sim._events[-1].event_kind == 'network_disconnect'
            assert sim._events[-1].payload['duration'] == 600
    finally:
        _cleanup_session(session_id)


# ---------- 6. 结果汇总 ----------

def test_simulator_get_result_summary():
    """get_result 返回 SimulationResult，包含完整统计字段。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        sim = TimelineSimulator(session_id, start_time=1000000000.0)
        with sim:
            sim.user_message("我去跑测试")
            sim.create_episode(
                topic="测试结果",
                origin_type=episodes_mod.OriginType.EXPECTED_RETURN,
                open_thread="测试结果",
            )
            sim.create_candidate(
                candidate_kind=candidates_mod.CandidateKind.RETURN_FOLLOWUP,
                topic="测试结果",
                source_messages=[
                    {"id": "m1", "role": "user", "content": "我去跑测试了"},
                ],
            )
            sim.advance(20 * 60)
            sim.run_decision_cycle()

        result = sim.get_result()
        assert isinstance(result, SimulationResult)
        assert result.session_id == session_id
        assert result.start_time == 1000000000.0
        assert result.end_time == 1000000000.0 + 20 * 60
        # 至少记录 user_message + episode_created + candidate_created + decision_run
        assert result.summary['total_events'] >= 4
        assert result.summary['total_decisions'] == 1
        assert result.summary['total_episodes'] == 1
        assert result.summary['total_candidates'] == 1
        assert result.summary['duration_seconds'] == 20 * 60
        # 决策计数分类应存在
        for key in ('send_count', 'suppress_count', 'defer_count', 'abandon_count'):
            assert key in result.summary
        # 总和应等于 total_decisions
        total = (
            result.summary['send_count']
            + result.summary['suppress_count']
            + result.summary['defer_count']
            + result.summary['abandon_count']
        )
        assert total == result.summary['total_decisions']
    finally:
        _cleanup_session(session_id)


# ---------- 7. 场景常量 ----------

def test_scenario_constants():
    """验证场景常量符合 spec 第 14 节要求（15 分钟~30 天）。"""
    assert SCENARIO_DURATION_SHORT == 15 * 60  # 15 分钟
    assert SCENARIO_DURATION_LONG == 30 * 24 * 3600  # 30 天
    assert DEFAULT_TICK_SECONDS == 60  # 每 tick 60 秒
    # 长场景覆盖足够时长
    assert SCENARIO_DURATION_LONG > 24 * 3600  # 超过 1 天
    assert SCENARIO_DURATION_LONG > 7 * 24 * 3600  # 超过 episode 默认生命周期
