"""Bounded, non-destructive KIG maintenance candidate generation."""
from __future__ import annotations

import asyncio
import json
import logging
import threading

from . import db, kig_sources, pwm

logger = logging.getLogger(__name__)
PROTOCOL_VERSION = "kig-maintenance-v1"
CANDIDATE_TYPES = frozenset({
    "duplicate_document", "possible_new_version", "stale_document", "orphan_chunk",
    "broken_source", "conflicting_claims", "unused_collection", "missing_metadata",
    "entity_merge_candidate", "entity_split_candidate", "reindex_required",
})
_task: asyncio.Task | None = None
_stop = threading.Event()


class MaintenanceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def create_candidate(*, candidate_type: str, object_kind: str, object_id: str,
                     related_object_ids: list[str] | None = None,
                     reason_codes: list[str] | None = None, confidence: float = 1.0,
                     decision_source: str = "deterministic", source_ref=None) -> dict:
    if candidate_type not in CANDIDATE_TYPES or decision_source not in {
        "deterministic", "llm_proposal"
    }:
        raise MaintenanceError("candidate_type_invalid", "maintenance candidate is invalid")
    candidate_id, now = db.new_id(), db.now()
    conn = db.connect()
    try:
        existing = conn.execute(
            "SELECT * FROM kig_maintenance_candidates WHERE candidate_type=? AND object_kind=? "
            "AND object_id=? AND status='proposed'",
            (candidate_type, object_kind, object_id),
        ).fetchone()
        if existing:
            return dict(existing)
        conn.execute(
            "INSERT INTO kig_maintenance_candidates(id,candidate_type,object_kind,object_id,"
            "related_object_ids_json,reason_codes_json,confidence,decision_source,status,"
            "requires_confirmation,protocol_version,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (candidate_id, candidate_type, object_kind, object_id,
             json.dumps(related_object_ids or [], ensure_ascii=False),
             json.dumps(reason_codes or [], ensure_ascii=False), float(confidence),
             decision_source, "proposed", 1, PROTOCOL_VERSION, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    if source_ref is not None:
        try:
            kig_sources.bind_dependency(
                derived_kind="maintenance_candidate", derived_id=candidate_id, source_ref=source_ref,
            )
        except Exception:
            conn = db.connect()
            try:
                conn.execute("DELETE FROM kig_maintenance_candidates WHERE id=?", (candidate_id,))
                conn.commit()
            finally:
                conn.close()
            raise
    return get_candidate(candidate_id)


def get_candidate(candidate_id: str) -> dict:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM kig_maintenance_candidates WHERE id=?", (candidate_id,),
        ).fetchone()
        if not row:
            raise MaintenanceError("candidate_missing", "maintenance candidate does not exist")
        result = dict(row)
        result["related_object_ids"] = json.loads(result.pop("related_object_ids_json"))
        result["reason_codes"] = json.loads(result.pop("reason_codes_json"))
        return result
    finally:
        conn.close()


