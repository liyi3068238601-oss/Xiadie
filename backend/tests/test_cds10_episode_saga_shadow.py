from __future__ import annotations

from dataclasses import replace
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from app import cognitive_decision as cds
from app import db, entities, episode_summary, memory
from app import episode_saga_shadow as shadow

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "cds10_episode_saga_shadow_v1.json"
QUALITY_PATH = Path(__file__).parent / "fixtures" / "cds10_episode_saga_quality_v1.json"
REPORT_PATH = PROJECT_DIR / "docs" / "reports" / "cds-10-episode-saga-shadow.json"
MARKDOWN_PATH = PROJECT_DIR / "docs" / "reports" / "cds-10-episode-saga-shadow.md"
GENERATOR_PATH = BACKEND_DIR / "scripts" / "generate_cds10_episode_saga_fixture.py"
RUNNER_PATH = BACKEND_DIR / "scripts" / "run_cds10_episode_saga_shadow.py"


@pytest.fixture(autouse=True)
def isolated_cds10_database():
    original_data_dir, original_db_path = db.DATA_DIR, db.DB_PATH
    data_dir = tempfile.mkdtemp(prefix="xiadie-cds10-test-")
    db.DATA_DIR = data_dir
    db.DB_PATH = os.path.join(data_dir, "xiadie.db")
    db.init_db()
    try:
        yield
    finally:
        db.DATA_DIR, db.DB_PATH = original_data_dir, original_db_path
        shutil.rmtree(data_dir, ignore_errors=True)


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _episode_input(**changes):
    candidate_ids = ("fragment-a", "fragment-b", "fragment-c")
    values = {
        "candidate_ids": candidate_ids,
        "same_goal": True,
        "causal_chain": True,
        "turning_point_ids": ("fragment-b",),
        "outcome_present": True,
        "projected_confidence": "high",
        "source_bindings": tuple(
            shadow.NarrativeSourceBinding(item, "memory_fragment", "1", "a" * 64)
            for item in candidate_ids
        ),
        "candidate_provenance": shadow.NarrativeCandidateProvenance(
            "episode-candidate", "memory_episode_candidate", "pending",
            "episode-group-v1", "b" * 64,
        ),
    }
    values.update(changes)
    return shadow.EpisodeBoundaryInput(**values)


def _saga_input(**changes):
    candidate_ids = ("episode-a", "episode-b")
    values = {
        "candidate_ids": candidate_ids,
        "target_saga_id": None,
        "target_status": None,
        "transition_hint": "create_new",
        "evidence_origin": "observed",
        "projected_confidence": "high",
        "source_bindings": tuple(
            shadow.NarrativeSourceBinding(item, "memory_episode", "1", "a" * 64)
            for item in candidate_ids
        ),
        "candidate_provenance": shadow.NarrativeCandidateProvenance(
            "saga-candidate", "saga_group_candidate", "qualified",
            "saga-group-v1", "b" * 64,
        ),
    }
    values.update(changes)
    if values["target_saga_id"] and "target_binding" not in changes:
        values["target_binding"] = shadow.NarrativeSourceBinding(
            values["target_saga_id"], "memory_saga", "1", "c" * 64,
        )
    return shadow.SagaTransitionInput(**values)


def _real_episode(summary: str, stamp: float) -> str:
    fragment = memory.create_memory("L1", summary)
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET created_at=?,updated_at=? WHERE id=?",
            (stamp, stamp, fragment["id"]),
        )
        source = dict(conn.execute(
            "SELECT * FROM memory_fragments WHERE id=?", (fragment["id"],)
        ).fetchone())
        episode_id = db.new_id()
        conn.execute(
            "INSERT INTO memory_episodes("
            "id,title,summary,start_at,end_at,status,source,source_fragment_ids_json,source_hash,"
            "summary_status,summary_protocol_version,summary_evidence_json,created_at,updated_at)"
            " VALUES(?,?,?,?,?,'active','automatic',?,?,'extractive_fallback',"
            "'episode-extractive-v1',?,?,?)",
            (
                episode_id, summary[:80], summary, stamp, stamp + 60,
                json.dumps([fragment["id"]]), episode_summary.source_hash([source]),
                json.dumps([fragment["id"]]), stamp, stamp,
            ),
        )
        conn.execute(
            "INSERT INTO memory_episode_fragments VALUES(?,?,0,?)",
            (episode_id, fragment["id"], stamp),
        )
        conn.commit()
        return episode_id
    finally:
        conn.close()


def _episode_candidate(fragment_ids: list[str]) -> str:
    candidate_id = db.new_id()
    now = db.now()
    entity = entities.create_entity(f"CDS10 Episode候选-{candidate_id}", "project")
    for fragment_id in fragment_ids:
        assert entities.link_fragment(entity["id"], fragment_id, source="test")
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO memory_episode_candidates("
            "id,title,summary,start_at,end_at,significance,confidence,status,grouping_key,created_at,"
            "entity_score,text_score,time_score,coherence_score,score_details_json,policy_version,"
            "expires_at,last_evaluated_at,summary_source_hash) "
            "VALUES(?,?,?,?,?,4,0.8,'pending',?,?,0.8,0.8,0.8,0.8,'{}',?,?,?,'')",
            (
                candidate_id, "测试候选", "测试候选", now, now,
                shadow.episodes._grouping_fingerprint(fragment_ids), now,
                shadow.episodes.GROUP_POLICY_VERSION, now + 1000, now,
            ),
        )
        for position, fragment_id in enumerate(fragment_ids):
            conn.execute(
                "INSERT INTO memory_episode_candidate_fragments(candidate_id,fragment_id,position) VALUES(?,?,?)",
                (candidate_id, fragment_id, position),
            )
        conn.commit()
        return candidate_id
    finally:
        conn.close()


