import asyncio

import pytest
from fastapi.testclient import TestClient

from app import db, kig_sources, knowledge, knowledge_worker, life_events, lore, memory
from app.main import app

TOKEN = "test-token-with-at-least-thirty-two-bytes"
client = TestClient(app, headers={"X-Xiadie-Token": TOKEN})


@pytest.fixture(autouse=True)
def _clean_dependencies():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM derived_dependencies")
        conn.commit()
    finally:
        conn.close()


def _seed_sources() -> dict[str, str]:
    imported = knowledge.import_file(
        f"kig-source-{db.new_id()}.md", "text/markdown",
        f"# KIG provenance\nunique-{db.new_id()}".encode(),
    )
    assert asyncio.run(knowledge_worker.process_due(limit=3)) == 3
    document_id = imported["document"]["id"]

    conn = db.connect()
    try:
        chunk_id = conn.execute(
            "SELECT id FROM knowledge_chunks WHERE document_id=? ORDER BY ordinal LIMIT 1",
            (document_id,),
        ).fetchone()["id"]
        session_id, message_id, tool_id = db.new_id(), db.new_id(), db.new_id()
        now = db.now()
        conn.execute(
            "INSERT INTO sessions(id,title,archived,created_at,updated_at) VALUES(?,?,?,?,?)",
            (session_id, "KIG source", 0, now, now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (message_id, session_id, "user", "message authority", now),
        )
        conn.execute(
            "INSERT INTO tool_logs(id,tool,risk_level,status,summary,created_at) VALUES(?,?,?,?,?,?)",
            (tool_id, "test.tool", "S0", "done", "tool authority", now),
        )
        conn.commit()
    finally:
        conn.close()

    fragment = memory.create_memory("L1", "memory authority")
    source = life_events.SourceRef("system_observation", db.new_id(), "1", "a" * 64)
    event, created = life_events.create_event(
        event_kind="observation", world_layer="observed", summary="life authority",
        source_refs=(source,), idempotency_key=f"kig-test:{db.new_id()}",
    )
    assert created
    section = lore._sections()[0]
    lore_id = kig_sources._sha256(section["title"])
    return {
        "knowledge_document": document_id, "knowledge_chunk": chunk_id,
        "message": message_id, "memory_fragment": fragment["id"],
        "life_event": event["id"], "tool_run": tool_id, "lore_section": lore_id,
    }


def test_schema_72_has_only_minimal_dependency_metadata():
    conn = db.connect()
    try:
        assert conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == "82"
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(derived_dependencies)")}
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"source_revision", "source_hash", "privacy_scope", "source_locator", "dependency_status"} <= columns
    assert not ({"content", "body", "summary", "source_body"} & columns)
    assert "source_refs" not in tables


def test_all_authoritative_adapters_are_body_free_and_exactly_validated():
    sources = _seed_sources()
    forbidden = {"content", "body", "summary", "attributes", "embedding", "vector"}
    for kind, source_id in sources.items():
        ref = kig_sources.registry.resolve(kind, source_id)
        assert ref.source_kind == kind and ref.source_id == source_id
        assert len(ref.content_hash) == 64 and ref.locator
        assert not (forbidden & set(ref.to_dict()))
        assert kig_sources.validate_ref(ref) == ref

        forged = kig_sources.SourceRef(**{**ref.to_dict(), "locator": ref.locator + "/forged"})
        with pytest.raises(kig_sources.SourceRefError) as caught:
            kig_sources.validate_ref(forged)
        assert caught.value.code == "source_ref_mismatch"


def test_adapter_privacy_scope_is_explicitly_allowlisted_and_fail_closed():
    registry = kig_sources.SourceAdapterRegistry()
    registry.register("message", lambda source_id: kig_sources.SourceRef(
        "message", source_id, "1", "a" * 64, "active", "highly_sensitive",
        f"conversation://messages/{source_id}",
    ))
    with pytest.raises(kig_sources.SourceRefError) as caught:
        registry.resolve("message", "message-1")
    assert caught.value.code == "source_privacy_invalid"

    for scope in ("normal:remote_allowed", "sensitive:ask_each_time", "normal:local_only"):
        assert kig_sources.validate_privacy_scope("knowledge_chunk", scope) == scope
    for scope in ("normal", "normal:remote_allowed:extra", "secret:remote_allowed"):
        with pytest.raises(kig_sources.SourceRefError):
            kig_sources.validate_privacy_scope("knowledge_chunk", scope)


