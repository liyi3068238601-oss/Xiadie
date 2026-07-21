"""Conversation Presence v2：用户在线状态、离开原因、open_thread。

按 EAP v0.2 spec 第 5.x 节，扩展 affect-observer-v1 的 4 值 user_status 为 8 值。
本模块独立于 affect/observer.py（已冻结 affect-observer-v1），通过
conversation-presence-v2 协议承载。

程序规则识别"晚安/我去测试/先这样"等高精度表达，不依赖 LLM。
LLM 增强留给后续阶段（如 EAP.F proactive-decision-v2）。
"""

import re
from dataclasses import dataclass
from typing import Optional

from .. import db
from .protocols import CONVERSATION_PRESENCE_V2

# 8 值状态枚举（spec 第 5.x 节）
class UserStatus:
    ONLINE = "online"                    # 在线活跃
    AWAY_BRIEF = "away_brief"            # 短暂离开（去测试、去吃饭）
    AWAY_SLEEP = "away_sleep"            # 睡眠（晚安）
    AWAY_BUSY = "away_busy"              # 忙碌（开会、全屏游戏）
    AWAY_EXTENDED = "away_extended"      # 长时间离开
    ENDED_CONVERSATION = "ended_conversation"  # 明确结束（先这样、再见）
    DO_NOT_DISTURB = "do_not_disturb"    # 勿扰
    UNKNOWN = "unknown"                  # 未知/无法判断

# 状态优先级（数值越大优先级越高，命中高优先级时覆盖低优先级）
PRIORITY = {
    UserStatus.DO_NOT_DISTURB: 7,
    UserStatus.ENDED_CONVERSATION: 6,
    UserStatus.AWAY_SLEEP: 5,
    UserStatus.AWAY_BUSY: 4,
    UserStatus.AWAY_EXTENDED: 3,
    UserStatus.AWAY_BRIEF: 2,
    UserStatus.ONLINE: 1,
    UserStatus.UNKNOWN: 0,
}

# 默认过期时间（秒）
DEFAULT_EXPIRY = {
    UserStatus.ONLINE: None,              # 不过期
    UserStatus.AWAY_BRIEF: 30 * 60,       # 30 分钟
    UserStatus.AWAY_SLEEP: 8 * 3600,      # 8 小时
    UserStatus.AWAY_BUSY: 2 * 3600,       # 2 小时
    UserStatus.AWAY_EXTENDED: 24 * 3600,  # 24 小时
    UserStatus.ENDED_CONVERSATION: None,  # 不过期（需新消息才能结束）
    UserStatus.DO_NOT_DISTURB: None,      # 不过期（用户手动解除）
    UserStatus.UNKNOWN: None,
}

# 默认预计返回时间（秒，从 detected_at 起算）
DEFAULT_EXPECTED_RETURN = {
    UserStatus.AWAY_BRIEF: 30 * 60,       # 30 分钟
    UserStatus.AWAY_SLEEP: 8 * 3600,      # 8 小时
    UserStatus.AWAY_BUSY: 2 * 3600,       # 2 小时
    UserStatus.AWAY_EXTENDED: 24 * 3600,  # 24 小时
}


@dataclass
class PresenceSignal:
    """程序规则识别的 presence 信号。"""
    user_status: str
    open_thread: bool = False
    open_thread_topic: Optional[str] = None
    expected_return_seconds: Optional[float] = None  # 覆盖默认预计返回时间


# 程序规则识别模式（高精度表达，避免误判）
# 按 spec："实现程序规则识别'晚安/我去测试/先这样'等高精度表达"
PRESENCE_PATTERNS = [
    # 睡眠（晚安、睡了、去睡觉）
    (
        re.compile(r"晚安|睡了|去睡|睡觉去了|我要睡了|该睡了|困了.*去睡", re.IGNORECASE),
        UserStatus.AWAY_SLEEP,
    ),
    # 明确结束（先这样、再见、拜拜、下次聊）
    (
        re.compile(r"先这样|就这样吧|再见|拜拜|下次聊|今天先到这|今天就到这|先聊到这", re.IGNORECASE),
        UserStatus.ENDED_CONVERSATION,
    ),
    # 勿扰
    (
        re.compile(r"勿扰|别打扰我|不要打扰|别烦我|先别找我", re.IGNORECASE),
        UserStatus.DO_NOT_DISTURB,
    ),
    # 短暂离开 - 去测试（开放话题：测试结果）
    (
        re.compile(r"我去测试|去跑.*测试|跑一下测试|去跑测试|测试一下|我去跑", re.IGNORECASE),
        UserStatus.AWAY_BRIEF,
        True,  # open_thread
        "测试结果",  # topic
    ),
    # 短暂离开 - 去吃饭
    (
        re.compile(r"去吃饭|吃饭去|去吃个饭|去觅食|去午饭|去晚饭|去早饭", re.IGNORECASE),
        UserStatus.AWAY_BRIEF,
        True,
        "吃饭",
    ),
    # 短暂离开 - 去洗澡
    (
        re.compile(r"去洗澡|去洗个澡|洗澡去|去沐浴", re.IGNORECASE),
        UserStatus.AWAY_BRIEF,
        True,
        "洗澡",
    ),
    # 忙碌 - 开会
    (
        re.compile(r"在开会|开会中|要去开会|开个会|会议中", re.IGNORECASE),
        UserStatus.AWAY_BUSY,
    ),
    # 忙碌 - 全屏游戏
    (
        re.compile(r"全屏.*游戏|打游戏|游戏ing|开黑", re.IGNORECASE),
        UserStatus.AWAY_BUSY,
    ),
    # 长时间离开
    (
        re.compile(r"出差|出门.*几天|离开.*几天|回老家|去旅游", re.IGNORECASE),
        UserStatus.AWAY_EXTENDED,
    ),
]


