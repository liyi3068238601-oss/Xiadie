from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from . import cognitive_decision as cds, db, episode_summary, episodes, sagas

EPISODE_DECISION_KIND = "episode_boundary_proposal"
SAGA_DECISION_KIND = "saga_transition_proposal"
EPISODE_POLICY_VERSION = "episode-boundary-proposal-v1"
SAGA_POLICY_VERSION = "saga-transition-proposal-v1"
CONFIDENCE_BANDS = frozenset(item.value for item in cds.ConfidenceBand)
EPISODE_ACTIONS = frozenset({"form_episode", "skip"})
SAGA_TRANSITIONS = frozenset({
    "append_existing", "create_new", "branch", "pause", "revive", "complete",
    "merge_suggestion", "skip",
})
SAGA_STATUSES = frozenset({"active", "completed", "archived"})
EVIDENCE_ORIGINS = frozenset({"user_confirmed", "observed", "automatic", "system_injected"})
EPISODE_REASONS = frozenset({
    "bounded_narrative", "low_confidence_skip", "goal_mismatch", "causal_chain_missing",
})
SAGA_REASONS = frozenset({
    "bounded_transition", "low_confidence_skip", "revive_requires_confirmation",
    "merge_requires_review",
})


@dataclass(frozen=True)
class NarrativeSourceBinding:
    source_id: str
    source_kind: str
    revision: str
    content_hash: str


@dataclass(frozen=True)
class NarrativeCandidateProvenance:
    candidate_id: str
    candidate_kind: str
    status: str
    policy_version: str
    content_hash: str


@dataclass(frozen=True)
class EpisodeBoundaryInput:
    candidate_ids: tuple[str, ...]
    same_goal: bool
    causal_chain: bool
    turning_point_ids: tuple[str, ...]
    outcome_present: bool
    projected_confidence: str
    source_bindings: tuple[NarrativeSourceBinding, ...] = ()
    candidate_provenance: NarrativeCandidateProvenance | None = None


@dataclass(frozen=True)
class EpisodeBoundaryProposal:
    action: str
    selected_ids: tuple[str, ...]
    excluded_ids: tuple[str, ...]
    boundary_start_id: str | None
    boundary_end_id: str | None
    proposed_action: str
    same_goal: bool
    causal_chain: bool
    turning_point_ids: tuple[str, ...]
    outcome_present: bool
    reason_codes: tuple[str, ...]
    confidence_band: str
    advisory_only: bool


@dataclass(frozen=True)
class SagaTransitionInput:
    candidate_ids: tuple[str, ...]
    target_saga_id: str | None
    target_status: str | None
    transition_hint: str
    evidence_origin: str
    projected_confidence: str
    source_bindings: tuple[NarrativeSourceBinding, ...] = ()
    target_binding: NarrativeSourceBinding | None = None
    candidate_provenance: NarrativeCandidateProvenance | None = None


@dataclass(frozen=True)
class SagaTransitionProposal:
    action: str
    selected_ids: tuple[str, ...]
    proposed_transition: str
    target_saga_id: str | None
    reason_codes: tuple[str, ...]
    confidence_band: str
    high_impact: bool
    execution_allowed: bool
    advisory_only: bool


def load_source_bindings(
    source_ids: tuple[str, ...], source_kind: str,
) -> tuple[NarrativeSourceBinding, ...]:
    ordered = tuple(dict.fromkeys(str(item) for item in source_ids if item))
    if not ordered:
        return ()
    conn = db.connect()
    try:
        conn.execute("BEGIN")
        bindings, _ = _load_bindings_from_connection(conn, ordered, source_kind)
        conn.commit()
        return bindings
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def source_snapshots(bindings: tuple[NarrativeSourceBinding, ...]) -> tuple[cds.SourceSnapshot, ...]:
    return tuple(cds.SourceSnapshot(
        item.source_kind, item.source_id, item.revision, item.content_hash,
    ) for item in bindings)


def candidate_refs(bindings: tuple[NarrativeSourceBinding, ...]) -> tuple[cds.CandidateRef, ...]:
    return tuple(cds.CandidateRef(
        item.source_id, item.source_kind, item.content_hash,
    ) for item in bindings)


def input_source_snapshots(payload: EpisodeBoundaryInput | SagaTransitionInput) -> tuple[cds.SourceSnapshot, ...]:
    bindings = payload.source_bindings
    if isinstance(payload, SagaTransitionInput) and payload.target_binding is not None:
        bindings = (*bindings, payload.target_binding)
    snapshots = source_snapshots(bindings)
    if payload.candidate_provenance is not None:
        provenance = payload.candidate_provenance
        snapshots = (*snapshots, cds.SourceSnapshot(
            provenance.candidate_kind, provenance.candidate_id,
            provenance.policy_version, provenance.content_hash,
        ))
    return snapshots


