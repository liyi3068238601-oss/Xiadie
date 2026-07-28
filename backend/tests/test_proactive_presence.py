"""EAP v0.2 Conversation Presence v2 测试。

覆盖：
1. 程序规则识别（9 类高精度模式）
2. 状态优先级
3. 默认过期时间
4. update_presence / get_current_presence / expire_stale_presences
5. should_block_proactive（明确结束和睡眠场景 100% 阻断延续候选）
6. schema：migration 49 新建 conversation_presence 表（migration 53 后 schema_version = "53"），8 值 CHECK
"""
import time

import pytest

from app import db
from app.proactive import presence
from app.proactive.presence import UserStatus


def _setup_session(session_id: str) -> None:
    """插入测试 session，满足 conversation_presence 的外键约束。"""
    now = db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (session_id, "presence 测试", now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_message(session_id: str, content: str = "测试消息") -> str:
    """插入测试 user 消息，返回 message_id（满足 source_message_id 外键）。"""
    msg_id = db.new_id()
    now = db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (msg_id, session_id, "user", content, now),
        )
        conn.commit()
    finally:
        conn.close()
    return msg_id


def _cleanup_session(session_id: str) -> None:
    conn = db.connect()
    try:
        conn.execute("DELETE FROM conversation_presence WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        conn.commit()
    finally:
        conn.close()


# ---------- 1. 程序规则识别测试 ----------

def test_detect_sleep_patterns():
    """晚安/睡了/去睡觉 → AWAY_SLEEP。"""
    assert presence.detect_presence_signals("晚安").user_status == UserStatus.AWAY_SLEEP
    assert presence.detect_presence_signals("我睡了").user_status == UserStatus.AWAY_SLEEP
    assert presence.detect_presence_signals("去睡觉了").user_status == UserStatus.AWAY_SLEEP
    assert presence.detect_presence_signals("我要睡了").user_status == UserStatus.AWAY_SLEEP


def test_detect_ended_patterns():
    """先这样/再见/拜拜 → ENDED_CONVERSATION。"""
    assert presence.detect_presence_signals("先这样").user_status == UserStatus.ENDED_CONVERSATION
    assert presence.detect_presence_signals("再见").user_status == UserStatus.ENDED_CONVERSATION
    assert presence.detect_presence_signals("拜拜").user_status == UserStatus.ENDED_CONVERSATION
    assert presence.detect_presence_signals("下次聊").user_status == UserStatus.ENDED_CONVERSATION


def test_detect_do_not_disturb():
    """勿扰/别打扰我 → DO_NOT_DISTURB。"""
    assert presence.detect_presence_signals("勿扰").user_status == UserStatus.DO_NOT_DISTURB
    assert presence.detect_presence_signals("别打扰我").user_status == UserStatus.DO_NOT_DISTURB
    assert presence.detect_presence_signals("先别找我").user_status == UserStatus.DO_NOT_DISTURB


def test_detect_away_brief_test():
    """我去测试 → AWAY_BRIEF + open_thread=True + topic='测试结果'。"""
    signal = presence.detect_presence_signals("我去测试一下")
    assert signal.user_status == UserStatus.AWAY_BRIEF
    assert signal.open_thread is True
    assert signal.open_thread_topic == "测试结果"


def test_detect_away_brief_meal():
    """去吃饭 → AWAY_BRIEF + open_thread=True + topic='吃饭'。"""
    signal = presence.detect_presence_signals("我去吃饭了")
    assert signal.user_status == UserStatus.AWAY_BRIEF
    assert signal.open_thread is True
    assert signal.open_thread_topic == "吃饭"


def test_detect_away_busy_meeting():
    """在开会 → AWAY_BUSY。"""
    signal = presence.detect_presence_signals("在开会")
    assert signal.user_status == UserStatus.AWAY_BUSY
    assert signal.open_thread is False


def test_detect_online_default():
    """普通文本 → ONLINE。"""
    assert presence.detect_presence_signals("今天天气怎么样").user_status == UserStatus.ONLINE
    assert presence.detect_presence_signals("帮我写段代码").user_status == UserStatus.ONLINE


def test_detect_empty_text():
    """空文本 → UNKNOWN。"""
    assert presence.detect_presence_signals("").user_status == UserStatus.UNKNOWN
    assert presence.detect_presence_signals("   ").user_status == UserStatus.UNKNOWN
    assert presence.detect_presence_signals(None).user_status == UserStatus.UNKNOWN


# ---------- 2. 状态优先级测试 ----------

def test_priority_order():
    """DO_NOT_DISTURB > ENDED > SLEEP > BUSY > EXTENDED > BRIEF > ONLINE > UNKNOWN。"""
    assert presence.PRIORITY[UserStatus.DO_NOT_DISTURB] > presence.PRIORITY[UserStatus.ENDED_CONVERSATION]
    assert presence.PRIORITY[UserStatus.ENDED_CONVERSATION] > presence.PRIORITY[UserStatus.AWAY_SLEEP]
    assert presence.PRIORITY[UserStatus.AWAY_SLEEP] > presence.PRIORITY[UserStatus.AWAY_BUSY]
    assert presence.PRIORITY[UserStatus.AWAY_BUSY] > presence.PRIORITY[UserStatus.AWAY_EXTENDED]
    assert presence.PRIORITY[UserStatus.AWAY_EXTENDED] > presence.PRIORITY[UserStatus.AWAY_BRIEF]
    assert presence.PRIORITY[UserStatus.AWAY_BRIEF] > presence.PRIORITY[UserStatus.ONLINE]
    assert presence.PRIORITY[UserStatus.ONLINE] > presence.PRIORITY[UserStatus.UNKNOWN]


def test_presence_v2_enum_is_frozen_to_eight_values():
    assert presence.USER_STATUS_VALUES == {
        "online", "away_brief", "away_sleep", "away_busy", "away_extended",
        "ended_conversation", "do_not_disturb", "unknown",
    }


def test_presence_reducer_handles_reappearance_and_rejects_unknown_status():
    transition = presence.reduce_presence(
        None, event="signal", signal=presence.PresenceSignal(UserStatus.ONLINE), now=100,
    )
    assert transition.active is True
    assert transition.reason == "reappearance"
    with pytest.raises(ValueError, match="invalid conversation-presence-v2 signal"):
        presence.reduce_presence(
            None, event="signal", signal=presence.PresenceSignal("new_status"), now=100,
        )


# ---------- 3. 过期时间测试 ----------

def test_expiry_for_away_brief():
    """AWAY_BRIEF 默认 30 分钟过期。"""
    assert presence.DEFAULT_EXPIRY[UserStatus.AWAY_BRIEF] == 30 * 60


def test_expiry_for_away_sleep():
    """AWAY_SLEEP 默认 8 小时过期。"""
    assert presence.DEFAULT_EXPIRY[UserStatus.AWAY_SLEEP] == 8 * 3600


def test_no_expiry_for_online():
    """ONLINE 不过期。"""
    assert presence.DEFAULT_EXPIRY[UserStatus.ONLINE] is None


def test_no_expiry_for_ended():
    """ENDED_CONVERSATION 不过期（需新消息才能结束）。"""
    assert presence.DEFAULT_EXPIRY[UserStatus.ENDED_CONVERSATION] is None


# ---------- 4. update_presence 测试 ----------

def test_update_presence_inserts_new_active():
    """插入新记录，is_active=1。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        signal = presence.PresenceSignal(user_status=UserStatus.AWAY_BRIEF)
        record = presence.update_presence(session_id, signal)
        assert record.is_active is True
        assert record.user_status == UserStatus.AWAY_BRIEF
        assert record.session_id == session_id
        assert record.expires_at is not None  # AWAY_BRIEF 有过期时间
        # DB 中确认 is_active=1
        current = presence.get_current_presence(session_id)
        assert current is not None
        assert current.is_active is True
        assert current.user_status == UserStatus.AWAY_BRIEF
    finally:
        _cleanup_session(session_id)


def test_update_presence_deactivates_previous():
    """之前 active 记录变为 is_active=0。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        # 第一次插入
        presence.update_presence(
            session_id,
            presence.PresenceSignal(user_status=UserStatus.AWAY_BRIEF),
        )
        # 第二次插入应使第一条变为 inactive
        presence.update_presence(
            session_id,
            presence.PresenceSignal(user_status=UserStatus.ONLINE),
        )
        conn = db.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM conversation_presence WHERE session_id=? ORDER BY detected_at",
                (session_id,),
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 2
        # 第一条 inactive，第二条 active
        assert rows[0]["is_active"] == 0
        assert rows[1]["is_active"] == 1
        # 当前 presence 是 ONLINE
        current = presence.get_current_presence(session_id)
        assert current is not None
        assert current.user_status == UserStatus.ONLINE
    finally:
        _cleanup_session(session_id)


def test_update_presence_with_open_thread():
    """open_thread 和 topic 正确保存。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        signal = presence.PresenceSignal(
            user_status=UserStatus.AWAY_BRIEF,
            open_thread=True,
            open_thread_topic="测试结果",
        )
        record = presence.update_presence(session_id, signal)
        assert record.open_thread is True
        assert record.open_thread_topic == "测试结果"
        # DB 中确认
        current = presence.get_current_presence(session_id)
        assert current is not None
        assert current.open_thread is True
        assert current.open_thread_topic == "测试结果"
    finally:
        _cleanup_session(session_id)


# ---------- 5. get_current_presence 测试 ----------

def test_get_current_presence_returns_active():
    """返回 is_active=1 的最新记录。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        # 插入两条，第二条应是 active
        presence.update_presence(
            session_id,
            presence.PresenceSignal(user_status=UserStatus.AWAY_BRIEF),
            detected_at=1000.0,
        )
        presence.update_presence(
            session_id,
            presence.PresenceSignal(user_status=UserStatus.ONLINE),
            detected_at=2000.0,
        )
        current = presence.get_current_presence(session_id)
        assert current is not None
        assert current.user_status == UserStatus.ONLINE
        assert current.is_active is True
        assert current.detected_at == 2000.0
    finally:
        _cleanup_session(session_id)


def test_get_current_presence_returns_none_if_no_active():
    """无 active 记录返回 None。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        assert presence.get_current_presence(session_id) is None
    finally:
        _cleanup_session(session_id)


# ---------- 6. expire_stale_presences 测试 ----------

def test_expire_stale_presences_clears_expired():
    """过期记录被清理。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        # AWAY_BRIEF 默认 30 分钟过期；用 detected_at 让它早已过期
        presence.update_presence(
            session_id,
            presence.PresenceSignal(user_status=UserStatus.AWAY_BRIEF),
            detected_at=1000.0,  # 1970 年附近，肯定已过期
        )
        cleared = presence.expire_stale_presences(now=time.time() + 999999)
        assert cleared == 1
        # 当前 presence 应为 None（active 记录已被清理）
        assert presence.get_current_presence(session_id) is None
    finally:
        _cleanup_session(session_id)


def test_expire_stale_presences_keeps_unexpired():
    """未过期记录保留。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        # 用 future detected_at 让 expires_at 在未来
        future_now = time.time() + 999999
        presence.update_presence(
            session_id,
            presence.PresenceSignal(user_status=UserStatus.AWAY_BRIEF),
            detected_at=future_now,
        )
        cleared = presence.expire_stale_presences(now=time.time())
        assert cleared == 0
        current = presence.get_current_presence(session_id)
        assert current is not None
        assert current.user_status == UserStatus.AWAY_BRIEF
    finally:
        _cleanup_session(session_id)


# ---------- 7. should_block_proactive 测试 ----------
# spec："明确结束和睡眠场景 100% 阻断延续候选"

def test_should_block_proactive_sleep():
    """AWAY_SLEEP 阻断。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        presence.update_presence(
            session_id,
            presence.PresenceSignal(user_status=UserStatus.AWAY_SLEEP),
        )
        current = presence.get_current_presence(session_id)
        assert presence.should_block_proactive(current) is True
    finally:
        _cleanup_session(session_id)


def test_should_block_proactive_ended():
    """ENDED_CONVERSATION 阻断。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        presence.update_presence(
            session_id,
            presence.PresenceSignal(user_status=UserStatus.ENDED_CONVERSATION),
        )
        current = presence.get_current_presence(session_id)
        assert presence.should_block_proactive(current) is True
    finally:
        _cleanup_session(session_id)


def test_should_block_proactive_do_not_disturb():
    """DO_NOT_DISTURB 阻断。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        presence.update_presence(
            session_id,
            presence.PresenceSignal(user_status=UserStatus.DO_NOT_DISTURB),
        )
        current = presence.get_current_presence(session_id)
        assert presence.should_block_proactive(current) is True
    finally:
        _cleanup_session(session_id)


def test_should_not_block_proactive_online():
    """ONLINE 不阻断。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        presence.update_presence(
            session_id,
            presence.PresenceSignal(user_status=UserStatus.ONLINE),
        )
        current = presence.get_current_presence(session_id)
        assert presence.should_block_proactive(current) is False
    finally:
        _cleanup_session(session_id)


def test_should_not_block_proactive_away_brief():
    """AWAY_BRIEF 不阻断（可延后）。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        presence.update_presence(
            session_id,
            presence.PresenceSignal(user_status=UserStatus.AWAY_BRIEF),
        )
        current = presence.get_current_presence(session_id)
        assert presence.should_block_proactive(current) is False
    finally:
        _cleanup_session(session_id)


def test_should_not_block_proactive_none():
    """无 presence 不阻断。"""
    assert presence.should_block_proactive(None) is False


# ---------- 8. schema 测试 ----------

def test_schema_version_is_52():
    """migration 54 后 schema_version = '54'。"""
    db.init_db()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        assert row[0] == "75"
    finally:
        conn.close()


def test_conversation_presence_table_exists():
    """conversation_presence 表存在。"""
    db.init_db()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='conversation_presence'"
        ).fetchone()
        assert row is not None
        assert row["name"] == "conversation_presence"
    finally:
        conn.close()


