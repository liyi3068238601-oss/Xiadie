"""LIFE.11 revision-bound sharing through the frozen EAP life adapter.

LIFE owns source eligibility and seed construction.  It never creates a
ContactEpisode, candidate, decision, ExpressionPlan, delivery, or message; the
frozen EAP adapter/orchestrator remains the only route to those objects.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

from . import db, diary, important_dates, life_events, personal_goals
from .proactive import life_adapter, orchestrator

POLICY_VERSION = "life-share-policy-v1"
MAX_SUMMARY_CHARS = 160
MAX_NORMAL_BATCH = 3
MAX_DAY_OFFLINE_BATCH = 2
MAX_LONG_OFFLINE_BATCH = 1


class LifeShareError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ShareRequest:
    source_type: str
    source_id: str
    explicit_authorization: bool = False
    provider_location: str = "unknown"
    certification_level: str = "none"


def _hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _summary(value: str) -> str:
    text = " ".join(str(value).split()).strip()
    if not text:
        raise LifeShareError("source_summary_empty", "LIFE source has no shareable summary")
    return text[:MAX_SUMMARY_CHARS]


def _already_seeded(source_type: str, source_id: str) -> bool:
    conn = db.connect()
    try:
        return conn.execute(
            "SELECT 1 FROM life_proactive_seeds WHERE source_event_type=? AND source_event_id=? LIMIT 1",
            (source_type, source_id),
        ).fetchone() is not None
    finally:
        conn.close()


def _snapshot(request: ShareRequest) -> tuple[str, str, str, str]:
    """Return revision, content hash, body-free summary, and EAP origin type."""
    if request.source_type == life_adapter.LifeSeedSourceType.LIFE_EVENT:
        item = life_events.get_event(request.source_id)
        if not item or item["lifecycle_status"] != "active":
            raise LifeShareError("source_unavailable", "life event is unavailable")
        if item["world_layer"] == "planned":
            raise LifeShareError("planned_not_shareable", "planned activity is not an occurred event")
        material = {
            "id": item["id"], "revision": item["revision"], "event_kind": item["event_kind"],
            "world_layer": item["world_layer"], "summary": item["summary"],
            "sources": item["sources"],
        }
        return str(item["revision"]), _hash(material), _summary(item["summary"]), "life_share"

    if request.source_type == life_adapter.LifeSeedSourceType.PERSONAL_GOAL:
        item = personal_goals.get_goal(request.source_id)
        if not item or item["status"] not in {"active", "completed"}:
            raise LifeShareError("source_unavailable", "personal goal is not shareable")
        material = {
            "id": item["id"], "revision": item["revision"], "status": item["status"],
            "title": item["title"], "sources": item["sources"],
        }
        return str(item["revision"]), _hash(material), _summary(item["title"]), "milestone"

    if request.source_type == life_adapter.LifeSeedSourceType.IMPORTANT_DATE:
        item = important_dates.get(request.source_id)
        if not item or item["status"] != "active" or item["celebration_policy"] == "none":
            raise LifeShareError("date_boundary_blocks_share", "important date is unavailable or silent")
        material = {
            key: item[key] for key in (
                "id", "revision", "label", "recurrence", "date_year", "date_month", "date_day",
                "timezone_id", "celebration_policy",
            )
        }
        return str(item["revision"]), _hash(material), _summary(item["label"]), "milestone"

    if request.source_type == life_adapter.LifeSeedSourceType.DIARY_ENTRY:
        item = diary.get_entry(request.source_id)
        if not item or not diary.can_share(
            item, provider_location=request.provider_location,
            certification_level=request.certification_level,
            explicit_authorization=request.explicit_authorization,
        ):
            raise LifeShareError("diary_boundary_blocks_share", "diary sharing boundary blocks this seed")
        # The seed intentionally carries only the title. Diary body remains private
        # until the later EAP/model gate independently authorizes necessary access.
        material = {
            "id": item["id"], "revision": item["revision"], "status": item["status"],
            "sensitivity": item["sensitivity"], "share_policy": item["share_policy"],
            "title": item["title"], "sources": item["sources"],
        }
        return str(item["revision"]), _hash(material), _summary(item["title"]), "life_share"

    raise LifeShareError("source_type_invalid", "unsupported LIFE share source")


def propose_share(*, session_id: str, request: ShareRequest, due_at: float | None = None,
                  now: float | None = None) -> dict[str, Any]:
    """Create one immutable LIFE seed and enqueue only the frozen EAP source."""
    now = db.now() if now is None else now
    if _already_seeded(request.source_type, request.source_id):
        return {"status": "duplicate", "reason_code": "life_source_already_shared"}
    revision, source_hash, summary, origin_type = _snapshot(request)
    seed = life_adapter.receive_life_seed(
        source_event_type=request.source_type, source_event_id=request.source_id,
        source_event_summary=summary, topic=summary, origin_type=origin_type,
        source_revision=revision, source_hash=source_hash, now=now,
    )
    if seed is None:
        return {"status": "duplicate", "reason_code": "life_revision_already_seeded"}
    runtime_source = orchestrator.enqueue_life_seed_fixture(
        session_id=session_id, seed_id=seed.id, due_at=now if due_at is None else due_at, now=now,
    )
    if runtime_source is None:
        life_adapter.reject_seed(seed.id, reason="eap_source_snapshot_unavailable", now=now)
        raise LifeShareError("eap_adapter_rejected", "frozen EAP adapter rejected the LIFE seed")
    return {
        "status": "queued", "policy_version": POLICY_VERSION,
        "seed_id": seed.id, "runtime_source_id": runtime_source["id"],
        "source_revision": revision, "source_hash": source_hash,
    }


def propose_batch(*, session_id: str, requests: Iterable[ShareRequest], offline_seconds: float = 0,
                  now: float | None = None) -> list[dict[str, Any]]:
    """Queue a small representative set; long return gaps never dump a backlog."""
    now = db.now() if now is None else now
    if offline_seconds >= 7 * 24 * 3600:
        cap = MAX_LONG_OFFLINE_BATCH
    elif offline_seconds >= 24 * 3600:
        cap = MAX_DAY_OFFLINE_BATCH
    else:
        cap = MAX_NORMAL_BATCH
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for request in requests:
        identity = (request.source_type, request.source_id)
        if identity in seen:
            continue
        seen.add(identity)
        try:
            result = propose_share(session_id=session_id, request=request, now=now)
        except LifeShareError as exc:
            result = {"status": "skipped", "reason_code": exc.code}
        results.append(result)
        if sum(item["status"] == "queued" for item in results) >= cap:
            break
    return results
