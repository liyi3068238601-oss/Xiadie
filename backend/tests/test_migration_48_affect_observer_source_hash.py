"""migration 48：affect_observer_runs 增加 source_hash 字段。"""
from app import db


def _setup_session_and_messages(conn, session_id, user_msg_id, assistant_msg_id):
    """插入 session 与 user/assistant 消息，满足 affect_observer_runs 的外键约束。"""
    now = db.now()
    conn.execute(
        "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
        (session_id, "migration48 测试", now, now),
    )
    conn.execute(
        "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
        (user_msg_id, session_id, "user", "migration48 用户消息", now),
    )
    conn.execute(
        "INSERT INTO messages(id,session_id,role,content,model,created_at) VALUES(?,?,?,?,?,?)",
        (assistant_msg_id, session_id, "assistant", "migration48 助手消息", "test-model", now + 0.1),
    )


def test_schema_version_is_48():
    """migration 48 应用后 schema_version = 54（后续 migration 49/50/51/52/53/54 已叠加）。"""
    db.init_db()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        assert row[0] == "63"
    finally:
        conn.close()


def test_affect_observer_runs_has_source_hash_column():
    """affect_observer_runs 表有 source_hash 列。"""
    db.init_db()
    conn = db.connect()
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(affect_observer_runs)")}
        assert "source_hash" in columns
    finally:
        conn.close()


def test_source_hash_has_default_empty_string():
    """已有行的 source_hash 默认为空字符串（兼容 affect-observer-v1 已冻结）。"""
    db.init_db()
    conn = db.connect()
    session_id, user_msg_id, assistant_msg_id = db.new_id(), db.new_id(), db.new_id()
    run_id = "test-m48-default"
    try:
        _setup_session_and_messages(conn, session_id, user_msg_id, assistant_msg_id)
        # 插入一条 run（不设 source_hash，应使用默认值）
        conn.execute(
            "INSERT INTO affect_observer_runs"
            " (id, idempotency_key, source_session_id, source_user_message_id,"
            "  source_assistant_message_id, model, status, protocol_version,"
            "  created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'queued', 'affect-observer-v1', 0, 0)",
            (run_id, run_id + "-key", session_id, user_msg_id, assistant_msg_id, "test-model"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT source_hash FROM affect_observer_runs WHERE id=?", (run_id,)
        ).fetchone()
        assert row[0] == ""
    finally:
        conn.execute("DELETE FROM affect_observer_runs WHERE id=?", (run_id,))
        conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        conn.commit()
        conn.close()


def test_source_hash_can_be_set_explicitly():
    """新行可以显式设置 source_hash（EAP 各阶段按需写入）。"""
    db.init_db()
    conn = db.connect()
    session_id, user_msg_id, assistant_msg_id = db.new_id(), db.new_id(), db.new_id()
    run_id = "test-m48-set"
    try:
        _setup_session_and_messages(conn, session_id, user_msg_id, assistant_msg_id)
        test_hash = "a" * 64  # 64 字符 hex
        conn.execute(
            "INSERT INTO affect_observer_runs"
            " (id, idempotency_key, source_session_id, source_user_message_id,"
            "  source_assistant_message_id, model, status, protocol_version,"
            "  source_hash, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'queued', 'affect-observer-v1', ?, 0, 0)",
            (run_id, run_id + "-key", session_id, user_msg_id, assistant_msg_id,
             "test-model", test_hash),
        )
        conn.commit()
        row = conn.execute(
            "SELECT source_hash FROM affect_observer_runs WHERE id=?", (run_id,)
        ).fetchone()
        assert row[0] == test_hash
    finally:
        conn.execute("DELETE FROM affect_observer_runs WHERE id=?", (run_id,))
        conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        conn.commit()
        conn.close()
