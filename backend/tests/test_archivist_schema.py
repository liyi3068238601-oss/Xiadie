"""Archivist E.1 生命周期与召回审计数据地基。"""
import sqlite3

import pytest

from app import db, memory

db.init_db()


@pytest.fixture
def fragment():
    item = memory.create_memory("L1", f"Archivist schema test {db.new_id()}")
    yield item
    conn = db.connect()
    try:
        conn.execute("DELETE FROM memory_recall_events WHERE fragment_id=?", (item["id"],))
        conn.execute("DELETE FROM memory_lifecycle_events WHERE fragment_id=?", (item["id"],))
        conn.execute("DELETE FROM memory_fragments WHERE id=?", (item["id"],))
        conn.commit()
    finally:
        conn.close()


def test_schema_23_adds_retention_fields_and_minimal_audit_tables():
    conn = db.connect()
    try:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()["value"]
        assert version == "71"
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(memory_fragments)")
        }
        assert {
            "last_recalled_at", "recall_count", "cooling_since", "frozen_at",
            "lifecycle_policy_version", "lifecycle_revision", "fts_indexed",
        } <= columns
        tables = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name IN ('memory_recall_events','memory_lifecycle_events')"
            )
        }
        assert tables == {"memory_recall_events", "memory_lifecycle_events"}
    finally:
        conn.close()


def test_schema_23_preserves_rows_and_backfills_existing_lifecycle_times():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.executescript(
            "CREATE TABLE sessions(id TEXT PRIMARY KEY);"
            "CREATE TABLE memory_fragments("
            "id TEXT PRIMARY KEY,status TEXT NOT NULL,enabled INTEGER NOT NULL,"
            "created_at REAL NOT NULL,updated_at REAL NOT NULL);"
            "INSERT INTO memory_fragments VALUES('active','active',1,10,20);"
            "INSERT INTO memory_fragments VALUES('cool','cooling',1,10,30);"
            "INSERT INTO memory_fragments VALUES('ice','frozen',1,10,40);"
        )
        migration = next(sql for version, sql in db.MIGRATIONS if version == 23)
        conn.executescript(migration)
        rows = {
            row["id"]: dict(row) for row in conn.execute("SELECT * FROM memory_fragments")
        }
        assert rows["active"]["last_recalled_at"] is None
        assert rows["active"]["recall_count"] == 0
        assert rows["cool"]["cooling_since"] == 30
        assert rows["ice"]["frozen_at"] == 40
        assert all(row["lifecycle_policy_version"] == "fragment-retention-v1"
                   for row in rows.values())
    finally:
        conn.close()


def test_recall_ledger_deduplicates_same_fragment_and_context(fragment):
    conn = db.connect()
    try:
        values = (
            db.new_id(), fragment["id"], "chat-turn-1", 42,
            "memory-recall-accounting-v1", db.now(),
        )
        conn.execute(
            "INSERT INTO memory_recall_events("
            "id,fragment_id,context_key,token_estimate,policy_version,injected_at)"
            " VALUES(?,?,?,?,?,?)", values,
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO memory_recall_events("
                "id,fragment_id,context_key,token_estimate,policy_version,injected_at)"
                " VALUES(?,?,?,?,?,?)", (db.new_id(), *values[1:]),
            )
        conn.rollback()
    finally:
        conn.close()


def test_lifecycle_event_requires_monotonic_revision_valid_state_and_score(fragment):
    conn = db.connect()
    try:
        base = (
            fragment["id"], 1, "active", "cooling", 0.4, '{"importance":0.2}',
            "retention_below_cooling", "archivist", "fragment-retention-v1", db.now(),
        )
        statement = (
            "INSERT INTO memory_lifecycle_events("
            "id,fragment_id,revision,from_status,to_status,retention_score,"
            "score_components_json,reason_code,source,policy_version,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)"
        )
        conn.execute(statement, (db.new_id(), *base))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(statement, (db.new_id(), *base))
        invalid = list(base)
        invalid[3] = "deleted"
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(statement, (db.new_id(), *invalid))
        invalid = list(base)
        invalid[1] = 2
        invalid[4] = 1.1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(statement, (db.new_id(), *invalid))
        conn.rollback()
    finally:
        conn.close()


def test_archivist_audit_schema_cannot_store_memory_body():
    conn = db.connect()
    try:
        for table in ("memory_recall_events", "memory_lifecycle_events"):
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            assert not ({"content", "summary", "tags", "source_text", "raw_output"} & columns)
    finally:
        conn.close()


def test_schema_24_upgrades_existing_fts_triggers_without_losing_searchability():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(
            "CREATE TABLE memory_fragments("
            "id TEXT PRIMARY KEY,content TEXT NOT NULL,tags TEXT NOT NULL DEFAULT '');"
            "CREATE VIRTUAL TABLE memory_fragments_fts USING fts5("
            "content,tags,content='memory_fragments',content_rowid='rowid',tokenize='trigram');"
            "CREATE TRIGGER memory_fragments_fts_insert AFTER INSERT ON memory_fragments BEGIN"
            " INSERT INTO memory_fragments_fts(rowid,content,tags)"
            " VALUES(new.rowid,new.content,new.tags); END;"
            "CREATE TRIGGER memory_fragments_fts_delete AFTER DELETE ON memory_fragments BEGIN"
            " INSERT INTO memory_fragments_fts(memory_fragments_fts,rowid,content,tags)"
            " VALUES('delete',old.rowid,old.content,old.tags); END;"
            "CREATE TRIGGER memory_fragments_fts_update"
            " AFTER UPDATE OF content,tags ON memory_fragments BEGIN"
            " INSERT INTO memory_fragments_fts(memory_fragments_fts,rowid,content,tags)"
            " VALUES('delete',old.rowid,old.content,old.tags);"
            " INSERT INTO memory_fragments_fts(rowid,content,tags)"
            " VALUES(new.rowid,new.content,new.tags); END;"
            "INSERT INTO memory_fragments VALUES('legacy','旧库可检索内容','');"
        )
        migration = next(sql for version, sql in db.MIGRATIONS if version == 24)
        conn.executescript(migration)
        row = conn.execute(
            "SELECT fts_indexed FROM memory_fragments WHERE id='legacy'"
        ).fetchone()
        assert row["fts_indexed"] == 1
        triggers = {
            item["name"] for item in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
                " AND name LIKE 'memory_fragments_fts_%'"
            )
        }
        assert triggers == {
            "memory_fragments_fts_insert", "memory_fragments_fts_delete",
            "memory_fragments_fts_update",
        }
        assert conn.execute(
            "SELECT COUNT(*) count FROM memory_fragments_fts"
            " WHERE memory_fragments_fts MATCH '可检索'"
        ).fetchone()["count"] == 1
        conn.execute("UPDATE memory_fragments SET content='更新后的检索内容' WHERE id='legacy'")
        assert conn.execute(
            "SELECT COUNT(*) count FROM memory_fragments_fts"
            " WHERE memory_fragments_fts MATCH '更新后'"
        ).fetchone()["count"] == 1
    finally:
        conn.close()
