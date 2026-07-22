"""EAP v0.2 ContactEpisode：同一话题的连续主动管理。

按 spec 第 5.7 节，ContactEpisode 代表"遐蝶因为某件事想接近用户"的连续过程，
可能包含多次接近尝试、降级和最终结束。承载 spec 第 5.9 节"未回复反馈模型"：

- unanswered_pressure 累积公式：ΔP = intensity × channel_intrusiveness × repetition_factor
- 衰减规则：随时间缓慢衰减，不在用户沉默期间反向增加
- 用户后续行为影响（6 类）：positive / normal / was_busy / continue_reminding /
  stop_pushing / explicit_reject

模块隔离：本模块不导入 db/检索器/摘要服务以外的内部模块。
本阶段不接入 main.py（接入留给 EAP.F）。
"""

import json
from dataclasses import dataclass
from typing import Optional

from .. import db
from .protocols import PROACTIVE_DECISION_V2
from .run_ledger import make_idempotency_key


# 10 值状态枚举（spec 第 5.7 节）
class EpisodeStatus:
    PROPOSED = "proposed"              # 候选已建立，尚未到达适合窗口
    WAITING = "waiting"                # 到达适合窗口，等待评估
    APPROACHED = "approached"          # 已发出第一次接近
    DEFERRED = "deferred"              # 因延后条件命中而延后
    QUIET_WAITING = "quiet_waiting"    # 已接近但用户未回复，降级为安静等待
    RESPONDED = "responded"            # 用户已回应
    CLOSED = "closed"                  # 正常结束
    EXPIRED = "expired"                # 超过最大生命周期
    CANCELLED = "cancelled"            # 用户回来或话题自然消失
    BLOCKED = "blocked"                # 用户明确拒绝或硬边界命中


ALL_STATUSES = (
    EpisodeStatus.PROPOSED,
    EpisodeStatus.WAITING,
    EpisodeStatus.APPROACHED,
    EpisodeStatus.DEFERRED,
    EpisodeStatus.QUIET_WAITING,
    EpisodeStatus.RESPONDED,
    EpisodeStatus.CLOSED,
    EpisodeStatus.EXPIRED,
    EpisodeStatus.CANCELLED,
    EpisodeStatus.BLOCKED,
)

# 终态：不能再转换
TERMINAL_STATUSES = frozenset({
    EpisodeStatus.CLOSED,
    EpisodeStatus.EXPIRED,
    EpisodeStatus.CANCELLED,
    EpisodeStatus.BLOCKED,
})

# 活跃状态：可被查询为"当前活跃"的 episode
ACTIVE_STATUSES = frozenset({
    EpisodeStatus.PROPOSED,
    EpisodeStatus.WAITING,
    EpisodeStatus.APPROACHED,
    EpisodeStatus.DEFERRED,
    EpisodeStatus.QUIET_WAITING,
})


# 来源类型（spec 第 5.7 节 origin_type 字段）
class OriginType:
    EXPECTED_RETURN = "expected_return"    # 用户离开时可预期回来（如"我去测试"）
    EMOTIONAL_CARE = "emotional_care"      # 情感关怀
    MILESTONE = "milestone"                # 里程碑事件
    LIFE_SHARE = "life_share"              # LIFE 生活事件分享


# 最终结果（spec 第 5.7 节 outcome 字段）
class Outcome:
    REPLIED = "replied"        # 用户回复
    IGNORED = "ignored"        # 被忽略
    REJECTED = "rejected"      # 被明确拒绝
    EXPIRED = "expired"        # 超时
    CANCELLED = "cancelled"    # 取消


# 用户后续行为类型（spec 第 5.9 节 6 类）
class UserResponseType:
    POSITIVE = "positive"                      # 积极回应
    NORMAL = "normal"                          # 普通回应
    WAS_BUSY = "was_busy"                      # 刚才在忙
    CONTINUE_REMINDING = "continue_reminding"  # 你可以继续提醒我
    STOP_PUSHING = "stop_pushing"              # 别一直催我
    EXPLICIT_REJECT = "explicit_reject"        # 明确拒绝


# 默认最大生命周期（spec 第 6.2 节"极宽工程熔断上限"：7 天）
DEFAULT_MAX_LIFETIME_SECONDS = 7 * 24 * 3600

