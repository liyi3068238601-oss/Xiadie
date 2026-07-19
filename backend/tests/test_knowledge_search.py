"""F.5 contentless FTS、原子索引与受限检索测试。"""
import asyncio
import hashlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db, knowledge, knowledge_embeddings, knowledge_search, knowledge_worker
from app.main import app

client = TestClient(
    app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"}
)


@pytest.fixture(autouse=True)
def clean_knowledge_index_data():
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


def _import(raw: bytes, name: str = "knowledge.md") -> dict:
    return knowledge.import_file(
        name, "text/markdown" if name.endswith(".md") else "text/plain", raw,
    )


def _index(raw: bytes, name: str = "knowledge.md") -> dict:
    imported = _import(raw, name)
    assert asyncio.run(knowledge_worker.process_due(limit=3)) == 3
    return imported


def _row(table: str, object_id: str) -> dict:
    conn = db.connect()
    try:
        return dict(conn.execute(f"SELECT * FROM {table} WHERE id=?", (object_id,)).fetchone())
    finally:
        conn.close()


def _fts_count() -> int:
    conn = db.connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM knowledge_chunks_fts").fetchone()[0]
    finally:
        conn.close()


def test_worker_builds_contentless_index_atomically_then_marks_document_indexed():
    imported = _index("# 星海\n遐蝶喜欢星空与花海。".encode())
    document = _row("knowledge_documents", imported["document"]["id"])
    run = knowledge_worker.get_run(imported["run"]["id"])

    assert document["status"] == "indexed" and document["indexed_at"] is not None
    assert document["index_version"] == knowledge_search.INDEX_VERSION
    assert run["status"] == "completed" and run["current_stage"] == "finalizing"
    assert run["progress"] == 100
    assert [event["action"] for event in run["events"]][-2:] == [
        "indexing_started", "indexing_completed",
    ]
    assert _fts_count() == document["chunk_count"]
    conn = db.connect()
    try:
        assert conn.execute("SELECT terms FROM knowledge_chunks_fts LIMIT 1").fetchone()[0] is None
    finally:
        conn.close()


