"""F.2 TXT/Markdown 安全准入、配额与原子本地副本。"""
import json
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app import db, knowledge
from app.main import app

client = TestClient(
    app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"}
)


@pytest.fixture(autouse=True)
def clean_knowledge_documents():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM knowledge_documents")
        conn.commit()
    finally:
        conn.close()
    knowledge.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    for path in knowledge.STORAGE_DIR.iterdir():
        if path.is_file():
            path.unlink()
    yield
    conn = db.connect()
    try:
        conn.execute("DELETE FROM knowledge_documents")
        conn.commit()
    finally:
        conn.close()
    for path in knowledge.STORAGE_DIR.iterdir():
        if path.is_file():
            path.unlink()


def test_import_copies_bytes_then_creates_queued_run_and_body_free_event():
    raw = b"\xef\xbb\xbf# Title\n\nhello"
    result = knowledge.import_file("笔记.md", "text/markdown", raw, sensitivity="sensitive")
    document, run = result["document"], result["run"]

    assert result["already_exists"] is False
    assert document["status"] == "queued"
    assert document["sensitivity"] == "sensitive"
    assert document["embedding_mode"] == "none"
    assert knowledge.storage_path_for(document).read_bytes() == raw
    assert run["status"] == "queued" and run["current_stage"] == "validation"
    conn = db.connect()
    try:
        event = conn.execute(
            "SELECT * FROM knowledge_import_events WHERE run_id=?", (run["id"],)
        ).fetchone()
        metadata = json.loads(event["metadata_json"])
        assert metadata == {"size_bytes": len(raw), "decoded_chars": 14}
        assert "笔记" not in event["metadata_json"] and "Title" not in event["metadata_json"]
    finally:
        conn.close()


def test_duplicate_hash_is_idempotent_but_same_name_different_content_is_allowed():
    first = knowledge.import_file("same.txt", "text/plain", b"one")
    duplicate = knowledge.import_file("renamed.txt", "text/plain", b"one")
    second = knowledge.import_file("same.txt", "text/plain", b"two")

    assert duplicate["already_exists"] is True
    assert duplicate["document"]["id"] == first["document"]["id"]
    assert duplicate["run"] is None
    assert second["document"]["id"] != first["document"]["id"]
    assert len(knowledge.list_documents()) == 2
    assert len(list(knowledge.STORAGE_DIR.iterdir())) == 2


@pytest.mark.parametrize(
    ("filename", "mime", "raw", "code"),
    [
        ("bad.pdf", "application/pdf", b"text", "pdf_signature_invalid"),
        ("bad.md", "application/pdf", b"text", "mime_type_mismatch"),
        ("bad.txt", "text/plain", b"\xff\xfeA\x00", "encoding_unsupported"),
        ("bad.txt", "text/plain", b"a\x00b", "binary_content_rejected"),
        ("../bad.txt", "text/plain", b"text", "filename_invalid"),
        ("empty.txt", "text/plain", b"", "file_empty"),
    ],
)
def test_validation_rejects_unsupported_or_malicious_inputs(filename, mime, raw, code):
    with pytest.raises(knowledge.KnowledgeImportError) as caught:
        knowledge.validate_file(filename, mime, raw)
    assert caught.value.code == code


def test_file_and_decoded_character_limits_accept_boundary_and_reject_one_over(monkeypatch):
    monkeypatch.setattr(knowledge, "MAX_FILE_BYTES", 6)
    assert knowledge.validate_file("a.txt", "text/plain", b"123456")["size_bytes"] == 6
    with pytest.raises(knowledge.KnowledgeImportError) as caught:
        knowledge.validate_file("a.txt", "text/plain", b"1234567")
    assert caught.value.code == "file_too_large"

    monkeypatch.setattr(knowledge, "MAX_FILE_BYTES", 100)
    monkeypatch.setattr(knowledge, "MAX_DECODED_CHARS", 2)
    assert knowledge.validate_file("a.txt", "text/plain", "甲乙".encode())["decoded_chars"] == 2
    with pytest.raises(knowledge.KnowledgeImportError) as caught:
        knowledge.validate_file("a.txt", "text/plain", "甲乙丙".encode())
    assert caught.value.code == "decoded_text_too_large"