# unanswered_pressure 每小时衰减 0.05（spec 第 5.9 节"随时间缓慢衰减"）
DEFAULT_DECAY_PER_HOUR = 0.05

# 衰减下限
MIN_PRESSURE = 0.0

# 渠道侵入性系数（spec 第 5.10 节"主动强度阶梯" Level 0~5）
# Level 越高，渠道越侵入；用于 unanswered_pressure 累积公式
CHANNEL_INTRUSIVENESS = {
    0: 0.0,   # 安静无动作
    1: 0.1,   # Live2D 视线/表情/轻微动作
    2: 0.3,   # 无通知小气泡
    3: 0.6,   # 正常聊天主动消息
    4: 0.9,   # 桌面系统通知
    5: 1.0,   # 外部渠道消息
}

# 重复程度基础值（无重复）
REPETITION_FACTOR_BASE = 1.0


@dataclass
class ContactEpisode:
    """contact_episodes 表的记录。"""
    id: str
    session_id: str
    topic: str
    origin_type: str
    source_refs: dict  # 解析后的 JSON
    open_thread: Optional[str]
    first_candidate_at: Optional[float]
    last_approach_at: Optional[float]
    approach_count: int
    unanswered_pressure: float
    current_intensity: int
    status: str
    expires_at: Optional[float]
    outcome: Optional[str]
    protocol_version: str
    created_at: float
    updated_at: float


def _serialize_source_refs(source_refs: Optional[dict]) -> str:
    """将 source_refs dict 序列化为 JSON 字符串（None → '{}'）。"""
    if not source_refs:
        return "{}"
    return json.dumps(source_refs, ensure_ascii=False, sort_keys=True)


def _parse_source_refs(raw: Optional[str]) -> dict:
    """从 JSON 字符串解析 source_refs（None 或空 → {}）。"""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def create_episode(
    session_id: str,
    *,
    topic: str,
    origin_type: str,
    open_thread: Optional[str] = None,
    source_refs: Optional[dict] = None,
    expires_at: Optional[float] = None,
    now: Optional[float] = None,
) -> ContactEpisode:
    """创建新的 ContactEpisode，初始状态 proposed。

    - origin_type 必须在 OriginType 中
    - 如果未提供 expires_at，按 DEFAULT_MAX_LIFETIME_SECONDS 计算
    """
    valid_origins = {
        OriginType.EXPECTED_RETURN,
        OriginType.EMOTIONAL_CARE,
        OriginType.MILESTONE,
        OriginType.LIFE_SHARE,
    }
    if origin_type not in valid_origins:
        raise ValueError(f"invalid origin_type: {origin_type!r}")

    if not topic or not topic.strip():
        raise ValueError("topic must be non-empty")

    now = now if now is not None else db.now()
    if expires_at is None:
        expires_at = now + DEFAULT_MAX_LIFETIME_SECONDS

    record_id = db.new_id()
    source_refs_json = _serialize_source_refs(source_refs)

    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO contact_episodes"
            " (id, session_id, topic, origin_type, source_refs, open_thread,"
            "  first_candidate_at, last_approach_at, approach_count,"
            "  unanswered_pressure, current_intensity, status, expires_at,"
            "  outcome, protocol_version, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 0, 0.0, 0, ?, ?, NULL, ?, ?, ?)",
            (
                record_id, session_id, topic, origin_type, source_refs_json,
                open_thread, EpisodeStatus.PROPOSED, expires_at,
                PROACTIVE_DECISION_V2, now, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return ContactEpisode(
        id=record_id, session_id=session_id, topic=topic, origin_type=origin_type,
        source_refs=source_refs or {}, open_thread=open_thread,
        first_candidate_at=None, last_approach_at=None, approach_count=0,
        unanswered_pressure=0.0, current_intensity=0,
        status=EpisodeStatus.PROPOSED, expires_at=expires_at, outcome=None,
        protocol_version=PROACTIVE_DECISION_V2, created_at=now, updated_at=now,
    )


def get_episode(episode_id: str) -> Optional[ContactEpisode]:
    """按 ID 查询。"""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM contact_episodes WHERE id=?",
            (episode_id,),
        ).fetchone()
        if not row:
            return None
        return _row_to_episode(row)
    finally:
        conn.close()


