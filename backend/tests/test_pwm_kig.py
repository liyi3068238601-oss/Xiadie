import json

import pytest
from fastapi.testclient import TestClient

from app import db, kig_integrations, kig_maintenance, pwm, pwm_extractor_shadow
from app.main import app


def _message(text="遐蝶项目使用 FastAPI"):
    sid, mid, now = db.new_id(), db.new_id(), db.now()
    conn = db.connect()
    try:
        conn.execute("INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
                     (sid, "pwm", now, now))
        conn.execute("INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
                     (mid, sid, "user", text, now))
        conn.commit()
    finally:
        conn.close()
    return mid


def _entity(name, source_id, *, entity_type="project", scope="reality"):
    return pwm.create_entity(
        entity_type=entity_type, canonical_name=name, source_kind="message",
        source_id=source_id, reality_scope=scope, confidence=0.8,
    )


def test_schema_80_contains_all_pwm_and_maintenance_tables():
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()["value"] == "80"
        names = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE 'pwm_%' "
            "OR name IN ('kig_system_proposals','kig_maintenance_candidates','kig_retrieval_feedback'))"
        ).fetchall()}
    finally:
        conn.close()
    assert {
        "pwm_claims", "pwm_entities", "pwm_entity_aliases", "pwm_relations",
        "pwm_world_events", "pwm_state_assertions", "pwm_entity_source_links",
        "pwm_entity_resolution_proposals", "pwm_entity_operations",
        "kig_system_proposals", "kig_maintenance_candidates", "kig_retrieval_feedback",
    } <= names


def test_all_pwm_projection_writes_are_shadow_and_sourced():
    source_id = _message()
    project = _entity("遐蝶", source_id)
    tool = _entity("FastAPI", source_id, entity_type="tool")
    alias = pwm.add_alias(
        entity_id=project["id"], alias="Xiadie", source_kind="message", source_id=source_id,
        confidence=0.8,
    )
    claim = pwm.create_claim(
        statement="遐蝶使用 FastAPI", claim_type="project_fact", predicate="uses",
        source_kind="message", source_id=source_id, subject_entity_id=project["id"],
        object_entity_id=tool["id"], support_type="model_inferred", confidence=0.7,
    )
    relation = pwm.create_relation(
        subject_entity_id=project["id"], predicate="uses", object_entity_id=tool["id"],
        source_kind="message", source_id=source_id, confidence=0.7,
    )
    event = pwm.create_world_event(
        event_type="project_change", title="引入 FastAPI", source_kind="message",
        source_id=source_id, event_layer="project_history", participant_entity_ids=[project["id"]],
    )
    state = pwm.create_state_assertion(
        subject_entity_id=project["id"], state_type="development_stage", value="building",
        source_kind="message", source_id=source_id,
    )
    assert project["extraction_mode"] == claim["extraction_mode"] == "shadow"
    assert claim["validity_state"] == "candidate" and claim["support_type"] == "model_inferred"
    assert all(item["sources"] for item in (project, alias, claim, relation, event, state))


def test_sensitive_attribute_and_real_action_guards_fail_closed():
    source_id = _message("私人信息")
    with pytest.raises(pwm.PWMError, match="sensitive"):
        _entity("用户的政治倾向", source_id, entity_type="person")
    with pytest.raises(pwm.PWMError) as error:
        pwm.create_world_event(
            event_type="tool_action", title="执行完成", source_kind="message", source_id=source_id,
            event_layer="agent_real_action", execution_state="performed",
        )
    assert error.value.code == "tool_run_required"


def test_reality_and_lore_scopes_do_not_cross_merge():
    source_id = _message()
    real = _entity("Cyrene", source_id)
    lore = _entity("Cyrene", source_id, scope="lore")
    with pytest.raises(pwm.PWMError) as error:
        pwm.propose_resolution(left_entity_id=real["id"], right_entity_id=lore["id"])
    assert error.value.code == "scope_mismatch"


