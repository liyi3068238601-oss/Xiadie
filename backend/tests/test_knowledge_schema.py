"""F.1 用户文件知识库的数据地基与隐私约束。"""
import sqlite3

import pytest

from app import db

db.init_db()


def test_schema_28_has_separate_knowledge_namespace_and_default_collection():
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()["value"] == "62"
        tables = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'knowledge_%'"
            )
        }
        assert {
            "knowledge_collections", "knowledge_documents",
            "knowledge_import_runs", "knowledge_import_events", "knowledge_parse_artifacts",
            "knowledge_chunks", "knowledge_chunks_fts",
        } <= tables
        default = conn.execute(
            "SELECT * FROM knowledge_collections WHERE id='default'"
        ).fetchone()
        assert default and default["status"] == "active"
        document_fks = {
            row["table"] for row in conn.execute("PRAGMA foreign_key_list(knowledge_documents)")
        }
        assert document_fks == {"knowledge_collections"}
    finally:
        conn.close()


def test_knowledge_constraints_reject_unsafe_or_incomplete_metadata():
    conn = db.connect()
    try:
        now = db.now()
        base = (
            db.new_id(), "default", "file", "notes.md", ".md", "text/markdown", 12,
            "a" * 64, db.new_id(), now, now,
        )
        conn.execute(
            "INSERT INTO knowledge_documents("
            "id,collection_id,source_type,original_name,extension,mime_type,size_bytes,"
            "content_sha256,storage_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            base,
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO knowledge_documents("
                "id,collection_id,source_type,original_name,extension,mime_type,size_bytes,"
                "content_sha256,storage_key,embedding_mode,embedding_provider_id,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,'none','remote-provider',?,?)",
                (db.new_id(), "default", "file", "bad.md", ".md", "text/markdown", 1,
                 "b" * 64, db.new_id(), now, now),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE knowledge_documents SET status='indexed' WHERE id=?", (base[0],)
            )
        conn.rollback()
    finally:
        conn.close()


def test_import_event_schema_contains_no_file_body_or_path_columns():
    conn = db.connect()
    try:
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(knowledge_import_events)")
        }
        assert {"run_id", "action", "stage", "error_code", "metadata_json"} <= columns
        assert not ({"content", "filename", "original_name", "path", "raw_output"} & columns)
    finally:
        conn.close()


def test_schema_28_migration_is_repeatable():
    db.init_db()
    db.init_db()
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT COUNT(*) count FROM knowledge_collections WHERE id='default'"
        ).fetchone()["count"] == 1
    finally:
        conn.close()


def test_schema_28_upgrades_old_database_without_touching_memory_rows():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(
            "PRAGMA foreign_keys=ON;"
            "CREATE TABLE memory_fragments(id TEXT PRIMARY KEY,content TEXT NOT NULL);"
            "INSERT INTO memory_fragments VALUES('legacy','旧记忆保持原样');"
        )
        migration = next(sql for version, sql in db.MIGRATIONS if version == 28)
        conn.executescript(migration)
        assert conn.execute(
            "SELECT content FROM memory_fragments WHERE id='legacy'"
        ).fetchone()["content"] == "旧记忆保持原样"
        assert conn.execute(
            "SELECT name FROM knowledge_collections WHERE id='default'"
        ).fetchone()["name"] == "默认知识库"
    finally:
        conn.close()