def reload_current_snapshots(
    payload: EpisodeBoundaryInput | SagaTransitionInput,
) -> tuple[cds.SourceSnapshot, ...]:
    provenance = payload.candidate_provenance
    if provenance is None:
        raise cds.DecisionProtocolError(
            "candidate_provenance_missing", "narrative candidate provenance is required",
        )
    conn = db.connect()
    try:
        conn.execute("BEGIN")
        if isinstance(payload, EpisodeBoundaryInput):
            current, ordered = _load_episode_candidate_provenance(
                conn, provenance.candidate_id,
            )
            bindings, snapshots = _load_bindings_from_connection(
                conn, ordered, "memory_fragment",
            )
            _require_episode_eligibility(conn, ordered, snapshots)
            target_binding = None
        else:
            current, ordered, mode, target_id = _load_saga_candidate_provenance(
                conn, provenance.candidate_id,
            )
            bindings, snapshots = _load_bindings_from_connection(
                conn, ordered, "memory_episode",
            )
            _require_saga_eligibility(
                conn, ordered, snapshots,
                target_saga_id=str(target_id) if target_id else None,
            )
            target_binding = None
            if mode == "append":
                if not target_id:
                    raise cds.DecisionProtocolError(
                        "candidate_ineligible", "saga append candidate requires a target",
                    )
                target_bindings, _ = _load_bindings_from_connection(
                    conn, (str(target_id),), "memory_saga",
                )
                target_binding = target_bindings[0]
            elif mode != "create" or target_id is not None:
                raise cds.DecisionProtocolError(
                    "candidate_ineligible", "saga candidate application mode is invalid",
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    current_payload = payload.__class__(**{
        **payload.__dict__,
        "candidate_ids": ordered,
        "source_bindings": bindings,
        "candidate_provenance": current,
        **({"target_binding": target_binding} if isinstance(payload, SagaTransitionInput) else {}),
    })
    return input_source_snapshots(current_payload)


def build_episode_input(candidate_id: str) -> EpisodeBoundaryInput:
    conn = db.connect()
    try:
        conn.execute("BEGIN")
        candidate, ordered = _load_episode_candidate_provenance(conn, candidate_id)
        bindings, snapshots = _load_bindings_from_connection(
            conn, ordered, "memory_fragment",
        )
        scores, entity_map = _require_episode_eligibility(conn, ordered, snapshots)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    shared = set.intersection(*(entity_map[item] for item in ordered)) if all(entity_map[item] for item in ordered) else set()
    same_goal = bool(shared or scores["text"] >= 0.15)
    causal_chain = bool(scores["time"] > 0 and scores["coherence"] >= 0.30)
    confidence = _confidence(scores["total"], episodes.GROUP_THRESHOLD)
    turning_points = tuple(
        item["id"] for item in snapshots[1:-1]
        if any(hint in str(item.get("content") or "") for hint in ("决定", "转折", "改为", "开始"))
    )
    outcome = any(
        hint in str(item.get("content") or "")
        for item in snapshots for hint in ("完成", "结束", "成功", "取消", "结果")
    )
    return EpisodeBoundaryInput(
        candidate_ids=ordered, same_goal=same_goal, causal_chain=causal_chain,
        turning_point_ids=turning_points, outcome_present=outcome,
        projected_confidence=confidence, source_bindings=bindings,
        candidate_provenance=candidate,
    )


def build_saga_input(
    candidate_id: str, *, transition_hint: str | None = None,
    target_saga_id: str | None = None, evidence_origin: str = "observed",
) -> SagaTransitionInput:
    conn = db.connect()
    try:
        conn.execute("BEGIN")
        candidate, ordered, candidate_mode, candidate_target = _load_saga_candidate_provenance(
            conn, candidate_id,
        )
        required_hint = "append_existing" if candidate_mode == "append" else "create_new"
        if transition_hint is not None and transition_hint != required_hint:
            raise cds.DecisionProtocolError("candidate_transition_mismatch", "saga transition does not match qualified candidate")
        transition_hint = required_hint
        if target_saga_id is not None and target_saga_id != candidate_target:
            raise cds.DecisionProtocolError("candidate_target_mismatch", "saga target does not match qualified candidate")
        target_saga_id = candidate_target
        bindings, snapshots = _load_bindings_from_connection(
            conn, ordered, "memory_episode",
        )
        scores, entity_map = _require_saga_eligibility(
            conn, ordered, snapshots, target_saga_id=target_saga_id,
        )
        target_binding = None
        target_status = None
        if target_saga_id:
            target_bindings, target_rows = _load_bindings_from_connection(
                conn, (target_saga_id,), "memory_saga",
            )
            target_binding = target_bindings[0]
            target_status = str(target_rows[0]["status"])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    confidence = _confidence(scores["total"], sagas.GROUP_THRESHOLD) if scores["theme_gate"] else cds.ConfidenceBand.LOW.value
    return SagaTransitionInput(
        candidate_ids=ordered, target_saga_id=target_saga_id,
        target_status=target_status, transition_hint=transition_hint,
        evidence_origin=evidence_origin, projected_confidence=confidence,
        source_bindings=bindings, target_binding=target_binding,
        candidate_provenance=candidate,
    )


def _load_episode_candidate_provenance(conn, candidate_id: str):
    row = conn.execute(
        "SELECT * FROM memory_episode_candidates WHERE id=?", (str(candidate_id),)
    ).fetchone()
    if not row or row["status"] != "pending":
        raise cds.DecisionProtocolError("candidate_provenance_missing", "pending episode candidate is required")
    links = conn.execute(
        "SELECT fragment_id,position FROM memory_episode_candidate_fragments "
        "WHERE candidate_id=? ORDER BY position,fragment_id", (candidate_id,),
    ).fetchall()
    ordered = tuple(str(item["fragment_id"]) for item in links)
    if (
        len(ordered) < episodes.MIN_GROUP_SIZE
        or row["policy_version"] != episodes.GROUP_POLICY_VERSION
        or row["application_attempt_count"] >= episodes.APPLICATION_MAX_ATTEMPTS
        or row["confidence"] < episodes.GROUP_THRESHOLD
        or row["grouping_key"] != episodes._grouping_fingerprint(list(ordered))
    ):
        raise cds.DecisionProtocolError("candidate_ineligible", "episode candidate is not eligible")
    provenance_hash = _candidate_hash(dict(row), [dict(item) for item in links])
    return NarrativeCandidateProvenance(
        str(row["id"]), "memory_episode_candidate", str(row["status"]),
        str(row["policy_version"]), provenance_hash,
    ), ordered


def _load_saga_candidate_provenance(conn, candidate_id: str):
    row = conn.execute(
        "SELECT * FROM saga_group_candidates WHERE id=?", (str(candidate_id),)
    ).fetchone()
    if not row or row["status"] != "qualified":
        raise cds.DecisionProtocolError("candidate_provenance_missing", "qualified saga candidate is required")
    ordered = tuple(str(item) for item in json.loads(row["episode_ids_json"]))
    scores = json.loads(row["score_details_json"])
    if (
        len(ordered) < sagas.MIN_GROUP_SIZE
        or row["policy_version"] != sagas.POLICY_VERSION
        or row["promoted_saga_id"] is not None
        or row["application_attempt_count"] >= 3
        or row["grouping_fingerprint"] != sagas.grouping_fingerprint(list(ordered))
        or not scores.get("qualified")
    ):
        raise cds.DecisionProtocolError("candidate_ineligible", "saga candidate is not eligible")
    provenance_hash = _candidate_hash(dict(row), [{"episode_id": item, "position": index} for index, item in enumerate(ordered)])
    return NarrativeCandidateProvenance(
        str(row["id"]), "saga_group_candidate", str(row["status"]),
        str(row["policy_version"]), provenance_hash,
    ), ordered, str(row["application_mode"]), row["target_saga_id"]


def _candidate_hash(row: dict, links: list[dict]) -> str:
    encoded = json.dumps(
        {"candidate": {key: row[key] for key in sorted(row)}, "links": links},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _require_episode_eligibility(conn, ordered: tuple[str, ...], snapshots: tuple[dict, ...]):
    if any(
        item.get("status") != "active" or not item.get("enabled")
        or item.get("sensitivity") != "normal"
        or item.get("observation_source") not in {"conversation", "user_confirmed_fact"}
        or not episode_summary.is_safe_source(str(item.get("content") or ""))
        for item in snapshots
    ):
        raise cds.DecisionProtocolError(
            "candidate_source_ineligible", "episode candidate source is no longer eligible",
        )
    placeholders = ",".join("?" for _ in ordered)
    occupied = conn.execute(
        f"SELECT fragment_id FROM memory_episode_fragments WHERE fragment_id IN ({placeholders}) LIMIT 1",
        ordered,
    ).fetchone()
    if occupied:
        raise cds.DecisionProtocolError(
            "candidate_source_ineligible", "episode candidate source already belongs to a formal episode",
        )
    entity_map = _fragment_entity_map(conn, ordered)
    try:
        scores = episodes.score_group(list(snapshots), entity_map)
    except ValueError as exc:
        raise cds.DecisionProtocolError(
            "candidate_source_ineligible", "episode candidate source is no longer eligible",
        ) from exc
    if scores["total"] < episodes.GROUP_THRESHOLD:
        raise cds.DecisionProtocolError(
            "candidate_source_ineligible", "episode candidate no longer meets the score threshold",
        )
    return scores, entity_map


def _require_saga_eligibility(
    conn, ordered: tuple[str, ...], snapshots: tuple[dict, ...],
    target_saga_id: str | None = None,
):
    if any(item.get("status") not in {"active", "completed"} for item in snapshots):
        raise cds.DecisionProtocolError(
            "candidate_source_ineligible", "saga candidate source is no longer eligible",
        )
    placeholders = ",".join("?" for _ in ordered)
    conflict_rows = conn.execute(
        f"SELECT episode_id,saga_id FROM memory_saga_episodes WHERE removed_at IS NULL"
        f" AND episode_id IN ({placeholders}) ORDER BY episode_id,saga_id",
        ordered,
    ).fetchall()
    for row in conflict_rows:
        if row["saga_id"] != target_saga_id:
            raise cds.DecisionProtocolError(
                "candidate_source_ineligible", "saga candidate source already belongs to another saga",
            )
    entity_map = _episode_entity_map(conn, ordered)
    try:
        scores = sagas.assess_group(list(snapshots), entity_map)
    except ValueError as exc:
        raise cds.DecisionProtocolError(
            "candidate_source_ineligible", "saga candidate source is no longer eligible",
        ) from exc
    if not scores["qualified"]:
        raise cds.DecisionProtocolError(
            "candidate_source_ineligible", "saga candidate no longer meets the qualification gates",
        )
    return scores, entity_map


def _load_bindings_from_connection(conn, ordered: tuple[str, ...], source_kind: str):
    table, revision_column = {
        "memory_fragment": ("memory_fragments", "lifecycle_revision"),
        "memory_episode": ("memory_episodes", "lifecycle_revision"),
        "memory_saga": ("memory_sagas", "revision"),
    }.get(source_kind, (None, None))
    if table is None:
        raise cds.DecisionProtocolError("source_kind_invalid", "narrative source kind is invalid")
    rows = conn.execute(
        f'SELECT * FROM "{table}" WHERE id IN ({",".join("?" for _ in ordered)})', ordered,
    ).fetchall()
    by_id = {str(row["id"]): dict(row) for row in rows}
    if set(by_id) != set(ordered):
        raise cds.DecisionProtocolError("source_missing", "narrative source binding is incomplete")
    snapshots = tuple(by_id[item] for item in ordered)
    entity_map = _source_entity_map(conn, ordered, source_kind)
    bindings = tuple(NarrativeSourceBinding(
        source_id=item["id"], source_kind=source_kind,
        revision=str(item[revision_column]),
        content_hash=_source_hash(conn, source_kind, item, entity_map[item["id"]]),
    ) for item in snapshots)
    return bindings, snapshots


def _source_hash(conn, source_kind: str, row: dict, entity_ids: set[str]) -> str:
    excluded = {"last_lifecycle_evaluated_at"}
    dependencies = []
    reverse_dependencies = []
    if source_kind == "memory_fragment":
        reverse_links = conn.execute(
            "SELECT ef.episode_id,ef.position,e.status AS episode_status "
            "FROM memory_episode_fragments ef "
            "JOIN memory_episodes e ON e.id=ef.episode_id "
            "WHERE ef.fragment_id=? ORDER BY ef.episode_id", (row["id"],),
        ).fetchall()
        reverse_dependencies = [dict(item) for item in reverse_links]
    elif source_kind == "memory_episode":
        links = conn.execute(
            "SELECT ef.position,f.* FROM memory_episode_fragments ef "
            "JOIN memory_fragments f ON f.id=ef.fragment_id WHERE ef.episode_id=? "
            "ORDER BY ef.position,f.id", (row["id"],),
        ).fetchall()
        dependencies = [dict(item) for item in links]
        reverse_links = conn.execute(
            "SELECT se.saga_id,se.position,se.role,s.status AS saga_status "
            "FROM memory_saga_episodes se "
            "JOIN memory_sagas s ON s.id=se.saga_id "
            "WHERE se.episode_id=? AND se.removed_at IS NULL "
            "ORDER BY se.saga_id", (row["id"],),
        ).fetchall()
        reverse_dependencies = [dict(item) for item in reverse_links]
    elif source_kind == "memory_saga":
        links = conn.execute(
            "SELECT se.position,se.role,e.* FROM memory_saga_episodes se "
            "JOIN memory_episodes e ON e.id=se.episode_id WHERE se.saga_id=? "
            "AND se.removed_at IS NULL ORDER BY se.position,e.id", (row["id"],),
        ).fetchall()
        dependencies = [dict(item) for item in links]
    active_entities = []
    if entity_ids:
        entity_rows = conn.execute(
            f"SELECT * FROM memory_entities WHERE status='active' AND id IN "
            f"({','.join('?' for _ in entity_ids)}) ORDER BY id",
            tuple(sorted(entity_ids)),
        ).fetchall()
        active_entities = [dict(item) for item in entity_rows]
    encoded = json.dumps({
        "source_kind": source_kind,
        "values": {key: row[key] for key in sorted(row) if key not in excluded},
        "active_entities": active_entities,
        "dependencies": dependencies,
        "reverse_dependencies": reverse_dependencies,
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _source_entity_map(
    conn, source_ids: tuple[str, ...], source_kind: str,
) -> dict[str, set[str]]:
    table, id_column = {
        "memory_fragment": ("memory_fragment_entities", "fragment_id"),
        "memory_episode": ("memory_episode_entities", "episode_id"),
        "memory_saga": ("memory_saga_entities", "saga_id"),
    }[source_kind]
    result = {item: set() for item in source_ids}
    rows = conn.execute(
        f'SELECT links."{id_column}" AS source_id,links.entity_id FROM "{table}" links '
        f'JOIN memory_entities entities ON entities.id=links.entity_id AND entities.status="active" '
        f'WHERE links."{id_column}" IN ({",".join("?" for _ in source_ids)})',
        source_ids,
    ).fetchall()
    for row in rows:
        result[row["source_id"]].add(row["entity_id"])
    return result


def _fragment_entity_map(conn, source_ids: tuple[str, ...]) -> dict[str, set[str]]:
    result = {item: set() for item in source_ids}
    rows = conn.execute(
        f"SELECT links.fragment_id,links.entity_id FROM memory_fragment_entities links "
        f"JOIN memory_entities entities ON entities.id=links.entity_id AND entities.status='active' "
        f"WHERE links.fragment_id IN ({','.join('?' for _ in source_ids)})",
        source_ids,
    ).fetchall()
    for row in rows:
        result[row["fragment_id"]].add(row["entity_id"])
    return result


def _episode_entity_map(conn, source_ids: tuple[str, ...]) -> dict[str, set[str]]:
    result = {item: set() for item in source_ids}
    rows = conn.execute(
        f"SELECT links.episode_id,links.entity_id FROM memory_episode_entities links "
        f"JOIN memory_entities entities ON entities.id=links.entity_id AND entities.status='active' "
        f"WHERE links.episode_id IN ({','.join('?' for _ in source_ids)})",
        source_ids,
    ).fetchall()
    for row in rows:
        result[row["episode_id"]].add(row["entity_id"])
    return result


def _confidence(total: float, threshold: float) -> str:
    if total >= threshold:
        return cds.ConfidenceBand.HIGH.value
    if total >= threshold * 0.80:
        return cds.ConfidenceBand.MEDIUM.value
    return cds.ConfidenceBand.LOW.value


def episode_fallback(payload: EpisodeBoundaryInput) -> EpisodeBoundaryProposal:
    selected = payload.candidate_ids
    reason = "bounded_narrative"
    if payload.projected_confidence == cds.ConfidenceBand.LOW.value:
        selected = ()
        reason = "low_confidence_skip"
    elif not payload.same_goal:
        selected = ()
        reason = "goal_mismatch"
    elif not payload.causal_chain:
        selected = ()
        reason = "causal_chain_missing"
    return EpisodeBoundaryProposal(
        action=cds.DecisionAction.SELECT.value if selected else cds.DecisionAction.SKIP.value,
        selected_ids=selected,
        excluded_ids=tuple(item for item in payload.candidate_ids if item not in selected),
        boundary_start_id=selected[0] if selected else None,
        boundary_end_id=selected[-1] if selected else None,
        proposed_action="form_episode" if selected else "skip",
        same_goal=payload.same_goal,
        causal_chain=payload.causal_chain,
        turning_point_ids=payload.turning_point_ids if selected else (),
        outcome_present=payload.outcome_present,
        reason_codes=(reason,),
        confidence_band=payload.projected_confidence,
        advisory_only=True,
    )


def saga_fallback(payload: SagaTransitionInput) -> SagaTransitionProposal:
    transition = payload.transition_hint
    selected = payload.candidate_ids
    reason = "bounded_transition"
    if payload.projected_confidence == cds.ConfidenceBand.LOW.value:
        transition, selected, reason = "skip", (), "low_confidence_skip"
    elif transition == "revive" and payload.evidence_origin != "user_confirmed":
        transition, selected, reason = "skip", (), "revive_requires_confirmation"
    elif transition == "merge_suggestion":
        reason = "merge_requires_review"
    return SagaTransitionProposal(
        action=cds.DecisionAction.SELECT.value if selected else cds.DecisionAction.SKIP.value,
        selected_ids=selected,
        proposed_transition=transition,
        target_saga_id=payload.target_saga_id if selected else None,
        reason_codes=(reason,),
        confidence_band=payload.projected_confidence,
        high_impact=transition == "merge_suggestion",
        execution_allowed=False,
        advisory_only=True,
    )


def validate_episode(payload: EpisodeBoundaryInput, result: EpisodeBoundaryProposal) -> None:
    _validate_candidate_ids(payload.candidate_ids, 2, 20)
    _validate_candidate_provenance(payload.candidate_provenance, "memory_episode_candidate", "pending")
    _validate_bindings(payload.candidate_ids, payload.source_bindings, "memory_fragment")
    if payload.projected_confidence not in CONFIDENCE_BANDS:
        raise cds.DecisionProtocolError("confidence_invalid", "episode confidence is invalid")
    if not isinstance(payload.same_goal, bool) or not isinstance(payload.causal_chain, bool) or not isinstance(payload.outcome_present, bool):
        raise cds.DecisionProtocolError("episode_input_invalid", "episode narrative signals are invalid")
    if not set(payload.turning_point_ids) <= set(payload.candidate_ids):
        raise cds.DecisionProtocolError("candidate_not_allowed", "episode turning points must be candidates")
    if result.proposed_action not in EPISODE_ACTIONS:
        raise cds.DecisionProtocolError("episode_action_invalid", "episode action is invalid")
    if (
        not isinstance(result.selected_ids, tuple)
        or not isinstance(result.excluded_ids, tuple)
        or not isinstance(result.turning_point_ids, tuple)
        or len(set(result.selected_ids)) != len(result.selected_ids)
        or len(set(result.excluded_ids)) != len(result.excluded_ids)
        or not set(result.selected_ids) <= set(payload.candidate_ids)
        or not set(result.excluded_ids) <= set(payload.candidate_ids)
    ):
        raise cds.DecisionProtocolError("candidate_not_allowed", "episode proposal selected an unknown candidate")
    if payload.projected_confidence == cds.ConfidenceBand.LOW.value and result != episode_fallback(payload):
        raise cds.DecisionProtocolError("episode_action_matrix_invalid", "low confidence episode matrix requires fallback")
    selected = bool(result.selected_ids)
    if selected:
        indexes = [payload.candidate_ids.index(item) for item in result.selected_ids]
        if indexes != list(range(indexes[0], indexes[-1] + 1)):
            raise cds.DecisionProtocolError("episode_boundary_non_contiguous", "episode boundaries must be contiguous")
    if (
        result.action != (cds.DecisionAction.SELECT.value if selected else cds.DecisionAction.SKIP.value)
        or result.proposed_action != ("form_episode" if selected else "skip")
        or set(result.selected_ids).isdisjoint(result.excluded_ids) is False
        or set(result.selected_ids) | set(result.excluded_ids) != set(payload.candidate_ids)
        or tuple(item for item in payload.candidate_ids if item in result.selected_ids) != result.selected_ids
        or result.boundary_start_id != (result.selected_ids[0] if selected else None)
        or result.boundary_end_id != (result.selected_ids[-1] if selected else None)
        or not set(result.turning_point_ids) <= set(result.selected_ids)
        or not isinstance(result.same_goal, bool)
        or not isinstance(result.causal_chain, bool)
        or not isinstance(result.outcome_present, bool)
        or (selected and len(result.selected_ids) < 2)
        or (selected and result.confidence_band == cds.ConfidenceBand.LOW.value)
    ):
        raise cds.DecisionProtocolError("episode_action_matrix_invalid", "episode proposal matrix is invalid")
    _validate_common(result.reason_codes, result.confidence_band, result.advisory_only, EPISODE_REASONS)


def validate_saga(payload: SagaTransitionInput, result: SagaTransitionProposal) -> None:
    _validate_candidate_ids(payload.candidate_ids, 2, 12)
    _validate_candidate_provenance(payload.candidate_provenance, "saga_group_candidate", "qualified")
    _validate_bindings(payload.candidate_ids, payload.source_bindings, "memory_episode")
    if payload.transition_hint not in SAGA_TRANSITIONS - {"skip"}:
        raise cds.DecisionProtocolError("saga_transition_invalid", "saga transition hint is invalid")
    if payload.projected_confidence not in CONFIDENCE_BANDS or payload.evidence_origin not in EVIDENCE_ORIGINS:
        raise cds.DecisionProtocolError("saga_input_invalid", "saga input is invalid")
    if payload.target_status is not None and payload.target_status not in SAGA_STATUSES:
        raise cds.DecisionProtocolError("saga_status_invalid", "saga target status is invalid")
    requires_target = payload.transition_hint != "create_new"
    if requires_target != bool(payload.target_saga_id) or (payload.target_saga_id is None) != (payload.target_status is None):
        raise cds.DecisionProtocolError("saga_target_invalid", "saga target does not match transition")
    if requires_target != bool(payload.target_binding):
        raise cds.DecisionProtocolError("source_binding_missing", "saga target binding is required")
    if payload.target_binding is not None and (
        payload.target_binding.source_id != payload.target_saga_id
        or payload.target_binding.source_kind != "memory_saga"
        or not payload.target_binding.revision
        or len(payload.target_binding.content_hash) != 64
    ):
        raise cds.DecisionProtocolError("source_binding_mismatch", "saga target binding changed")
    allowed_target_statuses = {
        "append_existing": {"active", "completed"},
        "branch": {"active", "completed"},
        "pause": {"active"},
        "revive": {"completed", "archived"},
        "complete": {"active"},
        "merge_suggestion": {"active", "completed"},
    }
    if payload.transition_hint in allowed_target_statuses and payload.target_status not in allowed_target_statuses[payload.transition_hint]:
        raise cds.DecisionProtocolError("saga_target_state_invalid", "saga target state does not allow transition")
    if (
        not isinstance(result.selected_ids, tuple)
        or len(set(result.selected_ids)) != len(result.selected_ids)
        or not set(result.selected_ids) <= set(payload.candidate_ids)
        or tuple(item for item in payload.candidate_ids if item in result.selected_ids) != result.selected_ids
    ):
        raise cds.DecisionProtocolError("candidate_not_allowed", "saga proposal selected an unknown candidate")
    if result.proposed_transition not in SAGA_TRANSITIONS:
        raise cds.DecisionProtocolError("saga_transition_invalid", "saga transition is invalid")
    if payload.projected_confidence == cds.ConfidenceBand.LOW.value and result != saga_fallback(payload):
        raise cds.DecisionProtocolError("saga_action_matrix_invalid", "low confidence saga must use fallback")
    if result.proposed_transition == "revive" and payload.evidence_origin != "user_confirmed":
        raise cds.DecisionProtocolError("revive_source_invalid", "saga revive requires user confirmation")
    if result.proposed_transition == "merge_suggestion" and result.execution_allowed:
        raise cds.DecisionProtocolError("merge_execution_forbidden", "saga merge cannot execute automatically")
    selected = bool(result.selected_ids)
    if selected and len(result.selected_ids) < sagas.MIN_GROUP_SIZE:
        raise cds.DecisionProtocolError("saga_member_count_invalid", "saga proposal requires at least two members")
    result_requires_target = result.proposed_transition not in {"create_new", "skip"}
    allowed_target_statuses = {
        "append_existing": {"active", "completed"},
        "branch": {"active", "completed"},
        "pause": {"active"},
        "revive": {"completed", "archived"},
        "complete": {"active"},
        "merge_suggestion": {"active", "completed"},
    }
    if (
        result.execution_allowed is not False
        or result.action != (cds.DecisionAction.SELECT.value if selected else cds.DecisionAction.SKIP.value)
        or selected != (result.proposed_transition != "skip")
        or result.target_saga_id != (payload.target_saga_id if result_requires_target else None)
        or (result_requires_target and payload.target_status not in allowed_target_statuses[result.proposed_transition])
        or result.high_impact != (result.proposed_transition == "merge_suggestion")
        or (selected and result.confidence_band == cds.ConfidenceBand.LOW.value)
    ):
        raise cds.DecisionProtocolError("saga_action_matrix_invalid", "saga proposal matrix is invalid")
    _validate_common(result.reason_codes, result.confidence_band, result.advisory_only, SAGA_REASONS)


def _validate_candidate_ids(candidate_ids: tuple[str, ...], minimum: int, maximum: int) -> None:
    if not isinstance(candidate_ids, tuple) or not minimum <= len(candidate_ids) <= maximum:
        raise cds.DecisionProtocolError("candidate_snapshot_mismatch", "candidate count is invalid")
    if any(not item for item in candidate_ids) or len(set(candidate_ids)) != len(candidate_ids):
        raise cds.DecisionProtocolError("candidate_identity_invalid", "candidate identities are invalid")


def _validate_bindings(
    candidate_ids: tuple[str, ...], bindings: tuple[NarrativeSourceBinding, ...], source_kind: str,
) -> None:
    if not bindings:
        raise cds.DecisionProtocolError("source_binding_missing", "narrative source bindings are required")
    if tuple(item.source_id for item in bindings) != candidate_ids:
        raise cds.DecisionProtocolError("source_binding_mismatch", "narrative source bindings changed")
    if any(
        item.source_kind != source_kind or not item.revision or len(item.content_hash) != 64
        for item in bindings
    ):
        raise cds.DecisionProtocolError("source_binding_invalid", "narrative source binding is invalid")


def _validate_candidate_provenance(
    provenance: NarrativeCandidateProvenance | None, candidate_kind: str, status: str,
) -> None:
    if provenance is None:
        raise cds.DecisionProtocolError("candidate_provenance_missing", "narrative candidate provenance is required")
    if (
        not provenance.candidate_id
        or provenance.candidate_kind != candidate_kind
        or provenance.status != status
        or not provenance.policy_version
        or len(provenance.content_hash) != 64
    ):
        raise cds.DecisionProtocolError("candidate_provenance_invalid", "narrative candidate provenance is invalid")


def _validate_common(reason_codes: tuple[str, ...], confidence_band: str, advisory_only: bool, allowed_reasons: frozenset[str]) -> None:
    if advisory_only is not True:
        raise cds.DecisionProtocolError("application_boundary_invalid", "CDS narrative proposals cannot apply directly")
    if not isinstance(reason_codes, tuple) or not reason_codes or not set(reason_codes) <= allowed_reasons:
        raise cds.DecisionProtocolError("reason_code_not_allowed", "narrative proposal reason is invalid")
    if confidence_band not in CONFIDENCE_BANDS:
        raise cds.DecisionProtocolError("confidence_invalid", "confidence band is invalid")


cds.REGISTRY.register(cds.DecisionKindDefinition(
    decision_kind=EPISODE_DECISION_KIND,
    input_type=EpisodeBoundaryInput,
    result_type=EpisodeBoundaryProposal,
    input_schema_version="episode-boundary-input-v1",
    output_schema_version=EPISODE_POLICY_VERSION,
    validator=validate_episode,
    validator_version="episode-boundary-validator-v1",
    fallback=episode_fallback,
    fallback_version="episode-boundary-pure-fallback-v1",
    fallback_owner="mem",
    application_owner="mem",
    privacy_class="user_private_body_free",
    max_candidates=20,
    timeout_seconds=8.0,
    result_ttl_seconds=cds.DIAGNOSTIC_TTL_SECONDS,
    model_binding_revision=cds.MODEL_BINDING_POLICY_VERSION,
    mode=cds.DecisionMode.SHADOW,
    prompt_template_hash=cds._canonical_hash(EPISODE_POLICY_VERSION),
))

cds.REGISTRY.register(cds.DecisionKindDefinition(
    decision_kind=SAGA_DECISION_KIND,
    input_type=SagaTransitionInput,
    result_type=SagaTransitionProposal,
    input_schema_version="saga-transition-input-v1",
    output_schema_version=SAGA_POLICY_VERSION,
    validator=validate_saga,
    validator_version="saga-transition-validator-v1",
    fallback=saga_fallback,
    fallback_version="saga-transition-pure-fallback-v1",
    fallback_owner="mem",
    application_owner="mem",
    privacy_class="user_private_body_free",
    max_candidates=12,
    timeout_seconds=8.0,
    result_ttl_seconds=cds.DIAGNOSTIC_TTL_SECONDS,
    model_binding_revision=cds.MODEL_BINDING_POLICY_VERSION,
    mode=cds.DecisionMode.SHADOW,
    prompt_template_hash=cds._canonical_hash(SAGA_POLICY_VERSION),
))