def test_merge_is_confirmed_reversible_and_restores_relationships():
    source_id = _message()
    primary = _entity("遐蝶", source_id, entity_type="agent")
    secondary = _entity("Xiadie Agent", source_id, entity_type="agent")
    tool = _entity("FastAPI", source_id, entity_type="tool")
    alias = pwm.add_alias(
        entity_id=secondary["id"], alias="Xiadie", source_kind="message", source_id=source_id,
    )
    relation = pwm.create_relation(
        subject_entity_id=secondary["id"], predicate="uses", object_entity_id=tool["id"],
        source_kind="message", source_id=source_id,
    )
    event = pwm.create_world_event(
        event_type="project_change", title="Synthetic event", source_kind="message",
        source_id=source_id, event_layer="project_history",
        participant_entity_ids=[secondary["id"]], location_entity_id=secondary["id"],
    )
    state = pwm.create_state_assertion(
        subject_entity_id=secondary["id"], state_type="stage", value="building",
        source_kind="message", source_id=source_id,
    )
    proposal = pwm.propose_resolution(
        left_entity_id=primary["id"], right_entity_id=secondary["id"], confidence=0.9,
    )
    assert proposal["requires_confirmation"] == 1
    applied = pwm.apply_merge(proposal["id"], expected_revision=proposal["revision"])
    assert pwm.get_row("pwm_relations", relation["id"])["subject_entity_id"] == primary["id"]
    assert pwm.get_row("pwm_entity_aliases", alias["id"])["entity_id"] == primary["id"]
    assert secondary["id"] not in pwm.get_row("pwm_world_events", event["id"])[
        "participant_entity_ids_json"
    ]
    assert pwm.get_row("pwm_state_assertions", state["id"])["subject_entity_id"] == primary["id"]
    conn = db.connect()
    try:
        journal = conn.execute(
            "SELECT before_json,after_json FROM pwm_entity_operations WHERE id=?",
            (applied["operation_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert "Synthetic event" not in journal["before_json"] + journal["after_json"]
    assert "statement" not in journal["before_json"] + journal["after_json"]
    rolled = pwm.rollback_merge(applied["operation_id"])
    assert rolled["restored"] is True
    assert pwm.get_row("pwm_relations", relation["id"])["subject_entity_id"] == secondary["id"]
    assert pwm.get_row("pwm_entity_aliases", alias["id"])["entity_id"] == secondary["id"]
    assert secondary["id"] in pwm.get_row("pwm_world_events", event["id"])[
        "participant_entity_ids_json"
    ]
    assert pwm.get_row("pwm_state_assertions", state["id"])["subject_entity_id"] == secondary["id"]


def test_memory_and_lifecycle_integrations_are_proposal_only():
    source_id = _message("一个可能的长期偏好")
    conn = db.connect()
    try:
        before = conn.execute("SELECT COUNT(*) AS n FROM memory_fragments").fetchone()["n"]
    finally:
        conn.close()
    proposal = kig_integrations.create_proposal(
        proposal_kind="memory_classification", source_kind="message", source_id=source_id,
        payload={"kind": "preference"}, confidence=0.7,
    )
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM memory_fragments").fetchone()["n"] == before
    finally:
        conn.close()
    assert proposal["status"] == "proposed"
    with pytest.raises(kig_integrations.IntegrationError) as body_error:
        kig_integrations.create_proposal(
            proposal_kind="memory_conflict", source_kind="message", source_id=source_id,
            payload={"raw_text": "must not persist"}, confidence=0.5,
        )
    assert body_error.value.code == "proposal_body_forbidden"
    decided = kig_integrations.decide_proposal(proposal["id"], accepted=True, owner_system="memory")
    assert decided["status"] == "accepted"
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM memory_fragments").fetchone()["n"] == before
    finally:
        conn.close()


def test_maintenance_never_deletes_and_requires_confirmation():
    source_id = _message()
    entity = _entity("孤立项目", source_id)
    candidate = kig_maintenance.create_candidate(
        candidate_type="entity_merge_candidate", object_kind="pwm_entity",
        object_id=entity["id"], reason_codes=["same_name"], confidence=0.6,
        decision_source="llm_proposal",
    )
    assert candidate["requires_confirmation"] == 1 and candidate["status"] == "proposed"
    decided = kig_maintenance.decide_candidate(candidate["id"], accepted=True)
    assert decided["status"] == "confirmed"
    assert pwm.get_entity(entity["id"])["status"] == "candidate"


def test_world_model_management_api_extends_knowledge_surface():
    client = TestClient(app)
    headers = {"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"}
    response = client.get("/api/knowledge/world-model/summary", headers=headers)
    assert response.status_code == 200
    assert response.json()["mode"] == "shadow"
    changed = client.patch(
        "/api/knowledge/world-model/settings", headers=headers,
        json={"enabled": False, "maintenance_frequency": "off"},
    )
    assert changed.status_code == 200 and changed.json()["enabled"] is False
    restored = client.patch(
        "/api/knowledge/world-model/settings", headers=headers, json={"enabled": True},
    )
    assert restored.status_code == 200 and restored.json()["enabled"] is True


def test_system_settings_and_temporary_chat_apply_to_adapters():
    assert kig_integrations.source_allowed(source="memory", temporary_chat=True) is False
    assert kig_integrations.source_allowed(source="history", temporary_chat=True) is False
    conn = db.connect()
    try:
        conn.execute("INSERT INTO settings(key,value) VALUES('kig_enabled','0') "
                     "ON CONFLICT(key) DO UPDATE SET value='0'")
        conn.commit()
        assert kig_integrations.source_allowed(source="knowledge") is False
        conn.execute("UPDATE settings SET value='1' WHERE key='kig_enabled'")
        conn.commit()
    finally:
        conn.close()


def test_temporary_session_never_enters_cross_session_fts():
    client = TestClient(app)
    headers = {"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"}
    response = client.post("/api/sessions", headers=headers, json={"temporary": True})
    assert response.status_code == 200 and response.json()["temporary"] == 1
    session_id, message_id = response.json()["id"], db.new_id()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (message_id, session_id, "user", "temporary unique content", db.now()),
        )
        conn.commit()
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM conversation_history_sessions_fts WHERE session_id=?",
            (session_id,),
        ).fetchone()["n"] == 0
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM conversation_history_messages_fts WHERE session_id=?",
            (session_id,),
        ).fetchone()["n"] == 0
    finally:
        conn.close()


