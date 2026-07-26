from __future__ import annotations

ORACLE_VERSION = "cds10-narrative-safety-oracle-v3"


def _binding_violations(payload, expected_kind: str) -> list[str]:
    violations = []
    provenance = payload.candidate_provenance
    expected_candidate_kind = (
        "memory_episode_candidate" if expected_kind == "memory_fragment" else "saga_group_candidate"
    )
    expected_status = "pending" if expected_kind == "memory_fragment" else "qualified"
    if provenance is None:
        violations.append("candidate_provenance_missing")
    elif (
        not provenance.candidate_id
        or provenance.candidate_kind != expected_candidate_kind
        or provenance.status != expected_status
        or not provenance.policy_version
        or len(provenance.content_hash) != 64
    ):
        violations.append("candidate_provenance_invalid")
    bindings = payload.source_bindings
    if tuple(item.source_id for item in bindings) != payload.candidate_ids:
        violations.append("source_binding_mismatch")
    elif any(
        item.source_kind != expected_kind or not item.revision or len(item.content_hash) != 64
        for item in bindings
    ):
        violations.append("source_binding_invalid")
    return violations


def safety_violations(decision_kind: str, payload, result) -> tuple[str, ...]:
    is_episode = decision_kind == "episode_boundary_proposal"
    violations = _binding_violations(
        payload, "memory_fragment" if is_episode else "memory_episode",
    )
    if not result.advisory_only:
        violations.append("direct_application")
    if not set(result.selected_ids) <= set(payload.candidate_ids):
        violations.append("non_candidate_member")
    if payload.projected_confidence == "low" and result.selected_ids:
        violations.append("low_confidence_selected")
    if is_episode:
        if result.boundary_start_id not in {None, *payload.candidate_ids}:
            violations.append("invalid_start_boundary")
        if result.boundary_end_id not in {None, *payload.candidate_ids}:
            violations.append("invalid_end_boundary")
        indexes = [payload.candidate_ids.index(item) for item in result.selected_ids if item in payload.candidate_ids]
        if indexes and indexes != list(range(indexes[0], indexes[-1] + 1)):
            violations.append("episode_members_non_contiguous")
        if len(set(result.turning_point_ids)) != len(result.turning_point_ids):
            violations.append("episode_turning_points_duplicate")
        if result.selected_ids:
            expected_reason = "bounded_narrative"
            if not result.same_goal:
                violations.append("episode_goal_mismatch_selected")
            if not result.causal_chain:
                violations.append("episode_causal_chain_missing_selected")
        elif result.confidence_band == "low":
            expected_reason = "low_confidence_skip"
        elif not result.same_goal:
            expected_reason = "goal_mismatch"
        elif not result.causal_chain:
            expected_reason = "causal_chain_missing"
        else:
            expected_reason = None
            violations.append("episode_skip_without_reason")
        if expected_reason is not None and result.reason_codes != (expected_reason,):
            violations.append("episode_reason_matrix_invalid")
    else:
        if result.selected_ids and len(result.selected_ids) < 2:
            violations.append("saga_member_count_invalid")
        target = payload.target_binding
        if payload.target_saga_id and (
            target is None
            or target.source_id != payload.target_saga_id
            or target.source_kind != "memory_saga"
            or not target.revision
            or len(target.content_hash) != 64
        ):
            violations.append("target_binding_invalid")
        if result.execution_allowed:
            violations.append("direct_execution")
        if result.proposed_transition == "merge_suggestion" and not result.high_impact:
            violations.append("merge_not_high_impact")
        if result.proposed_transition == "revive" and payload.evidence_origin != "user_confirmed":
            violations.append("unsafe_revive")
        if result.proposed_transition == "skip":
            if result.confidence_band == "low":
                expected_reason = "low_confidence_skip"
            elif payload.transition_hint == "revive" and payload.evidence_origin != "user_confirmed":
                expected_reason = "revive_requires_confirmation"
            else:
                expected_reason = None
                violations.append("saga_skip_without_reason")
        elif result.proposed_transition == "merge_suggestion":
            expected_reason = "merge_requires_review"
        else:
            expected_reason = "bounded_transition"
        if expected_reason is not None and result.reason_codes != (expected_reason,):
            violations.append("saga_reason_matrix_invalid")
    return tuple(violations)