def get_active_episode_for_session(session_id: str) -> Optional[ContactEpisode]:
    """获取会话当前活跃的 ContactEpisode（status 在 ACTIVE_STATUSES 中）。

    返回最新 updated_at 的一条。
    """
    conn = db.connect()
    try:
        placeholders = ",".join("?" * len(ACTIVE_STATUSES))
        row = conn.execute(
            f"SELECT * FROM contact_episodes "
            f"WHERE session_id=? AND status IN ({placeholders}) "
            f"ORDER BY updated_at DESC LIMIT 1",
            (session_id, *ACTIVE_STATUSES),
        ).fetchone()
        if not row:
            return None
        return _row_to_episode(row)
    finally:
        conn.close()


def list_active_episodes(*, now: Optional[float] = None) -> list:
    """列出所有活跃 ContactEpisode。"""
    conn = db.connect()
    try:
        placeholders = ",".join("?" * len(ACTIVE_STATUSES))
        rows = conn.execute(
            f"SELECT * FROM contact_episodes "
            f"WHERE status IN ({placeholders}) "
            f"ORDER BY updated_at DESC",
            tuple(ACTIVE_STATUSES),
        ).fetchall()
        return [_row_to_episode(row) for row in rows]
    finally:
        conn.close()


def transition_status(
    episode_id: str,
    new_status: str,
    *,
    outcome: Optional[str] = None,
    now: Optional[float] = None,
) -> ContactEpisode:
    """状态转换。

    - 终态状态不能再转换
    - 如果 new_status 是终态，可以同时设置 outcome
    - 校验 new_status 在 ALL_STATUSES 中
    - 更新 updated_at
    """
    if new_status not in ALL_STATUSES:
        raise ValueError(f"invalid status: {new_status!r}")

    now = now if now is not None else db.now()

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM contact_episodes WHERE id=?",
            (episode_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"episode not found: {episode_id}")

        current_status = row["status"]
        if current_status in TERMINAL_STATUSES:
            raise ValueError(
                f"cannot transition from terminal status: {current_status!r}"
            )

        # 终态可以同时设置 outcome
        if new_status in TERMINAL_STATUSES and outcome is not None:
            valid_outcomes = {
                Outcome.REPLIED, Outcome.IGNORED, Outcome.REJECTED,
                Outcome.EXPIRED, Outcome.CANCELLED,
            }
            if outcome not in valid_outcomes:
                raise ValueError(f"invalid outcome: {outcome!r}")
            conn.execute(
                "UPDATE contact_episodes SET status=?, outcome=?, updated_at=? WHERE id=?",
                (new_status, outcome, now, episode_id),
            )
        else:
            conn.execute(
                "UPDATE contact_episodes SET status=?, updated_at=? WHERE id=?",
                (new_status, now, episode_id),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM contact_episodes WHERE id=?",
            (episode_id,),
        ).fetchone()
    finally:
        conn.close()

    return _row_to_episode(row)


