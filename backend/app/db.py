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
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    layer TEXT NOT NULL CHECK(layer IN ('L0','L1','L2')),
    content TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
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