def test_conversation_presence_has_8_status_values():
    """CHECK 约束允许 8 值。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    valid_statuses = [
        UserStatus.ONLINE,
        UserStatus.AWAY_BRIEF,
        UserStatus.AWAY_SLEEP,
        UserStatus.AWAY_BUSY,
        UserStatus.AWAY_EXTENDED,
        UserStatus.ENDED_CONVERSATION,
        UserStatus.DO_NOT_DISTURB,
        UserStatus.UNKNOWN,
    ]
    conn = db.connect()
    try:
        for status in valid_statuses:
            record_id = db.new_id()
            now = db.now()
            # 先把之前的 active 记录置为 inactive（避免一个 session 多条 active）
            conn.execute(
                "UPDATE conversation_presence SET is_active=0 WHERE session_id=?",
                (session_id,),
            )
            conn.execute(
                "INSERT INTO conversation_presence"
                " (id, session_id, user_status, detected_at, expires_at, expected_return_at,"
                "  open_thread, open_thread_topic, source_message_id, priority, is_active,"
                "  created_at, updated_at)"
                " VALUES (?, ?, ?, ?, NULL, NULL, 0, NULL, NULL, 0, 1, ?, ?)",
                (record_id, session_id, status, now, now, now),
            )
            conn.commit()
        # 验证无效状态会被拒绝
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO conversation_presence"
                " (id, session_id, user_status, detected_at, expires_at, expected_return_at,"
                "  open_thread, open_thread_topic, source_message_id, priority, is_active,"
                "  created_at, updated_at)"
                " VALUES (?, ?, 'invalid_status', ?, NULL, NULL, 0, NULL, NULL, 0, 1, ?, ?)",
                (db.new_id(), session_id, db.now(), db.now(), db.now()),
            )
    finally:
        conn.close()
        _cleanup_session(session_id)
