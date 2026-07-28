import asyncio

import pytest
from fastapi.testclient import TestClient

from app import db, knowledge, knowledge_management, knowledge_search, knowledge_worker
from app.main import app

TOKEN = "test-token-with-at-least-thirty-two-bytes"
client = TestClient(app, headers={"X-Xiadie-Token": TOKEN})


def _index(text: str = "# Atomic rebuild\nold searchable marker") -> str:
    text = f"{text}\nunique-{db.new_id()}"
    result = knowledge.import_file(
        f"kig2-{db.new_id()}.md", "text/markdown", text.encode("utf-8"),
    )
    assert asyncio.run(knowledge_worker.process_due(limit=3)) == 3
    return result["document"]["id"]


def _document(document_id: str) -> dict:
    conn = db.connect()
    try:
        return dict(conn.execute("SELECT * FROM knowledge_documents WHERE id=?", (document_id,)).fetchone())
    finally:
        conn.close()


def test_schema_73_adds_staging_without_replacing_authoritative_tables():
    conn = db.connect()
    try:
        assert conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == "75"
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        document_columns = {row["name"] for row in conn.execute("PRAGMA table_info(knowledge_documents)")}
    finally:
        conn.close()
    assert "knowledge_rebuild_chunks" in tables
    assert {"governance_status", "rebuild_status", "active_index_revision"} <= document_columns
    assert "knowledge_documents_v2" not in tables and "knowledge_chunks_v2" not in tables


def test_rebuild_keeps_old_fts_until_single_transaction_switch():
    document_id = _index()
    before = _document(document_id)
    before_chunks = knowledge_worker.chunks_for_document(document_id)
    run = knowledge_management.enqueue_reindex(document_id)
    queued = _document(document_id)
    assert queued["status"] == "indexed" and queued["rebuild_status"] == "building"
    assert queued["index_version"] == before["index_version"]
    assert knowledge_search.search("old searchable marker", document_ids=[document_id])["result_count"] == 1

    assert asyncio.run(knowledge_worker.process_due(limit=1)) == 1
    assert knowledge_search.search("old searchable marker", document_ids=[document_id])["result_count"] == 1
    assert asyncio.run(knowledge_worker.process_due(limit=1)) == 1
    assert knowledge_search.search("old searchable marker", document_ids=[document_id])["result_count"] == 1
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM knowledge_rebuild_chunks WHERE run_id=?", (run["id"],)).fetchone()[0] > 0
    finally:
        conn.close()

    assert asyncio.run(knowledge_worker.process_due(limit=1)) == 1
    after = _document(document_id)
    assert after["status"] == "indexed" and after["rebuild_status"] == "idle"
    assert after["active_index_revision"] == before["active_index_revision"] + 1
    assert knowledge_search.search("old searchable marker", document_ids=[document_id])["result_count"] == 1
    assert [row["content_sha256"] for row in knowledge_worker.chunks_for_document(document_id)] == [
        row["content_sha256"] for row in before_chunks
    ]


def test_exhausted_rebuild_failure_preserves_active_chunks_and_fts(monkeypatch):
    document_id = _index()
    before = _document(document_id)
    before_chunk_ids = [row["id"] for row in knowledge_worker.chunks_for_document(document_id)]
    run = knowledge_management.enqueue_reindex(document_id)
    conn = db.connect()
    try:
        conn.execute("UPDATE knowledge_import_runs SET max_attempts=1 WHERE id=?", (run["id"],))
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(knowledge_worker.knowledge_parser, "parse",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("broken parser")))
    assert asyncio.run(knowledge_worker.process_due(limit=1)) == 1
    failed = _document(document_id)
    assert failed["status"] == "indexed" and failed["rebuild_status"] == "failed"
    assert failed["active_index_revision"] == before["active_index_revision"]
    assert [row["id"] for row in knowledge_worker.chunks_for_document(document_id)] == before_chunk_ids
    assert knowledge_search.search("old searchable marker", document_ids=[document_id])["result_count"] == 1


def test_archive_restore_and_impact_preview_control_retrieval_without_deleting():
    document_id = _index()
    preview = knowledge_management.impact_preview(document_id, action="archive")
    assert preview["removes_from_retrieval"] is True
    assert preview["preserves_original_file"] is True and preview["chunk_count"] > 0

    archived = client.patch(f"/api/knowledge/documents/{document_id}/archive", json={"archived": True})
    assert archived.status_code == 200 and archived.json()["governance_status"] == "archived"
    assert knowledge_search.search("old searchable marker", document_ids=[document_id])["results"] == []
    assert knowledge_worker.chunks_for_document(document_id)

    api_preview = client.get(
        f"/api/knowledge/documents/{document_id}/impact-preview", params={"action": "restore"},
    )
    assert api_preview.status_code == 200 and api_preview.json()["removes_from_retrieval"] is False
    restored = client.patch(f"/api/knowledge/documents/{document_id}/archive", json={"archived": False})
    assert restored.status_code == 200 and restored.json()["governance_status"] == "active"
    assert knowledge_search.search("old searchable marker", document_ids=[document_id])["result_count"] == 1


def test_lexical_search_survives_dense_unavailability(monkeypatch):
    _index()
    monkeypatch.setattr(knowledge_worker.knowledge_embeddings, "availability", lambda: {"available": False})
    result = knowledge_search.hybrid_search("old searchable marker")
    assert result["results"] and result["retrieval_mode"] == "fts"