def record_approach(
    episode_id: str,
    *,
    intensity: int,
    channel_intrusiveness: Optional[float] = None,
    repetition_factor: float = REPETITION_FACTOR_BASE,
    now: Optional[float] = None,
) -> ContactEpisode:
    """记录一次接近尝试。

    按 spec 第 5.9 节累积公式：
        unanswered_pressure += intensity × channel_intrusiveness × repetition_factor

    - intensity 必须在 0~5 范围内
    - 如果未提供 channel_intrusiveness，按 intensity 从 CHANNEL_INTRUSIVENESS 取
    - 更新 approach_count += 1, last_approach_at, current_intensity
    - 如果是第一次接近（approach_count 之前为 0），同时设置 first_candidate_at
    - 自动转换状态：proposed/waiting → approached；其他状态保持
    """
    if not isinstance(intensity, int) or isinstance(intensity, bool):
        raise ValueError(f"intensity must be int, got {type(intensity).__name__}")
    if intensity < 0 or intensity > 5:
        raise ValueError(f"intensity must be between 0 and 5, got {intensity}")

    if channel_intrusiveness is None:
        channel_intrusiveness = CHANNEL_INTRUSIVENESS.get(intensity, 0.0)
    if channel_intrusiveness < 0:
        raise ValueError(
            f"channel_intrusiveness must be >= 0, got {channel_intrusiveness}"
        )

    if repetition_factor < 0:
        raise ValueError(
            f"repetition_factor must be >= 0, got {repetition_factor}"
        )

    now = now if now is not None else db.now()

    # 累积增量
    delta = intensity * channel_intrusiveness * repetition_factor

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM contact_episodes WHERE id=?",
            (episode_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"episode not found: {episode_id}")

        current_count = row["approach_count"]
        current_pressure = row["unanswered_pressure"]
        current_status = row["status"]
        first_candidate_at = row["first_candidate_at"]

        new_count = current_count + 1
        new_pressure = current_pressure + delta
        # 第一次接近设置 first_candidate_at
        new_first_candidate = first_candidate_at if first_candidate_at is not None else now

        # 状态自动转换：proposed/waiting → approached
        if current_status in (EpisodeStatus.PROPOSED, EpisodeStatus.WAITING):
            new_status = EpisodeStatus.APPROACHED
        else:
            new_status = current_status

        conn.execute(
            "UPDATE contact_episodes SET "
            " approach_count=?, unanswered_pressure=?, current_intensity=?,"
            " last_approach_at=?, first_candidate_at=?, status=?, updated_at=? "
            "WHERE id=?",
            (
                new_count, new_pressure, intensity,
                now, new_first_candidate, new_status, now, episode_id,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM contact_episodes WHERE id=?",
            (episode_id,),
        ).fetchone()
    finally:
        conn.close()

    return _row_to_episode(row)


def decay_pressure(
    episode_id: str,
    *,
    hours_elapsed: Optional[float] = None,
    now: Optional[float] = None,
) -> ContactEpisode:
    """衰减单个 episode 的 unanswered_pressure。

    按 spec：随时间缓慢衰减，不在用户沉默期间反向增加。

    - hours_elapsed: 自 last_approach_at 以来的小时数
      （如果未提供，按 now - last_approach_at 计算）
    - 如果 last_approach_at 为 None，不衰减
    """
    now = now if now is not None else db.now()

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM contact_episodes WHERE id=?",
            (episode_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"episode not found: {episode_id}")

        last_approach = row["last_approach_at"]
        current_pressure = row["unanswered_pressure"]

        if last_approach is None:
            # 未接近过，不衰减
            return _row_to_episode(row)

        if hours_elapsed is None:
            hours_elapsed = max(0.0, (now - last_approach) / 3600.0)

        new_pressure = max(
            MIN_PRESSURE,
            current_pressure - hours_elapsed * DEFAULT_DECAY_PER_HOUR,
        )

        conn.execute(
            "UPDATE contact_episodes SET unanswered_pressure=?, updated_at=? WHERE id=?",
            (new_pressure, now, episode_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM contact_episodes WHERE id=?",
            (episode_id,),
        ).fetchone()
    finally:
        conn.close()

    return _row_to_episode(row)


def decay_all_pressures(*, now: Optional[float] = None) -> int:
    """批量衰减所有活跃 episode 的 unanswered_pressure。返回受影响行数。"""
    now = now if now is not None else db.now()

    conn = db.connect()
    try:
        placeholders = ",".join("?" * len(ACTIVE_STATUSES))
        rows = conn.execute(
            f"SELECT id, last_approach_at, unanswered_pressure FROM contact_episodes "
            f"WHERE status IN ({placeholders}) AND last_approach_at IS NOT NULL",
            tuple(ACTIVE_STATUSES),
        ).fetchall()

        affected = 0
        for row in rows:
            last_approach = row["last_approach_at"]
            current_pressure = row["unanswered_pressure"]
            hours_elapsed = max(0.0, (now - last_approach) / 3600.0)
            new_pressure = max(
                MIN_PRESSURE,
                current_pressure - hours_elapsed * DEFAULT_DECAY_PER_HOUR,
            )
            if new_pressure != current_pressure:
                conn.execute(
                    "UPDATE contact_episodes "
                    "SET unanswered_pressure=?, updated_at=? WHERE id=?",
                    (new_pressure, now, row["id"]),
                )
                affected += 1
        if affected:
            conn.commit()
        return affected
    finally:
        conn.close()


def apply_user_response(
    episode_id: str,
    response_type: str,
    *,
    now: Optional[float] = None,
) -> ContactEpisode:
    """应用用户后续行为影响（6 类）。

    按 spec 第 5.9 节"用户后续行为影响"表：
    - positive：unanswered_pressure *= 0.1（快速降低），状态→responded，outcome=replied
    - normal：unanswered_pressure *= 0.4（适度降低），状态→responded，outcome=replied
    - was_busy：unanswered_pressure *= 0.6（降低），状态保持（不终态）
    - continue_reminding：unanswered_pressure *= 1.05（轻微提高），状态保持
    - stop_pushing：unanswered_pressure 不变，状态保持（行为修复留给 EAP.F）
    - explicit_reject：状态→blocked，outcome=rejected
    """
    valid_responses = {
        UserResponseType.POSITIVE,
        UserResponseType.NORMAL,
        UserResponseType.WAS_BUSY,
        UserResponseType.CONTINUE_REMINDING,
        UserResponseType.STOP_PUSHING,
        UserResponseType.EXPLICIT_REJECT,
    }
    if response_type not in valid_responses:
        raise ValueError(f"invalid response_type: {response_type!r}")

    now = now if now is not None else db.now()

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM contact_episodes WHERE id=?",
            (episode_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"episode not found: {episode_id}")

        current_pressure = row["unanswered_pressure"]
        current_status = row["status"]

        # 终态不能再处理用户响应
        if current_status in TERMINAL_STATUSES:
            raise ValueError(
                f"cannot apply user response to terminal status: {current_status!r}"
            )

        if response_type == UserResponseType.POSITIVE:
            new_pressure = current_pressure * 0.1
            new_status = EpisodeStatus.RESPONDED
            new_outcome = Outcome.REPLIED
        elif response_type == UserResponseType.NORMAL:
            new_pressure = current_pressure * 0.4
            new_status = EpisodeStatus.RESPONDED
            new_outcome = Outcome.REPLIED
        elif response_type == UserResponseType.WAS_BUSY:
            new_pressure = current_pressure * 0.6
            new_status = current_status
            new_outcome = row["outcome"]
        elif response_type == UserResponseType.CONTINUE_REMINDING:
            new_pressure = current_pressure * 1.05
            new_status = current_status
            new_outcome = row["outcome"]
        elif response_type == UserResponseType.STOP_PUSHING:
            new_pressure = current_pressure  # 不变
            new_status = current_status
            new_outcome = row["outcome"]
        else:  # explicit_reject
            new_pressure = current_pressure
            new_status = EpisodeStatus.BLOCKED
            new_outcome = Outcome.REJECTED

        # 保证非负
        if new_pressure < MIN_PRESSURE:
            new_pressure = MIN_PRESSURE

        conn.execute(
            "UPDATE contact_episodes SET "
            " unanswered_pressure=?, status=?, outcome=?, updated_at=? "
            "WHERE id=?",
            (new_pressure, new_status, new_outcome, now, episode_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM contact_episodes WHERE id=?",
            (episode_id,),
        ).fetchone()
    finally:
        conn.close()

    return _row_to_episode(row)


def expire_episodes(*, now: Optional[float] = None) -> int:
    """批量过期超过 expires_at 的活跃 episode。

    - 状态转为 expired，outcome 设为 'expired'
    - 返回受影响行数
    """
    now = now if now is not None else db.now()

    conn = db.connect()
    try:
        placeholders = ",".join("?" * len(ACTIVE_STATUSES))
        cursor = conn.execute(
            f"UPDATE contact_episodes "
            f"SET status=?, outcome=?, updated_at=? "
            f"WHERE status IN ({placeholders}) "
            f"AND expires_at IS NOT NULL AND expires_at < ?",
            (
                EpisodeStatus.EXPIRED, Outcome.EXPIRED, now,
                *ACTIVE_STATUSES, now,
            ),
        )
        affected = cursor.rowcount or 0
        if affected:
            conn.commit()
        return affected
    finally:
        conn.close()


def _row_to_episode(row) -> ContactEpisode:
    """内部：从 sqlite3.Row 构造 ContactEpisode。"""
    return ContactEpisode(
        id=row["id"], session_id=row["session_id"], topic=row["topic"],
        origin_type=row["origin_type"],
        source_refs=_parse_source_refs(row["source_refs"]),
        open_thread=row["open_thread"],
        first_candidate_at=row["first_candidate_at"],
        last_approach_at=row["last_approach_at"],
        approach_count=row["approach_count"],
        unanswered_pressure=row["unanswered_pressure"],
        current_intensity=row["current_intensity"],
        status=row["status"], expires_at=row["expires_at"],
        outcome=row["outcome"], protocol_version=row["protocol_version"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )
