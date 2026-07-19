"""K.1 文档远传策略、Provider 位置和 schema 35 升级测试。"""
import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db, knowledge, knowledge_policy, knowledge_worker
from app.main import app


client = TestClient(app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"})


@pytest.fixture(autouse=True)
def clean_policy_documents():
    db.init_db()
    conn = db.connect()
    try:
        conn.execute("DELETE FROM knowledge_documents")
        conn.commit()
    finally:
        conn.close()
    yield
    conn = db.connect()
    try:
        conn.execute("DELETE FROM knowledge_documents")
        conn.commit()
    finally:
        conn.close()
    for directory in (knowledge.STORAGE_DIR, knowledge.PARSED_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        for path in directory.iterdir():
            if path.is_file():
                path.unlink()


def _import(*, sensitivity: str = "normal") -> dict:
    marker = "敏感" if sensitivity == "sensitive" else "普通"
    result = knowledge.import_file(
        f"{marker}策略.md", "text/markdown", f"# {marker}策略\n只用于虚构测试。".encode(),
        sensitivity=sensitivity,
    )
    asyncio.run(knowledge_worker.process_due(limit=3))
    return result["document"]


def test_schema_35_upgrades_old_rows_with_conservative_defaults():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(
            "PRAGMA foreign_keys=ON;"
            "CREATE TABLE providers(id TEXT PRIMARY KEY,base_url TEXT NOT NULL);"
            "INSERT INTO providers VALUES('deepseek','https://api.deepseek.com/v1');"
            "INSERT INTO providers VALUES('ollama','http://127.0.0.1:11434/v1');"
            "INSERT INTO providers VALUES('custom','http://localhost:9000/v1');"
            "CREATE TABLE knowledge_documents("
            "id TEXT PRIMARY KEY,sensitivity TEXT NOT NULL,updated_at REAL NOT NULL);"
            "INSERT INTO knowledge_documents VALUES('normal','normal',10);"
            "INSERT INTO knowledge_documents VALUES('secret','sensitive',11);"
        )
        migration = next(sql for version, sql in db.MIGRATIONS if version == 35)
        conn.executescript(migration)
        documents = conn.execute(
            "SELECT id,transmission_policy,policy_revision,policy_updated_at "
            "FROM knowledge_documents ORDER BY id"
        ).fetchall()
        assert [(row["transmission_policy"], row["policy_revision"]) for row in documents] == [
            ("ask_each_time", 1), ("ask_each_time", 1),
        ]
        assert [row["policy_updated_at"] for row in documents] == [10, 11]
        locations = {
            row["id"]: row["execution_location"]
            for row in conn.execute("SELECT id,execution_location FROM providers")
        }
        assert locations == {"deepseek": "remote", "ollama": "local", "custom": "unknown"}
    finally:
        conn.close()


def test_schema_35_is_repeatable_and_policy_events_are_body_free():
    db.init_db()
    db.init_db()
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "40"
        columns = {
            row["name"] for row in conn.execute(
                "PRAGMA table_info(knowledge_document_policy_events)"
            )
        }
        assert {"document_id", "before_policy", "after_policy", "policy_revision"} <= columns
        assert not ({"content", "query", "path", "token", "original_name"} & columns)
    finally:
        conn.close()


def test_import_defaults_and_sensitive_constraint_are_conservative():
    normal = _import()
    sensitive = _import(sensitivity="sensitive")
    assert normal["transmission_policy"] == "ask_each_time"
    assert sensitive["transmission_policy"] == "local_only"
    conn = db.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE knowledge_documents SET transmission_policy='remote_allowed' WHERE id=?",
                (sensitive["id"],),
            )
        conn.rollback()
    finally:
        conn.close()


def test_duplicate_reimport_can_only_upgrade_to_sensitive_local_only():
    data = "# 重复资料\n同一份内容。".encode()
    first = knowledge.import_file("普通.md", "text/markdown", data)["document"]
    assert first["sensitivity"] == "normal" and first["transmission_policy"] == "ask_each_time"
    upgraded = knowledge.import_file(
        "敏感.md", "text/markdown", data, sensitivity="sensitive",
    )
    assert upgraded["already_exists"] is True
    assert upgraded["document"]["id"] == first["id"]
    assert upgraded["document"]["sensitivity"] == "sensitive"
    assert upgraded["document"]["transmission_policy"] == "local_only"
    assert upgraded["document"]["policy_revision"] == first["policy_revision"] + 1
    events = knowledge_policy.list_document_policy_events(first["id"])
    assert events and events[0]["reason_code"] == "sensitivity_upgrade"

    repeated_normal = knowledge.import_file("普通.md", "text/markdown", data)
    assert repeated_normal["document"]["sensitivity"] == "sensitive"
    assert repeated_normal["document"]["transmission_policy"] == "local_only"


def test_document_policy_api_revises_audits_and_rejects_sensitive_remote():
    normal = _import()
    response = client.patch(
        f"/api/knowledge/documents/{normal['id']}/transmission-policy",
        json={"transmission_policy": "remote_allowed"},
    )
    assert response.status_code == 200
    assert response.json()["policy_revision"] == 2
    repeated = client.patch(
        f"/api/knowledge/documents/{normal['id']}/transmission-policy",
        json={"transmission_policy": "remote_allowed"},
    )
    assert repeated.status_code == 200 and repeated.json()["policy_revision"] == 2
    events = client.get(f"/api/knowledge/documents/{normal['id']}/policy-events").json()
    assert len(events) == 1
    assert events[0]["before_policy"] == "ask_each_time"
    assert events[0]["after_policy"] == "remote_allowed"
    assert "content" not in events[0] and "query" not in events[0]

    sensitive = _import(sensitivity="sensitive")
    blocked = client.patch(
        f"/api/knowledge/documents/{sensitive['id']}/transmission-policy",
        json={"transmission_policy": "remote_allowed"},
    )
    assert blocked.status_code == 400


def test_provider_location_is_conservative_and_url_changes_revision():
    assert knowledge_policy.automatic_provider_location("deepseek", "http://127.0.0.1:9") == "remote"
    assert knowledge_policy.automatic_provider_location("custom", "http://127.0.0.1:9") == "unknown"
    assert knowledge_policy.automatic_provider_location("ollama", "http://127.0.0.1:11434/v1") == "local"
    assert knowledge_policy.automatic_provider_location("ollama", "https://example.com/v1") == "remote"
    assert not knowledge_policy.is_loopback_url("http://user@127.0.0.1:11434/v1")
    assert knowledge_policy.requires_remote_controls("unknown")
    assert knowledge_policy.requires_remote_controls("remote")
    assert not knowledge_policy.requires_remote_controls("local")

    before = next(item for item in client.get("/api/providers").json() if item["id"] == "ollama")
    changed = client.patch(
        "/api/providers/ollama", json={"base_url": "https://example.com/v1"},
    )
    assert changed.status_code == 200
    assert changed.json()["execution_location"] == "remote"
    assert changed.json()["location_revision"] == before["location_revision"] + 1
    blocked = client.patch(
        "/api/providers/ollama",
        json={"base_url": "https://example.com/v1", "execution_location": "local"},
    )
    assert blocked.status_code == 400
    restored = client.patch(
        "/api/providers/ollama",
        json={"base_url": "http://127.0.0.1:11434/v1", "execution_location": "local"},
    )
    assert restored.status_code == 200 and restored.json()["execution_location"] == "local"