def test_temporary_chat_skips_cross_session_and_companion_observers(monkeypatch):
    from app import llm
    from app import main as main_module

    calls: list[str] = []

    async def fake_stream(*_args, **_kwargs):
        yield "temporary reply"

    def record(name):
        return lambda *_args, **_kwargs: calls.append(name)

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    monkeypatch.setattr(main_module.memory, "build_digest", record("memory_digest"))
    monkeypatch.setattr(
        main_module.conversation_summaries, "active_revision_internal", record("summary_read")
    )
    monkeypatch.setattr(main_module.history_recall, "prepare_locked", record("history_recall"))
    monkeypatch.setattr(
        main_module.companion_state, "commit_interaction", record("companion_state")
    )
    monkeypatch.setattr(
        main_module.memory_observer_service, "enqueue_turn", record("memory_observer")
    )
    monkeypatch.setattr(
        main_module.companion_cognition_service, "enqueue_turn", record("affect_observer")
    )
    monkeypatch.setattr(
        main_module.conversation_summary_service, "enqueue_after_chat", record("summary_write")
    )
    monkeypatch.setattr(
        main_module.proactive_orchestrator, "handle_user_message", record("proactive_return")
    )
    monkeypatch.setattr(
        main_module.proactive_orchestrator, "enqueue_after_chat", record("proactive_after")
    )

    client = TestClient(app)
    headers = {"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"}
    session = client.post("/api/sessions", headers=headers, json={}).json()
    with client.stream(
        "POST", "/api/chat", headers=headers,
        json={"session_id": session["id"], "content": "do not remember", "temporary_chat": True},
    ) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200 and "event: done" in body
    assert calls == []
    refreshed = next(
        item for item in client.get("/api/sessions", headers=headers).json()
        if item["id"] == session["id"]
    )
    assert refreshed["temporary"] == 1


