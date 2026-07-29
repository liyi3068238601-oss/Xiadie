from __future__ import annotations

import hashlib
import json

from app import db, inner_state_projection as projection, persona, persona_v2


def _state(boundary: str = "softly_guarded", cluster: str = "serene") -> dict:
    return {
        "affect": {
            "valence": 0.2, "arousal": -0.1, "contact_need": 0.3, "updated_at": 10,
        },
        "relationship": {
            "bond": 0.5, "trust": 0.5, "interaction_count": 8, "updated_at": 10,
        },
        "derived": {"cluster": cluster, "guardedness_band": boundary},
    }


def _inputs() -> dict:
    return {
        "state": _state(),
        "goals": [
            {"id": "0000000000000003", "status": "active", "priority": 2, "revision": 1, "updated_at": 9},
            {"id": "0000000000000002", "status": "paused", "priority": 5, "revision": 2, "updated_at": 2},
            {"id": "0000000000000001", "status": "active", "priority": 5, "revision": 3, "updated_at": 8},
        ],
        "sagas": [
            {"id": "1000000000000002", "status": "active", "revision": 2, "end_at": 20},
            {"id": "1000000000000001", "status": "active", "revision": 1, "end_at": 30},
        ],
        "life_events": [
            {"id": "2000000000000002", "lifecycle_status": "active", "revision": 1, "created_at": 20},
            {"id": "2000000000000001", "lifecycle_status": "active", "revision": 2, "created_at": 30},
        ],
        "short_memos": [
            {"id": "3000000000000002", "revision": 1, "expires_at": 90, "updated_at": 10},
            {"id": "3000000000000001", "revision": 2, "expires_at": 80, "updated_at": 11},
        ],
        "request_mode": "companionship",
        "current_intent": "open_conversation",
    }


def test_same_authoritative_snapshot_builds_the_same_bounded_projection():
    first = projection.build(**_inputs())
    second = projection.build(**_inputs())
    assert first == second and first is not None
    value = first.as_mapping()
    assert value["protocol_version"] == "inner-state-projection-v1"
    assert len(value["source_snapshot_hash"]) == 64
    assert value["affect_band"] == "serene"
    assert value["relationship_boundary"] == "softly_guarded"
    assert value["open_goal_ids"] == ["0000000000000001", "0000000000000003"]
    assert len(value["open_saga_ids"]) <= 2
    assert len(value["recent_life_event_ids"]) <= 3
    assert len(value["relevant_short_memo_ids"]) <= 3
    assert {"calm", "warm", "gently_curious", "offer_help"} <= set(value["expression_flags"])
    serialized = json.dumps(value, ensure_ascii=False)
    assert "content" not in serialized and "summary" not in serialized and "title" not in serialized


def test_no_sources_returns_none_and_removed_sources_leave_no_residue():
    assert projection.build(state=None) is None
    before = projection.build(**_inputs())
    changed = _inputs()
    changed["short_memos"] = []
    changed["goals"] = []
    after = projection.build(**changed)
    assert before and after
    assert before.source_snapshot_hash != after.source_snapshot_hash
    assert after.relevant_short_memo_ids == () and after.open_goal_ids == ()


def test_relationship_boundary_controls_curiosity_and_help_flags():
    guarded = _inputs()
    guarded["state"] = _state("defensive")
    guarded_value = projection.build(**guarded)
    assert guarded_value
    assert "gently_curious" not in guarded_value.expression_flags
    assert "offer_help" not in guarded_value.expression_flags
    focused = _inputs()
    focused["request_mode"] = "focused_work"
    focused["current_intent"] = "focused_work"
    focused_value = projection.build(**focused)
    assert focused_value and "concise" in focused_value.expression_flags


def test_build_has_no_schema_or_table_side_effects():
    db.init_db()
    conn = db.connect()
    try:
        before_schema = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        before_tables = tuple(row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ))
    finally:
        conn.close()
    assert projection.build(**_inputs())
    conn = db.connect()
    try:
        after_schema = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        after_tables = tuple(row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ))
    finally:
        conn.close()
    assert before_schema == after_schema == "82"
    assert before_tables == after_tables


def test_persona_projection_shadow_never_changes_selected_production_prompt(tmp_path, monkeypatch):
    static, manifest, _ = persona_v2.compile_candidate(mode="companionship")
    fingerprint = persona_v2.model_fingerprint({
        "id": "deepseek", "base_url": "https://api.deepseek.com", "execution_location": "remote",
    }, "deepseek-v4-flash")
    certificate = tmp_path / "certifications.json"
    certificate.write_text(json.dumps({
        "certifications": [{
            "model_fingerprint": fingerprint,
            "profile_version": manifest["profile_version"],
            "compiler_version": manifest["compiler_version"],
            "compiled_hashes": {"companionship": hashlib.sha256(static.encode()).hexdigest()},
            "sampling_profile": {"temperature": 0.0}, "status": "certified",
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(persona_v2, "CERTIFICATIONS_PATH", certificate)
    value = projection.build(**_inputs())
    assert value
    shadow = persona_v2.compile_for_request(
        legacy_prompt=persona.PERSONA_PROMPT, mode="companionship", style=None,
        provider={"id": "deepseek", "base_url": "https://api.deepseek.com", "execution_location": "remote"},
        model="deepseek-v4-flash", rollout_mode="active",
        projection=value.as_mapping(), projection_rollout_mode="shadow",
    )
    active = persona_v2.compile_for_request(
        legacy_prompt=persona.PERSONA_PROMPT, mode="companionship", style=None,
        provider={"id": "deepseek", "base_url": "https://api.deepseek.com", "execution_location": "remote"},
        model="deepseek-v4-flash", rollout_mode="active",
        projection=value.as_mapping(), projection_rollout_mode="active",
    )
    assert shadow.selected_v2 and shadow.prompt == static
    assert "本轮只读状态投影" in shadow.candidate_prompt
    assert active.selected_v2 and "本轮只读状态投影" in active.prompt
    assert shadow.compiled_hash == active.compiled_hash == hashlib.sha256(static.encode()).hexdigest()


def test_persona_rejects_unbounded_or_body_bearing_projection():
    invalid = _inputs()
    value = projection.build(**invalid)
    assert value
    payload = value.as_mapping() | {"inner_monologue": "hidden body"}
    result = persona_v2.compile_for_request(
        legacy_prompt=persona.PERSONA_PROMPT, mode="companionship", style=None,
        provider=None, model="mock", rollout_mode="shadow",
        projection=payload, projection_rollout_mode="shadow",
    )
    assert result.prompt == persona.PERSONA_PROMPT
    assert result.fallback_reason == "persona_resource_invalid"
    assert "hidden body" not in result.candidate_prompt