def _saga_candidate(
    episode_ids: list[str], *, mode: str = "create", target_saga_id: str | None = None,
) -> str:
    candidate_id = db.new_id()
    now = db.now()
    entity = entities.create_entity(f"CDS10 Saga候选-{candidate_id}", "project")
    conn = db.connect()
    try:
        for episode_id in episode_ids:
            conn.execute(
                "INSERT INTO memory_episode_entities(episode_id,entity_id,created_at) VALUES(?,?,?)",
                (episode_id, entity["id"], now),
            )
        conn.execute(
            "INSERT INTO saga_group_candidates("
            "id,grouping_fingerprint,status,episode_ids_json,shared_entity_ids_json,entity_score,"
            "text_score,time_score,coherence_score,total_score,score_details_json,policy_version,"
            "first_seen_at,last_evaluated_at,expires_at,application_mode,target_saga_id) "
            "VALUES(?,?,'qualified',?,'[]',0.8,0.8,0.8,0.8,0.8,?,?,?, ?,?,?,?)",
            (
                candidate_id, shadow.sagas.grouping_fingerprint(episode_ids),
                json.dumps(episode_ids), json.dumps({"qualified": True}),
                shadow.sagas.POLICY_VERSION, now, now, now + 1000, mode, target_saga_id,
            ),
        )
        conn.commit()
        return candidate_id
    finally:
        conn.close()


def test_registry_exposes_two_distinct_shadow_only_mem_contracts():
    episode = cds.REGISTRY.get(shadow.EPISODE_DECISION_KIND)
    saga = cds.REGISTRY.get(shadow.SAGA_DECISION_KIND)
    assert episode.mode is saga.mode is cds.DecisionMode.SHADOW
    assert episode.input_type is shadow.EpisodeBoundaryInput
    assert saga.input_type is shadow.SagaTransitionInput
    assert episode.input_schema_hash != saga.input_schema_hash
    assert episode.output_schema_hash != saga.output_schema_hash
    assert episode.application_owner == saga.application_owner == "mem"
    assert episode.fallback_owner == saga.fallback_owner == "mem"


def test_fixture_is_deterministic_synthetic_balanced_and_semantically_unique():
    fixture = _fixture()
    assert fixture == runpy.run_path(str(GENERATOR_PATH))["build_fixture"]()
    assert fixture["synthetic_only"] is True and fixture["contains_user_data"] is False
    assert fixture["scenario_count"] == len(fixture["cases"]) == 240
    assert len({case["group"] for case in fixture["cases"]}) == 12
    assert {
        kind: sum(case["decision_kind"] == kind for case in fixture["cases"])
        for kind in {case["decision_kind"] for case in fixture["cases"]}
    } == {shadow.EPISODE_DECISION_KIND: 100, shadow.SAGA_DECISION_KIND: 140}
    assert len({
        json.dumps(case["input"], sort_keys=True, ensure_ascii=False)
        for case in fixture["cases"]
    }) == 240
    quality = json.loads(QUALITY_PATH.read_text(encoding="utf-8"))
    assert quality == runpy.run_path(str(GENERATOR_PATH))["build_quality_corpus"]()
    assert quality["corpus_role"] == "labeled_raw_narrative_regression"
    assert quality["candidate_path"] == "real_database_candidates"
    assert quality["label_authorship"] == "human_authored_synthetic_not_reviewed"
    assert quality["sample_count"] == len(quality["cases"])
    assert all("raw_narrative" in case and "input" not in case for case in quality["cases"])


def test_independent_oracle_checks_safety_without_expected_outputs():
    runner = runpy.run_path(str(RUNNER_PATH))
    oracle = __import__("app.episode_saga_shadow_oracle", fromlist=["safety_violations"])
    for case in _fixture()["cases"]:
        payload = runner["_with_synthetic_bindings"](runner["payload_from_case"](case))
        result = shadow.episode_fallback(payload) if case["decision_kind"] == shadow.EPISODE_DECISION_KIND else shadow.saga_fallback(payload)
        assert oracle.safety_violations(case["decision_kind"], payload, result) == ()


def test_independent_oracle_rejects_provenance_bindings_and_member_shape_tampering():
    oracle = __import__("app.episode_saga_shadow_oracle", fromlist=["safety_violations"])
    episode_payload = _episode_input()
    episode_result = replace(
        shadow.episode_fallback(episode_payload),
        selected_ids=("fragment-a", "fragment-c"), excluded_ids=("fragment-b",),
        boundary_end_id="fragment-c", turning_point_ids=(),
    )
    assert "episode_members_non_contiguous" in oracle.safety_violations(
        shadow.EPISODE_DECISION_KIND, episode_payload, episode_result,
    )
    assert "episode_goal_mismatch_selected" in oracle.safety_violations(
        shadow.EPISODE_DECISION_KIND, episode_payload,
        replace(shadow.episode_fallback(episode_payload), same_goal=False),
    )
    assert "episode_causal_chain_missing_selected" in oracle.safety_violations(
        shadow.EPISODE_DECISION_KIND, episode_payload,
        replace(shadow.episode_fallback(episode_payload), causal_chain=False),
    )
    assert "episode_turning_points_duplicate" in oracle.safety_violations(
        shadow.EPISODE_DECISION_KIND, episode_payload,
        replace(
            shadow.episode_fallback(episode_payload),
            turning_point_ids=("fragment-b", "fragment-b"),
        ),
    )
    skipped_without_reason = replace(
        shadow.episode_fallback(episode_payload),
        action="skip", selected_ids=(), excluded_ids=episode_payload.candidate_ids,
        boundary_start_id=None, boundary_end_id=None, turning_point_ids=(),
        proposed_action="skip", reason_codes=("bounded_narrative",),
    )
    assert "episode_skip_without_reason" in oracle.safety_violations(
        shadow.EPISODE_DECISION_KIND, episode_payload, skipped_without_reason,
    )
    assert "episode_reason_matrix_invalid" in oracle.safety_violations(
        shadow.EPISODE_DECISION_KIND, episode_payload,
        replace(
            shadow.episode_fallback(episode_payload),
            reason_codes=("causal_chain_missing",),
        ),
    )
    assert "candidate_provenance_missing" in oracle.safety_violations(
        shadow.EPISODE_DECISION_KIND,
        replace(episode_payload, candidate_provenance=None),
        shadow.episode_fallback(replace(episode_payload, candidate_provenance=None)),
    )
    assert "source_binding_mismatch" in oracle.safety_violations(
        shadow.EPISODE_DECISION_KIND,
        replace(episode_payload, source_bindings=episode_payload.source_bindings[:-1]),
        shadow.episode_fallback(episode_payload),
    )
    saga_payload = _saga_input()
    saga_result = replace(shadow.saga_fallback(saga_payload), selected_ids=("episode-a",))
    assert "saga_member_count_invalid" in oracle.safety_violations(
        shadow.SAGA_DECISION_KIND, saga_payload, saga_result,
    )
    assert "saga_reason_matrix_invalid" in oracle.safety_violations(
        shadow.SAGA_DECISION_KIND, saga_payload,
        replace(
            shadow.saga_fallback(saga_payload),
            reason_codes=("merge_requires_review",),
        ),
    )


def test_runner_binds_target_saga_as_source_but_not_candidate():
    runner = runpy.run_path(str(RUNNER_PATH))
    case = next(case for case in _fixture()["cases"] if case["group"] == "saga_append")
    payload = runner["_with_synthetic_bindings"](runner["payload_from_case"](case))
    source = shadow.input_source_snapshots(payload)
    candidates = shadow.candidate_refs(payload.source_bindings)
    assert [item.kind for item in source] == [
        "memory_episode", "memory_episode", "memory_saga", "saga_group_candidate",
    ]
    assert [item.id for item in candidates] == list(payload.candidate_ids)
    assert payload.target_saga_id not in {item.id for item in candidates}


def test_episode_projection_selects_only_bounded_candidate_members():
    payload = _episode_input()
    result = shadow.episode_fallback(payload)
    shadow.validate_episode(payload, result)
    assert result.proposed_action == "form_episode"
    assert result.selected_ids == payload.candidate_ids
    assert result.excluded_ids == ()
    assert result.boundary_start_id == "fragment-a"
    assert result.boundary_end_id == "fragment-c"
    with pytest.raises(cds.DecisionProtocolError, match="candidate"):
        shadow.validate_episode(payload, replace(result, selected_ids=("outside",)))


def test_episode_low_confidence_skips_and_validator_rejects_semantic_drift():
    payload = _episode_input(projected_confidence="low")
    result = shadow.episode_fallback(payload)
    shadow.validate_episode(payload, result)
    assert result.action == "skip"
    assert result.proposed_action == "skip"
    assert result.selected_ids == ()
    with pytest.raises(cds.DecisionProtocolError, match="matrix"):
        shadow.validate_episode(payload, replace(result, proposed_action="form_episode"))


def test_episode_validator_accepts_safe_model_boundary_difference():
    payload = _episode_input()
    result = replace(
        shadow.episode_fallback(payload),
        selected_ids=("fragment-a", "fragment-b"),
        excluded_ids=("fragment-c",),
        boundary_end_id="fragment-b",
        outcome_present=False,
    )
    shadow.validate_episode(payload, result)


def test_episode_validator_rejects_inconsistent_candidate_action_and_boundaries():
    payload = _episode_input()
    result = shadow.episode_fallback(payload)
    invalid_results = (
        replace(result, action="skip"),
        replace(result, excluded_ids=("fragment-c",)),
        replace(result, boundary_start_id="fragment-b"),
        replace(
            result, selected_ids=("fragment-a", "fragment-b"),
            excluded_ids=("fragment-c",), boundary_end_id="fragment-b",
            turning_point_ids=("fragment-c",),
        ),
    )
    for invalid in invalid_results:
        with pytest.raises(cds.DecisionProtocolError):
            shadow.validate_episode(payload, invalid)


def test_episode_validator_rejects_semantically_incoherent_action_matrix():
    payload = _episode_input()
    result = shadow.episode_fallback(payload)
    invalid_results = (
        replace(result, same_goal=False),
        replace(result, causal_chain=False),
        replace(result, reason_codes=("goal_mismatch",)),
        replace(result, turning_point_ids=("fragment-b", "fragment-b")),
        replace(
            result, action="skip", selected_ids=(), excluded_ids=payload.candidate_ids,
            boundary_start_id=None, boundary_end_id=None, turning_point_ids=(),
            proposed_action="skip", reason_codes=("bounded_narrative",),
        ),
    )
    for invalid in invalid_results:
        with pytest.raises(cds.DecisionProtocolError):
            shadow.validate_episode(payload, invalid)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, "create_new"),
        ({"target_saga_id": "saga-a", "target_status": "active", "transition_hint": "append_existing"}, "append_existing"),
        ({"target_saga_id": "saga-a", "target_status": "active", "transition_hint": "branch"}, "branch"),
        ({"target_saga_id": "saga-a", "target_status": "active", "transition_hint": "pause"}, "pause"),
        ({"target_saga_id": "saga-a", "target_status": "active", "transition_hint": "complete"}, "complete"),
        ({"target_saga_id": "saga-a", "target_status": "completed", "transition_hint": "revive", "evidence_origin": "user_confirmed"}, "revive"),
    ],
)
def test_saga_projection_supports_bounded_transition_matrix(changes, expected):
    payload = _saga_input(**changes)
    result = shadow.saga_fallback(payload)
    shadow.validate_saga(payload, result)
    assert result.proposed_transition == expected
    assert result.selected_ids == payload.candidate_ids
    assert result.target_saga_id == payload.target_saga_id
    assert result.advisory_only is True


def test_saga_merge_is_shadow_only_and_never_executable():
    payload = _saga_input(
        target_saga_id="saga-a", target_status="active",
        transition_hint="merge_suggestion",
    )
    result = shadow.saga_fallback(payload)
    shadow.validate_saga(payload, result)
    assert result.proposed_transition == "merge_suggestion"
    assert result.high_impact is True
    assert result.execution_allowed is False
    with pytest.raises(cds.DecisionProtocolError, match="merge"):
        shadow.validate_saga(payload, replace(result, execution_allowed=True))


def test_saga_revive_requires_user_confirmed_evidence():
    payload = _saga_input(
        target_saga_id="saga-a", target_status="completed",
        transition_hint="revive", evidence_origin="automatic",
    )
    result = shadow.saga_fallback(payload)
    shadow.validate_saga(payload, result)
    assert result.proposed_transition == "skip"
    assert result.selected_ids == ()
    forged = replace(
        result, action="select", selected_ids=payload.candidate_ids,
        proposed_transition="revive", target_saga_id="saga-a",
    )
    with pytest.raises(cds.DecisionProtocolError, match="revive"):
        shadow.validate_saga(payload, forged)


def test_saga_low_confidence_uses_skip_projection():
    payload = _saga_input(projected_confidence="low")
    result = shadow.saga_fallback(payload)
    shadow.validate_saga(payload, result)
    assert result.action == "skip"
    assert result.proposed_transition == "skip"
    assert result.selected_ids == ()


def test_saga_validator_accepts_safe_model_transition_and_candidate_differences():
    payload = _saga_input(
        target_saga_id="saga-a", target_status="active",
        transition_hint="append_existing",
    )
    result = replace(
        shadow.saga_fallback(payload),
        selected_ids=("episode-a", "episode-b"), proposed_transition="branch",
    )
    shadow.validate_saga(payload, result)


def test_saga_validator_rejects_inconsistent_state_action_and_impact_flags():
    payload = _saga_input(
        target_saga_id="saga-a", target_status="active",
        transition_hint="append_existing",
    )
    result = shadow.saga_fallback(payload)
    invalid_results = (
        replace(result, action="skip"),
        replace(result, target_saga_id=None),
        replace(result, proposed_transition="revive"),
        replace(result, high_impact=True),
    )
    for invalid in invalid_results:
        with pytest.raises(cds.DecisionProtocolError):
            shadow.validate_saga(payload, invalid)


def test_saga_validator_rejects_reason_codes_that_disagree_with_transition():
    branch_payload = _saga_input(
        target_saga_id="saga-a", target_status="active", transition_hint="branch",
    )
    branch = shadow.saga_fallback(branch_payload)
    with pytest.raises(cds.DecisionProtocolError, match="matrix"):
        shadow.validate_saga(
            branch_payload, replace(branch, reason_codes=("merge_requires_review",)),
        )

    merge_payload = _saga_input(
        target_saga_id="saga-a", target_status="active",
        transition_hint="merge_suggestion",
    )
    merge = shadow.saga_fallback(merge_payload)
    with pytest.raises(cds.DecisionProtocolError, match="matrix"):
        shadow.validate_saga(
            merge_payload, replace(merge, reason_codes=("bounded_transition",)),
        )


def test_common_validators_reject_direct_application_and_unlisted_members():
    episode_payload = _episode_input()
    episode_result = shadow.episode_fallback(episode_payload)
    with pytest.raises(cds.DecisionProtocolError) as error:
        shadow.validate_episode(episode_payload, replace(episode_result, advisory_only=False))
    assert error.value.code == "application_boundary_invalid"
    saga_payload = _saga_input()
    saga_result = shadow.saga_fallback(saga_payload)
    with pytest.raises(cds.DecisionProtocolError, match="candidate"):
        shadow.validate_saga(saga_payload, replace(saga_result, selected_ids=("outside",)))


def test_validators_reject_binding_tamper_and_illegal_target_state():
    binding = shadow.NarrativeSourceBinding(
        source_id="fragment-a", source_kind="memory_fragment",
        revision="1", content_hash="a" * 64,
    )
    payload = _episode_input(source_bindings=(binding,))
    with pytest.raises(cds.DecisionProtocolError) as error:
        shadow.validate_episode(payload, shadow.episode_fallback(payload))
    assert error.value.code == "source_binding_mismatch"
    saga_payload = _saga_input(
        target_saga_id="saga-a", target_status="completed",
        transition_hint="pause",
    )
    with pytest.raises(cds.DecisionProtocolError) as error:
        shadow.validate_saga(saga_payload, shadow.saga_fallback(saga_payload))
    assert error.value.code == "saga_target_state_invalid"


def test_validators_require_complete_candidate_provenance_and_source_bindings():
    episode_payload = _episode_input(candidate_provenance=None)
    with pytest.raises(cds.DecisionProtocolError) as error:
        shadow.validate_episode(episode_payload, shadow.episode_fallback(episode_payload))
    assert error.value.code == "candidate_provenance_missing"
    saga_payload = _saga_input(candidate_provenance=None)
    with pytest.raises(cds.DecisionProtocolError) as error:
        shadow.validate_saga(saga_payload, shadow.saga_fallback(saga_payload))
    assert error.value.code == "candidate_provenance_missing"