def test_budget_policy_is_bounded_and_parseable():
    policy = pwm.budget_policy()
    assert policy.max_claims_per_source >= 1
    conn = db.connect()
    try:
        raw = conn.execute("SELECT value FROM settings WHERE key='pwm_budget_policy'").fetchone()["value"]
    finally:
        conn.close()
    assert json.loads(raw)["max_disambiguation_candidates"] == 8


def test_shadow_extractor_rejects_sensitive_or_unbounded_output_before_any_write():
    source_id = _message("只是一段合成资料")
    before = len(pwm.list_entities(limit=100))
    with pytest.raises(pwm.PWMError):
        pwm_extractor_shadow.validate_payload({
            "entities": [{"key": "e1", "type": "person", "name": "某人的政治倾向",
                          "scope": "reality", "confidence": 0.8}],
            "claims": [], "relations": [], "events": [],
        })
    with pytest.raises(pwm_extractor_shadow.ExtractionError) as error:
        pwm_extractor_shadow.validate_payload({
            "entities": [
                {"key": f"e{i}", "type": "project", "name": f"项目{i}",
                 "scope": "reality", "confidence": 0.5}
                for i in range(pwm_extractor_shadow.MAX_ENTITIES + 1)
            ],
            "claims": [], "relations": [], "events": [],
        })
    assert error.value.code == "output_budget_exceeded"
    assert len(pwm.list_entities(limit=100)) == before


def test_shadow_extractor_persists_only_model_inferred_candidates():
    source_id = _message("遐蝶项目使用 FastAPI")
    payload = pwm_extractor_shadow.validate_payload({
        "entities": [
            {"key": "project", "type": "project", "name": "遐蝶", "scope": "reality", "confidence": 0.9},
            {"key": "tool", "type": "tool", "name": "FastAPI", "scope": "reality", "confidence": 0.9},
        ],
        "claims": [{"statement": "遐蝶使用 FastAPI", "type": "fact", "subject_key": "project",
                    "predicate": "uses", "object_key": "tool", "object_value": "", "confidence": 0.8}],
        "relations": [{"subject_key": "project", "predicate": "uses", "object_key": "tool",
                       "object_value": "", "confidence": 0.8}],
        "events": [],
    })
    saved = pwm_extractor_shadow.persist_payload(payload, source_kind="message", source_id=source_id)
    claim = pwm.get_row("pwm_claims", saved["claims"][0])
    assert claim["support_type"] == "model_inferred"
    assert claim["validity_state"] == "candidate" and claim["extraction_mode"] == "shadow"


def test_eap_adapter_is_body_free_read_only_and_does_not_change_counts():
    conn = db.connect()
    try:
        before = conn.total_changes
        counts = {
            table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            for table in ("proactive_candidates", "proactive_deliveries")
        }
    finally:
        conn.close()
    snapshot = kig_integrations.eap_readonly_snapshot()
    assert snapshot["read_only"] is True and snapshot["writes_performed"] == 0
    conn = db.connect()
    try:
        after = {
            table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            for table in counts
        }
    finally:
        conn.close()
    assert after == counts and before >= 0


def test_kig_off_leaves_owner_memory_available_and_pipeline_inactive():
    source_id = _message("保留原有行为")
    conn = db.connect()
    try:
        conn.execute("UPDATE settings SET value='0' WHERE key='kig_enabled'")
        conn.commit()
    finally:
        conn.close()
    from app import kig_pipeline, memory
    assert kig_pipeline.prepare_for_chat(
        query="综合所有来源", source_message_id=source_id, session_id="session",
        provider={"execution_location": "local"}, recall_mode="explicit",
    ) is None
    memory.create_memory("L2", "独立长期记忆仍然可用", source="test")
    assert any("独立长期记忆" in item["content"] for item in memory.search_memories("独立长期记忆"))
    db.set_setting("kig_enabled", "1")
