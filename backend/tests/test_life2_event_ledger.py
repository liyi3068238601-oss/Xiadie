"""LIFE.2 provenance, truth layers and lifecycle safety."""
from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from app import db, life_events
from app.main import app

client = TestClient(app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"})


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _source(source_id: str = "statement-1", revision: str = "1") -> life_events.SourceRef:
    return life_events.SourceRef("user_statement", source_id, revision, _hash(f"{source_id}:{revision}"))


def _create(*, event_kind: str = "activity", world_layer: str = "planned",
            source: life_events.SourceRef | None = None, semantic_key: str = "morning-plan",
            tool_run_id: str | None = None):
    refs = (source or _source(),)
    return life_events.create_event(
        event_kind=event_kind, world_layer=world_layer, summary="synthetic event",
        source_refs=refs,
        idempotency_key=life_events.make_idempotency_key(
            event_kind=event_kind, source_refs=refs, semantic_key=semantic_key,
        ),
        attributes={"synthetic": True}, tool_run_id=tool_run_id,
    )


@pytest.fixture(autouse=True)
def clean_life_events():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM life_event_audit_events")
        conn.execute("DELETE FROM life_event_sources")
        conn.execute("DELETE FROM life_event_revisions")
        conn.execute("DELETE FROM life_events")
        conn.execute("DELETE FROM tool_logs")
        conn.commit()
    finally:
        conn.close()


def test_schema_64_adds_one_provenance_ledger_and_reuses_tool_logs():
    conn = db.connect()
    try:
        version = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        foreign_keys = conn.execute("PRAGMA foreign_key_list(life_events)").fetchall()
    finally:
        conn.close()
    assert version == "72"
    assert {"life_events", "life_event_revisions", "life_event_sources", "life_event_audit_events"} <= tables
    assert not ({"life_event_runs", "life_tool_runs"} & tables)
    assert any(row["table"] == "tool_logs" and row["from"] == "tool_run_id" for row in foreign_keys)


def test_planned_and_simulated_are_never_reported_as_performed():
    planned, _ = _create(world_layer="planned", semantic_key="planned")
    simulated, _ = _create(world_layer="simulated", semantic_key="simulated")
    assert planned["world_layer"] == "planned" and planned["tool_run_id"] is None
    assert simulated["world_layer"] == "simulated" and simulated["tool_run_id"] is None
    assert not [item for item in life_events.list_events() if item["world_layer"] == "performed"]


def test_performed_agent_action_requires_completed_real_tool_run():
    with pytest.raises(life_events.LifeEventError) as missing:
        _create(event_kind="agent_action", world_layer="performed", semantic_key="no-tool")
    assert missing.value.code == "tool_run_required"
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO tool_logs(id,tool,risk_level,status,summary,created_at) VALUES(?,?,?,?,?,?)",
            ("tool-pending", "synthetic", "S0", "running", "", db.now()),
        )
        conn.execute(
            "INSERT INTO tool_logs(id,tool,risk_level,status,summary,created_at) VALUES(?,?,?,?,?,?)",
            ("tool-done", "synthetic", "S0", "done", "", db.now()),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(life_events.LifeEventError) as pending:
        _create(event_kind="agent_action", world_layer="performed", semantic_key="pending", tool_run_id="tool-pending")
    assert pending.value.code == "tool_run_invalid"
    event, created = _create(
        event_kind="agent_action", world_layer="performed", semantic_key="done", tool_run_id="tool-done",
    )
    assert created is True and event["tool_run_id"] == "tool-done"


def test_idempotent_materialization_returns_same_event_and_conflict_fails():
    source = _source()
    key = life_events.make_idempotency_key(event_kind="activity", source_refs=(source,), semantic_key="same")
    first, created = life_events.create_event(
        event_kind="activity", world_layer="planned", summary="same", source_refs=(source,),
        idempotency_key=key,
    )
    second, created_again = life_events.create_event(
        event_kind="activity", world_layer="planned", summary="same", source_refs=(source,),
        idempotency_key=key,
    )
    assert created is True and created_again is False and first["id"] == second["id"]
    with pytest.raises(life_events.LifeEventError) as conflict:
        life_events.create_event(
            event_kind="activity", world_layer="simulated", summary="different", source_refs=(source,),
            idempotency_key=key,
        )
    assert conflict.value.code == "idempotency_conflict"


def test_correction_is_append_only_and_stale_revision_cannot_write():
    event, _ = _create()
    corrected = life_events.correct_event(
        event["id"], expected_revision=1, summary="corrected synthetic event",
        attributes={"synthetic": True, "corrected": True}, reason_code="user_correction",
    )
    assert corrected["revision"] == 2 and corrected["summary"] == "corrected synthetic event"
    conn = db.connect()
    try:
        revisions = conn.execute(
            "SELECT revision,summary FROM life_event_revisions WHERE event_id=? ORDER BY revision",
            (event["id"],),
        ).fetchall()
    finally:
        conn.close()
    assert [(row["revision"], row["summary"]) for row in revisions] == [
        (1, "synthetic event"), (2, "corrected synthetic event"),
    ]
    with pytest.raises(life_events.LifeEventError) as stale:
        life_events.correct_event(
            event["id"], expected_revision=1, summary="stale", attributes={}, reason_code="stale",
        )
    assert stale.value.code == "revision_conflict"


def test_revoked_event_cannot_be_corrected_or_revoked_twice():
    event, _ = _create()
    revoked = life_events.revoke_event(event["id"], expected_revision=1, reason_code="user_revoked")
    assert revoked["lifecycle_status"] == "revoked"
    with pytest.raises(life_events.LifeEventError):
        life_events.correct_event(
            event["id"], expected_revision=1, summary="illegal", attributes={}, reason_code="illegal",
        )
    with pytest.raises(life_events.LifeEventError):
        life_events.revoke_event(event["id"], expected_revision=1, reason_code="again")


def test_source_removal_revokes_only_when_no_active_provenance_remains():
    source_a, source_b = _source("a"), _source("b")
    refs = (source_a, source_b)
    event, _ = life_events.create_event(
        event_kind="observation", world_layer="observed", summary="two sources",
        source_refs=refs,
        idempotency_key=life_events.make_idempotency_key(
            event_kind="observation", source_refs=refs, semantic_key="two-sources",
        ),
    )
    assert life_events.remove_source(source_kind="user_statement", source_id="a", reason_code="source_deleted") == 1
    assert life_events.get_event(event["id"])["lifecycle_status"] == "active"
    assert life_events.remove_source(source_kind="user_statement", source_id="b", reason_code="source_deleted") == 1
    assert life_events.get_event(event["id"])["lifecycle_status"] == "revoked"


def test_read_only_api_and_body_free_audit_diagnostics():
    event, _ = _create()
    listing = client.get("/api/life/events")
    assert listing.status_code == 200 and listing.json()["items"][0]["id"] == event["id"]
    diagnostic = client.get(f"/api/life/events/diagnostics?event_id={event['id']}")
    assert diagnostic.status_code == 200
    assert set(diagnostic.json()["items"][0]) == {
        "id", "event_id", "event_type", "from_status", "to_status", "revision",
        "reason_code", "created_at",
    }
    assert client.post("/api/life/events", json={}).status_code == 405
