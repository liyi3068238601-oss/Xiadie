"""EAP v0.2 确定性时间线模拟器（spec 第 14 节验收场景支撑）。

按 spec 第 14 节四类验收场景要求，本模块提供确定性时间线模拟器，
用于长期场景测试（15 分钟~30 天）。模拟器封装一组工具，按确定性时间线推进
（不依赖 wall clock），并可驱动真实 orchestrator/delivery/feedback 生产路径。

关键设计：
- 使用受控时间（不依赖 time.time()），通过 unittest.mock.patch 控制 db.now()
- 每次 tick 推进模拟时间
- 调度事件并触发相应模块
- 记录所有决策、强度计划、表达计划用于审计
- 不实际调用 LLM（测试中使用 None 或 mock llm_advice）

模块隔离：本模块不接入 main.py，但调用与主应用相同的生产 repository 和 reducer。
旧的纯领域辅助方法只为兼容既有场景保留；R6 验收必须使用 production_* 方法。
"""

from dataclasses import dataclass
from typing import Optional
from unittest.mock import patch

from .. import db
from . import candidates as candidates_mod
from . import decision as decision_mod
from . import delivery as delivery_mod
from . import episodes as episodes_mod
from . import expression as expression_mod
from . import feedback as feedback_mod
from . import intensity as intensity_mod
from . import orchestrator as orchestrator_mod
from . import presence as presence_mod
from . import settings as settings_mod


# 场景时长常量（spec 第 14 节）
SCENARIO_DURATION_SHORT = 15 * 60  # 15 分钟
SCENARIO_DURATION_LONG = 30 * 24 * 3600  # 30 天
DEFAULT_TICK_SECONDS = 60  # 每 tick 60 秒


@dataclass
class TimelineEvent:
    """时间线上的一个事件。"""
    timestamp: float  # 模拟时间戳
    event_kind: str  # 'user_message' / 'presence_change' / 'decision_run' / ...
    payload: dict  # 事件数据
    description: str = ''


@dataclass
class SimulationResult:
    """模拟运行结果。"""
    session_id: str
    start_time: float
    end_time: float
    events: list  # TimelineEvent 列表
    decisions: list  # ProactiveDecision 列表
    intensity_plans: list  # IntensityPlan 列表
    expression_plans: list  # ExpressionPlan 列表
    episodes: list  # ContactEpisode 列表
    summary: dict  # 汇总统计


# ============================================================================
# 内部工具：测试 session 管理
# ============================================================================