def test_search_supports_chinese_bigrams_english_words_and_document_filtering():
    chinese = _index("遐蝶喜欢星空与花海，也会守护记忆。".encode(), "中文.md")
    english = _index(b"Memory retrieval keeps a stable locator.", "english.txt")
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE knowledge_documents SET tags_json=? WHERE id=?",
            (json.dumps(["角色资料", "英文"], ensure_ascii=False), english["document"]["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    result = knowledge_search.search("星空")
    assert [item["document_id"] for item in result["results"]] == [chinese["document"]["id"]]
    assert result["results"][0]["content_sha256"]
    assert knowledge_search.search("蝶")["result_count"] == 1
    assert knowledge_search.search(
        "memory retrieval", document_ids=[english["document"]["id"]]
    )["results"][0]["original_name"] == "english.txt"
    tagged = knowledge_search.search("memory", tags=["英文"])["results"]
    assert tagged[0]["tags"] == ["角色资料", "英文"]
    assert knowledge_search.search("memory", tags=["不存在"])["results"] == []
    assert knowledge_search.search(
        "星空", document_ids=[english["document"]["id"]]
    )["results"] == []
    assert knowledge_search.search("星空", collection_id="missing")["results"] == []


def test_only_indexed_documents_in_active_collections_are_visible():
    imported = _index("仅在有效索引中出现的星槎资料。".encode())
    document_id = imported["document"]["id"]
    assert knowledge_search.search("星槎")["result_count"] == 1
    conn = db.connect()
    try:
        conn.execute("UPDATE knowledge_documents SET status='parsing' WHERE id=?", (document_id,))
        conn.commit()
        assert knowledge_search.search("星槎")["results"] == []
        conn.execute("UPDATE knowledge_documents SET status='indexed' WHERE id=?", (document_id,))
        conn.execute("UPDATE knowledge_documents SET index_version='stale' WHERE id=?", (document_id,))
        conn.commit()
        assert knowledge_search.search("星槎")["results"] == []
        conn.execute(
            "UPDATE knowledge_documents SET index_version=? WHERE id=?",
            (knowledge_search.INDEX_VERSION, document_id),
        )
        conn.execute("UPDATE knowledge_collections SET status='disabled' WHERE id='default'")
        conn.commit()
        assert knowledge_search.search("星槎")["results"] == []
    finally:
        conn.execute("UPDATE knowledge_collections SET status='active' WHERE id='default'")
        conn.commit()
        conn.close()


def test_context_window_keeps_each_neighbor_locator_and_deduplicates_results():
    imported = _index(
        "# 第一节\n邻居甲\n# 第二节\n唯一检索词星轨核心\n# 第三节\n邻居乙".encode()
    )
    result = knowledge_search.search("星轨核心", context_window=1, max_chars=2000)
    assert [item["match_type"] for item in result["results"]] == [
        "primary", "context", "context",
    ]
    assert len({item["chunk_id"] for item in result["results"]}) == 3
    assert all(item["document_id"] == imported["document"]["id"] for item in result["results"])
    primary = result["results"][0]
    assert all(item["context_of"] == primary["chunk_id"] for item in result["results"][1:])
    assert [item["ordinal"] for item in result["results"]] == [1, 0, 2]


def test_search_budget_and_query_validation_are_bounded_and_syntax_safe():
    _index(("可检索预算内容。" * 80).encode(), "budget.txt")
    result = knowledge_search.search("检索预算", max_chars=300, limit=12)
    assert result["used_chars"] <= 300
    assert knowledge_search.search('" OR * malicious')["results"] == []
    with pytest.raises(knowledge_search.SearchError):
        knowledge_search.search("***")
    with pytest.raises(knowledge_search.SearchError):
        knowledge_search.search("a" * 257)
    with pytest.raises(knowledge_search.SearchError):
        knowledge_search.search("valid", document_ids=[str(i) for i in range(21)])
    with pytest.raises(knowledge_search.SearchError):
        knowledge_search.search("valid", tags=[str(i) for i in range(11)])


def test_hybrid_search_allows_vector_only_when_fts_has_no_terms(monkeypatch):
    monkeypatch.setattr(knowledge_embeddings, "search", lambda *_a, **_k: {
        "results": [], "available": True, "error_code": None,
    })
    result = knowledge_search.hybrid_search("🦋🦋")
    assert result["retrieval_mode"] == "vector"
    assert result["vector_available"] is True

    monkeypatch.setattr(knowledge_embeddings, "search", lambda *_a, **_k: {
        "results": [], "available": False, "error_code": "embedding_unavailable",
    })
    degraded = knowledge_search.hybrid_search("🦋🦋")
    assert degraded["retrieval_mode"] == "fts_unavailable"


def test_hybrid_search_clusters_exact_duplicates_and_overlapping_neighbors(monkeypatch):
    base = {
        "document_id": "doc-a", "ordinal": 0, "content": "星港删除规则" * 20,
        "content_sha256": "a" * 64, "match_type": "primary", "rank": -1.0,
    }
    duplicate = {**base, "chunk_id": "chunk-b", "document_id": "doc-b"}
    first = {**base, "chunk_id": "chunk-a"}
    neighbor_content = "星港删除规则" * 19 + "补充"
    neighbor = {**base, "chunk_id": "chunk-c", "ordinal": 1, "content": neighbor_content,
                "content_sha256": hashlib.sha256(neighbor_content.encode()).hexdigest()}
    monkeypatch.setattr(knowledge_search, "search", lambda *_a, **_k: {
        "results": [first, duplicate, neighbor], "result_count": 3,
    })
    monkeypatch.setattr(knowledge_embeddings, "search", lambda *_a, **_k: {
        "results": [], "available": False, "error_code": "embedding_unavailable",
    })
    result = knowledge_search.hybrid_search("星港删除")
    assert [item["chunk_id"] for item in result["results"]] == ["chunk-a"]
    assert result["results"][0]["duplicate_document_ids"] == ["doc-a", "doc-b"]
    assert result["diagnostics"]["exact_duplicates_removed"] == 1
    assert result["diagnostics"]["adjacent_duplicates_removed"] == 1


def test_k7_reranks_full_pool_then_enforces_strict_source_diversity(monkeypatch):
    def candidate(index: int, collection: str, document: str, content: str) -> dict:
        return {
            "chunk_id": f"chunk-{index}", "document_id": document,
            "collection_id": collection, "original_name": f"{document}.md",
            "ordinal": index, "content": content,
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "heading_path": ["重试预算"] if index == 4 else [],
            "paragraph_start": index, "line_start": index, "char_start": index,
            "page_start": None, "match_type": "primary", "rank": float(index),
        }

    rows = [
        candidate(0, "collection-a", "doc-a", "网关说明一"),
        candidate(1, "collection-a", "doc-a", "网关说明二"),
        candidate(2, "collection-a", "doc-a", "网关说明三"),
        candidate(3, "collection-b", "doc-b", "另一份网关说明"),
        candidate(4, "collection-c", "doc-c", "重试预算是 2026 次"),
    ]
    monkeypatch.setattr(knowledge_search, "search", lambda *_a, **_k: {
        "results": rows, "result_count": len(rows),
    })
    result = knowledge_search.hybrid_search(
        "重试预算 2026", mode="fts", limit=4, max_per_collection=2,
    )

    assert result["diagnostics"]["search_protocol_version"] == "knowledge-search-v2"
    assert result["diagnostics"]["rerank_pool_count"] == 5
    assert result["results"][0]["chunk_id"] == "chunk-4"
    assert sum(item["collection_id"] == "collection-a" for item in result["results"]) == 2
    assert {item["collection_id"] for item in result["results"]} == {
        "collection-a", "collection-b", "collection-c",
    }


def test_k7_diversity_obeys_character_budget_after_reordering():
    rows = [
        {
            "chunk_id": f"c{index}", "document_id": f"d{index}",
            "collection_id": f"collection-{index}", "content": "甲" * 180,
            "fusion_score": 1.0 - index / 10,
        }
        for index in range(3)
    ]
    selected = knowledge_search._diversity_select(
        rows, limit=3, max_chars=300, max_per_collection=2,
    )
    assert len(selected) == 1
    assert sum(len(item["content"]) for item in selected) <= 300


def test_cancel_during_index_preparation_cleans_fts_chunks_and_artifact(monkeypatch):
    imported = _import(("段落。" * 400).encode(), "cancel.txt")
    asyncio.run(knowledge_worker.process_due(limit=2))
    original = knowledge_search.prepare_document_index

    def cancel_then_prepare(document_id, *, should_cancel=None):
        assert knowledge_worker.cancel(imported["run"]["id"])["status"] == "cancel_requested"
        return original(document_id, should_cancel=should_cancel)

    monkeypatch.setattr(knowledge_search, "prepare_document_index", cancel_then_prepare)
    asyncio.run(knowledge_worker.process_due(limit=1))
    document = _row("knowledge_documents", imported["document"]["id"])
    assert document["status"] == "cancelled" and document["chunk_count"] == 0
    assert knowledge_worker.get_run(imported["run"]["id"])["status"] == "cancelled"
    assert knowledge_worker.chunks_for_document(document["id"]) == []
    assert knowledge_worker.artifact_for_document(document["id"]) is None
    assert _fts_count() == 0


def test_index_transaction_failure_rolls_back_and_log_hides_details(monkeypatch, caplog):
    imported = _import(b"first paragraph\n\nsecond paragraph", "failure.txt")
    asyncio.run(knowledge_worker.process_due(limit=2))

    def fail_after_one(conn, _document_id, prepared):
        conn.execute(
            "INSERT INTO knowledge_chunks_fts(rowid,terms) VALUES(?,?)",
            (prepared[0]["rowid"], prepared[0]["terms"]),
        )
        raise sqlite3.OperationalError("private body and path")

    monkeypatch.setattr(knowledge_search, "apply_document_index_locked", fail_after_one)
    asyncio.run(knowledge_worker.process_due(limit=1))
    document = _row("knowledge_documents", imported["document"]["id"])
    run = knowledge_worker.get_run(imported["run"]["id"])
    assert _fts_count() == 0
    assert document["status"] == "parsing" and document["indexed_at"] is None
    assert run["status"] == "recovery_pending" and run["error_code"] == "knowledge_index_failed"
    assert "private body" not in caplog.text and "path" not in caplog.text


def test_chunk_count_mismatch_blocks_index_visibility_without_deleting_chunks():
    imported = _import(b"stable chunk", "mismatch.txt")
    asyncio.run(knowledge_worker.process_due(limit=2))
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE knowledge_documents SET chunk_count=chunk_count+1 WHERE id=?",
            (imported["document"]["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    asyncio.run(knowledge_worker.process_due(limit=1))
    run = knowledge_worker.get_run(imported["run"]["id"])
    assert run["status"] == "recovery_pending"
    assert run["error_code"] == "knowledge_chunk_count_mismatch"
    assert _fts_count() == 0
    assert len(knowledge_worker.chunks_for_document(imported["document"]["id"])) == 1


def test_document_delete_trigger_removes_contentless_fts_row():
    imported = _index(b"delete searchable entry", "delete.txt")
    assert _fts_count() == 1
    conn = db.connect()
    try:
        conn.execute("DELETE FROM knowledge_documents WHERE id=?", (imported["document"]["id"],))
        conn.commit()
    finally:
        conn.close()
    assert _fts_count() == 0


def test_search_api_is_guarded_bounded_and_returns_real_locators():
    _index("API 可以检索星海资料。".encode(), "api.md")
    response = client.post(
        "/api/knowledge/search", json={"query": "星海", "context_window": 0, "limit": 3},
    )
    assert response.status_code == 200
    item = response.json()["results"][0]
    assert item["content"] == "API 可以检索星海资料。"
    assert item["char_end"] > item["char_start"] and item["page_start"] is None
    assert TestClient(app).post(
        "/api/knowledge/search", json={"query": "星海"},
    ).status_code == 401
    assert client.post("/api/knowledge/search", json={"query": "x" * 257}).status_code == 422


def test_schema_31_contentless_index_and_delete_trigger_upgrade_existing_chunks():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(
            "CREATE TABLE knowledge_documents(id TEXT PRIMARY KEY);"
            "CREATE TABLE knowledge_chunks(id TEXT PRIMARY KEY,content TEXT NOT NULL);"
            "INSERT INTO knowledge_chunks VALUES('chunk','kept body');"
        )
        migration = next(sql for version, sql in db.MIGRATIONS if version == 31)
        conn.executescript(migration)
        assert conn.execute(
            "SELECT tags_json FROM knowledge_documents WHERE 0"
        ).description[0][0] == "tags_json"
        rowid = conn.execute("SELECT rowid FROM knowledge_chunks WHERE id='chunk'").fetchone()[0]
        conn.execute("INSERT INTO knowledge_chunks_fts(rowid,terms) VALUES(?,?)", (rowid, "kept body"))
        assert conn.execute("SELECT terms FROM knowledge_chunks_fts").fetchone()[0] is None
        conn.execute("DELETE FROM knowledge_chunks WHERE id='chunk'")
        assert conn.execute("SELECT COUNT(*) FROM knowledge_chunks_fts").fetchone()[0] == 0
    finally:
        conn.close()