def test_episode_validator_requires_contiguous_selected_boundaries():
    payload = _episode_input()
    result = replace(
        shadow.episode_fallback(payload),
        selected_ids=("fragment-a", "fragment-c"),
        excluded_ids=("fragment-b",),
        boundary_end_id="fragment-c",
        turning_point_ids=(),
    )
    with pytest.raises(cds.DecisionProtocolError) as error:
        shadow.validate_episode(payload, result)
    assert error.value.code == "episode_boundary_non_contiguous"


def test_saga_validator_requires_at_least_two_selected_members():
    payload = _saga_input(
        target_saga_id="saga-a", target_status="active",
        transition_hint="append_existing",
    )
    result = replace(shadow.saga_fallback(payload), selected_ids=("episode-a",))
    with pytest.raises(cds.DecisionProtocolError) as error:
        shadow.validate_saga(payload, result)
    assert error.value.code == "saga_member_count_invalid"


def test_real_adapters_reject_raw_source_ids_without_candidate_provenance():
    first = memory.create_memory("L1", "候选来源开始")
    memory.create_memory("L1", "候选来源完成")
    with pytest.raises(cds.DecisionProtocolError) as error:
        shadow.build_episode_input(first["id"])
    assert error.value.code == "candidate_provenance_missing"


def test_real_adapters_recheck_candidate_source_eligibility():
    first = memory.create_memory("L1", "资格复核开始")
    second = memory.create_memory("L1", "资格复核完成")
    candidate_id = _episode_candidate([first["id"], second["id"]])
    conn = db.connect()
    try:
        conn.execute("UPDATE memory_fragments SET enabled=0 WHERE id=?", (first["id"],))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(cds.DecisionProtocolError) as error:
        shadow.build_episode_input(candidate_id)
    assert error.value.code == "candidate_source_ineligible"

    first_episode = _real_episode("资格故事开始", 100.0)
    second_episode = _real_episode("资格故事完成", 100.0 + 86_400)
    saga_candidate_id = _saga_candidate([first_episode, second_episode])
    conn = db.connect()
    try:
        conn.execute("UPDATE memory_episodes SET status='tombstone' WHERE id=?", (first_episode,))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(cds.DecisionProtocolError) as error:
        shadow.build_saga_input(saga_candidate_id)
    assert error.value.code == "candidate_source_ineligible"
    first_episode = _real_episode("候选故事开始", 100.0)
    second_episode = _real_episode("候选故事完成", 100.0 + 86_400)
    with pytest.raises(cds.DecisionProtocolError) as error:
        shadow.build_saga_input(first_episode, transition_hint="create_new")
    assert error.value.code == "candidate_provenance_missing"


