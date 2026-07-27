"""F.8 local dense embeddings, hybrid fallback, and vector lifecycle tests."""
import asyncio

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import (
    db,
    knowledge,
    knowledge_embeddings,
    knowledge_management,
    knowledge_search,
    knowledge_worker,
)
from app.main import app
from tests.test_knowledge_formats import _pdf

client = TestClient(
    app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"},
)


class FakeProvider:
    provider_id = knowledge_embeddings.PROVIDER_ID
    model = knowledge_embeddings.MODEL_NAME
    version = knowledge_embeddings.EMBEDDING_VERSION
    dimension = knowledge_embeddings.DIMENSION

    def encode(self, texts: list[str]):
        vectors = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for index, value in enumerate(texts):
            # Deterministic stand-in for the local encoder: flower-like text and
            # star-like text occupy different axes, with a stable fallback axis.
            axis = 0 if any(word in value.casefold() for word in ("flower", "花")) else (
                1 if any(word in value.casefold() for word in ("star", "星")) else 2
            )
            vectors[index, axis] = 1.0
        return vectors


@pytest.fixture(autouse=True)
def clean_embedding_data(monkeypatch):
    conn = db.connect()
    try:
        conn.execute("DELETE FROM knowledge_documents")
        conn.execute(
            "INSERT INTO settings(key,value) VALUES('knowledge_local_embedding_enabled','1')"
            " ON CONFLICT(key) DO UPDATE SET value='1'"
        )
        conn.commit()
    finally:
        conn.close()
    for directory in (knowledge.STORAGE_DIR, knowledge.PARSED_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        for path in directory.iterdir():
            if path.is_file():
                path.unlink()
    monkeypatch.setattr(
        knowledge_embeddings,
        "availability",
        lambda: {"available": True, "local_only": True},
    )
    monkeypatch.setattr(knowledge_embeddings, "provider", lambda: FakeProvider())
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


def _index(raw: bytes = b"A quiet flower garden.", name: str = "garden.txt") -> str:
    imported = knowledge.import_file(name, "text/plain", raw)
    assert asyncio.run(knowledge_worker.process_due(limit=3)) == 3
    return imported["document"]["id"]


def _counts(document_id: str) -> tuple[int, int]:
    conn = db.connect()
    try:
        return (
            conn.execute(
                "SELECT COUNT(*) FROM knowledge_chunk_embeddings WHERE document_id=?", (document_id,),
            ).fetchone()[0],
            conn.execute(
                "SELECT COUNT(*) FROM knowledge_chunks_fts f JOIN knowledge_chunks c ON c.rowid=f.rowid"
                " WHERE c.document_id=?", (document_id,),
            ).fetchone()[0],
        )
    finally:
        conn.close()


def test_embedding_build_is_atomic_versioned_and_searchable():
    document_id = _index()
    run = knowledge_embeddings.enqueue(document_id)
    # Index completion already enqueues once; enqueue is idempotent while active.
    assert run and run["status"] == "queued"
    assert knowledge_embeddings.process_due(limit=1) == 1

    conn = db.connect()
    try:
        document = dict(conn.execute(
            "SELECT * FROM knowledge_documents WHERE id=?", (document_id,),
        ).fetchone())
        completed = dict(conn.execute(
            "SELECT * FROM knowledge_embedding_runs WHERE document_id=? ORDER BY created_at DESC LIMIT 1",
            (document_id,),
        ).fetchone())
    finally:
        conn.close()
    assert document["embedding_mode"] == "local"
    assert document["embedding_version"] == knowledge_embeddings.EMBEDDING_VERSION
    assert document["embedding_dimension"] == 1024
    assert completed["status"] == "completed" and completed["vector_count"] == document["chunk_count"]
    assert _counts(document_id) == (document["chunk_count"], document["chunk_count"])
    result = knowledge_embeddings.search("flower", limit=3)
    assert result["available"] and result["results"][0]["document_id"] == document_id


def test_auto_search_fuses_dense_and_fts_and_falls_back_when_dense_fails(monkeypatch):
    document_id = _index(b"The garden contains a rare flower and a bench.")
    knowledge_embeddings.process_due(limit=1)
    hybrid = knowledge_search.hybrid_search("flower", mode="auto")
    assert hybrid["retrieval_mode"] == "hybrid"
    assert hybrid["results"][0]["document_id"] == document_id
    assert hybrid["results"][0]["match_type"] == "hybrid"

    monkeypatch.setattr(knowledge_embeddings, "search", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError()))
    fallback = knowledge_search.hybrid_search("flower", mode="auto")
    assert fallback["retrieval_mode"] == "fts"
    assert fallback["vector_error_code"] == "embedding_search_failed"
    assert fallback["results"][0]["document_id"] == document_id