def test_document_removal_cascades_run_and_body_free_events():
    conn = db.connect()
    document_id, run_id = db.new_id(), db.new_id()
    try:
        now = db.now()
        conn.execute(
            "INSERT INTO knowledge_documents("
            "id,original_name,extension,mime_type,size_bytes,content_sha256,storage_key,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (document_id, "cascade.txt", ".txt", "text/plain", 4, "c" * 64,
             db.new_id(), now, now),
        )
        conn.execute(
            "INSERT INTO knowledge_import_runs("
            "id,document_id,idempotency_key,trigger,created_at,updated_at)"
            " VALUES(?,?,?,'import',?,?)", (run_id, document_id, db.new_id(), now, now),
        )
        conn.execute(
            "INSERT INTO knowledge_import_events("
            "id,run_id,action,after_status,stage,created_at)"
            " VALUES(?,?,'queued','queued','validation',?)", (db.new_id(), run_id, now),
        )
        conn.commit()
        conn.execute("DELETE FROM knowledge_documents WHERE id=?", (document_id,))
        conn.commit()
        assert conn.execute(
            "SELECT COUNT(*) count FROM knowledge_import_runs WHERE id=?", (run_id,)
        ).fetchone()["count"] == 0
        assert conn.execute(
            "SELECT COUNT(*) count FROM knowledge_import_events WHERE run_id=?", (run_id,)
        ).fetchone()["count"] == 0
    finally:
        conn.execute("DELETE FROM knowledge_documents WHERE id=?", (document_id,))
        conn.commit()
        conn.close()


def test_schema_29_upgrades_existing_knowledge_rows_without_rewriting_them():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(
            "PRAGMA foreign_keys=ON;"
            "CREATE TABLE knowledge_documents(id TEXT PRIMARY KEY,original_name TEXT NOT NULL);"
            "CREATE TABLE knowledge_import_runs(id TEXT PRIMARY KEY,document_id TEXT NOT NULL,"
            "status TEXT NOT NULL,current_stage TEXT NOT NULL,created_at REAL NOT NULL);"
            "INSERT INTO knowledge_documents VALUES('doc','原文.md');"
            "INSERT INTO knowledge_import_runs VALUES('run','doc','queued','validation',1);"
        )
        migration = next(sql for version, sql in db.MIGRATIONS if version == 29)
        conn.executescript(migration)
        assert conn.execute(
            "SELECT original_name FROM knowledge_documents WHERE id='doc'"
        ).fetchone()["original_name"] == "原文.md"
        assert conn.execute(
            "SELECT status FROM knowledge_import_runs WHERE id='run'"
        ).fetchone()["status"] == "queued"
        assert "parsed_at" in {
            row["name"] for row in conn.execute("PRAGMA table_info(knowledge_documents)")
        }
    finally:
        conn.close()


def test_schema_30_adds_chunks_without_rewriting_existing_parsed_rows():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(
            "PRAGMA foreign_keys=ON;"
            "CREATE TABLE knowledge_documents(id TEXT PRIMARY KEY,chunk_count INTEGER NOT NULL DEFAULT 0);"
            "INSERT INTO knowledge_documents VALUES('doc',0);"
        )
        migration = next(sql for version, sql in db.MIGRATIONS if version == 30)
        conn.executescript(migration)
        row = conn.execute(
            "SELECT chunk_count,chunked_at FROM knowledge_documents WHERE id='doc'"
        ).fetchone()
        assert row["chunk_count"] == 0 and row["chunked_at"] is None
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='knowledge_chunks'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_schema_34_separates_vector_versions_and_keeps_events_body_free():
    conn = db.connect()
    try:
        tables = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'knowledge_embedding%'"
            )
        }
        assert {"knowledge_embedding_runs", "knowledge_embedding_events"} <= tables
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='knowledge_chunk_embeddings'"
        ).fetchone()[0] == 1
        document_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(knowledge_documents)")
        }
        assert {
            "embedding_version", "embedding_indexed_at", "embedding_dimension",
            "embedding_error_code",
        } <= document_columns
        event_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(knowledge_embedding_events)")
        }
        assert not ({"content", "query", "original_name", "path", "raw_output"} & event_columns)
        vector_fks = {
            (row["table"], row["on_delete"]) for row in conn.execute(
                "PRAGMA foreign_key_list(knowledge_chunk_embeddings)"
            )
        }
        assert {("knowledge_chunks", "CASCADE"), ("knowledge_documents", "CASCADE")} <= vector_fks
    finally:
        conn.close()