def test_document_count_and_total_storage_quotas_are_checked_before_copy(monkeypatch):
    monkeypatch.setattr(knowledge, "MAX_DOCUMENTS", 1)
    knowledge.import_file("a.txt", "text/plain", b"one")
    with pytest.raises(knowledge.KnowledgeImportError) as caught:
        knowledge.import_file("b.txt", "text/plain", b"two")
    assert caught.value.code == "document_quota_exceeded"
    assert len(list(knowledge.STORAGE_DIR.iterdir())) == 1

    conn = db.connect()
    try:
        conn.execute("DELETE FROM knowledge_documents")
        conn.commit()
    finally:
        conn.close()
    for path in knowledge.STORAGE_DIR.iterdir():
        path.unlink()
    monkeypatch.setattr(knowledge, "MAX_DOCUMENTS", 100)
    monkeypatch.setattr(knowledge, "MAX_TOTAL_BYTES", 5)
    knowledge.import_file("a.txt", "text/plain", b"123")
    assert knowledge.import_file("b.txt", "text/plain", b"45")["already_exists"] is False
    with pytest.raises(knowledge.KnowledgeImportError) as caught:
        knowledge.import_file("c.txt", "text/plain", b"6")
    assert caught.value.code == "storage_quota_exceeded"


def test_delete_failed_file_still_counts_toward_physical_storage_quota(monkeypatch):
    document = knowledge.import_file("stuck.txt", "text/plain", b"123")["document"]
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE knowledge_documents SET status='delete_failed' WHERE id=?",
            (document["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(knowledge, "MAX_TOTAL_BYTES", 3)
    with pytest.raises(knowledge.KnowledgeImportError) as caught:
        knowledge.import_file("new.txt", "text/plain", b"4")
    assert caught.value.code == "storage_quota_exceeded"


def test_copy_failure_leaves_no_document_run_or_temporary_file(monkeypatch):
    def fail_copy(_path, _data):
        raise OSError("disk full")

    monkeypatch.setattr(knowledge, "_atomic_write", fail_copy)
    with pytest.raises(OSError):
        knowledge.import_file("fail.txt", "text/plain", b"content")
    assert knowledge.list_documents() == []
    assert list(knowledge.STORAGE_DIR.iterdir()) == []


def test_raw_import_api_is_token_guarded_stream_limited_and_lists_document(monkeypatch):
    response = client.post(
        "/api/knowledge/documents/import",
        content="你好".encode(),
        headers={
            "Content-Type": "text/plain",
            "X-Xiadie-Filename": quote("资料.txt"),
            "X-Xiadie-Sensitivity": "normal",
        },
    )
    assert response.status_code == 200
    assert response.json()["document"]["original_name"] == "资料.txt"
    assert "storage_key" not in response.json()["document"]
    assert "idempotency_key" not in response.json()["run"]
    assert client.get("/api/knowledge/documents").json()[0]["id"] == response.json()["document"]["id"]
    assert TestClient(app).post(
        "/api/knowledge/documents/import", content=b"no token",
        headers={"X-Xiadie-Filename": "bad.txt", "Content-Type": "text/plain"},
    ).status_code == 401

    monkeypatch.setattr(knowledge, "MAX_FILE_BYTES", 1)
    too_large = client.post(
        "/api/knowledge/documents/import", content=b"12",
        headers={"X-Xiadie-Filename": "large.txt", "Content-Type": "text/plain"},
    )
    assert too_large.status_code == 413


def test_document_transition_guard_rejects_skips_and_allows_declared_path():
    knowledge.assert_document_transition("staged", "queued")
    knowledge.assert_document_transition("queued", "parsing")
    knowledge.assert_document_transition("parsing", "indexed")
    with pytest.raises(knowledge.KnowledgeImportError) as caught:
        knowledge.assert_document_transition("staged", "indexed")
    assert caught.value.code == "document_transition_invalid"


def test_begin_immediate_serializes_concurrent_document_quota(monkeypatch):
    monkeypatch.setattr(knowledge, "MAX_DOCUMENTS", 1)

    def admit(item):
        name, raw = item
        try:
            return knowledge.import_file(name, "text/plain", raw)["already_exists"]
        except knowledge.KnowledgeImportError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(admit, [("a.txt", b"one"), ("b.txt", b"two")]))
    assert sorted(str(result) for result in results) == ["False", "document_quota_exceeded"]
    assert len(knowledge.list_documents()) == 1


def test_worker_wake_failure_does_not_undo_committed_import(monkeypatch):
    from app import knowledge_worker

    def fail_wake():
        raise RuntimeError("wake failed")

    monkeypatch.setattr(knowledge_worker, "wake_worker", fail_wake)
    result = knowledge.import_file("kept.txt", "text/plain", b"still committed")

    assert result["document"]["status"] == "queued"
    assert knowledge.storage_path_for(result["document"]).read_bytes() == b"still committed"
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM knowledge_documents WHERE id=?", (result["document"]["id"],)
        ).fetchone()[0] == 1
    finally:
        conn.close()
