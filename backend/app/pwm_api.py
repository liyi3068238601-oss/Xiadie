"""HTTP management surface for PWM and non-destructive KIG maintenance."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from . import db, kig_maintenance, pwm

router = APIRouter(prefix="/api/knowledge/world-model", tags=["world-model"])


class PWMSettingsIn(BaseModel):
    enabled: bool | None = None
    shadow_extraction_enabled: bool | None = None
    maintenance_frequency: str | None = Field(default=None, pattern=r"^(off|daily|weekly)$")


class ResolutionIn(BaseModel):
    left_entity_id: str = Field(min_length=1, max_length=64)
    right_entity_id: str = Field(min_length=1, max_length=64)
    proposal_type: str = Field(default="merge", pattern=r"^(link_alias|merge|split|memory_alias_sync)$")
    confidence: float = Field(default=0.5, ge=0, le=1)


class MergeDecisionIn(BaseModel):
    expected_revision: int = Field(ge=1)


class MaintenanceDecisionIn(BaseModel):
    accepted: bool


class FeedbackIn(BaseModel):
    feedback_type: str
    source_kind: str | None = None
    source_id: str | None = None
    retrieval_bundle_id: str | None = None
    metadata: dict = Field(default_factory=dict)


def _translate(error: Exception) -> HTTPException:
    code = getattr(error, "code", "world_model_error")
    status = 404 if code.endswith("missing") else 409 if any(
        part in code for part in ("stale", "budget", "disabled", "required", "mismatch")
    ) else 400
    return HTTPException(status, {"code": code, "message": str(error)})


@router.get("/summary")
def summary() -> dict:
    conn = db.connect()
    try:
        counts = {}
        for table in ("pwm_entities", "pwm_claims", "pwm_relations", "pwm_world_events",
                      "pwm_state_assertions", "kig_maintenance_candidates"):
            counts[table] = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        settings = dict(conn.execute(
            "SELECT key,value FROM settings WHERE key IN ('pwm_enabled',"
            "'pwm_shadow_extraction_enabled','kig_maintenance_frequency','pwm_budget_policy')"
        ).fetchall())
        return {
            "protocol_version": pwm.PROTOCOL_VERSION,
            "mode": "shadow",
            "counts": counts,
            "settings": {
                "enabled": settings.get("pwm_enabled", "1") == "1",
                "shadow_extraction_enabled": settings.get("pwm_shadow_extraction_enabled", "1") == "1",
                "maintenance_frequency": settings.get("kig_maintenance_frequency", "weekly"),
                "budget_policy": json.loads(settings.get("pwm_budget_policy", "{}")),
            },
        }
    finally:
        conn.close()


@router.patch("/settings")
def update_settings(body: PWMSettingsIn) -> dict:
    conn = db.connect()
    try:
        values = {
            "pwm_enabled": body.enabled,
            "pwm_shadow_extraction_enabled": body.shadow_extraction_enabled,
            "kig_maintenance_frequency": body.maintenance_frequency,
        }
        for key, value in values.items():
            if value is None:
                continue
            encoded = ("1" if value else "0") if isinstance(value, bool) else str(value)
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, encoded),
            )
        conn.commit()
    finally:
        conn.close()
    return summary()["settings"]


@router.get("/entities")
def entities(query: str = "", entity_type: str | None = None,
             scope: str = Query(default="reality", pattern=r"^(reality|lore)$"),
             limit: int = Query(default=50, ge=1, le=100)) -> dict:
    try:
        return {"items": pwm.list_entities(query=query, entity_type=entity_type, scope=scope, limit=limit)}
    except pwm.PWMError as error:
        raise _translate(error) from error


@router.get("/entities/{entity_id}")
def entity_detail(entity_id: str) -> dict:
    try:
        entity = pwm.get_entity(entity_id)
        conn = db.connect()
        try:
            entity["relations"] = [dict(row) for row in conn.execute(
                "SELECT * FROM pwm_relations WHERE (subject_entity_id=? OR object_entity_id=?) "
                "AND status!='revoked' ORDER BY updated_at DESC LIMIT 100", (entity_id, entity_id),
            ).fetchall()]
            entity["events"] = [dict(row) for row in conn.execute(
                "SELECT * FROM pwm_world_events WHERE status!='revoked' AND "
                "(participant_entity_ids_json LIKE ? OR object_entity_ids_json LIKE ?) "
                "ORDER BY COALESCE(start_at,created_at) DESC LIMIT 100",
                (f'%"{entity_id}"%', f'%"{entity_id}"%'),
            ).fetchall()]
            entity["states"] = [dict(row) for row in conn.execute(
                "SELECT * FROM pwm_state_assertions WHERE subject_entity_id=? AND status!='revoked' "
                "ORDER BY COALESCE(valid_from,created_at) DESC LIMIT 100", (entity_id,),
            ).fetchall()]
        finally:
            conn.close()
        return entity
    except pwm.PWMError as error:
        raise _translate(error) from error


@router.get("/timeline")
def timeline(limit: int = Query(default=100, ge=1, le=200)) -> dict:
    conn = db.connect()
    try:
        return {"items": [dict(row) for row in conn.execute(
            "SELECT * FROM pwm_world_events WHERE status!='revoked' "
            "ORDER BY COALESCE(start_at,created_at) DESC,id LIMIT ?", (limit,),
        ).fetchall()]}
    finally:
        conn.close()


@router.get("/diagnostics")
def diagnostics(limit: int = Query(default=20, ge=1, le=100)) -> dict:
    """Body-free developer diagnostics; no query or private source text is returned."""
    conn = db.connect()
    try:
        bundles = [dict(row) for row in conn.execute(
            "SELECT id,request_id,protocol_version,planner_protocol,selected_sources_json,"
            "candidate_counts_json,selected_count,conflict_notes_json,insufficiency_notes_json,"
            "status,created_at,finished_at FROM kig_retrieval_bundles "
            "ORDER BY created_at DESC,id LIMIT ?", (limit,),
        ).fetchall()]
        resolutions = [dict(row) for row in conn.execute(
            "SELECT id,proposal_type,scope,confidence,decision_source,impact_level,"
            "requires_confirmation,status,revision,updated_at FROM pwm_entity_resolution_proposals "
            "ORDER BY updated_at DESC,id LIMIT ?", (limit,),
        ).fetchall()]
        return {
            "body_free": True, "query_text_recorded": False,
            "retrieval_bundles": bundles, "resolution_proposals": resolutions,
        }
    finally:
        conn.close()


@router.get("/source-impact")
def source_impact(source_kind: str, source_id: str) -> dict:
    return pwm.deletion_impact(source_kind, source_id)


@router.post("/resolution-proposals")
def create_resolution(body: ResolutionIn) -> dict:
    try:
        return pwm.propose_resolution(
            left_entity_id=body.left_entity_id, right_entity_id=body.right_entity_id,
            proposal_type=body.proposal_type, confidence=body.confidence,
        )
    except pwm.PWMError as error:
        raise _translate(error) from error


@router.post("/resolution-proposals/{proposal_id}/apply")
def apply_resolution(proposal_id: str, body: MergeDecisionIn) -> dict:
    try:
        return pwm.apply_merge(proposal_id, expected_revision=body.expected_revision)
    except pwm.PWMError as error:
        raise _translate(error) from error


@router.post("/operations/{operation_id}/rollback")
def rollback_resolution(operation_id: str) -> dict:
    try:
        return pwm.rollback_merge(operation_id)
    except pwm.PWMError as error:
        raise _translate(error) from error


@router.post("/operations/{operation_id}/split")
def split_resolution(operation_id: str) -> dict:
    try:
        return pwm.split_merged_entity(operation_id)
    except pwm.PWMError as error:
        raise _translate(error) from error


@router.get("/maintenance")
def maintenance(limit: int = Query(default=100, ge=1, le=200)) -> dict:
    conn = db.connect()
    try:
        return {"items": [dict(row) for row in conn.execute(
            "SELECT * FROM kig_maintenance_candidates ORDER BY updated_at DESC,id LIMIT ?", (limit,),
        ).fetchall()]}
    finally:
        conn.close()


@router.post("/maintenance/scan")
def maintenance_scan(limit: int | None = Query(default=None, ge=1, le=1000)) -> dict:
    return kig_maintenance.scan(limit=limit)


@router.post("/maintenance/{candidate_id}/decision")
def maintenance_decision(candidate_id: str, body: MaintenanceDecisionIn) -> dict:
    try:
        return kig_maintenance.decide_candidate(candidate_id, accepted=body.accepted)
    except kig_maintenance.MaintenanceError as error:
        raise _translate(error) from error


@router.post("/feedback")
def feedback(body: FeedbackIn) -> dict:
    try:
        return kig_maintenance.record_feedback(**body.model_dump())
    except kig_maintenance.MaintenanceError as error:
        raise _translate(error) from error
