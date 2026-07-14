"""SQLite 存储层：会话、消息、记忆、任务、供应商、设置、工具日志。

本地优先原则：所有数据保存在 backend/data/xiadie.db。
"""
import json
import os
import sqlite3
import time
import uuid

DATA_DIR = os.environ.get(
    "XIADIE_DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"),
)
DB_PATH = os.path.join(DATA_DIR, "xiadie.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '新对话',
    archived INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    model TEXT,
    favorite INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'todo' CHECK(status IN ('todo','doing','done','archived')),
    due_date TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    source_session_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL DEFAULT '',
    api_key TEXT NOT NULL DEFAULT '',
    models TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 0,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_logs (
    id TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    risk_level TEXT NOT NULL DEFAULT 'S0',
    status TEXT NOT NULL DEFAULT 'done',
    summary TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
"""

MIGRATIONS = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS companion_state (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            connection REAL NOT NULL CHECK(connection BETWEEN 0 AND 1),
            pride REAL NOT NULL CHECK(pride BETWEEN -1 AND 1),
            valence REAL NOT NULL CHECK(valence BETWEEN -1 AND 1),
            arousal REAL NOT NULL CHECK(arousal BETWEEN -1 AND 1),
            immersion REAL NOT NULL CHECK(immersion BETWEEN 0 AND 1),
            updated_at REAL NOT NULL
        );
        """,
    ),
    (
        2,
        """
        DROP TABLE IF EXISTS memories;

        CREATE TABLE IF NOT EXISTS memory_fragments (
            id TEXT PRIMARY KEY,
            layer TEXT NOT NULL CHECK(layer IN ('L0','L1','L2')),
            content TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'manual',
            source_session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
            source_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
            confidence REAL NOT NULL DEFAULT 1.0 CHECK(confidence BETWEEN 0 AND 1),
            sensitivity TEXT NOT NULL DEFAULT 'normal'
                CHECK(sensitivity IN ('normal','sensitive')),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active','cooling','frozen','tombstone')),
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memory_fragments_active
            ON memory_fragments(status, enabled, layer, updated_at);
        CREATE INDEX IF NOT EXISTS idx_memory_fragments_source
            ON memory_fragments(source_session_id, source_message_id);

        CREATE TABLE IF NOT EXISTS memory_candidates (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            proposed_layer TEXT NOT NULL CHECK(proposed_layer IN ('L0','L1','L2')),
            tags TEXT NOT NULL DEFAULT '',
            source_session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
            source_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
            confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
            sensitivity TEXT NOT NULL DEFAULT 'normal'
                CHECK(sensitivity IN ('normal','sensitive')),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','accepted','rejected')),
            resolved_memory_id TEXT REFERENCES memory_fragments(id) ON DELETE SET NULL,
            resolution_note TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            resolved_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_memory_candidates_status
            ON memory_candidates(status, created_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_candidates_source_content
            ON memory_candidates(source_message_id, content);

        CREATE TABLE IF NOT EXISTS memory_entities (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL DEFAULT 'concept',
            summary TEXT NOT NULL DEFAULT '',
            aliases TEXT NOT NULL DEFAULT '[]',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_entities_name_type
            ON memory_entities(name, entity_type);

        CREATE TABLE IF NOT EXISTS memory_fragment_entities (
            fragment_id TEXT NOT NULL REFERENCES memory_fragments(id) ON DELETE CASCADE,
            entity_id TEXT NOT NULL REFERENCES memory_entities(id) ON DELETE CASCADE,
            relation TEXT NOT NULL DEFAULT 'mentions',
            created_at REAL NOT NULL,
            PRIMARY KEY(fragment_id, entity_id, relation)
        );

        CREATE TABLE IF NOT EXISTS memory_events (
            id TEXT PRIMARY KEY,
            object_type TEXT NOT NULL CHECK(object_type IN ('candidate','fragment','entity')),
            object_id TEXT NOT NULL,
            action TEXT NOT NULL,
            before_json TEXT,
            after_json TEXT,
            source TEXT NOT NULL DEFAULT 'system',
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memory_events_object
            ON memory_events(object_type, object_id, created_at);
        """,
    ),
    (
        3,
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fragments_fts USING fts5(
            content,
            tags,
            content='memory_fragments',
            content_rowid='rowid',
            tokenize='trigram'
        );

        CREATE TRIGGER IF NOT EXISTS memory_fragments_fts_insert
        AFTER INSERT ON memory_fragments BEGIN
            INSERT INTO memory_fragments_fts(rowid, content, tags)
            VALUES (new.rowid, new.content, new.tags);
        END;

        CREATE TRIGGER IF NOT EXISTS memory_fragments_fts_delete
        AFTER DELETE ON memory_fragments BEGIN
            INSERT INTO memory_fragments_fts(memory_fragments_fts, rowid, content, tags)
            VALUES ('delete', old.rowid, old.content, old.tags);
        END;

        CREATE TRIGGER IF NOT EXISTS memory_fragments_fts_update
        AFTER UPDATE OF content, tags ON memory_fragments BEGIN
            INSERT INTO memory_fragments_fts(memory_fragments_fts, rowid, content, tags)
            VALUES ('delete', old.rowid, old.content, old.tags);
            INSERT INTO memory_fragments_fts(rowid, content, tags)
            VALUES (new.rowid, new.content, new.tags);
        END;

        INSERT INTO memory_fragments_fts(memory_fragments_fts) VALUES('rebuild');
        """,
    ),
    (
        4,
        """
        ALTER TABLE memory_entities ADD COLUMN tags TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE memory_entities ADD COLUMN current_status TEXT NOT NULL DEFAULT '';
        ALTER TABLE memory_entities ADD COLUMN status_since TEXT NOT NULL DEFAULT '';
        ALTER TABLE memory_entities ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
        ALTER TABLE memory_entities ADD COLUMN source TEXT NOT NULL DEFAULT 'manual';
        ALTER TABLE memory_entities ADD COLUMN merged_into_id TEXT;
        ALTER TABLE memory_fragment_entities ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0;
        CREATE INDEX IF NOT EXISTS idx_memory_entities_status_type
            ON memory_entities(status, entity_type, updated_at);
        """,
    ),
    (
        5,
        """
        CREATE TABLE IF NOT EXISTS memory_episodes (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            start_at REAL NOT NULL,
            end_at REAL NOT NULL,
            significance INTEGER NOT NULL DEFAULT 4 CHECK(significance BETWEEN 1 AND 10),
            confidence REAL NOT NULL DEFAULT 0.7 CHECK(confidence BETWEEN 0 AND 1),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active','archived','tombstone')),
            source TEXT NOT NULL DEFAULT 'candidate_confirmed',
            candidate_id TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memory_episodes_status_time
            ON memory_episodes(status, end_at DESC);

        CREATE TABLE IF NOT EXISTS memory_episode_fragments (
            episode_id TEXT NOT NULL REFERENCES memory_episodes(id) ON DELETE CASCADE,
            fragment_id TEXT NOT NULL REFERENCES memory_fragments(id) ON DELETE CASCADE,
            position INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            PRIMARY KEY(episode_id, fragment_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_episode_fragment_single_active
            ON memory_episode_fragments(fragment_id);

        CREATE TABLE IF NOT EXISTS memory_episode_entities (
            episode_id TEXT NOT NULL REFERENCES memory_episodes(id) ON DELETE CASCADE,
            entity_id TEXT NOT NULL REFERENCES memory_entities(id) ON DELETE CASCADE,
            relation TEXT NOT NULL DEFAULT 'involves',
            created_at REAL NOT NULL,
            PRIMARY KEY(episode_id, entity_id)
        );

        CREATE TABLE IF NOT EXISTS memory_episode_candidates (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            start_at REAL NOT NULL,
            end_at REAL NOT NULL,
            significance INTEGER NOT NULL DEFAULT 4 CHECK(significance BETWEEN 1 AND 10),
            confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','accepted','rejected')),
            grouping_key TEXT NOT NULL UNIQUE,
            resolved_episode_id TEXT REFERENCES memory_episodes(id) ON DELETE SET NULL,
            resolution_note TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            resolved_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_episode_candidates_status
            ON memory_episode_candidates(status, created_at DESC);

        CREATE TABLE IF NOT EXISTS memory_episode_candidate_fragments (
            candidate_id TEXT NOT NULL REFERENCES memory_episode_candidates(id) ON DELETE CASCADE,
            fragment_id TEXT NOT NULL REFERENCES memory_fragments(id) ON DELETE CASCADE,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(candidate_id, fragment_id)
        );

        DROP INDEX IF EXISTS idx_memory_events_object;
        ALTER TABLE memory_events RENAME TO memory_events_v4;
        CREATE TABLE memory_events (
            id TEXT PRIMARY KEY,
            object_type TEXT NOT NULL CHECK(object_type IN (
                'candidate','fragment','entity','episode_candidate','episode'
            )),
            object_id TEXT NOT NULL,
            action TEXT NOT NULL,
            before_json TEXT,
            after_json TEXT,
            source TEXT NOT NULL DEFAULT 'system',
            created_at REAL NOT NULL
        );
        INSERT INTO memory_events
            (id, object_type, object_id, action, before_json, after_json, source, created_at)
        SELECT id, object_type, object_id, action, before_json, after_json, source, created_at
        FROM memory_events_v4;
        DROP TABLE memory_events_v4;
        CREATE INDEX idx_memory_events_object
            ON memory_events(object_type, object_id, created_at);
        """,
    ),
]

# 默认供应商：全部 OpenAI-Compatible。api_key 开发期存本地库，
# 正式版迁移到系统安全存储（见需求 MODEL-005）。
DEFAULT_PROVIDERS = [
    ("mock",        "内置演示",    "",                                          ["xiadie-mock"], 1),
    ("deepseek",    "DeepSeek",    "https://api.deepseek.com/v1",               ["deepseek-chat", "deepseek-reasoner"], 0),
    ("openai",      "OpenAI",      "https://api.openai.com/v1",                 ["gpt-4o-mini", "gpt-4o"], 0),
    ("glm",         "智谱 GLM",    "https://open.bigmodel.cn/api/paas/v4",      ["glm-4-flash", "glm-4-plus"], 0),
    ("qwen",        "通义千问",    "https://dashscope.aliyuncs.com/compatible-mode/v1", ["qwen-plus", "qwen-turbo"], 0),
    ("kimi",        "Kimi",        "https://api.moonshot.cn/v1",                ["moonshot-v1-8k"], 0),
    ("openrouter",  "OpenRouter",  "https://openrouter.ai/api/v1",              ["openrouter/auto"], 0),
    ("siliconflow", "硅基流动",    "https://api.siliconflow.cn/v1",             ["Qwen/Qwen2.5-7B-Instruct"], 0),
    ("ollama",      "Ollama 本地", "http://127.0.0.1:11434/v1",                 ["qwen2.5:7b"], 0),
    ("custom",      "自定义接口",  "",                                          [], 0),
]


def now() -> float:
    return time.time()


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def connect() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _apply_migrations(conn)
        for i, (pid, name, base_url, models, enabled) in enumerate(DEFAULT_PROVIDERS):
            conn.execute(
                "INSERT OR IGNORE INTO providers(id, name, base_url, models, enabled, sort)"
                " VALUES(?,?,?,?,?,?)",
                (pid, name, base_url, json.dumps(models, ensure_ascii=False), enabled, i),
            )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('current_model', ?)",
            (json.dumps({"provider_id": "mock", "model": "xiadie-mock"}),),
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('memory_enabled', '1')"
        )
        conn.commit()
    finally:
        conn.close()


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """按版本顺序执行幂等迁移；未版本化的开发库从 0 开始。"""
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    version = int(row["value"]) if row else 0
    for target, sql in MIGRATIONS:
        if target <= version:
            continue
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(target),),
        )
        version = target


def get_setting(key: str, default: str = "") -> str:
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()
