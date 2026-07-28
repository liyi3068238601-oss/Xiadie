"""Sourced Personal World Model projections and reversible entity resolution.

PWM is a rebuildable navigation layer.  Every write is Shadow-only and is bound
to current owner-system SourceRef metadata; no source body is copied here.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable

from . import db, kig_sources

PROTOCOL_VERSION = "pwm-projection-v1"
RESOLUTION_PROTOCOL_VERSION = "pwm-entity-resolution-v1"
ENTITY_TYPES = frozenset({
    "user", "agent", "project", "organization", "document", "repository", "model",
    "provider", "tool", "task", "goal", "person", "place", "concept", "important_date",
    "event", "product", "other",
})
PREDICATES = frozenset({
    "alias_of", "owns", "uses", "depends_on", "part_of", "references", "works_on",
    "plans", "prefers", "created", "completed", "supersedes", "related_to",
    "occurred_at", "involves",
})
EVENT_LAYERS = frozenset({
    "external_world", "user_life", "shared_conversation", "agent_simulated_life",
    "agent_real_action", "project_history",
})
SENSITIVE_ATTRIBUTE = re.compile(
    r"(?:medical|diagnos|religio|politic|income|salary|asset|intimate|sexual|身份证|"
    r"诊断|疾病|宗教|政治|收入|资产|亲密关系|性取向)", re.I,
)


class PWMError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BudgetPolicy:
    max_claims_per_source: int = 64
    max_new_entities_per_day: int = 128
    candidate_ttl_days: int = 30
    max_aliases_per_entity: int = 16
    max_disambiguation_candidates: int = 8
    max_maintenance_batch: int = 100
    orphan_archive_days: int = 90


def budget_policy() -> BudgetPolicy:
    conn = db.connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key='pwm_budget_policy'").fetchone()
    finally:
        conn.close()
    try:
        raw = json.loads(row["value"] if row else "{}")
    except (TypeError, json.JSONDecodeError):
        raw = {}
    defaults = asdict(BudgetPolicy())
    clean = {key: max(1, min(int(raw.get(key, value)), 10000)) for key, value in defaults.items()}
    return BudgetPolicy(**clean)


def enabled() -> bool:
    conn = db.connect()
    try:
        values = dict(conn.execute(
            "SELECT key,value FROM settings WHERE key IN ('pwm_enabled','pwm_shadow_extraction_enabled')"
        ).fetchall())
    finally:
        conn.close()
    return values.get("pwm_enabled", "1") == "1" and values.get(
        "pwm_shadow_extraction_enabled", "1"
    ) == "1"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _source(source_kind: str, source_id: str) -> kig_sources.SourceRef:
    if not enabled():
        raise PWMError("pwm_disabled", "personal world model shadow writes are disabled")
    ref = kig_sources.registry.resolve(source_kind, source_id)
    if ref.status != "active":
        raise PWMError("source_inactive", "source must be active at write time")
    return ref


def _bind_or_remove(table: str, object_id: str, derived_kind: str, ref: kig_sources.SourceRef) -> None:
    try:
        kig_sources.bind_dependency(derived_kind=derived_kind, derived_id=object_id, source_ref=ref)
    except Exception:
        conn = db.connect()
        try:
            conn.execute(f"DELETE FROM {table} WHERE id=?", (object_id,))
            conn.commit()
        finally:
            conn.close()
        raise


def _consume_entity_budget(conn, source_key: str) -> None:
    policy = budget_policy()
    today = _day()
    row = conn.execute(
        "SELECT COALESCE(SUM(used_count),0) AS used FROM pwm_budget_counters "
        "WHERE budget_date=? AND budget_kind='new_entity'", (today,),
    ).fetchone()
    if int(row["used"]) >= policy.max_new_entities_per_day:
        raise PWMError("daily_entity_budget_exhausted", "daily new entity budget is exhausted")
    conn.execute(
        "INSERT INTO pwm_budget_counters(budget_date,budget_kind,scope_key,used_count,updated_at) "
        "VALUES(?,?,?,?,?) ON CONFLICT(budget_date,budget_kind,scope_key) DO UPDATE SET "
        "used_count=used_count+1,updated_at=excluded.updated_at",
        (today, "new_entity", source_key, 1, db.now()),
    )


def _expiry(confidence: float) -> float | None:
    if confidence >= 0.75:
        return None
    return db.now() + budget_policy().candidate_ttl_days * 86400


def _validate_sensitive(*values: object) -> None:
    if any(SENSITIVE_ATTRIBUTE.search(str(value or "")) for value in values):
        raise PWMError(
            "sensitive_attribute_extraction_disabled",
            "automatic extraction of sensitive personal attributes is disabled",
        )


def create_entity(*, entity_type: str, canonical_name: str, source_kind: str,
                  source_id: str, description: str = "", reality_scope: str = "reality",
                  confidence: float = 0.5) -> dict:
    if entity_type not in ENTITY_TYPES or reality_scope not in {"reality", "lore"}:
        raise PWMError("entity_type_invalid", "entity type or scope is not allowlisted")
    canonical_name = canonical_name.strip()
    if not canonical_name or len(canonical_name) > 240 or len(description) > 1000:
        raise PWMError("entity_value_invalid", "entity name or description is invalid")
    _validate_sensitive(canonical_name, description)
    ref = _source(source_kind, source_id)
    entity_id, now = db.new_id(), db.now()
    conn = db.connect()
    try:
        _consume_entity_budget(conn, f"{source_kind}:{source_id}")
        conn.execute(
            "INSERT INTO pwm_entities(id,entity_type,canonical_name,description,sensitivity,"
            "reality_scope,confidence,status,extraction_mode,expires_at,revision,protocol_version,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (entity_id, entity_type, canonical_name, description, "normal", reality_scope,
             float(confidence), "candidate", "shadow", _expiry(float(confidence)), 1,
             PROTOCOL_VERSION, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    _bind_or_remove("pwm_entities", entity_id, "pwm_entity", ref)
    link_entity_source(entity_id=entity_id, source_ref=ref)
    return get_entity(entity_id)


def link_entity_source(*, entity_id: str, source_ref: kig_sources.SourceRef,
                       link_role: str = "derived_from") -> dict:
    owner = {
        "knowledge_document": "knowledge", "knowledge_chunk": "knowledge", "message": "conversation",
        "memory_fragment": "memory", "life_event": "life", "tool_run": "tool", "lore_section": "lore",
    }[source_ref.source_kind]
    now, link_id = db.now(), db.new_id()
    conn = db.connect()
    try:
        row = conn.execute("SELECT id FROM pwm_entities WHERE id=?", (entity_id,)).fetchone()
        if not row:
            raise PWMError("entity_missing", "entity does not exist")
        conn.execute(
            "INSERT INTO pwm_entity_source_links(id,entity_id,owner_system,owner_object_kind,"
            "owner_object_id,link_role,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(entity_id,owner_system,owner_object_kind,owner_object_id,link_role) "
            "DO UPDATE SET status='active',updated_at=excluded.updated_at",
            (link_id, entity_id, owner, source_ref.source_kind, source_ref.source_id,
             link_role, "active", now, now),
        )
        conn.commit()
        saved = conn.execute(
            "SELECT * FROM pwm_entity_source_links WHERE entity_id=? AND owner_object_kind=? "
            "AND owner_object_id=? AND link_role=?",
            (entity_id, source_ref.source_kind, source_ref.source_id, link_role),
        ).fetchone()
        result = dict(saved)
    finally:
        conn.close()
    try:
        kig_sources.bind_dependency(
            derived_kind="pwm_entity_source_link", derived_id=result["id"], source_ref=source_ref,
        )
    except Exception:
        conn = db.connect()
        try:
            conn.execute("DELETE FROM pwm_entity_source_links WHERE id=?", (result["id"],))
            conn.commit()
        finally:
            conn.close()
        raise
    return result


def add_alias(*, entity_id: str, alias: str, source_kind: str, source_id: str,
              language: str = "und", scope: str = "reality", confidence: float = 0.5) -> dict:
    alias = alias.strip()
    if not alias or len(alias) > 240 or scope not in {"reality", "lore"}:
        raise PWMError("alias_invalid", "alias or scope is invalid")
    _validate_sensitive(alias)
    ref = _source(source_kind, source_id)
    conn = db.connect()
    try:
        entity = conn.execute("SELECT reality_scope FROM pwm_entities WHERE id=?", (entity_id,)).fetchone()
        if not entity:
            raise PWMError("entity_missing", "entity does not exist")
        if entity["reality_scope"] != scope:
            raise PWMError("scope_mismatch", "reality and lore aliases cannot cross scopes")
        count = conn.execute(
            "SELECT COUNT(*) AS total FROM pwm_entity_aliases WHERE entity_id=? AND status!='revoked'",
            (entity_id,),
        ).fetchone()["total"]
        if count >= budget_policy().max_aliases_per_entity:
            raise PWMError("alias_budget_exhausted", "entity alias budget is exhausted")
        alias_id, now = db.new_id(), db.now()
        conn.execute(
            "INSERT INTO pwm_entity_aliases(id,entity_id,alias,language,scope,confidence,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (alias_id, entity_id, alias, language[:16] or "und", scope, float(confidence),
             "candidate", now, now),
        )
        conn.commit()
    finally:
        conn.close()
    _bind_or_remove("pwm_entity_aliases", alias_id, "pwm_entity_alias", ref)
    return get_row("pwm_entity_aliases", alias_id)


def _claims_for_source(source_kind: str, source_id: str) -> int:
    conn = db.connect()
    try:
        return int(conn.execute(
            "SELECT COUNT(*) AS total FROM derived_dependencies d JOIN pwm_claims c "
            "ON c.id=d.derived_id AND d.derived_kind='pwm_claim' WHERE d.source_kind=? "
            "AND d.source_id=? AND c.validity_state!='revoked'", (source_kind, source_id),
        ).fetchone()["total"])
    finally:
        conn.close()


def create_claim(*, statement: str, claim_type: str, predicate: str, source_kind: str,
                 source_id: str, subject_entity_id: str | None = None,
                 object_entity_id: str | None = None, object_value: object | None = None,
                 qualifiers: dict | None = None, confidence: float = 0.5,
                 support_type: str = "model_inferred", valid_from: float | None = None,
                 valid_until: float | None = None) -> dict:
    if predicate not in PREDICATES:
        predicate = "related_to"
    if support_type not in {"explicit", "strongly_implied", "model_inferred"}:
        raise PWMError("support_type_invalid", "claim support type is invalid")
    if not statement.strip() or len(statement) > 2000:
        raise PWMError("claim_invalid", "claim statement is invalid")
    _validate_sensitive(statement, predicate, qualifiers)
    ref = _source(source_kind, source_id)
    if _claims_for_source(source_kind, source_id) >= budget_policy().max_claims_per_source:
        raise PWMError("source_claim_budget_exhausted", "per-source claim budget is exhausted")
    claim_id, now = db.new_id(), db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO pwm_claims(id,statement,claim_type,subject_entity_id,predicate,"
            "object_entity_id,object_value_json,qualifiers_json,confidence,support_type,"
            "validity_state,valid_from,valid_until,extraction_mode,protocol_version,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (claim_id, statement.strip(), claim_type[:64], subject_entity_id, predicate,
             object_entity_id, _json(object_value) if object_value is not None else None,
             _json(qualifiers or {}), float(confidence), support_type, "candidate", valid_from,
             valid_until, "shadow", "pwm-claim-v1", now, now),
        )
        conn.commit()
    finally:
        conn.close()
    _bind_or_remove("pwm_claims", claim_id, "pwm_claim", ref)
    return get_row("pwm_claims", claim_id)


def create_relation(*, subject_entity_id: str, predicate: str, source_kind: str,
                    source_id: str, object_entity_id: str | None = None,
                    object_value: object | None = None, qualifiers: dict | None = None,
                    temporal_scope: dict | None = None, confidence: float = 0.5) -> dict:
    if predicate not in PREDICATES:
        predicate = "related_to"
    _validate_sensitive(predicate, qualifiers, object_value)
    ref = _source(source_kind, source_id)
    relation_id, now = db.new_id(), db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO pwm_relations(id,subject_entity_id,predicate,object_entity_id,"
            "object_value_json,qualifiers_json,confidence,temporal_scope_json,status,"
            "extraction_mode,protocol_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (relation_id, subject_entity_id, predicate, object_entity_id,
             _json(object_value) if object_value is not None else None, _json(qualifiers or {}),
             float(confidence), _json(temporal_scope or {}), "candidate", "shadow",
             "pwm-relation-v1", now, now),
        )
        conn.commit()
    finally:
        conn.close()
    _bind_or_remove("pwm_relations", relation_id, "pwm_relation", ref)
    return get_row("pwm_relations", relation_id)


def create_world_event(*, event_type: str, title: str, source_kind: str, source_id: str,
                       event_layer: str, summary: str = "", start_at: float | None = None,
                       end_at: float | None = None, participant_entity_ids: Iterable[str] = (),
                       object_entity_ids: Iterable[str] = (), location_entity_id: str | None = None,
                       confidence: float = 0.5, execution_state: str = "inferred") -> dict:
    if event_layer not in EVENT_LAYERS or execution_state not in {
        "planned", "materialized", "performed", "inferred"
    }:
        raise PWMError("event_type_invalid", "event layer or execution state is invalid")
    if event_layer == "agent_real_action" and source_kind != "tool_run":
        raise PWMError("tool_run_required", "agent real actions require an authoritative ToolRun")
    if source_kind == "life_event" and execution_state == "performed":
        raise PWMError("life_projection_semantics", "LIFE projection cannot promote an event to performed")
    _validate_sensitive(title, summary, event_type)
    ref = _source(source_kind, source_id)
    event_id, now = db.new_id(), db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO pwm_world_events(id,event_type,title,summary,start_at,end_at,"
            "participant_entity_ids_json,object_entity_ids_json,location_entity_id,confidence,"
            "event_layer,status,execution_state,extraction_mode,protocol_version,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (event_id, event_type[:64], title[:500], summary[:2000], start_at, end_at,
             _json(list(participant_entity_ids)), _json(list(object_entity_ids)), location_entity_id,
             float(confidence), event_layer, "candidate", execution_state, "shadow",
             "pwm-world-event-v1", now, now),
        )
        conn.commit()
    finally:
        conn.close()
    _bind_or_remove("pwm_world_events", event_id, "pwm_world_event", ref)
    return get_row("pwm_world_events", event_id)


def create_state_assertion(*, subject_entity_id: str, state_type: str, value: object,
                           source_kind: str, source_id: str, scope: str = "reality",
                           confidence: float = 0.5, valid_from: float | None = None,
                           valid_until: float | None = None) -> dict:
    if scope not in {"reality", "lore"}:
        raise PWMError("scope_invalid", "state scope is invalid")
    _validate_sensitive(state_type, value)
    ref = _source(source_kind, source_id)
    assertion_id, now = db.new_id(), db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO pwm_state_assertions(id,subject_entity_id,state_type,value_json,"
            "valid_from,valid_until,scope,confidence,status,extraction_mode,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (assertion_id, subject_entity_id, state_type[:64], _json(value), valid_from,
             valid_until, scope, float(confidence), "candidate", "shadow", now, now),
        )
        conn.commit()
    finally:
        conn.close()
    _bind_or_remove("pwm_state_assertions", assertion_id, "pwm_state_assertion", ref)
    return get_row("pwm_state_assertions", assertion_id)


def get_row(table: str, object_id: str) -> dict:
    allowed = {"pwm_entities", "pwm_entity_aliases", "pwm_claims", "pwm_relations",
               "pwm_world_events", "pwm_state_assertions"}
    if table not in allowed:
        raise PWMError("table_invalid", "PWM table is not public")
    conn = db.connect()
    try:
        row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (object_id,)).fetchone()
        if not row:
            raise PWMError("object_missing", "PWM object does not exist")
        result = dict(row)
        result["sources"] = [dict(item) for item in conn.execute(
            "SELECT source_kind,source_id,source_revision,source_hash,dependency_status,"
            "privacy_scope,source_locator FROM derived_dependencies WHERE derived_id=? "
            "ORDER BY source_kind,source_id", (object_id,),
        ).fetchall()]
        return result
    finally:
        conn.close()


def get_entity(entity_id: str) -> dict:
    result = get_row("pwm_entities", entity_id)
    conn = db.connect()
    try:
        result["aliases"] = [dict(row) for row in conn.execute(
            "SELECT * FROM pwm_entity_aliases WHERE entity_id=? AND status!='revoked' ORDER BY alias",
            (entity_id,),
        ).fetchall()]
    finally:
        conn.close()
    return result


def list_entities(*, query: str = "", entity_type: str | None = None,
                  scope: str = "reality", limit: int = 50) -> list[dict]:
    limit = max(1, min(int(limit), 100))
    terms, params = ["e.reality_scope=?", "e.status!='revoked'"], [scope]
    if entity_type:
        terms.append("e.entity_type=?")
        params.append(entity_type)
    if query.strip():
        terms.append("(e.canonical_name LIKE ? OR EXISTS(SELECT 1 FROM pwm_entity_aliases a "
                     "WHERE a.entity_id=e.id AND a.alias LIKE ? AND a.status!='revoked'))")
        params.extend([f"%{query.strip()}%", f"%{query.strip()}%"])
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT e.* FROM pwm_entities e WHERE " + " AND ".join(terms) +
            " ORDER BY e.updated_at DESC,e.id LIMIT ?", (*params, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def disambiguation_candidates(*, alias: str, entity_type: str | None = None,
                              scope: str = "reality") -> list[dict]:
    alias = alias.strip()
    if not alias:
        return []
    params: list[object] = [scope, alias, alias]
    type_sql = ""
    if entity_type:
        type_sql, params = " AND e.entity_type=?", [scope, alias, alias, entity_type]
    params.append(budget_policy().max_disambiguation_candidates)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT DISTINCT e.* FROM pwm_entities e LEFT JOIN pwm_entity_aliases a ON a.entity_id=e.id "
            "WHERE e.reality_scope=? AND e.status IN ('candidate','active') "
            "AND (lower(e.canonical_name)=lower(?) OR (lower(a.alias)=lower(?) AND a.status!='revoked'))" +
            type_sql + " ORDER BY e.confidence DESC,e.updated_at DESC LIMIT ?", tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def propose_resolution(*, left_entity_id: str, right_entity_id: str,
                       proposal_type: str = "merge", confidence: float = 0.5,
                       decision_source: str = "llm_proposal") -> dict:
    if left_entity_id == right_entity_id or proposal_type not in {
        "link_alias", "merge", "split", "memory_alias_sync"
    }:
        raise PWMError("resolution_invalid", "resolution proposal is invalid")
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM pwm_entities WHERE id IN (?,?) ORDER BY id",
            (left_entity_id, right_entity_id),
        ).fetchall()
        if len(rows) != 2:
            raise PWMError("entity_missing", "both entities must exist")
        by_id = {row["id"]: row for row in rows}
        left, right = by_id[left_entity_id], by_id[right_entity_id]
        if left["reality_scope"] != right["reality_scope"]:
            raise PWMError("scope_mismatch", "reality and lore entities cannot be merged")
        high_impact = left["entity_type"] in {"person", "user", "organization"} or right[
            "entity_type"
        ] in {"person", "user", "organization"}
        requires_confirmation = high_impact or proposal_type in {"split", "memory_alias_sync"}
        if decision_source == "llm_proposal":
            requires_confirmation = True
        preview = merge_preview(left_entity_id, right_entity_id)
        proposal_id, now = db.new_id(), db.now()
        conn.execute(
            "INSERT INTO pwm_entity_resolution_proposals(id,left_entity_id,right_entity_id,"
            "proposal_type,scope,confidence,decision_source,impact_level,requires_confirmation,"
            "rationale_codes_json,preview_json,status,revision,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (proposal_id, left_entity_id, right_entity_id, proposal_type, left["reality_scope"],
             float(confidence), decision_source, "high" if high_impact else "medium",
             int(requires_confirmation), _json(["same_scope", "proposal_only"]), _json(preview),
             "proposed", 1, now, now),
        )
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM pwm_entity_resolution_proposals WHERE id=?", (proposal_id,),
        ).fetchone())
    finally:
        conn.close()


def merge_preview(primary_entity_id: str, secondary_entity_id: str) -> dict:
    conn = db.connect()
    try:
        return {
            "primary_entity_id": primary_entity_id,
            "secondary_entity_id": secondary_entity_id,
            "aliases_to_move": conn.execute(
                "SELECT COUNT(*) AS n FROM pwm_entity_aliases WHERE entity_id=? AND status!='revoked'",
                (secondary_entity_id,),
            ).fetchone()["n"],
            "relations_as_subject": conn.execute(
                "SELECT COUNT(*) AS n FROM pwm_relations WHERE subject_entity_id=? AND status!='revoked'",
                (secondary_entity_id,),
            ).fetchone()["n"],
            "relations_as_object": conn.execute(
                "SELECT COUNT(*) AS n FROM pwm_relations WHERE object_entity_id=? AND status!='revoked'",
                (secondary_entity_id,),
            ).fetchone()["n"],
            "claims_affected": conn.execute(
                "SELECT COUNT(*) AS n FROM pwm_claims WHERE (subject_entity_id=? OR object_entity_id=?) "
                "AND validity_state!='revoked'", (secondary_entity_id, secondary_entity_id),
            ).fetchone()["n"],
            "events_affected": conn.execute(
                "SELECT COUNT(*) AS n FROM pwm_world_events e WHERE e.location_entity_id=? "
                "OR EXISTS(SELECT 1 FROM json_each(COALESCE(e.participant_entity_ids_json,'[]')) p "
                "WHERE p.value=?) OR EXISTS(SELECT 1 FROM json_each("
                "COALESCE(e.object_entity_ids_json,'[]')) o WHERE o.value=?)",
                (secondary_entity_id, secondary_entity_id, secondary_entity_id),
            ).fetchone()["n"],
            "states_affected": conn.execute(
                "SELECT COUNT(*) AS n FROM pwm_state_assertions WHERE subject_entity_id=?",
                (secondary_entity_id,),
            ).fetchone()["n"],
            "owner_data_deleted": False,
        }
    finally:
        conn.close()


def propose_exact_resolution(*, left_entity_id: str, right_entity_id: str) -> dict:
    """Create a deterministic low-impact merge only for exact same-scope/type aliases."""
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id,entity_type,canonical_name,reality_scope FROM pwm_entities WHERE id IN (?,?)",
            (left_entity_id, right_entity_id),
        ).fetchall()
        if len(rows) != 2:
            raise PWMError("entity_missing", "both entities must exist")
        by_id = {row["id"]: row for row in rows}
        left, right = by_id[left_entity_id], by_id[right_entity_id]
        if left["entity_type"] != right["entity_type"] or left["reality_scope"] != right["reality_scope"]:
            raise PWMError("exact_resolution_unproven", "entity type or scope differs")
        if left["entity_type"] in {"person", "user", "organization"}:
            raise PWMError("confirmation_required", "high-impact entities cannot auto-merge")
        names = {}
        for entity_id, row in ((left_entity_id, left), (right_entity_id, right)):
            aliases = {str(item["alias"]).casefold() for item in conn.execute(
                "SELECT alias FROM pwm_entity_aliases WHERE entity_id=? AND status IN ('candidate','active')",
                (entity_id,),
            ).fetchall()}
            names[entity_id] = aliases | {str(row["canonical_name"]).casefold()}
        if not names[left_entity_id] & names[right_entity_id]:
            raise PWMError("exact_resolution_unproven", "no exact canonical name or alias match")
    finally:
        conn.close()
    return propose_resolution(
        left_entity_id=left_entity_id, right_entity_id=right_entity_id,
        proposal_type="merge", confidence=1.0, decision_source="deterministic",
    )


def apply_merge(proposal_id: str, *, expected_revision: int, actor: str = "user") -> dict:
    conn = db.connect()
    try:
        proposal = conn.execute(
            "SELECT * FROM pwm_entity_resolution_proposals WHERE id=?", (proposal_id,),
        ).fetchone()
        if not proposal or proposal["status"] != "proposed" or proposal["revision"] != expected_revision:
            raise PWMError("proposal_stale", "resolution proposal is missing, stale or already decided")
        if proposal["proposal_type"] != "merge":
            raise PWMError("proposal_type_invalid", "proposal is not a merge")
        if actor not in {"user", "system"}:
            raise PWMError("actor_invalid", "merge actor is invalid")
        if actor == "system" and (
            proposal["requires_confirmation"] or proposal["decision_source"] != "deterministic"
            or float(proposal["confidence"]) < 0.98 or proposal["impact_level"] == "high"
        ):
            raise PWMError("confirmation_required", "this merge requires explicit user confirmation")
        primary, secondary = proposal["left_entity_id"], proposal["right_entity_id"]
        before = _entity_operation_snapshot(conn, primary, secondary)
        conn.execute("UPDATE pwm_relations SET subject_entity_id=?,updated_at=? WHERE subject_entity_id=?",
                     (primary, db.now(), secondary))
        conn.execute("UPDATE pwm_relations SET object_entity_id=?,updated_at=? WHERE object_entity_id=?",
                     (primary, db.now(), secondary))
        conn.execute("UPDATE pwm_claims SET subject_entity_id=?,updated_at=? WHERE subject_entity_id=?",
                     (primary, db.now(), secondary))
        conn.execute("UPDATE pwm_claims SET object_entity_id=?,updated_at=? WHERE object_entity_id=?",
                     (primary, db.now(), secondary))
        for event in conn.execute(
            "SELECT id,participant_entity_ids_json,object_entity_ids_json,location_entity_id "
            "FROM pwm_world_events e WHERE e.location_entity_id=? OR EXISTS(SELECT 1 FROM "
            "json_each(COALESCE(e.participant_entity_ids_json,'[]')) p WHERE p.value=?) OR "
            "EXISTS(SELECT 1 FROM json_each(COALESCE(e.object_entity_ids_json,'[]')) o "
            "WHERE o.value=?)",
            (secondary, secondary, secondary),
        ).fetchall():
            participants = [primary if item == secondary else item for item in
                            json.loads(event["participant_entity_ids_json"])]
            objects = [primary if item == secondary else item for item in
                       json.loads(event["object_entity_ids_json"])]
            conn.execute(
                "UPDATE pwm_world_events SET participant_entity_ids_json=?,object_entity_ids_json=?,"
                "location_entity_id=?,updated_at=? WHERE id=?",
                (_json(list(dict.fromkeys(participants))), _json(list(dict.fromkeys(objects))),
                 primary if event["location_entity_id"] == secondary else event["location_entity_id"],
                 db.now(), event["id"]),
            )
        conn.execute(
            "UPDATE pwm_state_assertions SET subject_entity_id=?,updated_at=? WHERE subject_entity_id=?",
            (primary, db.now(), secondary),
        )
        for alias in conn.execute("SELECT * FROM pwm_entity_aliases WHERE entity_id=?", (secondary,)).fetchall():
            exists = conn.execute(
                "SELECT id FROM pwm_entity_aliases WHERE entity_id=? AND alias=? AND language=? AND scope=?",
                (primary, alias["alias"], alias["language"], alias["scope"]),
            ).fetchone()
            if exists:
                conn.execute("UPDATE pwm_entity_aliases SET status='revoked',updated_at=? WHERE id=?",
                             (db.now(), alias["id"]))
            else:
                conn.execute("UPDATE pwm_entity_aliases SET entity_id=?,updated_at=? WHERE id=?",
                             (primary, db.now(), alias["id"]))
        conn.execute("UPDATE pwm_entity_source_links SET entity_id=?,updated_at=? WHERE entity_id=? "
                     "AND NOT EXISTS(SELECT 1 FROM pwm_entity_source_links x WHERE x.entity_id=? "
                     "AND x.owner_system=pwm_entity_source_links.owner_system AND "
                     "x.owner_object_kind=pwm_entity_source_links.owner_object_kind AND "
                     "x.owner_object_id=pwm_entity_source_links.owner_object_id AND "
                     "x.link_role=pwm_entity_source_links.link_role)",
                     (primary, db.now(), secondary, primary))
        conn.execute("UPDATE pwm_entities SET status='merged',revision=revision+1,updated_at=? WHERE id=?",
                     (db.now(), secondary))
        conn.execute("UPDATE pwm_entities SET revision=revision+1,updated_at=? WHERE id=?",
                     (db.now(), primary))
        after = _entity_operation_snapshot(conn, primary, secondary)
        operation_id, now = db.new_id(), db.now()
        conn.execute(
            "INSERT INTO pwm_entity_operations(id,proposal_id,operation_type,primary_entity_id,"
            "secondary_entity_id,before_json,after_json,actor,reversible,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (operation_id, proposal_id, "merge", primary, secondary, _json(before), _json(after),
             actor, 1, now),
        )
        conn.execute(
            "UPDATE pwm_entity_resolution_proposals SET status='applied',revision=revision+1,"
            "updated_at=? WHERE id=?", (now, proposal_id),
        )
        conn.commit()
        return {"operation_id": operation_id, "preview": after, "reversible": True}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def rollback_merge(operation_id: str, *, actor: str = "user") -> dict:
    if actor != "user":
        raise PWMError("confirmation_required", "merge rollback requires explicit user action")
    conn = db.connect()
    try:
        operation = conn.execute("SELECT * FROM pwm_entity_operations WHERE id=?", (operation_id,)).fetchone()
        if not operation or operation["operation_type"] != "merge" or not operation["reversible"] \
                or operation["reversed_by_operation_id"]:
            raise PWMError("operation_not_reversible", "merge operation cannot be rolled back")
        before = json.loads(operation["before_json"])
        primary, secondary = operation["primary_entity_id"], operation["secondary_entity_id"]
        _restore_operation_snapshot(conn, before)
        rollback_id, now = db.new_id(), db.now()
        after = _entity_operation_snapshot(conn, primary, secondary)
        conn.execute(
            "INSERT INTO pwm_entity_operations(id,proposal_id,operation_type,primary_entity_id,"
            "secondary_entity_id,before_json,after_json,actor,reversible,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (rollback_id, operation["proposal_id"], "rollback", primary, secondary,
             operation["after_json"], _json(after), actor, 0, now),
        )
        conn.execute("UPDATE pwm_entity_operations SET reversed_by_operation_id=? WHERE id=?",
                     (rollback_id, operation_id))
        conn.execute("UPDATE pwm_entity_resolution_proposals SET status='rolled_back',updated_at=? WHERE id=?",
                     (now, operation["proposal_id"]))
        conn.commit()
        return {"operation_id": rollback_id, "rolled_back": operation_id, "restored": True}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def split_merged_entity(operation_id: str, *, actor: str = "user") -> dict:
    """Split a prior merge using its exact source/alias/relation impact snapshot."""
    result = rollback_merge(operation_id, actor=actor)
    return {**result, "operation_type": "split", "migration_source": operation_id}


def _entity_operation_snapshot(conn, primary: str, secondary: str) -> dict:
    return {
        "entities": [dict(row) for row in conn.execute(
            "SELECT id,status,revision,updated_at FROM pwm_entities WHERE id IN (?,?) ORDER BY id",
            (primary, secondary),
        ).fetchall()],
        "aliases": [dict(row) for row in conn.execute(
            "SELECT id,entity_id,status,updated_at FROM pwm_entity_aliases "
            "WHERE entity_id IN (?,?) ORDER BY id", (primary, secondary),
        ).fetchall()],
        "relations": [dict(row) for row in conn.execute(
            "SELECT id,subject_entity_id,object_entity_id,updated_at FROM pwm_relations "
            "WHERE subject_entity_id IN (?,?) OR object_entity_id IN (?,?) ORDER BY id",
            (primary, secondary, primary, secondary),
        ).fetchall()],
        "claims": [dict(row) for row in conn.execute(
            "SELECT id,subject_entity_id,object_entity_id,updated_at FROM pwm_claims "
            "WHERE subject_entity_id IN (?,?) OR object_entity_id IN (?,?) ORDER BY id",
            (primary, secondary, primary, secondary),
        ).fetchall()],
        "links": [dict(row) for row in conn.execute(
            "SELECT id,entity_id,status,updated_at FROM pwm_entity_source_links "
            "WHERE entity_id IN (?,?) ORDER BY id", (primary, secondary),
        ).fetchall()],
        "events": [dict(row) for row in conn.execute(
            "SELECT id,participant_entity_ids_json,object_entity_ids_json,location_entity_id,updated_at "
            "FROM pwm_world_events e WHERE e.location_entity_id IN (?,?) OR "
            "EXISTS(SELECT 1 FROM json_each(COALESCE(e.participant_entity_ids_json,'[]')) p "
            "WHERE p.value IN (?,?)) OR EXISTS(SELECT 1 FROM json_each("
            "COALESCE(e.object_entity_ids_json,'[]')) o WHERE o.value IN (?,?)) ORDER BY id",
            (primary, secondary, primary, secondary, primary, secondary),
        ).fetchall()],
        "states": [dict(row) for row in conn.execute(
            "SELECT id,subject_entity_id,updated_at FROM pwm_state_assertions "
            "WHERE subject_entity_id IN (?,?) ORDER BY id", (primary, secondary),
        ).fetchall()],
    }


def _restore_operation_snapshot(conn, snapshot: dict) -> None:
    for table, rows in (("pwm_entities", snapshot["entities"]),
                        ("pwm_entity_aliases", snapshot["aliases"]),
                        ("pwm_relations", snapshot["relations"]),
                        ("pwm_claims", snapshot["claims"]),
                        ("pwm_entity_source_links", snapshot["links"]),
                        ("pwm_world_events", snapshot.get("events", [])),
                        ("pwm_state_assertions", snapshot.get("states", []))):
        for row in rows:
            columns = list(row)
            assignments = ",".join(f"{column}=?" for column in columns if column != "id")
            conn.execute(
                f"UPDATE {table} SET {assignments} WHERE id=?",
                (*[row[column] for column in columns if column != "id"], row["id"]),
            )


def archive_expired_candidates(*, limit: int | None = None) -> int:
    bounded = min(limit or budget_policy().max_maintenance_batch, budget_policy().max_maintenance_batch)
    conn = db.connect()
    try:
        ids = [row["id"] for row in conn.execute(
            "SELECT id FROM pwm_entities WHERE status='candidate' AND expires_at IS NOT NULL "
            "AND expires_at<=? ORDER BY expires_at,id LIMIT ?", (db.now(), bounded),
        ).fetchall()]
        if ids:
            conn.executemany(
                "UPDATE pwm_entities SET status='archived',revision=revision+1,updated_at=? WHERE id=?",
                [(db.now(), entity_id) for entity_id in ids],
            )
            conn.commit()
        return len(ids)
    finally:
        conn.close()


def deletion_impact(source_kind: str, source_id: str) -> dict:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT derived_kind,derived_id,dependency_status FROM derived_dependencies "
            "WHERE source_kind=? AND source_id=? ORDER BY derived_kind,derived_id",
            (source_kind, source_id),
        ).fetchall()
        by_kind: dict[str, int] = {}
        for row in rows:
            by_kind[row["derived_kind"]] = by_kind.get(row["derived_kind"], 0) + 1
        return {
            "source_kind": source_kind, "source_id": source_id,
            "derived_objects": by_kind, "total_affected": len(rows),
            "owner_data_deleted": False, "automatic_deletion": False,
        }
    finally:
        conn.close()