def test_reindex_keeps_active_vectors_until_switch_but_delete_removes_immediately():
    document_id = _index()
    knowledge_embeddings.process_due(limit=1)
    assert _counts(document_id)[0] > 0

    knowledge_management.enqueue_reindex(document_id)
    assert _counts(document_id)[0] > 0
    assert knowledge_embeddings.search("flower")["results"]
    assert asyncio.run(knowledge_worker.process_due(limit=3)) == 3
    assert _counts(document_id)[0] == 0 and _counts(document_id)[1] > 0
    knowledge_embeddings.process_due(limit=1)
    assert _counts(document_id)[0] > 0

    deletion = knowledge_management.enqueue_delete(document_id)
    assert deletion["status"] == "queued" and _counts(document_id) == (0, 0)
    assert knowledge_embeddings.search("flower")["results"] == []
    assert knowledge_management.process_delete_due(limit=1) == 1
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM knowledge_documents WHERE id=?", (document_id,),
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_embedding_failure_retries_without_breaking_fts(monkeypatch):
    document_id = _index()

    class BrokenProvider:
        def encode(self, _texts):
            raise knowledge_embeddings.EmbeddingError("embedding_test_failure", "private detail")

    monkeypatch.setattr(knowledge_embeddings, "provider", lambda: BrokenProvider())
    assert knowledge_embeddings.process_due(limit=1) == 1
    assert knowledge_search.search("flower")["result_count"] == 1
    assert knowledge_embeddings.process_due(limit=1) == 1
    conn = db.connect()
    try:
        run = dict(conn.execute(
            "SELECT * FROM knowledge_embedding_runs WHERE document_id=? ORDER BY created_at DESC LIMIT 1",
            (document_id,),
        ).fetchone())
        document = dict(conn.execute(
            "SELECT * FROM knowledge_documents WHERE id=?", (document_id,),
        ).fetchone())
    finally:
        conn.close()
    assert run["status"] == "failed" and run["error_code"] == "embedding_test_failure"
    assert document["status"] == "indexed" and document["embedding_error_code"] == "embedding_test_failure"
    assert _counts(document_id)[0] == 0


def test_remote_provider_requires_explicit_per_request_consent():
    remote = knowledge_embeddings.RemoteEmbeddingProvider()
    with pytest.raises(knowledge_embeddings.EmbeddingError) as error:
        remote.encode(["never send this"])
    assert error.value.code == "remote_embedding_consent_required"


def test_local_provider_rejects_a_model_that_does_not_match_its_version(tmp_path):
    (tmp_path / "onnx").mkdir()
    (tmp_path / "onnx" / "model_quantized.onnx").write_bytes(b"not-the-approved-model")
    with pytest.raises(knowledge_embeddings.EmbeddingError) as error:
        knowledge_embeddings.LocalBgeM3Provider(tmp_path)
    assert error.value.code == "embedding_model_hash_mismatch"


def test_api_e2e_pdf_to_hybrid_retrieval_and_verified_deletion():
    imported = client.post(
        "/api/knowledge/documents/import",
        content=_pdf("A flower grows on the first page.", "A star appears on the second page."),
        headers={
            "Content-Type": "application/pdf",
            "X-Xiadie-Filename": "e2e-pages.pdf",
            "X-Xiadie-Collection": "default",
            "X-Xiadie-Sensitivity": "normal",
        },
    )
    assert imported.status_code == 200
    document_id = imported.json()["document"]["id"]
    assert asyncio.run(knowledge_worker.process_due(limit=3)) == 3
    assert knowledge_embeddings.process_due(limit=1) == 1

    listed = client.get("/api/knowledge/documents")
    assert listed.status_code == 200
    document = next(row for row in listed.json() if row["id"] == document_id)
    assert document["page_count"] == 2
    assert document["latest_embedding"]["status"] == "completed"
    assert document["embedding_dimension"] == 1024

    searched = client.post(
        "/api/knowledge/search", json={"query": "flower", "mode": "auto", "limit": 3},
    )
    assert searched.status_code == 200
    result = searched.json()
    assert result["retrieval_mode"] == "hybrid"
    assert result["results"][0]["document_id"] == document_id
    assert result["results"][0]["page_start"] == 1

    deleted = client.delete(f"/api/knowledge/documents/{document_id}")
    assert deleted.status_code == 202
    assert knowledge_embeddings.search("flower")["results"] == []
    assert knowledge_management.process_delete_due(limit=1) == 1
    assert all(row["id"] != document_id for row in client.get("/api/knowledge/documents").json())
