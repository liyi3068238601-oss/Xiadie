"""KIG.6 bounded, failure-isolated multi-source retrieval candidates.

The module adapts existing authoritative recall paths.  It persists neither
query text nor excerpts and never changes an owner system's lifecycle state.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Callable

from . import db, history_recall, kig_sources, knowledge_search, lore, memory

SOURCES = ("knowledge", "memory", "history", "life", "task", "lore")
SOURCE_KINDS = {
    "knowledge": frozenset({"knowledge_chunk"}),
    "memory": frozenset({"memory_fragment"}),
    "history": frozenset({"message"}),
    "life": frozenset({"life_event"}),
    "task": frozenset({"tool_run"}),
    "lore": frozenset({"lore_section"}),
}
DEFAULT_SOURCE_LIMIT = 6
MAX_SOURCE_LIMIT = 20
MAX_TOTAL_LIMIT = 60
MAX_EXCERPT_CHARS = 1_200
ACTIVE_STATUSES = ("active",)
_TERM = re.compile(r"[A-Za-z0-9_+.#-]{2,40}|[\u3400-\u9fff]{2,20}")


class RetrievalError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RetrievalFilters:
    source_ids: tuple[str, ...] = ()
    document_ids: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    versions: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ACTIVE_STATUSES
    date_from: float | None = None
    date_to: float | None = None


@dataclass(frozen=True)
class RetrievalRequest:
    query: str
    selected_sources: tuple[str, ...]
    filters: RetrievalFilters = field(default_factory=RetrievalFilters)
    per_source_limits: dict[str, int] = field(default_factory=dict)
    total_limit: int = 24
    current_session_id: str | None = None


@dataclass(frozen=True)
class RetrievalCandidate:
    candidate_id: str
    source: str
    source_type: str
    source_id: str
    source_revision: str
    source_hash: str
    source_status: str
    privacy_scope: str
    locator: str
    excerpt: str
    excerpt_hash: str
    lexical_score: float
    vector_score: float | None
    metadata_match: float
    recency: float
    freshness_state: str
    source_authority: str
    candidate_role: str
    metadata: dict


@dataclass(frozen=True)
class RetrievalBatch:
    candidates: tuple[RetrievalCandidate, ...]
    diagnostics: dict[str, dict]
    failed_sources: tuple[str, ...]
    lexical_fallback_sources: tuple[str, ...]


Adapter = Callable[[RetrievalRequest, int], tuple[list[RetrievalCandidate], dict]]


def retrieve(
    request: RetrievalRequest, *, adapters: dict[str, Adapter] | None = None,
) -> RetrievalBatch:
    """Recall sources independently, then deduplicate and round-robin for diversity."""
    _validate_request(request)
    registry = adapters or ADAPTERS
    pools: dict[str, list[RetrievalCandidate]] = {}
    diagnostics: dict[str, dict] = {}
    failed: list[str] = []
    lexical_fallback: list[str] = []
    for source in request.selected_sources:
        limit = request.per_source_limits.get(source, DEFAULT_SOURCE_LIMIT)
        adapter = registry.get(source)
        if adapter is None:
            diagnostics[source] = {"status": "failed", "error_code": "adapter_unavailable", "count": 0}
            failed.append(source)
            continue
        try:
            raw, detail = adapter(request, limit)
            admitted = []
            for candidate in raw:
                if candidate.source != source:
                    raise RetrievalError("adapter_source_mismatch", "adapter returned another source")
                validate_candidate(candidate)
                if _matches_filters(candidate, request.filters):
                    admitted.append(candidate)
            pools[source] = _deduplicate(admitted)[:limit]
            diagnostics[source] = {
                "status": "completed", "error_code": None,
                "count": len(pools[source]), **_safe_diagnostics(detail),
            }
            if detail.get("lexical_fallback"):
                lexical_fallback.append(source)
        except Exception:  # noqa: BLE001 - one source must never block the batch
            pools[source] = []
            diagnostics[source] = {"status": "failed", "error_code": "source_recall_failed", "count": 0}
            failed.append(source)
    selected = _diverse_round_robin(pools, request.selected_sources, request.total_limit)
    return RetrievalBatch(
        candidates=tuple(selected), diagnostics=diagnostics,
        failed_sources=tuple(failed), lexical_fallback_sources=tuple(lexical_fallback),
    )


def validate_candidate(candidate: RetrievalCandidate) -> None:
    if candidate.source not in SOURCES or candidate.source_type not in SOURCE_KINDS[candidate.source]:
        raise RetrievalError("candidate_source_invalid", "candidate source mapping is invalid")
    if not candidate.candidate_id or not candidate.excerpt or len(candidate.excerpt) > MAX_EXCERPT_CHARS:
        raise RetrievalError("candidate_excerpt_invalid", "candidate excerpt is invalid")
    if hashlib.sha256(candidate.excerpt.encode("utf-8")).hexdigest() != candidate.excerpt_hash:
        raise RetrievalError("candidate_excerpt_hash_invalid", "candidate excerpt hash changed")
    if not (0.0 <= candidate.lexical_score <= 1.0):
        raise RetrievalError("candidate_score_invalid", "lexical score is out of bounds")
    if candidate.vector_score is not None and not (0.0 <= candidate.vector_score <= 1.0):
        raise RetrievalError("candidate_score_invalid", "vector score is out of bounds")
    if not (0.0 <= candidate.metadata_match <= 1.0 and 0.0 <= candidate.recency <= 1.0):
        raise RetrievalError("candidate_score_invalid", "metadata score is out of bounds")
    current = kig_sources.registry.resolve(candidate.source_type, candidate.source_id)
    if (
        current.revision != candidate.source_revision
        or current.content_hash != candidate.source_hash
        or current.status != candidate.source_status
        or current.privacy_scope != candidate.privacy_scope
        or current.locator != candidate.locator
    ):
        raise RetrievalError("candidate_source_changed", "candidate no longer matches its source")


def _validate_request(request: RetrievalRequest) -> None:
    if not request.query.strip() or len(request.query) > knowledge_search.MAX_QUERY_CHARS:
        raise RetrievalError("query_invalid", "query is empty or too long")
    if tuple(dict.fromkeys(request.selected_sources)) != request.selected_sources:
        raise RetrievalError("sources_invalid", "selected sources must be unique")
    if not request.selected_sources or not set(request.selected_sources) <= set(SOURCES):
        raise RetrievalError("sources_invalid", "selected sources are invalid")
    if not 1 <= request.total_limit <= MAX_TOTAL_LIMIT:
        raise RetrievalError("total_limit_invalid", "total limit is invalid")
    if not set(request.per_source_limits) <= set(request.selected_sources):
        raise RetrievalError("source_limit_invalid", "limit exists for an unselected source")
    if any(not 1 <= int(value) <= MAX_SOURCE_LIMIT for value in request.per_source_limits.values()):
        raise RetrievalError("source_limit_invalid", "source limit is invalid")
    filters = request.filters
    for values, maximum in (
        (filters.source_ids, 40), (filters.document_ids, 20),
        (filters.tags, 10), (filters.versions, 20), (filters.statuses, 6),
    ):
        if len(values) > maximum or tuple(dict.fromkeys(values)) != values:
            raise RetrievalError("filter_invalid", "metadata filter is invalid")
    if not filters.statuses or not set(filters.statuses) <= kig_sources.DEPENDENCY_STATUSES:
        raise RetrievalError("filter_invalid", "source status filter is invalid")
    if filters.date_from is not None and filters.date_to is not None and filters.date_from > filters.date_to:
        raise RetrievalError("filter_invalid", "date range is reversed")


def _candidate(
    *, source: str, ref: kig_sources.SourceRef, excerpt: str,
    lexical_score: float, vector_score: float | None, occurred_at: float | None,
    authority: str, role: str = "candidate", metadata: dict | None = None,
) -> RetrievalCandidate:
    body = excerpt.strip()[:MAX_EXCERPT_CHARS]
    excerpt_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    identity = f"{source}:{ref.source_kind}:{ref.source_id}:{ref.revision}:{excerpt_hash}"
    return RetrievalCandidate(
        candidate_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        source=source, source_type=ref.source_kind, source_id=ref.source_id,
        source_revision=ref.revision, source_hash=ref.content_hash,
        source_status=ref.status, privacy_scope=ref.privacy_scope, locator=ref.locator,
        excerpt=body, excerpt_hash=excerpt_hash,
        lexical_score=_score(lexical_score), vector_score=(None if vector_score is None else _score(vector_score)),
        metadata_match=1.0, recency=_recency(occurred_at),
        freshness_state="current" if ref.status == "active" else ref.status,
        source_authority=authority, candidate_role=role,
        metadata={**(metadata or {}), "occurred_at": occurred_at},
    )


def _knowledge(request: RetrievalRequest, limit: int) -> tuple[list[RetrievalCandidate], dict]:
    found = knowledge_search.hybrid_search(
        request.query, document_ids=list(request.filters.document_ids) or None,
        tags=list(request.filters.tags) or None, limit=min(limit * 2, knowledge_search.MAX_LIMIT),
        context_window=1, max_chars=min(30_000, limit * MAX_EXCERPT_CHARS * 3), mode="auto",
    )
    candidates = []
    for rank, item in enumerate(found.get("results", ()), start=1):
        ref = kig_sources.registry.resolve("knowledge_chunk", item["chunk_id"])
        raw_rank = item.get("rank")
        lexical = 1.0 / rank if raw_rank is not None else 0.0
        candidates.append(_candidate(
            source="knowledge", ref=ref, excerpt=item["content"], lexical_score=lexical,
            vector_score=item.get("vector_score"), occurred_at=item.get("created_at"),
            authority="imported_source",
            role="neighbor" if item.get("context_of") else "candidate",
            metadata={
                "document_id": item["document_id"], "collection_id": item["collection_id"],
                "ordinal": item["ordinal"], "tags": tuple(item.get("tags") or ()),
                "heading_path": tuple(item.get("heading_path") or ()),
                "page_start": item.get("page_start"), "page_end": item.get("page_end"),
                "context_of": item.get("context_of"), "match_type": item.get("match_type"),
            },
        ))
    return candidates, {
        "retrieval_mode": found.get("retrieval_mode", "fts"),
        "vector_available": bool(found.get("vector_available")),
        "lexical_fallback": not bool(found.get("vector_available")),
        "vector_error_code": found.get("vector_error_code"),
    }


def _memory(request: RetrievalRequest, limit: int) -> tuple[list[RetrievalCandidate], dict]:
    rows = memory.search_memories(request.query, limit=min(limit * 2, MAX_SOURCE_LIMIT))
    result = []
    for rank, item in enumerate(rows, start=1):
        ref = kig_sources.registry.resolve("memory_fragment", item["id"])
        result.append(_candidate(
            source="memory", ref=ref, excerpt=item["content"], lexical_score=1.0 / rank,
            vector_score=None, occurred_at=float(item.get("updated_at") or 0),
            authority="user_memory", metadata={
                "layer": item.get("layer"), "kind": item.get("kind"),
                "confidence": item.get("confidence"), "tags": item.get("tags"),
            },
        ))
    return result, {"retrieval_mode": "fts_or_like", "lexical_fallback": False}


def _history(request: RetrievalRequest, limit: int) -> tuple[list[RetrievalCandidate], dict]:
    if history_recall.settings()["mode"] == "off":
        return [], {"retrieval_mode": "disabled", "lexical_fallback": False}
    terms = history_recall._query_terms(request.query)  # noqa: SLF001 - read-only frozen CTX adapter
    if not terms:
        return [], {"retrieval_mode": "fts", "lexical_fallback": False}
    conn = db.connect()
    try:
        sessions = history_recall._select_sessions(  # noqa: SLF001
            conn, request.query, terms, request.current_session_id or "__none__",
        )
        turns = history_recall._select_turns(conn, sessions, terms)  # noqa: SLF001
    finally:
        conn.close()
    result = []
    for rank, turn in enumerate(turns[:limit * 2], start=1):
        ref = kig_sources.registry.resolve("message", turn.assistant_message_id)
        result.append(_candidate(
            source="history", ref=ref, excerpt=turn.assistant_text,
            lexical_score=min(1.0, max(1.0 / rank, turn.score / 20.0)), vector_score=None,
            occurred_at=turn.assistant_created_at, authority="recorded_conversation",
            metadata={
                "session_id": turn.session_id, "session_title": turn.session_title,
                "session_archived": turn.session_archived,
                "user_message_id": turn.user_message_id,
            },
        ))
    return result, {"retrieval_mode": "fts", "lexical_fallback": False}


def _life(request: RetrievalRequest, limit: int) -> tuple[list[RetrievalCandidate], dict]:
    terms = _terms(request.query)
    if not terms:
        return [], {"retrieval_mode": "like", "lexical_fallback": False}
    clauses = " OR ".join("summary LIKE ?" for _ in terms)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM self_timeline_entries WHERE source_type='life_event' "
            "AND source_status NOT IN ('revoked','disabled','private') AND (" + clauses + ") "
            "ORDER BY occurred_at DESC LIMIT ?",
            tuple(f"%{term}%" for term in terms) + (limit * 2,),
        ).fetchall()
    finally:
        conn.close()
    result = []
    for rank, row in enumerate(rows, start=1):
        item = dict(row)
        ref = kig_sources.registry.resolve("life_event", item["source_id"])
        result.append(_candidate(
            source="life", ref=ref, excerpt=item["summary"], lexical_score=1.0 / rank,
            vector_score=None, occurred_at=float(item["occurred_at"]),
            authority="life_ledger", metadata={"world_layer": item["world_layer"]},
        ))
    return result, {"retrieval_mode": "like", "lexical_fallback": False}


def _task(request: RetrievalRequest, limit: int) -> tuple[list[RetrievalCandidate], dict]:
    terms = _terms(request.query)
    if not terms:
        return [], {"retrieval_mode": "like", "lexical_fallback": False}
    clauses = " OR ".join("(summary LIKE ? OR tool LIKE ?)" for _ in terms)
    params = tuple(value for term in terms for value in (f"%{term}%", f"%{term}%"))
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM tool_logs WHERE " + clauses + " ORDER BY created_at DESC LIMIT ?",
            params + (limit * 2,),
        ).fetchall()
    finally:
        conn.close()
    result = []
    for rank, row in enumerate(rows, start=1):
        item = dict(row)
        ref = kig_sources.registry.resolve("tool_run", item["id"])
        result.append(_candidate(
            source="task", ref=ref, excerpt=item["summary"] or f"Tool {item['tool']}",
            lexical_score=1.0 / rank, vector_score=None,
            occurred_at=float(item["created_at"]), authority="tool_execution",
            metadata={"tool": item["tool"], "risk_level": item["risk_level"], "run_status": item["status"]},
        ))
    return result, {"retrieval_mode": "like", "lexical_fallback": False}


def _lore(request: RetrievalRequest, limit: int) -> tuple[list[RetrievalCandidate], dict]:
    rows = lore.retrieve_lore_candidates(request.query, max_sections=min(limit * 2, MAX_SOURCE_LIMIT))
    result = []
    for rank, item in enumerate(rows, start=1):
        ref = kig_sources.registry.resolve("lore_section", item["section_id"])
        result.append(_candidate(
            source="lore", ref=ref, excerpt=item["content"], lexical_score=1.0 / rank,
            vector_score=None, occurred_at=None, authority="built_in_lore",
            metadata={"legacy_rank": item["legacy_rank"]},
        ))
    return result, {"retrieval_mode": "keyword", "lexical_fallback": False}


ADAPTERS: dict[str, Adapter] = {
    "knowledge": _knowledge, "memory": _memory, "history": _history,
    "life": _life, "task": _task, "lore": _lore,
}


def _matches_filters(candidate: RetrievalCandidate, filters: RetrievalFilters) -> bool:
    if candidate.source_status not in filters.statuses:
        return False
    if filters.source_ids and candidate.source_id not in filters.source_ids:
        return False
    if filters.versions and candidate.source_revision not in filters.versions:
        return False
    document_id = candidate.metadata.get("document_id")
    if filters.document_ids and document_id not in filters.document_ids:
        return False
    tags = set(candidate.metadata.get("tags") or ())
    if filters.tags and not tags.intersection(filters.tags):
        return False
    occurred_at = candidate.metadata.get("occurred_at")
    if filters.date_from is not None and (occurred_at is None or occurred_at < filters.date_from):
        return False
    if filters.date_to is not None and (occurred_at is None or occurred_at > filters.date_to):
        return False
    return True


def _deduplicate(candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
    seen_ids: set[str] = set()
    seen_excerpt: set[tuple[str, str]] = set()
    result = []
    for item in sorted(candidates, key=_rank_key, reverse=True):
        normalized = re.sub(r"\s+", " ", item.excerpt).strip().casefold()
        fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if item.candidate_id in seen_ids or (item.source, fingerprint) in seen_excerpt:
            continue
        seen_ids.add(item.candidate_id)
        seen_excerpt.add((item.source, fingerprint))
        result.append(item)
    return result


def _diverse_round_robin(
    pools: dict[str, list[RetrievalCandidate]], source_order: tuple[str, ...], limit: int,
) -> list[RetrievalCandidate]:
    result: list[RetrievalCandidate] = []
    positions = {source: 0 for source in source_order}
    while len(result) < limit:
        progressed = False
        for source in source_order:
            position = positions[source]
            pool = pools.get(source, ())
            if position >= len(pool):
                continue
            result.append(pool[position])
            positions[source] += 1
            progressed = True
            if len(result) >= limit:
                break
        if not progressed:
            break
    return result


def _rank_key(item: RetrievalCandidate) -> tuple[float, float, float, str]:
    return (
        max(item.lexical_score, item.vector_score or 0.0),
        item.metadata_match, item.recency, item.candidate_id,
    )


def _score(value: float | int | None) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(number, 1.0)), 6)


def _recency(timestamp: float | None) -> float:
    if not timestamp:
        return 0.0
    age_days = max(0.0, (db.now() - float(timestamp)) / 86_400.0)
    return round(1.0 / (1.0 + age_days / 30.0), 6)


def _terms(query: str) -> tuple[str, ...]:
    result: list[str] = []
    for raw in _TERM.findall(query):
        if re.fullmatch(r"[\u3400-\u9fff]+", raw) and len(raw) > 4:
            values = (raw,) + tuple(raw[index:index + 3] for index in range(0, len(raw) - 2, 2))
        else:
            values = (raw,)
        for value in values:
            if value not in result:
                result.append(value)
            if len(result) >= 8:
                return tuple(result)
    return tuple(result)


def _safe_diagnostics(detail: dict) -> dict:
    allowed = {
        "retrieval_mode", "vector_available", "lexical_fallback", "vector_error_code",
    }
    return {key: detail[key] for key in allowed if key in detail}
