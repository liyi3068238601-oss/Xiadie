"""F.7 文档管理、重建、删除账本和残留清理测试。"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import db, knowledge, knowledge_management, knowledge_search, knowledge_worker, llm
from app.main import app

client = TestClient(app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"})


@pytest.fixture(autouse=True)
def clean_management_data():
    db.init_db()
    conn = db.connect()
    try:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM knowledge_deletion_runs")
        conn.execute("DELETE FROM knowledge_documents")
        conn.commit()
    finally:
        conn.close()
    for directory in (knowledge.STORAGE_DIR, knowledge.PARSED_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        for path in directory.iterdir():
            if path.is_file():
                path.unlink()
    yield
    conn = db.connect()
    try:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM knowledge_deletion_runs")
        conn.execute("DELETE FROM knowledge_documents")
        conn.commit()
    finally:
        conn.close()


def _import(body: str, name: str = "资料.md") -> dict:
    return knowledge.import_file(name, "text/markdown", body.encode("utf-8"))


def _index(body: str = "# 星海\n星空是安静的。", name: str = "资料.md") -> dict:
    imported = _import(body, name)
    assert asyncio.run(knowledge_worker.process_due(limit=3)) == 3
    return imported


def _document(document_id: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM knowledge_documents WHERE id=?", (document_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def test_schema_33_deletion_ledger_has_no_filename_path_or_body():
    conn = db.connect()
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(knowledge_deletion_runs)")}
        event_columns = {row["name"] for row in conn.execute("PRAGMA table_info(knowledge_deletion_events)")}
        assert {"document_id", "content_sha256", "status", "error_code"} <= columns
        assert not ({"filename", "path", "content", "query"} & (columns | event_columns))
        assert conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == "39"
    finally:
        conn.close()


def test_tags_are_bounded_deduplicated_and_searchable_by_literal_filename():
    first = _index(name="同名%资料.md")["document"]
    second = _index(body="# 另一份\n完全不同的正文。", name="同名%资料.md")["document"]
    updated = knowledge_management.update_tags(first["id"], ["角色", "角色", "role"])
    assert updated["tags"] == ["角色", "role"]
    assert {item["id"] for item in knowledge.list_documents(query="%资料")} == {first["id"], second["id"]}
    assert knowledge.list_documents(query="_资料") == []
    with pytest.raises(knowledge.KnowledgeImportError):
        knowledge_management.update_tags(first["id"], ["x" * 41])


def test_reindex_exits_retrieval_then_rebuilds_from_managed_original():
    imported = _index()
    document_id = imported["document"]["id"]
    source = knowledge.storage_path_for(_document(document_id))
    original = source.read_bytes()
    run = knowledge_management.enqueue_reindex(document_id)
    assert run["trigger"] == "reindex" and run["status"] == "queued"
    assert knowledge_search.search("星空")["results"] == []
    assert asyncio.run(knowledge_worker.process_due(limit=3)) == 3
    rebuilt = _document(document_id)
    assert rebuilt["status"] == "indexed" and rebuilt["index_version"] == knowledge_search.INDEX_VERSION
    assert source.read_bytes() == original and knowledge_search.search("星空")["result_count"] == 1


def test_delete_immediately_exits_retrieval_and_clears_every_managed_derivative():
    imported = _index()
    document_id = imported["document"]["id"]
    document = _document(document_id)
    source = knowledge.storage_path_for(document)
    artifact = knowledge_worker.artifact_for_document(document_id)
    artifact_path = knowledge_worker.artifact_path_for(artifact)
    run = knowledge_management.enqueue_delete(document_id)
    assert run["status"] == "queued" and _document(document_id)["status"] == "delete_pending"
    assert knowledge_search.search("星空")["results"] == []
    assert knowledge_management.process_delete_due(limit=1) == 1
    assert not source.exists() and not artifact_path.exists() and _document(document_id) is None
    completed = knowledge_management.get_deletion_run(run["id"])
    assert completed["status"] == "completed"
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE document_id=?", (document_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM knowledge_chunks_fts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM knowledge_parse_artifacts WHERE document_id=?", (document_id,)).fetchone()[0] == 0
    finally:
        conn.close()


def test_delete_failure_stays_unsearchable_and_explicit_retry_finishes(monkeypatch):
    imported = _index()
    document_id = imported["document"]["id"]
    run = knowledge_management.enqueue_delete(document_id)
    original_unlink = knowledge_management._unlink_strict
    monkeypatch.setattr(
        knowledge_management, "_unlink_strict",
        lambda _path: (_ for _ in ()).throw(OSError("private path must not leak")),
    )
    assert knowledge_management.process_delete_due(limit=1) == 1
    failed = _document(document_id)
    assert failed["status"] == "delete_failed" and knowledge_search.search("星空")["results"] == []
    assert knowledge_management.get_deletion_run(run["id"])["error_code"] == "knowledge_delete_io_failed"
    monkeypatch.setattr(knowledge_management, "_unlink_strict", original_unlink)
    retried = knowledge_management.retry_delete(run["id"])
    assert retried["status"] == "queued"
    assert knowledge_management.process_delete_due(limit=1) == 1
    assert _document(document_id) is None


def test_delete_cancels_queued_import_and_keeps_body_out_of_events():
    imported = _import("绝不能进入删除审计的正文")
    document_id = imported["document"]["id"]
    run = knowledge_management.enqueue_delete(document_id)
    conn = db.connect()
    try:
        import_run = conn.execute(
            "SELECT status,error_code FROM knowledge_import_runs WHERE id=?", (imported["run"]["id"],),
        ).fetchone()
        assert tuple(import_run) == ("cancelled", "document_delete_requested")
        serialized = json.dumps(knowledge_management.get_deletion_run(run["id"]), ensure_ascii=False)
        assert "绝不能进入" not in serialized and imported["document"]["original_name"] not in serialized
    finally:
        conn.close()
    assert knowledge_management.process_delete_due(limit=1) == 1


def test_api_management_filters_tags_reindex_delete_and_audit(monkeypatch):
    imported = _index(name="同名资料.md")
    document_id = imported["document"]["id"]
    assert client.get("/api/knowledge/collections").json()[0]["id"] == "default"
    assert client.get("/api/knowledge/documents", params={"status": "indexed", "query": "同名"}).json()[0]["id"] == document_id
    tags = client.patch(f"/api/knowledge/documents/{document_id}/tags", json={"tags": ["设定"]})
    assert tags.status_code == 200 and tags.json()["tags"] == ["设定"]

    async def fake_stream(*_args):
        yield "引用回答 [资料:K1]"

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    session = client.post("/api/sessions", json={}).json()
    with client.stream("POST", "/api/chat", json={
        "session_id": session["id"], "content": "请根据文档告诉我星空",
    }) as response:
        "".join(response.iter_text())
    audits = client.get("/api/knowledge/retrievals", params={"session_id": session["id"]}).json()
    assert audits[0]["query_fingerprint"] and "query_sha256" not in audits[0]

    citation_id = client.get(f"/api/sessions/{session['id']}/messages").json()[-1]["knowledge_citations"][0]["id"]
    deletion = client.delete(f"/api/knowledge/documents/{document_id}")
    assert deletion.status_code == 202
    assert client.get(f"/api/knowledge/citations/{citation_id}").status_code == 410
    assert knowledge_management.process_delete_due(limit=1) == 1
    messages = client.get(f"/api/sessions/{session['id']}/messages").json()
    assert messages[-1]["knowledge_citations"][0]["id"] == citation_id
    assert client.get(f"/api/knowledge/citations/{citation_id}").status_code == 410
    assert client.get(f"/api/knowledge/deletion-runs/{deletion.json()['id']}").json()["status"] == "completed"
