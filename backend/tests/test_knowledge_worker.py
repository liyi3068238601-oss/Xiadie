"""F.3 本地解析 worker、取消、重试、陈旧恢复与事件时间线。"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import db, knowledge, knowledge_chunker, knowledge_parser, knowledge_worker
from app.main import app

client = TestClient(
    app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"}
)


@pytest.fixture(autouse=True)
def clean_knowledge_worker_data():
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
    yield
    conn = db.connect()
    try:
        conn.execute("DELETE FROM knowledge_documents")
        conn.commit()
    finally:
        conn.close()
    for directory in (knowledge.STORAGE_DIR, knowledge.PARSED_DIR):
        for path in directory.iterdir():
            if path.is_file():
                path.unlink()


def _import(name="notes.md", raw=b"# One\r\ntext\r\n## Two\nmore"):
    return knowledge.import_file(name, "text/markdown" if name.endswith(".md") else "text/plain", raw)


def _db_row(table: str, object_id: str):
    conn = db.connect()
    try:
        return dict(conn.execute(f"SELECT * FROM {table} WHERE id=?", (object_id,)).fetchone())
    finally:
        conn.close()


def test_parser_normalizes_newlines_extracts_markdown_headings_and_ignores_fences():
    raw = b"# Top\r\n```md\r\n## Not a heading\r\n```\r### Child ###\rBody"
    first = knowledge_parser.parse(raw, extension=".md")
    second = knowledge_parser.parse(raw, extension=".md")
    assert first == second
    assert "\r" not in first["normalized_text"]
    assert first["headings"] == [
        {"level": 1, "title": "Top", "line": 1},
        {"level": 3, "title": "Child", "line": 5},
    ]
    assert knowledge_parser.parse(b"# plain", extension=".txt")["headings"] == []


def test_worker_parses_to_private_artifact_then_queues_chunking_without_indexing():
    imported = _import()
    assert asyncio.run(knowledge_worker.process_due(limit=1)) == 1
    document = _db_row("knowledge_documents", imported["document"]["id"])
    run = knowledge_worker.get_run(imported["run"]["id"])
    artifact = knowledge_worker.artifact_for_document(document["id"])

    assert document["status"] == "parsing"
    assert document["parsed_at"] is not None
    assert document["parser_version"] == knowledge_parser.parser_version_for(".md")
    assert document["parse_heading_count"] == 2
    assert document["indexed_at"] is None
    assert run["status"] == "queued" and run["current_stage"] == "chunking"
    assert run["progress"] == knowledge_worker.PARSED_PROGRESS
    payload = json.loads(knowledge_worker.artifact_path_for(artifact).read_text("utf-8"))
    assert payload["normalized_text"] == "# One\ntext\n## Two\nmore"
    assert [event["action"] for event in run["events"]] == [
        "admitted", "parsing_started", "parsing_completed",
    ]
    serialized = json.dumps(run["events"], ensure_ascii=False)
    assert "# One" not in serialized and "notes.md" not in serialized


def test_queued_cancel_is_terminal_and_creates_no_parse_artifact():
    imported = _import()
    cancelled = knowledge_worker.cancel(imported["run"]["id"])
    assert cancelled["status"] == "cancelled"
    assert _db_row("knowledge_documents", imported["document"]["id"])["status"] == "cancelled"
    assert knowledge_worker.artifact_for_document(imported["document"]["id"]) is None
    assert asyncio.run(knowledge_worker.process_due(limit=1)) == 0


def test_running_cancel_is_observed_at_parser_checkpoint(monkeypatch):
    imported = _import()
    original = knowledge_parser.parse

    def cancel_during_parse(data, *, extension):
        requested = knowledge_worker.cancel(imported["run"]["id"])
        assert requested["status"] == "cancel_requested"
        return original(data, extension=extension)

    monkeypatch.setattr(knowledge_parser, "parse", cancel_during_parse)
    assert asyncio.run(knowledge_worker.process_due(limit=1)) == 1
    assert knowledge_worker.get_run(imported["run"]["id"])["status"] == "cancelled"
    assert _db_row("knowledge_documents", imported["document"]["id"])["status"] == "cancelled"
    assert list(knowledge.PARSED_DIR.iterdir()) == []


def test_failures_back_off_then_exhaust_without_raw_error_leak(monkeypatch):
    imported = _import()

    def fail_parse(_data, *, extension):
        raise OSError(f"private path and body {extension}")

    monkeypatch.setattr(knowledge_parser, "parse", fail_parse)
    for attempt in range(1, 4):
        assert asyncio.run(knowledge_worker.process_due(limit=1)) == 1
        run = knowledge_worker.get_run(imported["run"]["id"])
        assert run["attempt_count"] == attempt
        if attempt < 3:
            assert run["status"] == "recovery_pending"
            conn = db.connect()
            try:
                conn.execute(
                    "UPDATE knowledge_import_runs SET next_attempt_at=0 WHERE id=?",
                    (run["id"],),
                )
                conn.commit()
            finally:
                conn.close()
        else:
            assert run["status"] == "failed"
    document = _db_row("knowledge_documents", imported["document"]["id"])
    assert document["status"] == "failed"
    serialized = json.dumps(run["events"], ensure_ascii=False)
    assert "private path" not in serialized and "body" not in serialized


def test_stale_running_recovers_once_and_stale_cancel_finishes():
    imported = _import()
    old = db.now() - knowledge_worker.RUNNING_STALE_SECONDS - 1
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE knowledge_import_runs SET status='running',current_stage='parsing',"
            "attempt_count=1,updated_at=? WHERE id=?", (old, imported["run"]["id"]),
        )
        conn.execute(
            "UPDATE knowledge_documents SET status='parsing' WHERE id=?",
            (imported["document"]["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    assert knowledge_worker.recover_stale_runs() == 1
    assert knowledge_worker.recover_stale_runs() == 0
    assert knowledge_worker.get_run(imported["run"]["id"])["status"] == "recovery_pending"

    conn = db.connect()
    try:
        conn.execute(
            "UPDATE knowledge_import_runs SET status='cancel_requested',updated_at=? WHERE id=?",
            (old, imported["run"]["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    assert knowledge_worker.recover_stale_runs() == 1
    assert knowledge_worker.get_run(imported["run"]["id"])["status"] == "cancelled"


def test_run_api_returns_event_timeline_without_internal_idempotency_key():
    imported = _import()
    response = client.get(f"/api/knowledge/import-runs/{imported['run']['id']}")
    assert response.status_code == 200
    assert "idempotency_key" not in response.json()
    assert response.json()["events"][0]["action"] == "admitted"
    cancelled = client.post(f"/api/knowledge/import-runs/{imported['run']['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"


def test_cancel_after_parsing_removes_artifact_but_keeps_original_copy():
    imported = _import()
    asyncio.run(knowledge_worker.process_due(limit=1))
    artifact = knowledge_worker.artifact_for_document(imported["document"]["id"])
    artifact_path = knowledge_worker.artifact_path_for(artifact)
    original_path = knowledge.storage_path_for(imported["document"])
    assert artifact_path.exists() and original_path.exists()

    cancelled = knowledge_worker.cancel(imported["run"]["id"])
    assert cancelled["status"] == "cancelled"
    assert not artifact_path.exists()
    assert original_path.exists()
    assert knowledge_worker.artifact_for_document(imported["document"]["id"]) is None


def test_source_copy_hash_mismatch_retries_without_parse_artifact():
    imported = _import()
    knowledge.storage_path_for(imported["document"]).write_bytes(b"tampered")
    asyncio.run(knowledge_worker.process_due(limit=1))
    run = knowledge_worker.get_run(imported["run"]["id"])
    assert run["status"] == "recovery_pending"
    assert run["error_code"] == "source_hash_mismatch"
    assert knowledge_worker.artifact_for_document(imported["document"]["id"]) is None
    assert list(knowledge.PARSED_DIR.iterdir()) == []


def test_schema_29_parse_artifact_is_metadata_only_and_cascades():
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()["value"] == "60"
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(knowledge_parse_artifacts)")
        }
        assert {"artifact_key", "normalized_sha256", "char_count", "heading_count"} <= columns
        assert not ({"content", "normalized_text", "filename", "path"} & columns)
    finally:
        conn.close()


def test_worker_atomically_chunks_then_waits_for_indexing_without_claiming_indexed():
    imported = _import(raw=b"# One\nbody\n\n## Two\nmore")
    assert asyncio.run(knowledge_worker.process_due(limit=2)) == 2
    document = _db_row("knowledge_documents", imported["document"]["id"])
    run = knowledge_worker.get_run(imported["run"]["id"])
    chunks = knowledge_worker.chunks_for_document(document["id"])

    assert document["status"] == "parsing"
    assert document["indexed_at"] is None
    assert document["chunked_at"] is not None
    assert document["chunker_version"] == knowledge_chunker.CHUNKER_VERSION
    assert document["chunk_count"] == len(chunks) == 2
    assert run["status"] == "queued" and run["current_stage"] == "indexing"
    assert run["progress"] == knowledge_worker.CHUNKED_PROGRESS
    assert [event["action"] for event in run["events"]][-2:] == [
        "chunking_started", "chunking_completed",
    ]
    assert all(chunk["page_start"] is None and chunk["page_end"] is None for chunk in chunks)


def test_chunk_ids_and_locators_remain_stable_when_same_document_is_rechunked():
    imported = _import(raw=b"# Stable\nbody\n\nsecond")
    asyncio.run(knowledge_worker.process_due(limit=2))
    first = knowledge_worker.chunks_for_document(imported["document"]["id"])
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE knowledge_import_runs SET status='queued',current_stage='chunking',"
            "attempt_count=0 WHERE id=?", (imported["run"]["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    asyncio.run(knowledge_worker.process_due(limit=1))
    second = knowledge_worker.chunks_for_document(imported["document"]["id"])
    stable_fields = (
        "id", "ordinal", "content", "content_sha256", "heading_path_json", "paragraph_start",
        "paragraph_end", "line_start", "line_end", "char_start", "char_end", "page_start", "page_end",
    )
    assert [[row[key] for key in stable_fields] for row in first] == [
        [row[key] for key in stable_fields] for row in second
    ]


def test_cancel_during_chunk_loop_cleans_chunks_and_private_artifact(monkeypatch):
    imported = _import(raw=b"paragraph one\n\nparagraph two")
    asyncio.run(knowledge_worker.process_due(limit=1))
    original = knowledge_chunker.chunk_artifact

    def cancel_then_chunk(payload, *, should_cancel=None):
        assert knowledge_worker.cancel(imported["run"]["id"])["status"] == "cancel_requested"
        return original(payload, should_cancel=should_cancel)

    monkeypatch.setattr(knowledge_chunker, "chunk_artifact", cancel_then_chunk)
    asyncio.run(knowledge_worker.process_due(limit=1))
    document = _db_row("knowledge_documents", imported["document"]["id"])
    assert knowledge_worker.get_run(imported["run"]["id"])["status"] == "cancelled"
    assert document["status"] == "cancelled" and document["chunk_count"] == 0
    assert document["parsed_at"] is None and document["chunked_at"] is None
    assert knowledge_worker.chunks_for_document(document["id"]) == []
    assert knowledge_worker.artifact_for_document(document["id"]) is None


def test_tampered_private_artifact_retries_without_committing_chunks():
    imported = _import()
    asyncio.run(knowledge_worker.process_due(limit=1))
    artifact = knowledge_worker.artifact_for_document(imported["document"]["id"])
    artifact_path = knowledge_worker.artifact_path_for(artifact)
    payload = json.loads(artifact_path.read_text("utf-8"))
    payload["headings"][0]["title"] = "tampered heading path"
    artifact_path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    asyncio.run(knowledge_worker.process_due(limit=1))
    run = knowledge_worker.get_run(imported["run"]["id"])
    assert run["status"] == "recovery_pending"
    assert run["current_stage"] == "chunking"
    assert run["error_code"] == "parse_artifact_invalid"
    assert knowledge_worker.chunks_for_document(imported["document"]["id"]) == []


def test_chunk_rows_cascade_with_document_and_schema_contains_locator_contract():
    imported = _import()
    asyncio.run(knowledge_worker.process_due(limit=2))
    document_id = imported["document"]["id"]
    conn = db.connect()
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(knowledge_chunks)")}
        assert {
            "ordinal", "content", "content_sha256", "heading_path_json", "paragraph_start",
            "paragraph_end", "line_start", "line_end", "char_start", "char_end",
            "page_start", "page_end", "chunker_version",
        } <= columns
        conn.execute("DELETE FROM knowledge_documents WHERE id=?", (document_id,))
        conn.commit()
        assert conn.execute(
            "SELECT COUNT(*) FROM knowledge_chunks WHERE document_id=?", (document_id,)
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_chunk_transaction_rolls_back_as_a_unit_and_unexpected_log_hides_details(caplog):
    imported = _import(raw=(b"A" * 1300))
    asyncio.run(knowledge_worker.process_due(limit=1))
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TRIGGER reject_second_chunk BEFORE INSERT ON knowledge_chunks "
            "WHEN NEW.ordinal=1 BEGIN SELECT RAISE(ABORT,'private body and path'); END"
        )
        conn.commit()
    finally:
        conn.close()
    try:
        asyncio.run(knowledge_worker.process_due(limit=1))
    finally:
        conn = db.connect()
        try:
            conn.execute("DROP TRIGGER IF EXISTS reject_second_chunk")
            conn.commit()
        finally:
            conn.close()
    document = _db_row("knowledge_documents", imported["document"]["id"])
    run = knowledge_worker.get_run(imported["run"]["id"])
    assert knowledge_worker.chunks_for_document(document["id"]) == []
    assert document["chunked_at"] is None and document["chunk_count"] == 0
    assert run["status"] == "recovery_pending" and run["error_code"] == "knowledge_chunk_failed"
    assert "private body" not in caplog.text and "path" not in caplog.text


def test_idle_maintenance_sweeps_expired_grant_states_without_deleting_rows(monkeypatch):
    calls = []
    monkeypatch.setattr(
        knowledge_worker.knowledge_grants, "expire_due",
        lambda *, limit: calls.append(limit) or 3,
    )
    assert knowledge_worker.expire_grants_once() == 3
    assert calls == [100]