def test_dependency_status_propagates_stale_missing_revoked_and_unverified(monkeypatch):
    sources = _seed_sources()
    tool_ref = kig_sources.registry.resolve("tool_run", sources["tool_run"])
    dependency = kig_sources.bind_dependency(
        derived_kind="evidence_link", derived_id="evidence-1", source_ref=tool_ref,
    )
    conn = db.connect()
    try:
        conn.execute("UPDATE tool_logs SET summary='changed authority' WHERE id=?", (sources["tool_run"],))
        conn.commit()
    finally:
        conn.close()
    assert kig_sources.check_dependency(dependency["id"])["dependency_status"] == "stale"

    message_ref = kig_sources.registry.resolve("message", sources["message"])
    missing = kig_sources.bind_dependency(
        derived_kind="evidence_link", derived_id="evidence-2", source_ref=message_ref,
    )
    conn = db.connect()
    try:
        conn.execute("DELETE FROM messages WHERE id=?", (sources["message"],))
        conn.commit()
    finally:
        conn.close()
    assert kig_sources.check_dependency(missing["id"])["dependency_status"] == "missing"

    memory_ref = kig_sources.registry.resolve("memory_fragment", sources["memory_fragment"])
    revoked = kig_sources.bind_dependency(
        derived_kind="evidence_link", derived_id="evidence-3", source_ref=memory_ref,
    )
    conn = db.connect()
    try:
        conn.execute("UPDATE memory_fragments SET status='tombstone',enabled=0 WHERE id=?",
                     (sources["memory_fragment"],))
        conn.commit()
    finally:
        conn.close()
    assert kig_sources.check_dependency(revoked["id"])["dependency_status"] == "revoked"

    original = kig_sources.registry._resolvers["tool_run"]
    monkeypatch.setitem(kig_sources.registry._resolvers, "tool_run",
                        lambda _source_id: (_ for _ in ()).throw(RuntimeError("adapter offline")))
    assert kig_sources.check_dependency(dependency["id"])["dependency_status"] == "unverified"
    monkeypatch.setitem(kig_sources.registry._resolvers, "tool_run", original)
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM tool_logs WHERE id=?", (sources["tool_run"],)).fetchone()[0] == 1
    finally:
        conn.close()


def test_dependency_upsert_and_sweeper_are_bounded_without_body_copy():
    sources = _seed_sources()
    ref = kig_sources.registry.resolve("tool_run", sources["tool_run"])
    first = kig_sources.bind_dependency(derived_kind="retrieval_bundle", derived_id="bundle-1", source_ref=ref)
    second = kig_sources.bind_dependency(derived_kind="retrieval_bundle", derived_id="bundle-1", source_ref=ref)
    assert first["id"] == second["id"]
    kig_sources.bind_dependency(derived_kind="retrieval_bundle", derived_id="bundle-2", source_ref=ref)
    result = kig_sources.sweep_dependencies(limit=1)
    assert result["checked"] == 1 and sum(result[s] for s in kig_sources.DEPENDENCY_STATUSES) == 1
    conn = db.connect()
    try:
        row = dict(conn.execute("SELECT * FROM derived_dependencies WHERE id=?", (first["id"],)).fetchone())
    finally:
        conn.close()
    assert "tool authority" not in str(row)


def test_source_locator_api_rejects_forged_locator():
    sources = _seed_sources()
    source_id = sources["tool_run"]
    resolved = client.get(f"/api/kig/sources/tool_run/{source_id}")
    assert resolved.status_code == 200
    payload = resolved.json()["source_ref"]
    assert "summary" not in payload and payload["locator"] == f"tool://runs/{source_id}"
    assert client.post("/api/kig/sources/validate", json=payload).status_code == 200
    payload["locator"] += "/spoof"
    rejected = client.post("/api/kig/sources/validate", json=payload)
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "source_ref_mismatch"
