"""K.8 collection 策略、审计保留、导出清单和完整清除验收。"""
import asyncio
import hashlib

import pytest
from fastapi.testclient import TestClient

from app import (
    db, knowledge, knowledge_cleanup, knowledge_management, knowledge_policy,
    knowledge_search, knowledge_worker,
)
from app.main import app

CLIENT = TestClient(
    app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"},
)


@pytest.fixture(autouse=True)
def clean_knowledge_lifecycle_data():
    db.init_db()
    conn = db.connect()
    try:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM knowledge_deletion_runs")
        conn.execute("DELETE FROM knowledge_documents")
        conn.execute(
            "UPDATE knowledge_collections SET default_transmission_policy='ask_each_time',"
            "policy_revision=1,policy_updated_at=updated_at WHERE id='default'"
        )
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
        conn.execute(
            "UPDATE knowledge_collections SET default_transmission_policy='ask_each_time',"
            "policy_revision=1 WHERE id='default'"
        )
        conn.commit()
    finally:
        conn.close()


def _import(name: str, body: str, *, sensitivity: str = "normal") -> dict:
    return knowledge.import_file(
        name, "text/markdown", body.encode("utf-8"), sensitivity=sensitivity,
    )


def test_schema_41_collection_default_is_used_and_bulk_change_is_atomic():
    normal = _import("普通.md", "普通资料")
    sensitive = _import("敏感.md", "敏感资料", sensitivity="sensitive")
    with pytest.raises(knowledge_policy.KnowledgePolicyError) as captured:
        knowledge_policy.update_collection_policy(
            "default", "remote_allowed", apply_existing=True,
        )
    assert captured.value.code == "sensitive_remote_forbidden"

    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT default_transmission_policy FROM knowledge_collections WHERE id='default'"
        ).fetchone()[0] == "ask_each_time"
        policies = dict(conn.execute(
            "SELECT id,transmission_policy FROM knowledge_documents"
        ).fetchall())
        assert policies[normal["document"]["id"]] == "ask_each_time"
        assert policies[sensitive["document"]["id"]] == "local_only"
    finally:
        conn.close()

    changed = knowledge_policy.update_collection_policy(
        "default", "local_only", apply_existing=True,
    )
    assert changed["updated_document_count"] == 1
    future = _import("后续.md", "后续普通资料")
    assert future["document"]["transmission_policy"] == "local_only"


def test_expired_retrieval_with_visible_citation_is_minimized_not_deleted():
    now = db.now()
    old = now - (knowledge_cleanup.RETENTION_CHAT_RETRIEVALS + 1) * 86400
    session_id, user_id, assistant_id = db.new_id(), db.new_id(), db.new_id()
    cited_id, uncited_id = db.new_id(), db.new_id()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,'audit',?,?)",
            (session_id, old, old),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,'user','q',?)",
            (user_id, session_id, old),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,'assistant','a',?)",
            (assistant_id, session_id, old),
        )
        for retrieval_id in (cited_id, uncited_id):
            conn.execute(
                "INSERT INTO knowledge_chat_retrievals("
                "id,session_id,user_message_id,assistant_message_id,trigger_reason,query_sha256,"
                "candidate_count,injected_count,knowledge_tokens,knowledge_token_budget,lore_tokens,"
                "memory_tokens,status,created_at,search_protocol_version)"
                " VALUES(?,?,?,?,? ,?,2,1,100,700,10,20,'completed',?,'knowledge-search-v2')",
                (retrieval_id, session_id, user_id, assistant_id, "explicit_request",
                 hashlib.sha256(retrieval_id.encode()).hexdigest(), old),
            )
        conn.execute(
            "INSERT INTO knowledge_message_citations("
            "id,assistant_message_id,retrieval_id,citation_key,document_id,chunk_id,original_name,"
            "ordinal,paragraph_start,paragraph_end,line_start,line_end,char_start,char_end,"
            "content_sha256,created_at) VALUES(?,?,?,'K1','deleted-doc','deleted-chunk','旧资料.md',"
            "0,1,1,1,1,0,4,?,?)",
            (db.new_id(), assistant_id, cited_id, "a" * 64, old),
        )
        conn.commit()
    finally:
        conn.close()

    assert knowledge_cleanup.run_once(now=now) == 2
    conn = db.connect()
    try:
        cited = conn.execute(
            "SELECT * FROM knowledge_chat_retrievals WHERE id=?", (cited_id,),
        ).fetchone()
        assert cited and cited["audit_state"] == "minimized"
        assert cited["trigger_reason"] == "retained_citation" and cited["candidate_count"] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM knowledge_chat_retrievals WHERE id=?", (uncited_id,),
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM knowledge_message_citations WHERE retrieval_id=?", (cited_id,),
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_manifest_has_no_body_vector_or_token_and_clear_all_removes_derivatives():
    first = _import("甲.md", "# 甲\n星港资料甲")
    second = _import("乙.md", "# 乙\n星港资料乙")
    assert asyncio.run(knowledge_worker.process_due(limit=6)) == 6
    manifest = knowledge_management.export_manifest()
    serialized = str(manifest)
    assert manifest["contains_knowledge_body"] is False
    assert manifest["contains_tokens"] is False and manifest["contains_vectors"] is False
    assert "星港资料甲" not in serialized and "vector_blob" not in serialized

    result = knowledge_management.clear_all()
    assert result["status"] == "cleanup_queued" and result["queued_document_count"] == 2
    assert knowledge_search.search("星港")["results"] == []
    assert asyncio.run(knowledge_worker.process_due(limit=10)) >= 2
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM knowledge_documents").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM knowledge_chunk_embeddings").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM knowledge_transmission_grants").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM knowledge_chat_retrievals").fetchone()[0] == 0
    finally:
        conn.close()
    assert not knowledge.storage_path_for(first["document"]).exists()
    assert not knowledge.storage_path_for(second["document"]).exists()