def test_reload_current_snapshots_recomputes_episode_eligibility_fail_closed():
    first = memory.create_memory("L1", "共同项目开始推进")
    second = memory.create_memory("L1", "共同项目完成结果")
    candidate_id = _episode_candidate([first["id"], second["id"]])
    payload = shadow.build_episode_input(candidate_id)
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET created_at=created_at+? WHERE id=?",
            (shadow.episodes.WINDOW_SECONDS + 1, second["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(cds.DecisionProtocolError) as error:
        shadow.reload_current_snapshots(payload)
    assert error.value.code == "candidate_source_ineligible"


def test_reload_current_snapshots_recomputes_saga_eligibility_fail_closed():
    first = _real_episode("共同项目开始推进", 100.0)
    second = _real_episode("共同项目完成结果", 100.0 + 86_400)
    candidate_id = _saga_candidate([first, second])
    payload = shadow.build_saga_input(candidate_id)
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_episodes SET start_at=100,end_at=160 WHERE id=?", (second,),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(cds.DecisionProtocolError) as error:
        shadow.reload_current_snapshots(payload)
    assert error.value.code == "candidate_source_ineligible"


def test_reload_current_snapshots_includes_candidate_provenance_mutations():
    first = memory.create_memory("L1", "候选账本开始")
    second = memory.create_memory("L1", "候选账本完成")
    candidate_id = _episode_candidate([first["id"], second["id"]])
    payload = shadow.build_episode_input(candidate_id)
    expected = shadow.input_source_snapshots(payload)
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_episode_candidates SET title='候选账本已修改' WHERE id=?",
            (candidate_id,),
        )
        conn.commit()
    finally:
        conn.close()
    current = shadow.reload_current_snapshots(payload)
    assert {(item.kind, item.id) for item in current} == {
        (item.kind, item.id) for item in expected
    }
    assert current[-1].kind == "memory_episode_candidate"
    assert current[-1].content_hash != expected[-1].content_hash


def test_reload_current_snapshots_includes_saga_target_and_deep_dependencies():
    first = _real_episode("长期项目开始推进", 100.0)
    second = _real_episode("长期项目继续完成", 100.0 + 86_400)
    saga_id = db.new_id()
    now = db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO memory_sagas("
            "id,title,summary,theme,current_stage,start_at,end_at,status,source,"
            "source_episode_ids_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,'active','automatic',?,?,?)",
            (
                saga_id, "长期项目", "长期项目继续", "项目", "继续", 100.0,
                100.0 + 86_460, json.dumps([first]), now, now,
            ),
        )
        conn.execute(
            "INSERT INTO memory_saga_episodes(saga_id,episode_id,position,role,added_at) "
            "VALUES(?,?,0,'anchor',?)", (saga_id, first, now),
        )
        conn.commit()
    finally:
        conn.close()
    candidate_id = _saga_candidate(
        [first, second], mode="append", target_saga_id=saga_id,
    )
    payload = shadow.build_saga_input(candidate_id)
    expected = shadow.input_source_snapshots(payload)
    conn = db.connect()
    try:
        fragment_id = conn.execute(
            "SELECT fragment_id FROM memory_episode_fragments WHERE episode_id=?", (first,),
        ).fetchone()["fragment_id"]
        conn.execute(
            "UPDATE memory_fragments SET observation_source='user_confirmed_fact' WHERE id=?",
            (fragment_id,),
        )
        conn.execute(
            "UPDATE memory_sagas SET current_stage='新的阶段',revision=revision+1 WHERE id=?",
            (saga_id,),
        )
        conn.commit()
    finally:
        conn.close()
    current = shadow.reload_current_snapshots(payload)
    expected_map = {(item.kind, item.id): item for item in expected}
    current_map = {(item.kind, item.id): item for item in current}
    assert current_map[("memory_episode", first)] != expected_map[("memory_episode", first)]
    assert current_map[("memory_saga", saga_id)] != expected_map[("memory_saga", saga_id)]


def test_episode_chain_hash_changes_when_fragment_message_provenance_changes():
    first = _real_episode("消息来源开始", 100.0)
    second = _real_episode("消息来源完成", 100.0 + 86_400)
    before = shadow.load_source_bindings((first, second), "memory_episode")
    conn = db.connect()
    try:
        fragment_id = conn.execute(
            "SELECT fragment_id FROM memory_episode_fragments WHERE episode_id=?", (first,)
        ).fetchone()["fragment_id"]
        conn.execute("UPDATE memory_fragments SET observation_source='user_confirmed_fact' WHERE id=?", (fragment_id,))
        conn.commit()
    finally:
        conn.close()
    after = shadow.load_source_bindings((first, second), "memory_episode")
    assert before[0].content_hash != after[0].content_hash
    assert before[1].content_hash == after[1].content_hash


def test_real_episode_adapter_uses_one_snapshot_and_binds_projection_dependencies(monkeypatch):
    first = memory.create_memory("L1", "共同项目开始")
    second = memory.create_memory("L1", "共同项目完成")
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET created_at=100,updated_at=100,lifecycle_revision=3 WHERE id=?",
            (first["id"],),
        )
        conn.execute(
            "UPDATE memory_fragments SET created_at=200,updated_at=200,lifecycle_revision=4 WHERE id=?",
            (second["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    original_connect = db.connect
    calls = []

    def counted_connect():
        calls.append(None)
        return original_connect()

    candidate_id = _episode_candidate([first["id"], second["id"]])
    monkeypatch.setattr(db, "connect", counted_connect)
    payload = shadow.build_episode_input(candidate_id)
    assert len(calls) == 1
    assert tuple(item.revision for item in payload.source_bindings) == ("3", "4")
    assert payload.candidate_ids == (first["id"], second["id"])
    assert shadow.candidate_refs(payload.source_bindings)[0].source_kind == "memory_fragment"
    before = payload.source_bindings[0].content_hash
    monkeypatch.setattr(db, "connect", original_connect)
    conn = db.connect()
    try:
        conn.execute("UPDATE memory_fragments SET kind='correction' WHERE id=?", (first["id"],))
        conn.commit()
    finally:
        conn.close()
    after = shadow.load_source_bindings((first["id"],), "memory_fragment")[0].content_hash
    assert after != before


def test_episode_source_hash_binds_entity_link_dependencies():
    first = memory.create_memory("L1", "实体依赖开始")
    second = memory.create_memory("L1", "实体依赖完成")
    candidate_id = _episode_candidate([first["id"], second["id"]])
    before = shadow.build_episode_input(candidate_id)
    entity = entities.create_entity(f"CDS10实体-{db.new_id()}", "project")
    assert entities.link_fragment(entity["id"], first["id"], source="test")
    after = shadow.build_episode_input(candidate_id)
    assert after.source_bindings[0].content_hash != before.source_bindings[0].content_hash
    assert after.source_bindings[1].content_hash == before.source_bindings[1].content_hash


def test_source_hash_binds_active_entity_state_but_ignores_archived_entities():
    first = memory.create_memory("L1", "实体状态依赖开始")
    second = memory.create_memory("L1", "实体状态依赖完成")
    candidate_id = _episode_candidate([first["id"], second["id"]])
    entity = entities.create_entity(f"CDS10状态实体-{db.new_id()}", "project")
    assert entities.link_fragment(entity["id"], first["id"], source="test")
    before = shadow.build_episode_input(candidate_id).source_bindings[0].content_hash
    conn = db.connect()
    try:
        conn.execute("UPDATE memory_entities SET current_status='推进中' WHERE id=?", (entity["id"],))
        conn.commit()
    finally:
        conn.close()
    active_changed = shadow.build_episode_input(candidate_id).source_bindings[0].content_hash
    assert active_changed != before
    conn = db.connect()
    try:
        conn.execute("UPDATE memory_entities SET status='archived' WHERE id=?", (entity["id"],))
        conn.commit()
    finally:
        conn.close()
    archived = shadow.build_episode_input(candidate_id)
    assert archived.source_bindings[0].content_hash != active_changed
    conn = db.connect()
    try:
        conn.execute("UPDATE memory_entities SET current_status='已结束' WHERE id=?", (entity["id"],))
        conn.commit()
    finally:
        conn.close()
    assert shadow.build_episode_input(candidate_id).source_bindings[0].content_hash == archived.source_bindings[0].content_hash


def test_saga_entity_link_mutation_recomputes_eligibility_fail_closed():
    first = _real_episode("实体依赖项目开始", 100.0)
    second = _real_episode("实体依赖项目完成", 100.0 + 86_400)
    candidate_id = _saga_candidate([first, second])
    before = shadow.build_saga_input(candidate_id, transition_hint="create_new")
    entity = entities.create_entity(f"CDS10 Saga实体-{db.new_id()}", "project")
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO memory_episode_entities(episode_id,entity_id,created_at) VALUES(?,?,?)",
            (first, entity["id"], db.now()),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(cds.DecisionProtocolError) as error:
        shadow.reload_current_snapshots(before)
    assert error.value.code == "candidate_source_ineligible"


def test_real_saga_adapter_binds_episode_revisions_and_rejects_stale_runtime_source():
    first = _real_episode("共同项目开始", 100.0)
    second = _real_episode("共同项目完成", 100.0 + 86_400)
    conn = db.connect()
    try:
        conn.execute("UPDATE memory_episodes SET lifecycle_revision=5 WHERE id=?", (first,))
        conn.execute("UPDATE memory_episodes SET lifecycle_revision=6 WHERE id=?", (second,))
        conn.commit()
    finally:
        conn.close()
    candidate_id = _saga_candidate([first, second])
    payload = shadow.build_saga_input(candidate_id, transition_hint="create_new")
    assert tuple(item.revision for item in payload.source_bindings) == ("5", "6")
    source = shadow.input_source_snapshots(payload)
    candidates = shadow.candidate_refs(payload.source_bindings)
    definition = cds.REGISTRY.get(shadow.SAGA_DECISION_KIND)
    header = cds.build_header(
        decision_kind=shadow.SAGA_DECISION_KIND,
        policy_version=definition.output_schema_version,
        request_id=f"cds10-stale-{first}",
        mode=cds.DecisionMode.SHADOW,
        source_snapshot=source,
    )
    run, _ = cds.create_run(header, payload, candidates)
    conn = db.connect()
    try:
        conn.execute("UPDATE memory_episodes SET significance=9 WHERE id=?", (first,))
        conn.commit()
    finally:
        conn.close()
    current = shadow.reload_current_snapshots(payload)
    outcome = cds.evaluate_output(
        run.id, header, payload, json.dumps(shadow.saga_fallback(payload).__dict__),
        current_snapshot=current,
    )
    assert outcome["error_code"] == "source_revision_changed"
    assert outcome["application_allowed"] is False


def _formal_episode_with_fragment(fragment_id: str, stamp: float = 100.0) -> str:
    """创建一个正式 Episode 并将指定 Fragment 加入,模拟片段已被归属的场景."""
    episode_id = db.new_id()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO memory_episodes("
            "id,title,summary,start_at,end_at,status,source,source_fragment_ids_json,source_hash,"
            "summary_status,summary_protocol_version,summary_evidence_json,created_at,updated_at)"
            " VALUES(?,?,?,?,?,'active','automatic',?,?,'extractive_fallback',"
            "'episode-extractive-v1',?,?,?)",
            (
                episode_id, "正式归属 Episode", "正式归属 Episode", stamp, stamp + 60,
                json.dumps([fragment_id]), "formal-hash", json.dumps([fragment_id]),
                stamp, stamp,
            ),
        )
        conn.execute(
            "INSERT INTO memory_episode_fragments(episode_id,fragment_id,position,created_at)"
            " VALUES(?,?,0,?)",
            (episode_id, fragment_id, stamp),
        )
        conn.commit()
        return episode_id
    finally:
        conn.close()


def _formal_saga_with_episode(episode_id: str, stamp: float = 100.0) -> str:
    """创建一个正式 Saga 并将指定 Episode 加入,模拟故事已被归属的场景."""
    saga_id = db.new_id()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO memory_sagas("
            "id,title,summary,theme,current_stage,start_at,end_at,status,source,"
            "source_episode_ids_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,'active','automatic',?,?,?)",
            (
                saga_id, "正式归属 Saga", "正式归属 Saga", "项目", "继续", stamp,
                stamp + 86_400, json.dumps([episode_id]), stamp, stamp,
            ),
        )
        conn.execute(
            "INSERT INTO memory_saga_episodes(saga_id,episode_id,position,role,added_at)"
            " VALUES(?,?,0,'anchor',?)",
            (saga_id, episode_id, stamp),
        )
        conn.commit()
        return saga_id
    finally:
        conn.close()


def test_episode_eligibility_rejects_fragment_already_in_formal_episode():
    first = memory.create_memory("L1", "归属检查开始")
    second = memory.create_memory("L1", "归属检查完成")
    candidate_id = _episode_candidate([first["id"], second["id"]])
    _formal_episode_with_fragment(first["id"], stamp=50.0)
    with pytest.raises(cds.DecisionProtocolError) as error:
        shadow.build_episode_input(candidate_id)
    assert error.value.code == "candidate_source_ineligible"


def test_saga_eligibility_rejects_episode_already_in_formal_saga_create_mode():
    first = _real_episode("故事归属开始", 100.0)
    second = _real_episode("故事归属完成", 100.0 + 86_400)
    candidate_id = _saga_candidate([first, second], mode="create")
    _formal_saga_with_episode(first, stamp=50.0)
    with pytest.raises(cds.DecisionProtocolError) as error:
        shadow.build_saga_input(candidate_id, transition_hint="create_new")
    assert error.value.code == "candidate_source_ineligible"


def test_saga_eligibility_accepts_episode_in_target_saga_append_mode():
    first = _real_episode("追加故事已有", 100.0)
    second = _real_episode("追加故事已有二", 100.0 + 86_400)
    third = _real_episode("追加故事新增", 100.0 + 2 * 86_400)
    target_saga_id = db.new_id()
    now = db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO memory_sagas("
            "id,title,summary,theme,current_stage,start_at,end_at,status,source,"
            "source_episode_ids_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,'active','automatic',?,?,?)",
            (
                target_saga_id, "追加目标 Saga", "追加目标 Saga", "项目", "继续", 100.0,
                100.0 + 86_400, json.dumps([first, second]), now, now,
            ),
        )
        for position, episode_id in enumerate((first, second)):
            conn.execute(
                "INSERT INTO memory_saga_episodes(saga_id,episode_id,position,role,added_at)"
                " VALUES(?,?,?,'anchor',?)",
                (target_saga_id, episode_id, position, now),
            )
        conn.commit()
    finally:
        conn.close()
    candidate_id = _saga_candidate(
        [first, second, third], mode="append", target_saga_id=target_saga_id,
    )
    payload = shadow.build_saga_input(candidate_id, transition_hint="append_existing")
    assert payload.target_saga_id == target_saga_id
    assert set(payload.candidate_ids) == {first, second, third}


def test_saga_eligibility_rejects_episode_in_other_saga_append_mode():
    first = _real_episode("跨故事开始", 100.0)
    second = _real_episode("跨故事完成", 100.0 + 86_400)
    third = _real_episode("跨故事冲突", 100.0 + 2 * 86_400)
    target_saga_id = _formal_saga_with_episode(first, stamp=50.0)
    _formal_saga_with_episode(third, stamp=60.0)
    candidate_id = _saga_candidate(
        [first, second, third], mode="append", target_saga_id=target_saga_id,
    )
    with pytest.raises(cds.DecisionProtocolError) as error:
        shadow.build_saga_input(candidate_id, transition_hint="append_existing")
    assert error.value.code == "candidate_source_ineligible"


def test_fragment_source_hash_binds_reverse_episode_membership():
    first = memory.create_memory("L1", "反向依赖片段开始")
    second = memory.create_memory("L1", "反向依赖片段完成")
    candidate_id = _episode_candidate([first["id"], second["id"]])
    before = shadow.build_episode_input(candidate_id)
    _formal_episode_with_fragment(first["id"], stamp=50.0)
    after = shadow.load_source_bindings((first["id"], second["id"]), "memory_fragment")
    assert after[0].content_hash != before.source_bindings[0].content_hash
    assert after[1].content_hash == before.source_bindings[1].content_hash


def test_episode_source_hash_binds_reverse_saga_membership():
    first = _real_episode("反向故事开始", 100.0)
    second = _real_episode("反向故事完成", 100.0 + 86_400)
    before = shadow.load_source_bindings((first, second), "memory_episode")
    _formal_saga_with_episode(first, stamp=50.0)
    after = shadow.load_source_bindings((first, second), "memory_episode")
    assert after[0].content_hash != before[0].content_hash
    assert after[1].content_hash == before[1].content_hash


def test_reload_current_snapshots_fails_when_fragment_added_to_formal_episode():
    first = memory.create_memory("L1", "重载归属开始")
    second = memory.create_memory("L1", "重载归属完成")
    candidate_id = _episode_candidate([first["id"], second["id"]])
    payload = shadow.build_episode_input(candidate_id)
    _formal_episode_with_fragment(first["id"], stamp=50.0)
    with pytest.raises(cds.DecisionProtocolError) as error:
        shadow.reload_current_snapshots(payload)
    assert error.value.code == "candidate_source_ineligible"


def test_reload_current_snapshots_fails_when_episode_added_to_formal_saga():
    first = _real_episode("重载故事开始", 100.0)
    second = _real_episode("重载故事完成", 100.0 + 86_400)
    candidate_id = _saga_candidate([first, second], mode="create")
    payload = shadow.build_saga_input(candidate_id, transition_hint="create_new")
    _formal_saga_with_episode(first, stamp=50.0)
    with pytest.raises(cds.DecisionProtocolError) as error:
        shadow.reload_current_snapshots(payload)
    assert error.value.code == "candidate_source_ineligible"


def test_runner_report_proves_shadow_ledger_and_mem_zero_write():
    subprocess.run([sys.executable, str(RUNNER_PATH)], cwd=BACKEND_DIR, check=True)
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["sample_count"] == 240
    assert report["proposal_exact_rate"] == 1.0
    assert report["candidate_subset_rate"] == 1.0
    assert report["low_confidence_selection_rate"] == 0.0
    assert report["merge_execution_rate"] == 0.0
    assert report["application_allowed_rate"] == 0.0
    assert report["safety_violation_count"] == 0
    assert report["shadow_ledger_write_count"] == 2
    assert report["mem_domain_write_count"] == 0
    assert report["schema_version"] == 81
    assert report["stage_schema_baseline"] == 62 and report["schema_changed"] is False
    assert report["application_owner"] == "mem"
    assert report["quality_corpus_role"] == "labeled_raw_narrative_regression"
    assert report["quality_metrics"]["candidate_path"] == "real_database_candidates"
    assert report["quality_metrics"]["label_authorship"] == "human_authored_synthetic_not_reviewed"
    assert report["quality_metrics"]["promotion_evidence_eligible"] is False
    assert report["quality_metrics"]["sample_count"] > 0
    assert 0.0 <= report["quality_metrics"]["accuracy"] <= 1.0
    assert report["quality_metrics"]["correct_count"] + report["quality_metrics"]["error_count"] == report["quality_metrics"]["sample_count"]
    encoded = REPORT_PATH.read_text(encoding="utf-8")
    for forbidden in ("user_text", "raw_model_output", "prompt", "fragment_content", "episode_summary"):
        assert forbidden not in encoded
    assert "MEM 领域表写入数 | 0" in MARKDOWN_PATH.read_text(encoding="utf-8")