def decide_candidate(candidate_id: str, *, accepted: bool) -> dict:
    """Confirm/reject a candidate only; destructive owner action is deliberately separate."""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT status FROM kig_maintenance_candidates WHERE id=?", (candidate_id,),
        ).fetchone()
        if not row or row["status"] != "proposed":
            raise MaintenanceError("candidate_stale", "candidate is missing or already decided")
        conn.execute(
            "UPDATE kig_maintenance_candidates SET status=?,updated_at=? WHERE id=?",
            ("confirmed" if accepted else "rejected", db.now(), candidate_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_candidate(candidate_id)


def scan(*, limit: int | None = None) -> dict[str, int]:
    """Run bounded deterministic checks; never delete or rebuild owner data."""
    maximum = pwm.budget_policy().max_maintenance_batch
    limit = max(1, min(int(limit or maximum), maximum))
    counts = {"checked": 0, "created": 0, "archived_candidates": 0}
    conn = db.connect()
    try:
        duplicates = conn.execute(
            "SELECT content_sha256,GROUP_CONCAT(id) ids,COUNT(*) total FROM knowledge_documents "
            "WHERE content_sha256 IS NOT NULL AND status!='deleted' GROUP BY content_sha256 "
            "HAVING COUNT(*)>1 ORDER BY total DESC LIMIT ?", (limit,),
        ).fetchall()
    finally:
        conn.close()
    for row in duplicates:
        ids = sorted(str(row["ids"]).split(","))
        ref = kig_sources.registry.resolve("knowledge_document", ids[0])
        created = create_candidate(
            candidate_type="duplicate_document", object_kind="knowledge_document",
            object_id=ids[0], related_object_ids=ids[1:], reason_codes=["same_content_sha256"],
            confidence=1.0, source_ref=ref,
        )
        counts["created"] += int(created["status"] == "proposed")
        counts["checked"] += 1
    remaining = max(0, limit - counts["checked"])
    if remaining:
        conn = db.connect()
        try:
            anomalies = conn.execute(
                "SELECT 'missing_metadata' candidate_type,'knowledge_document' object_kind,id object_id,"
                "'missing_document_metadata' reason FROM knowledge_documents "
                "WHERE status!='deleted' AND (trim(original_name)='' OR content_sha256 IS NULL) "
                "UNION ALL SELECT 'reindex_required','knowledge_document',id,"
                "COALESCE(rebuild_error_code,'rebuild_failed') FROM knowledge_documents "
                "WHERE rebuild_status='failed' "
                "UNION ALL SELECT 'stale_document','knowledge_document',id,'source_or_index_stale' "
                "FROM knowledge_documents WHERE status='indexed' AND "
                "(governance_status!='active' OR index_version IS NULL) "
                "UNION ALL SELECT 'orphan_chunk','knowledge_chunk',c.id,'owner_document_inactive' "
                "FROM knowledge_chunks c JOIN knowledge_documents d ON d.id=c.document_id "
                "WHERE d.status!='indexed' OR d.governance_status!='active' LIMIT ?", (remaining,),
            ).fetchall()
        finally:
            conn.close()
        for item in anomalies:
            source_kind = "knowledge_chunk" if item["object_kind"] == "knowledge_chunk" \
                else "knowledge_document"
            ref = kig_sources.registry.resolve(source_kind, item["object_id"])
            create_candidate(
                candidate_type=item["candidate_type"], object_kind=item["object_kind"],
                object_id=item["object_id"], reason_codes=[item["reason"]], confidence=1.0,
                source_ref=ref,
            )
            counts["checked"] += 1
            counts["created"] += 1
    remaining = max(0, limit - counts["checked"])
    if remaining:
        conn = db.connect()
        try:
            dependencies = conn.execute(
                "SELECT id FROM derived_dependencies ORDER BY COALESCE(checked_at,0),id LIMIT ?",
                (remaining,),
            ).fetchall()
        finally:
            conn.close()
        for item in dependencies:
            checked = kig_sources.check_dependency(item["id"])
            counts["checked"] += 1
            if checked["dependency_status"] in {"missing", "revoked", "inaccessible"}:
                create_candidate(
                    candidate_type="broken_source", object_kind=checked["derived_kind"],
                    object_id=checked["derived_id"], reason_codes=[checked["dependency_status"]],
                    confidence=1.0,
                )
                counts["created"] += 1
    counts["archived_candidates"] = pwm.archive_expired_candidates(limit=limit)
    return counts


def record_feedback(*, feedback_type: str, source_kind: str | None = None,
                    source_id: str | None = None, retrieval_bundle_id: str | None = None,
                    metadata: dict | None = None) -> dict:
    allowed = {
        "source_opened", "source_irrelevant", "source_outdated", "source_disabled",
        "answer_corrected", "entity_selected", "authoritative_version_selected",
    }
    if feedback_type not in allowed:
        raise MaintenanceError("feedback_invalid", "feedback type is not allowlisted")
    feedback_id, now = db.new_id(), db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO kig_retrieval_feedback(id,feedback_type,source_kind,source_id,"
            "retrieval_bundle_id,metadata_json,created_at) VALUES(?,?,?,?,?,?,?)",
            (feedback_id, feedback_type, source_kind, source_id, retrieval_bundle_id,
             json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True), now),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM kig_retrieval_feedback WHERE id=?", (feedback_id,)).fetchone())
    finally:
        conn.close()


async def _worker() -> None:
    while not _stop.is_set():
        frequency = db.get_setting("kig_maintenance_frequency", "weekly")
        if frequency != "off":
            try:
                await asyncio.to_thread(scan)
            except Exception:  # noqa: BLE001 - maintenance must not affect chat
                logger.warning("kig_maintenance_scan_failed", exc_info=True)
        interval = {"off": 3600, "daily": 86400, "weekly": 604800}.get(frequency, 604800)
        try:
            await asyncio.wait_for(asyncio.to_thread(_stop.wait, interval), timeout=interval + 1)
        except asyncio.TimeoutError:
            pass


async def start_worker() -> None:
    global _task
    if _task and not _task.done():
        return
    _stop.clear()
    _task = asyncio.create_task(_worker(), name="kig-maintenance")


async def stop_worker() -> None:
    global _task
    _stop.set()
    if _task:
        await _task
    _task = None