def test_audit_cleanup_never_removes_current_document_or_search_index():
    imported = _import("保留.md", "# 保留\n生命周期清理仍可检索")
    assert asyncio.run(knowledge_worker.process_due(limit=3)) == 3
    before = knowledge_search.search("生命周期清理")["results"]
    assert before and before[0]["document_id"] == imported["document"]["id"]
    knowledge_cleanup.run_once(now=db.now() + 400 * 86400)
    after = knowledge_search.search("生命周期清理")["results"]
    assert after and after[0]["document_id"] == imported["document"]["id"]


def test_decision_and_grant_retention_delete_only_terminal_unprotected_audits():
    now = db.now()
    conn = db.connect()
    try:
        session_id = db.new_id()
        conn.execute(
            "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,'retention',?,?)",
            (session_id, now - 200 * 86400, now),
        )

        def decision(created_at: float) -> str:
            decision_id = db.new_id()
            conn.execute(
                "INSERT INTO knowledge_recall_decisions("
                "id,session_id,protocol_version,recall_mode,action,reason_code,confidence_band,"
                "query_sha256,policy_snapshot_sha256,status,created_at,finished_at)"
                " VALUES(? ,?,'knowledge-recall-decision-v1','smart','skip','no_candidates','low',"
                "?,?,'completed',?,?)",
                (decision_id, session_id, "a" * 64, "b" * 64, created_at, created_at),
            )
            return decision_id

        terminal_decision = decision(now - 31 * 86400)
        protected_decision = decision(now - 100 * 86400)
        expired_decision = decision(now - 91 * 86400)
        for decision_id, status, age in (
            (terminal_decision, "denied", 31), (protected_decision, "pending", 100),
        ):
            grant_id = db.new_id()
            conn.execute(
                "INSERT INTO knowledge_transmission_grants("
                "id,recall_decision_id,session_id,request_nonce,user_content_sha256,query_sha256,"
                "model,provider_location,provider_location_revision,plan_sha256,policy_snapshot_sha256,"
                "threshold_version,status,document_count,chunk_count,token_min,token_max,expires_at,"
                "created_at,updated_at,recall_mode) VALUES(?,?,?, ?,?,?, 'model','remote',1,?,?,"
                "'knowledge-recall-thresholds-v2',?,0,0,0,0,?,?,?,'smart')",
                (grant_id, decision_id, session_id, f"nonce-{grant_id}"[:32], "c" * 64,
                 "d" * 64, "e" * 64, "f" * 64, status, now + 86400,
                 now - age * 86400, now - age * 86400),
            )
        conn.commit()
    finally:
        conn.close()

    assert knowledge_cleanup.run_once(now=now) == 2
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM knowledge_transmission_grants WHERE status='denied'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM knowledge_transmission_grants WHERE status='pending'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM knowledge_recall_decisions WHERE id=?", (protected_decision,),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM knowledge_recall_decisions WHERE id=?", (expired_decision,),
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_audit_policy_is_body_free_and_declares_citation_lifetime():
    current = knowledge_cleanup.stats()
    assert current["policy_version"] == "knowledge-audit-retention-v1"
    assert current["citations"] == "message_lifetime"
    assert current["expired_cited_retrieval_behavior"] == "minimize_metadata_keep_citation"
    assert current["document_bodies_in_audit"] is False


def test_k8_management_endpoints_require_explicit_clear_confirmation():
    lifecycle = CLIENT.get("/api/knowledge/audit-lifecycle")
    assert lifecycle.status_code == 200
    assert lifecycle.json()["citations"] == "message_lifetime"
    manifest = CLIENT.get("/api/knowledge/export-manifest")
    assert manifest.status_code == 200 and manifest.json()["contains_knowledge_body"] is False
    policy = CLIENT.patch(
        "/api/knowledge/collections/default/transmission-policy",
        json={"default_transmission_policy": "local_only", "apply_existing": False},
    )
    assert policy.status_code == 200
    assert policy.json()["default_transmission_policy"] == "local_only"
    assert CLIENT.post(
        "/api/knowledge/clear-all", json={"confirmation": "yes"},
    ).status_code == 400