def _setup_session(session_id: str) -> None:
    """插入测试 session。"""
    now = db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (session_id, "timeline 模拟", now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _cleanup_session(session_id: str) -> None:
    """清理测试 session 相关数据。"""
    conn = db.connect()
    try:
        # 按外键依赖顺序清理
        conn.execute(
            "DELETE FROM proactive_preference_weights WHERE source_feedback_id IN "
            "(SELECT id FROM proactive_feedback WHERE session_id=?)", (session_id,),
        )
        conn.execute("DELETE FROM proactive_feedback WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM proactive_deliveries WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM expression_plans WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM proactive_intensity_plans WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM proactive_decisions WHERE session_id=?", (session_id,))
        conn.execute(
            "DELETE FROM proactive_candidate_claims WHERE candidate_id IN "
            "(SELECT id FROM proactive_candidates WHERE session_id=?)", (session_id,),
        )
        conn.execute(
            "DELETE FROM proactive_runtime_sagas WHERE candidate_id IN "
            "(SELECT id FROM proactive_candidates WHERE session_id=?)", (session_id,),
        )
        conn.execute("DELETE FROM proactive_runtime_sources WHERE session_id=?", (session_id,))
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


# ============================================================================
# TimelineSimulator
# ============================================================================

class TimelineSimulator:
    """确定性时间线模拟器。

    - 使用受控时间（不依赖 time.time()），通过 mock patch 控制 db.now()
    - 每次 tick 推进模拟时间
    - 调度事件并触发相应模块
    - 记录所有决策、强度计划、表达计划用于审计

    用法：
        sim = TimelineSimulator(session_id)
        with sim:  # 启动 db.now mock
            sim.user_message("我去跑一下测试")
            sim.advance(20 * 60)
            sim.run_decision_cycle()
        result = sim.get_result()
    """

    def __init__(self, session_id: str, *, start_time: float = 1000000000.0):
        """初始化模拟器。start_time 使用固定值确保确定性。"""
        self.session_id = session_id
        self._start_time = start_time
        self._current_time = start_time
        self._events: list = []
        self._scheduled_events: list = []  # (fire_at, event_kind, payload, description)
        self._decisions: list = []
        self._intensity_plans: list = []
        self._expression_plans: list = []
        self._episodes: list = []  # ContactEpisode 对象列表
        self._episode_ids: list = []
        self._candidates: list = []  # ProactiveCandidate 对象列表
        self._candidate_ids: list = []
        self._presence_records: list = []
        self._production_message_ids: list[str] = []
        self._production_feedback: list[dict] = []
        self._db_now_patcher = None
        self._mocking_active = False

    # ---------- 时间控制 ----------

    def now(self) -> float:
        """返回当前模拟时间。"""
        return self._current_time

    def advance(self, seconds: float):
        """推进模拟时间。"""
        if seconds < 0:
            raise ValueError(f"advance seconds must be >= 0, got {seconds}")
        self._current_time += seconds

    # ---------- mock 控制 ----------

    def start_mocking(self):
        """开始 mock db.now() 返回当前模拟时间。"""
        if self._mocking_active:
            return
        # 使用 side_effect 动态返回 self._current_time
        self._db_now_patcher = patch(
            "app.db.now", side_effect=lambda: self._current_time,
        )
        self._db_now_patcher.start()
        self._mocking_active = True

    def stop_mocking(self):
        """停止 mock db.now()。"""
        if not self._mocking_active:
            return
        if self._db_now_patcher is not None:
            self._db_now_patcher.stop()
            self._db_now_patcher = None
        self._mocking_active = False

    def __enter__(self):
        self.start_mocking()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_mocking()
        return False

    # ---------- 事件记录 ----------

    def _record_event(
        self, event_kind: str, payload: dict, description: str = '',
    ) -> TimelineEvent:
        """记录一个事件到时间线。"""
        event = TimelineEvent(
            timestamp=self._current_time,
            event_kind=event_kind,
            payload=payload,
            description=description,
        )
        self._events.append(event)
        return event

    # ---------- 调度事件 ----------

    def schedule_event(
        self, delay_seconds: float, event_kind: str,
        payload: dict, description: str = '',
    ):
        """调度一个未来事件。

        事件在 run_pending_events() 被调用且当前时间 >= 调度时间时触发。
        """
        if delay_seconds < 0:
            raise ValueError(
                f"delay_seconds must be >= 0, got {delay_seconds}"
            )
        fire_at = self._current_time + delay_seconds
        self._scheduled_events.append(
            (fire_at, event_kind, payload, description),
        )

    def run_pending_events(self):
        """执行所有到期的预定事件。

        按 fire_at 升序执行。每个事件的 timestamp 设为其 fire_at。
        """
        ready = [e for e in self._scheduled_events if e[0] <= self._current_time]
        self._scheduled_events = [
            e for e in self._scheduled_events if e[0] > self._current_time
        ]
        ready.sort(key=lambda x: x[0])
        for fire_at, event_kind, payload, description in ready:
            event = TimelineEvent(
                timestamp=fire_at,
                event_kind=event_kind,
                payload=payload,
                description=description,
            )
            self._events.append(event)
            self._dispatch_scheduled_event(event)

    def _dispatch_scheduled_event(self, event: TimelineEvent):
        """分发调度事件到对应处理器。

        本方法处理由 schedule_event 调度的延迟事件。
        直接调用（user_message/go_sleep 等）不走此路径。
        """
        # 调度事件主要用于记录（如"用户在 t=X 未回复"），
        # 实际状态变更由直接调用方法完成。
        # 子类或测试可扩展此方法实现自定义事件处理。
        pass

    # ---------- 用户行为模拟 ----------

    def user_message(self, text: str, *, delay: float = 0):
        """模拟用户发送消息。触发 presence 检测。

        - 检测 presence 信号（晚安/去测试/先这样等）
        - 更新 conversation_presence 表
        - 记录事件
        """
        if delay > 0:
            self.advance(delay)
        signal = presence_mod.detect_presence_signals(text)
        record = presence_mod.update_presence(
            self.session_id, signal, detected_at=self._current_time,
        )
        self._presence_records.append(record)
        self._record_event(
            'user_message',
            {
                'text': text,
                'presence_status': signal.user_status,
                'open_thread': signal.open_thread,
                'open_thread_topic': signal.open_thread_topic,
            },
            description=f'用户消息: {text[:50]}',
        )
        return record

    # ---------- R6 生产路径驱动 ----------

    def initialize_production(self, *, delivery_enabled: bool = True) -> None:
        """Create the session and configure controls through production setting APIs."""
        db.init_db()
        conn = db.connect()
        try:
            exists = conn.execute(
                "SELECT 1 FROM sessions WHERE id=?", (self.session_id,),
            ).fetchone()
        finally:
            conn.close()
        if not exists:
            _setup_session(self.session_id)
        for key, value in (
            ("proactive_enabled", "1"),
            ("proactive_local_delivery_enabled", "1" if delivery_enabled else "0"),
            ("proactive_desktop_notification_enabled", "0"),
            ("proactive_quiet_hours_start", "0"),
            ("proactive_quiet_hours_end", "0"),
            ("proactive_pause_until", ""),
        ):
            settings_mod.write_public_setting(key, value)
        db.set_setting("proactive_emergency_stop", "0")
        db.set_setting("proactive_last_reliable_now", "0")

    def production_turn(self, user_text: str, assistant_text: str = "我在这里") -> dict:
        """Persist a real chat turn and call the same hooks as app.main."""
        if not self._session_exists():
            self.initialize_production()
        user_id, assistant_id = db.new_id(), db.new_id()
        conn = db.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
                (user_id, self.session_id, "user", user_text, self._current_time),
            )
            conn.execute(
                "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
                (assistant_id, self.session_id, "assistant", assistant_text, self._current_time),
            )
            conn.execute(
                "UPDATE sessions SET updated_at=? WHERE id=?",
                (self._current_time, self.session_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        orchestrator_mod.handle_user_message(self.session_id, now=self._current_time)
        inferred = feedback_mod.capture_natural_feedback(
            self.session_id, user_id, user_text, now=self._current_time,
        )
        if inferred:
            self._production_feedback.append(inferred)
        signal = presence_mod.detect_presence_signals(user_text)
        presence_mod.update_presence(
            self.session_id, signal, source_message_id=user_id,
            detected_at=self._current_time,
        )
        queued = orchestrator_mod.enqueue_after_chat(
            session_id=self.session_id, user_message_id=user_id,
            assistant_message_id=assistant_id, now=self._current_time,
        )
        self._production_message_ids.extend((user_id, assistant_id))
        self._record_event(
            "production_turn",
            {"user_message_id": user_id, "assistant_message_id": assistant_id,
             "queued_source_ids": [item["id"] for item in queued]},
            description="真实聊天 hook 已执行",
        )
        return {"user_message_id": user_id, "assistant_message_id": assistant_id,
                "sources": queued, "feedback": inferred}

    def _session_exists(self) -> bool:
        conn = db.connect()
        try:
            return conn.execute(
                "SELECT 1 FROM sessions WHERE id=?", (self.session_id,),
            ).fetchone() is not None
        finally:
            conn.close()

    def run_production_cycle(self, *, level: Optional[int] = None) -> int:
        """Drive expiry, decay and the real recoverable orchestrator."""
        presence_mod.expire_stale_presences(now=self._current_time)
        episodes_mod.expire_episodes(now=self._current_time)
        episodes_mod.decay_all_pressures(now=self._current_time)
        level_patch = (
            patch.object(intensity_mod, "select_minimum_sufficient_level", return_value=level)
            if level is not None else None
        )
        if level_patch:
            level_patch.start()
        try:
            processed = orchestrator_mod.process_due(
                now=self._current_time, worker_id=f"timeline-{self.session_id}",
            )
        finally:
            if level_patch:
                level_patch.stop()
        self._record_event(
            "production_cycle", {"processed": processed},
            description="生产 orchestrator 周期",
        )
        return processed

    def consume_production_deliveries(self, consumer_id: str = "timeline") -> list[dict]:
        """Consume confirmed local deliveries through claim/begin/ack."""
        completed: list[dict] = []
        while True:
            claimed = delivery_mod.claim_next(consumer_id, now=self._current_time)
            if not claimed:
                break
            begun = delivery_mod.begin_delivery(
                claimed["id"], consumer_id, claimed["lease_token"], now=self._current_time,
            )
            if begun["status"] == "delivering":
                begun = delivery_mod.acknowledge_delivery(
                    begun["id"], consumer_id, begun["lease_token"],
                    success=True, now=self._current_time,
                )
            completed.append(begun)
        self._record_event(
            "production_delivery", {"delivery_ids": [item["id"] for item in completed]},
            description="生产 Delivery 消费",
        )
        return completed

    def production_feedback(self, delivery_id: str, feedback_kind: str) -> dict:
        item = feedback_mod.create_feedback(
            delivery_id, feedback_kind, request_nonce=db.new_id(), now=self._current_time,
        )
        self._production_feedback.append(item)
        self._record_event(
            "production_feedback", {"feedback_id": item["id"], "kind": feedback_kind},
            description="grounded feedback 已应用",
        )
        return item

    def production_metrics(self) -> dict:
        """Return body-free release metrics calculated from production ledgers."""
        conn = db.connect()
        try:
            visible = conn.execute(
                "SELECT COUNT(*) FROM proactive_deliveries WHERE session_id=? "
                "AND level>0 AND status='delivered'", (self.session_id,),
            ).fetchone()[0]
            traced = conn.execute(
                "SELECT COUNT(*) FROM proactive_deliveries d "
                "JOIN proactive_decisions pd ON pd.id=d.decision_id "
                "JOIN proactive_candidates c ON c.id=d.candidate_id "
                "WHERE d.session_id=? AND d.level>0 AND d.status='delivered'",
                (self.session_id,),
            ).fetchone()[0]
            duplicates = conn.execute(
                "SELECT COUNT(*) FROM (SELECT decision_id FROM proactive_deliveries "
                "WHERE session_id=? GROUP BY decision_id HAVING COUNT(*)>1)",
                (self.session_id,),
            ).fetchone()[0]
            level5 = conn.execute(
                "SELECT COUNT(*) FROM proactive_deliveries WHERE session_id=? AND level=5",
                (self.session_id,),
            ).fetchone()[0]
            orphan_sources = conn.execute(
                "SELECT COUNT(*) FROM proactive_candidates c LEFT JOIN proactive_runtime_sources s "
                "ON s.id=c.runtime_source_id WHERE c.session_id=? AND s.id IS NULL",
                (self.session_id,),
            ).fetchone()[0]
        finally:
            conn.close()
        return {
            "visible_deliveries": visible,
            "traceability_rate": 1.0 if visible == 0 else traced / visible,
            "duplicate_delivery_count": duplicates,
            "level5_delivery_count": level5,
            "orphan_source_count": orphan_sources,
        }

    def user_responds(
        self, response_type: str, *,
        delay: float = 0,
        episode_id: Optional[str] = None,
    ):
        """模拟用户响应。

        response_type: positive/normal/was_busy/continue_reminding/
                       stop_pushing/explicit_reject
        """
        if delay > 0:
            self.advance(delay)
        if episode_id is None and self._episode_ids:
            episode_id = self._episode_ids[-1]
        if episode_id is None:
            raise ValueError("no episode to apply response to")
        episode = episodes_mod.apply_user_response(
            episode_id, response_type, now=self._current_time,
        )
        # 更新缓存的 episode 对象
        for i, ep in enumerate(self._episodes):
            if ep.id == episode.id:
                self._episodes[i] = episode
                break
        else:
            self._episodes.append(episode)
        self._record_event(
            'user_response',
            {
                'response_type': response_type,
                'episode_id': episode_id,
            },
            description=f'用户响应: {response_type}',
        )
        return episode

    def go_sleep(self, *, delay: float = 0, duration: float = 8 * 3600):
        """模拟用户睡眠。设置 presence 为 away_sleep。"""
        if delay > 0:
            self.advance(delay)
        signal = presence_mod.PresenceSignal(
            user_status=presence_mod.UserStatus.AWAY_SLEEP,
        )
        record = presence_mod.update_presence(
            self.session_id, signal, detected_at=self._current_time,
        )
        self._presence_records.append(record)
        self._record_event(
            'sleep',
            {'duration': duration},
            description=f'用户睡眠 {duration/3600:.1f}h',
        )
        return record

    def wake_up(self, *, delay: float = 0):
        """模拟用户醒来。设置 presence 为 online。"""
        if delay > 0:
            self.advance(delay)
        signal = presence_mod.PresenceSignal(
            user_status=presence_mod.UserStatus.ONLINE,
        )
        record = presence_mod.update_presence(
            self.session_id, signal, detected_at=self._current_time,
        )
        self._presence_records.append(record)
        settings_mod.mark_system_resume(now=self._current_time)
        self._record_event('wake', {}, description='用户醒来')
        return record

    # ---------- 异常场景模拟 ----------

    def simulate_clock_rollback(
        self, *, delay: float = 0, rollback_seconds: float = 3600,
    ):
        """模拟时钟回拨。

        将模拟时间回拨 rollback_seconds，但不低于 start_time。
        时钟回拨期间不应发送主动消息（避免重复投递）。
        """
        if delay > 0:
            self.advance(delay)
        new_time = max(self._start_time, self._current_time - rollback_seconds)
        actual_rollback = self._current_time - new_time
        self._current_time = new_time
        self._record_event(
            'clock_rollback',
            {
                'rollback_seconds': actual_rollback,
                'new_time': self._current_time,
            },
            description=f'时钟回拨 {actual_rollback}s',
        )

    def simulate_crash_recovery(self, *, delay: float = 0):
        """模拟应用崩溃后恢复。

        崩溃恢复后系统应检查未完成的决策/投递，避免重复发送。
        本方法只记录事件，实际恢复逻辑由调用方/测试验证。
        """
        if delay > 0:
            self.advance(delay)
        delivery_changes = delivery_mod.recover_stale(now=self._current_time)
        processed = orchestrator_mod.process_due(
            now=self._current_time, worker_id=f"recovery-{self.session_id}",
        )
        self._record_event(
            'crash_recovery', {"delivery_changes": delivery_changes, "processed": processed},
            description='应用崩溃恢复',
        )

    def simulate_network_disconnect(
        self, *, delay: float = 0, duration: float = 600,
    ):
        """模拟网络断开。

        EAP v1 只有本机通道，因此断网不应破坏本机决策与投递；任何
        需要网络的观察 Provider 失败都由其独立 worker 保守降级。
        """
        if delay > 0:
            self.advance(delay)
        self._record_event(
            'network_disconnect',
            {'duration': duration, 'disconnect_at': self._current_time},
            description=f'网络断开 {duration}s',
        )

    # ---------- Episode/Candidate 管理 ----------

    def create_episode(
        self, *, topic: str, origin_type: str,
        open_thread: Optional[str] = None,
    ) -> episodes_mod.ContactEpisode:
        """创建 ContactEpisode 并记录。"""
        episode = episodes_mod.create_episode(
            self.session_id,
            topic=topic,
            origin_type=origin_type,
            open_thread=open_thread,
            now=self._current_time,
        )
        self._episodes.append(episode)
        self._episode_ids.append(episode.id)
        self._record_event(
            'episode_created',
            {
                'episode_id': episode.id,
                'topic': topic,
                'origin_type': origin_type,
            },
            description=f'创建 Episode: {topic}',
        )
        return episode

    def create_candidate(
        self, *,
        candidate_kind: str,
        topic: str,
        episode_id: Optional[str] = None,
        open_thread: Optional[str] = None,
        source_messages: Optional[list] = None,
    ) -> candidates_mod.ProactiveCandidate:
        """创建 ProactiveCandidate 并记录。"""
        if episode_id is None and self._episode_ids:
            episode_id = self._episode_ids[-1]
        if source_messages is None:
            source_messages = [
                {"id": "m1", "role": "user", "content": "我去跑测试了"},
                {"id": "m2", "role": "assistant", "content": "好的，等你回来"},
            ]
        candidate = candidates_mod.create_candidate(
            self.session_id,
            candidate_kind=candidate_kind,
            topic=topic,
            episode_id=episode_id,
            open_thread=open_thread,
            source_messages=source_messages,
            now=self._current_time,
        )
        self._candidates.append(candidate)
        self._candidate_ids.append(candidate.id)
        self._record_event(
            'candidate_created',
            {
                'candidate_id': candidate.id,
                'candidate_kind': candidate_kind,
                'topic': topic,
            },
            description=f'创建 Candidate: {topic}',
        )
        return candidate

    def record_approach(
        self, episode_id: Optional[str] = None, *,
        intensity: int = 3,
    ) -> episodes_mod.ContactEpisode:
        """记录一次接近尝试。"""
        if episode_id is None and self._episode_ids:
            episode_id = self._episode_ids[-1]
        if episode_id is None:
            raise ValueError("no episode to record approach to")
        episode = episodes_mod.record_approach(
            episode_id, intensity=intensity, now=self._current_time,
        )
        for i, ep in enumerate(self._episodes):
            if ep.id == episode.id:
                self._episodes[i] = episode
                break
        else:
            self._episodes.append(episode)
        self._record_event(
            'approach',
            {'episode_id': episode_id, 'intensity': intensity},
            description=f'接近尝试 Level {intensity}',
        )
        return episode

    def mark_candidate_delivered(self, candidate_id: Optional[str] = None):
        """标记候选为已投递（用于测试 ALREADY_DELIVERED 场景）。"""
        if candidate_id is None and self._candidate_ids:
            candidate_id = self._candidate_ids[-1]
        if candidate_id is None:
            raise ValueError("no candidate to mark as delivered")
        candidate = candidates_mod.transition_candidate_status(
            candidate_id,
            candidates_mod.CandidateStatus.DELIVERED,
            now=self._current_time,
        )
        for i, c in enumerate(self._candidates):
            if c.id == candidate.id:
                self._candidates[i] = candidate
                break
        else:
            self._candidates.append(candidate)
        self._record_event(
            'candidate_delivered',
            {'candidate_id': candidate_id},
            description='候选已投递',
        )
        return candidate

    # ---------- 决策周期 ----------

    def run_decision_cycle(
        self, candidate_id: Optional[str] = None, *,
        llm_advice: Optional[decision_mod.LLMAdvice] = None,
    ):
        """运行一次决策周期。

        流程：
        1. 过期 stale presence
        2. 过期 episodes
        3. 衰减 unanswered_pressure
        4. 对指定 candidate 运行 decide_candidate
        5. 如决策为 send，生成 intensity plan 和 expression plan

        返回 ProactiveDecision 或 None（如无 candidate）。
        """
        # 1. 过期 stale presence
        presence_mod.expire_stale_presences(now=self._current_time)
        # 2. 过期 episodes
        episodes_mod.expire_episodes(now=self._current_time)
        # 3. 衰减 pressure
        episodes_mod.decay_all_pressures(now=self._current_time)

        # 4. 运行决策
        decision = None
        if candidate_id is None and self._candidate_ids:
            candidate_id = self._candidate_ids[-1]
        if candidate_id is not None:
            decision = decision_mod.decide_candidate(
                candidate_id,
                llm_advice=llm_advice,
                now=self._current_time,
            )
            self._decisions.append(decision)

            # 5. 如决策为 send，生成 intensity plan 和 expression plan
            if decision.decision == decision_mod.DecisionAction.SEND:
                plan = intensity_mod.plan_intensity_for_decision(
                    decision.id, now=self._current_time,
                )
                if plan is not None:
                    self._intensity_plans.append(plan)

                expr_plan = expression_mod.create_expression_plan(
                    self.session_id,
                    decision_id=decision.id,
                    intensity_plan_id=plan.id if plan else None,
                    expression_act=decision.expression_act,
                    now=self._current_time,
                )
                self._expression_plans.append(expr_plan)

        self._record_event(
            'decision_run',
            {
                'candidate_id': candidate_id,
                'decision': decision.decision if decision else None,
            },
            description=f'决策周期: {decision.decision if decision else "无候选"}',
        )
        return decision

    # ---------- 结果汇总 ----------

    def get_result(self) -> SimulationResult:
        """返回当前模拟结果汇总。"""
        # 获取最新的 episode 状态
        episodes = []
        for episode_id in self._episode_ids:
            ep = episodes_mod.get_episode(episode_id)
            if ep is not None:
                episodes.append(ep)

        # 统计
        send_count = sum(
            1 for d in self._decisions if d.decision == 'send'
        )
        suppress_count = sum(
            1 for d in self._decisions if d.decision == 'suppress'
        )
        defer_count = sum(
            1 for d in self._decisions if d.decision == 'defer'
        )
        abandon_count = sum(
            1 for d in self._decisions if d.decision == 'abandon'
        )
        summary = {
            'total_events': len(self._events),
            'total_decisions': len(self._decisions),
            'total_intensity_plans': len(self._intensity_plans),
            'total_expression_plans': len(self._expression_plans),
            'total_episodes': len(self._episode_ids),
            'total_candidates': len(self._candidate_ids),
            'send_count': send_count,
            'suppress_count': suppress_count,
            'defer_count': defer_count,
            'abandon_count': abandon_count,
            'duration_seconds': self._current_time - self._start_time,
        }
        return SimulationResult(
            session_id=self.session_id,
            start_time=self._start_time,
            end_time=self._current_time,
            events=list(self._events),
            decisions=list(self._decisions),
            intensity_plans=list(self._intensity_plans),
            expression_plans=list(self._expression_plans),
            episodes=episodes,
            summary=summary,
        )


# ============================================================================
# 场景函数（spec 第 14 节四类验收场景）
# ============================================================================

def _default_source_messages(topic: str = "测试结果") -> list:
    """生成默认的 source_messages（用于 compute_source_hash）。"""
    return [
        {"id": "m1", "role": "user", "content": f"我去跑测试了/{topic}"},
        {"id": "m2", "role": "assistant", "content": "好的，等你回来"},
    ]


def scenario_return_after_test_20min(session_id: str) -> SimulationResult:
    """14.1.1: 去测试 20 分钟后追问。

    时间线：
    - t=0: 用户说"我去跑一下测试"
    - t=20min: 用户未回复
    - 验证：建立 ContactEpisode(open_thread='测试结果')，决策可生成候选
    """
    db.init_db()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    sim = TimelineSimulator(session_id)
    try:
        with sim:
            # t=0: 用户说"我去跑一下测试"
            sim.user_message("我去跑一下测试")
            # 建立 ContactEpisode
            sim.create_episode(
                topic="测试结果",
                origin_type=episodes_mod.OriginType.EXPECTED_RETURN,
                open_thread="测试结果",
            )
            # 创建候选
            sim.create_candidate(
                candidate_kind=candidates_mod.CandidateKind.RETURN_FOLLOWUP,
                topic="测试结果",
                open_thread="测试结果",
                source_messages=_default_source_messages(),
            )
            # 推进 20 分钟
            sim.advance(20 * 60)
            # 运行决策周期
            sim.run_decision_cycle()
        return sim.get_result()
    finally:
        _cleanup_session(session_id)


def scenario_goodnight_next_day(session_id: str) -> SimulationResult:
    """14.1.2: 晚安后次日再评估。

    时间线：
    - t=0: 用户说"晚安"
    - t=8h: 用户醒来
    - 验证：presence.away_sleep 期间硬门阻断；醒来后可重新评估
    """
    db.init_db()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    sim = TimelineSimulator(session_id)
    try:
        with sim:
            # t=0: 用户说"晚安"
            sim.user_message("晚安")
            # 建立 Episode（情感关怀类）
            sim.create_episode(
                topic="晚安关怀",
                origin_type=episodes_mod.OriginType.EMOTIONAL_CARE,
            )
            sim.create_candidate(
                candidate_kind=candidates_mod.CandidateKind.EMOTIONAL_CARE,
                topic="晚安关怀",
                source_messages=_default_source_messages("晚安关怀"),
            )
            # t=1h: 尝试决策（应被 layer2 USER_SLEEPING 延后）
            sim.advance(3600)
            sim.run_decision_cycle()

            # t=8h: 用户醒来
            sim.advance(7 * 3600)
            sim.wake_up()
            # 醒来后可重新评估
            sim.create_candidate(
                candidate_kind=candidates_mod.CandidateKind.CASUAL_GREETING,
                topic="早安问候",
                source_messages=_default_source_messages("早安问候"),
            )
            sim.run_decision_cycle()
        return sim.get_result()
    finally:
        _cleanup_session(session_id)


def scenario_repeated_unanswered_downgrade(session_id: str) -> SimulationResult:
    """14.1.3: 多次未回复降级。

    时间线：
    - t=0: 建立 episode
    - t=0,30min,60min,90min: 多次接近，用户未回复
    - 验证：unanswered_pressure 上升，intensity 从 Level 3 降到 Level 2/1
    """
    db.init_db()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    sim = TimelineSimulator(session_id)
    try:
        with sim:
            # 建立 episode（用户在线）
            sim.user_message("我去跑一下测试")
            sim.create_episode(
                topic="测试结果",
                origin_type=episodes_mod.OriginType.EXPECTED_RETURN,
                open_thread="测试结果",
            )

            # t=0: 第一次接近（intensity 3）
            sim.create_candidate(
                candidate_kind=candidates_mod.CandidateKind.RETURN_FOLLOWUP,
                topic="测试结果",
                open_thread="测试结果",
                source_messages=_default_source_messages() + [
                    {"id": "m3", "role": "user", "content": "approach-1"},
                ],
            )
            sim.run_decision_cycle()
            sim.record_approach(intensity=3)

            # t=30min: 第二次接近
            sim.advance(30 * 60)
            sim.create_candidate(
                candidate_kind=candidates_mod.CandidateKind.RETURN_FOLLOWUP,
                topic="测试结果",
                open_thread="测试结果",
                source_messages=_default_source_messages() + [
                    {"id": "m4", "role": "user", "content": "approach-2"},
                ],
            )
            sim.run_decision_cycle()
            sim.record_approach(intensity=3)

            # t=60min: 第三次接近
            sim.advance(30 * 60)
            sim.create_candidate(
                candidate_kind=candidates_mod.CandidateKind.RETURN_FOLLOWUP,
                topic="测试结果",
                open_thread="测试结果",
                source_messages=_default_source_messages() + [
                    {"id": "m5", "role": "user", "content": "approach-3"},
                ],
            )
            sim.run_decision_cycle()
            sim.record_approach(intensity=2)

            # t=90min: 第四次接近
            sim.advance(30 * 60)
            sim.create_candidate(
                candidate_kind=candidates_mod.CandidateKind.RETURN_FOLLOWUP,
                topic="测试结果",
                open_thread="测试结果",
                source_messages=_default_source_messages() + [
                    {"id": "m6", "role": "user", "content": "approach-4"},
                ],
            )
            sim.run_decision_cycle()
            sim.record_approach(intensity=1)
        return sim.get_result()
    finally:
        _cleanup_session(session_id)


def scenario_continue_reminding_increase(session_id: str) -> SimulationResult:
    """14.1.5: "你可以继续提醒我"提高接受度。

    时间线：
    - t=0: 建立 episode
    - t=0: 用户说"你可以继续提醒我"
    - 验证：unanswered_pressure 不降低或轻微提高
    """
    db.init_db()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    sim = TimelineSimulator(session_id)
    try:
        with sim:
            # 建立 episode
            sim.create_episode(
                topic="任务提醒",
                origin_type=episodes_mod.OriginType.EMOTIONAL_CARE,
            )
            # 记录初始接近，产生 baseline pressure
            sim.record_approach(intensity=3)
            baseline_pressure = sim._episodes[-1].unanswered_pressure

            # 用户说"你可以继续提醒我"
            sim.user_message("你可以继续提醒我")
            # 应用 continue_reminding 响应
            sim.user_responds(
                episodes_mod.UserResponseType.CONTINUE_REMINDING,
            )

            # 记录验证数据
            sim._record_event(
                'pressure_check',
                {
                    'baseline': baseline_pressure,
                    'after_continue_reminding': sim._episodes[-1].unanswered_pressure,
                },
                description='验证 continue_reminding 后 pressure 变化',
            )
        return sim.get_result()
    finally:
        _cleanup_session(session_id)


def scenario_stop_pushing_behavior_fix(session_id: str) -> SimulationResult:
    """14.1.6: "别一直催我"调整行为。

    时间线：
    - t=0: 建立 episode
    - t=0: 用户说"别一直催我"
    - 验证：apply_user_response(stop_pushing) 后 episode 不终态但 pressure 不变
    """
    db.init_db()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    sim = TimelineSimulator(session_id)
    try:
        with sim:
            # 建立 episode
            sim.create_episode(
                topic="任务提醒",
                origin_type=episodes_mod.OriginType.EMOTIONAL_CARE,
            )
            # 记录初始接近
            sim.record_approach(intensity=3)
            pressure_before = sim._episodes[-1].unanswered_pressure

            # 用户说"别一直催我"
            sim.user_message("别一直催我")
            # 应用 stop_pushing 响应
            sim.user_responds(
                episodes_mod.UserResponseType.STOP_PUSHING,
            )

            sim._record_event(
                'pressure_check',
                {
                    'before': pressure_before,
                    'after_stop_pushing': sim._episodes[-1].unanswered_pressure,
                    'episode_status': sim._episodes[-1].status,
                },
                description='验证 stop_pushing 后 pressure 不变且 episode 不终态',
            )
        return sim.get_result()
    finally:
        _cleanup_session(session_id)


def scenario_disabled_proactive_zero_send(session_id: str) -> SimulationResult:
    """14.4.1: 关闭后投递率 0。

    时间线：
    - 关闭 proactive_enabled
    - 创建多个候选
    - 验证：所有决策 SUPPRESS，0 个 SEND
    """
    db.init_db()
    _setup_session(session_id)
    _set_proactive_enabled("0")  # 关闭主动陪伴
    sim = TimelineSimulator(session_id)
    try:
        with sim:
            sim.create_episode(
                topic="测试关闭",
                origin_type=episodes_mod.OriginType.EMOTIONAL_CARE,
            )
            # 创建 3 个候选
            for i in range(3):
                sim.create_candidate(
                    candidate_kind=candidates_mod.CandidateKind.EMOTIONAL_CARE,
                    topic=f"测试关闭-{i}",
                    source_messages=_default_source_messages(f"topic-{i}"),
                )
                sim.run_decision_cycle()
        return sim.get_result()
    finally:
        _cleanup_session(session_id)
        _set_proactive_enabled("1")  # 恢复默认


def scenario_rejected_topic_zero_mention(session_id: str) -> SimulationResult:
    """14.4.2: 禁提话题后提及率 0。

    时间线：
    - 在 settings 中拒绝某 topic
    - 创建该 topic 的候选
    - 验证：layer1 硬门 TOPIC_REJECTED 阻断
    """
    db.init_db()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    # 设置被拒绝的话题
    db.set_setting("proactive_rejected_topics", "敏感话题")
    sim = TimelineSimulator(session_id)
    try:
        with sim:
            sim.create_episode(
                topic="敏感话题",
                origin_type=episodes_mod.OriginType.EMOTIONAL_CARE,
            )
            sim.create_candidate(
                candidate_kind=candidates_mod.CandidateKind.EMOTIONAL_CARE,
                topic="敏感话题",
                source_messages=_default_source_messages("敏感话题"),
            )
            sim.run_decision_cycle()
        return sim.get_result()
    finally:
        _cleanup_session(session_id)
        db.set_setting("proactive_rejected_topics", "")


def scenario_unauthorized_external_zero_send(session_id: str) -> SimulationResult:
    """14.4.3: 外部渠道未授权投递率 0。

    时间线：
    - proactive_external_channels_enabled='0'
    - 创建候选并尝试 Level 5 强度
    - 验证：降级到 Level 3 或更低
    """
    db.init_db()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    db.set_setting("proactive_external_channels_enabled", "0")
    db.set_setting("proactive_desktop_notification_enabled", "0")
    sim = TimelineSimulator(session_id)
    try:
        with sim:
            sim.create_episode(
                topic="外部渠道测试",
                origin_type=episodes_mod.OriginType.MILESTONE,
            )
            sim.create_candidate(
                candidate_kind=candidates_mod.CandidateKind.MILESTONE_FOLLOWUP,
                topic="外部渠道测试",
                source_messages=_default_source_messages("外部渠道测试"),
            )
            # 使用 LLM advice 建议 Level 5（外部渠道）
            llm_advice = decision_mod.LLMAdvice(
                decision=decision_mod.DecisionAction.SEND,
                intensity=5,  # Level 5 外部渠道
                expression_act=decision_mod.ExpressionAct.FIRM_CARE,
                topic="外部渠道测试",
                confidence=0.9,
                reason_codes=["test"],
                source_refs=[],
            )
            sim.run_decision_cycle(llm_advice=llm_advice)
        return sim.get_result()
    finally:
        _cleanup_session(session_id)
        db.set_setting("proactive_external_channels_enabled", "0")
        db.set_setting("proactive_desktop_notification_enabled", "0")


def scenario_high_bond_no_override_reject(session_id: str) -> SimulationResult:
    """14.4.4: 高 bond 不覆盖拒绝。

    时间线：
    - 建立 episode
    - 用户明确拒绝（explicit_reject）
    - 验证：episode.status='blocked'
    - 即使 LLM 建议 send，硬边界仍阻断
    """
    db.init_db()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    sim = TimelineSimulator(session_id)
    try:
        with sim:
            sim.create_episode(
                topic="高关系测试",
                origin_type=episodes_mod.OriginType.EMOTIONAL_CARE,
            )
            # 用户明确拒绝
            sim.user_responds(
                episodes_mod.UserResponseType.EXPLICIT_REJECT,
            )

            # 将话题加入拒绝列表（模拟系统记录拒绝）
            db.set_setting("proactive_rejected_topics", "高关系测试")

            # 即使 LLM 建议 send，硬边界应阻断
            sim.create_candidate(
                candidate_kind=candidates_mod.CandidateKind.EMOTIONAL_CARE,
                topic="高关系测试",
                source_messages=_default_source_messages("高关系测试"),
            )
            llm_advice = decision_mod.LLMAdvice(
                decision=decision_mod.DecisionAction.SEND,
                intensity=3,
                expression_act=decision_mod.ExpressionAct.FIRM_CARE,
                topic="高关系测试",
                confidence=0.95,
                reason_codes=["high_bond"],
                source_refs=[],
            )
            sim.run_decision_cycle(llm_advice=llm_advice)
        return sim.get_result()
    finally:
        _cleanup_session(session_id)
        db.set_setting("proactive_rejected_topics", "")


def scenario_same_episode_no_duplicate_send(session_id: str) -> SimulationResult:
    """14.4.5: 同 ContactEpisode 不重复发送。

    时间线：
    - 建立 episode
    - 创建相同 source_hash 的候选
    - 第一个 delivered
    - 第二个：layer1 ALREADY_DELIVERED 阻断
    """
    db.init_db()
    _setup_session(session_id)
    _set_proactive_enabled("1")
    sim = TimelineSimulator(session_id)
    try:
        with sim:
            sim.create_episode(
                topic="重复投递测试",
                origin_type=episodes_mod.OriginType.EXPECTED_RETURN,
            )
            # 创建第一个候选
            source_msgs = _default_source_messages("重复投递测试")
            sim.create_candidate(
                candidate_kind=candidates_mod.CandidateKind.RETURN_FOLLOWUP,
                topic="重复投递测试",
                source_messages=source_msgs,
            )
            # 第一个决策为 send，标记为 delivered
            sim.run_decision_cycle()
            sim.mark_candidate_delivered()

            # 创建第二个候选（相同 source_messages → 相同 source_hash）
            sim.create_candidate(
                candidate_kind=candidates_mod.CandidateKind.RETURN_FOLLOWUP,
                topic="重复投递测试",
                source_messages=source_msgs,
            )
            # 第二个决策应被 ALREADY_DELIVERED 阻断
            sim.run_decision_cycle()
        return sim.get_result()
    finally:
        _cleanup_session(session_id)