def detect_presence_signals(user_text: str) -> PresenceSignal:
    """程序规则识别用户文本中的 presence 信号。

    返回第一个匹配的高精度模式。如无匹配返回 UNKNOWN。
    按 spec："实现程序规则识别'晚安/我去测试/先这样'等高精度表达"
    """
    if not user_text or not user_text.strip():
        return PresenceSignal(user_status=UserStatus.UNKNOWN)

    for pattern in PRESENCE_PATTERNS:
        match = pattern[0].search(user_text)
        if match:
            user_status = pattern[1]
            open_thread = pattern[2] if len(pattern) > 2 else False
            topic = pattern[3] if len(pattern) > 3 else None
            return PresenceSignal(
                user_status=user_status,
                open_thread=open_thread,
                open_thread_topic=topic,
            )

    # 无匹配 - 默认在线活跃
    return PresenceSignal(user_status=UserStatus.ONLINE)


@dataclass
class PresenceRecord:
    """conversation_presence 表的记录。"""
    id: str
    session_id: str
    user_status: str
    detected_at: float
    expires_at: Optional[float]
    expected_return_at: Optional[float]
    open_thread: bool
    open_thread_topic: Optional[str]
    source_message_id: Optional[str]
    priority: int
    is_active: bool


def update_presence(
    session_id: str,
    signal: PresenceSignal,
    *,
    source_message_id: Optional[str] = None,
    detected_at: Optional[float] = None,
) -> PresenceRecord:
    """更新会话的 presence 状态。

    - 将该 session 的现有 active 记录标记为 is_active=0
    - 插入新的 active 记录
    - 按 spec："新消息到达时自动使过期离开状态结束"
    """
    now = detected_at if detected_at is not None else db.now()

    # 计算过期时间和预计返回时间
    expires_seconds = DEFAULT_EXPIRY.get(signal.user_status)
    expected_seconds = signal.expected_return_seconds or DEFAULT_EXPECTED_RETURN.get(signal.user_status)
    expires_at = now + expires_seconds if expires_seconds else None
    expected_return_at = now + expected_seconds if expected_seconds else None

    priority = PRIORITY.get(signal.user_status, 0)
    record_id = db.new_id()

    conn = db.connect()
    try:
        # 将现有 active 记录标记为 inactive
        conn.execute(
            "UPDATE conversation_presence SET is_active=0, updated_at=? "
            "WHERE session_id=? AND is_active=1",
            (now, session_id),
        )
        # 插入新的 active 记录
        conn.execute(
            "INSERT INTO conversation_presence"
            " (id, session_id, user_status, detected_at, expires_at, expected_return_at,"
            "  open_thread, open_thread_topic, source_message_id, priority, is_active,"
            "  created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (
                record_id, session_id, signal.user_status, now, expires_at, expected_return_at,
                1 if signal.open_thread else 0, signal.open_thread_topic,
                source_message_id, priority, now, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return PresenceRecord(
        id=record_id, session_id=session_id, user_status=signal.user_status,
        detected_at=now, expires_at=expires_at, expected_return_at=expected_return_at,
        open_thread=signal.open_thread, open_thread_topic=signal.open_thread_topic,
        source_message_id=source_message_id, priority=priority, is_active=True,
    )


def get_current_presence(session_id: str) -> Optional[PresenceRecord]:
    """获取会话当前的 presence 状态（is_active=1 的记录）。"""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM conversation_presence "
            "WHERE session_id=? AND is_active=1 ORDER BY detected_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return PresenceRecord(
            id=row["id"], session_id=row["session_id"], user_status=row["user_status"],
            detected_at=row["detected_at"], expires_at=row["expires_at"],
            expected_return_at=row["expected_return_at"],
            open_thread=bool(row["open_thread"]),
            open_thread_topic=row["open_thread_topic"],
            source_message_id=row["source_message_id"],
            priority=row["priority"], is_active=bool(row["is_active"]),
        )
    finally:
        conn.close()


def expire_stale_presences(now: Optional[float] = None) -> int:
    """清理过期的 active presence 记录。

    按 spec："新消息到达时自动使过期离开状态结束"
    返回被清理的记录数。
    """
    now = now if now is not None else db.now()
    conn = db.connect()
    try:
        cursor = conn.execute(
            "UPDATE conversation_presence SET is_active=0, updated_at=? "
            "WHERE is_active=1 AND expires_at IS NOT NULL AND expires_at < ?",
            (now, now),
        )
        conn.commit()
        return cursor.rowcount or 0
    finally:
        conn.close()


def should_block_proactive(presence: Optional[PresenceRecord]) -> bool:
    """判断当前 presence 是否应阻断主动候选。

    按 spec："明确结束和睡眠场景 100% 阻断延续候选"
    """
    if not presence or not presence.is_active:
        return False
    blocking_statuses = {
        UserStatus.AWAY_SLEEP,
        UserStatus.ENDED_CONVERSATION,
        UserStatus.DO_NOT_DISTURB,
    }
    return presence.user_status in blocking_statuses
