"""CIE.5 bounded, fail-isolated third-party context contribution protocol.

Contributors are in-process adapters registered by trusted application code. Their
returned payload is still untrusted data: collection never mutates chat messages,
and KIG must govern every candidate before CTX may render it into a request.
Diagnostics intentionally retain no candidate body.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Mapping, Sequence

from . import db

PROTOCOL_VERSION = "context-contribution-v1"
DEFAULT_TIMEOUT_MS = 200
MAX_TIMEOUT_MS = 1_000
MAX_CONTRIBUTORS = 32
MAX_CONTRIBUTIONS_PER_SOURCE = 8
MAX_PAYLOAD_CHARS = 4_000
MAX_TOKEN_ESTIMATE = 2_000
MAX_TTL_SECONDS = 86_400
CONTRIBUTOR_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
CONTRIBUTION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_KINDS = frozenset({"fact", "tool_result", "environment_state", "selected_context"})
ALLOWED_PRIVACY = frozenset({"local_only", "remote_allowed", "public"})


@dataclass(frozen=True)
class ContributionRequest:
    request_id: str
    session_id: str
    query: str
    provider_id: str
    provider_location: str
    temporary_chat: bool
    now: float
    protocol_version: str = PROTOCOL_VERSION


@dataclass(frozen=True)
class EvidenceRef:
    source_kind: str
    source_id: str
    revision: str
    content_hash: str


@dataclass(frozen=True)
class ContextContribution:
    contribution_id: str
    source: str
    kind: str
    revision: str
    content_hash: str
    created_at: float
    expires_at: float
    privacy: str
    priority: int
    token_estimate: int
    candidate_payload: Mapping[str, object]
    evidence: tuple[EvidenceRef, ...]
    protocol_version: str = PROTOCOL_VERSION


@dataclass(frozen=True)
class GovernedContribution:
    contribution_id: str
    source: str
    kind: str
    revision: str
    content_hash: str
    privacy: str
    priority: int
    token_estimate: int
    text: str
    label: str
    evidence_locators: tuple[str, ...]
    protocol_version: str = PROTOCOL_VERSION


Handler = Callable[
    [ContributionRequest],
    Sequence[ContextContribution] | Awaitable[Sequence[ContextContribution]],
]


@dataclass(frozen=True)
class ContributorSpec:
    contributor_id: str
    handler: Handler
    allowed_kinds: frozenset[str]
    allowed_privacy: frozenset[str]
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    version: str = "1"


@dataclass(frozen=True)
class ContributorRun:
    contributor_id: str
    status: str
    elapsed_ms: int
    candidate_count: int
    reason_code: str | None = None


@dataclass(frozen=True)
class CollectionBatch:
    request_id: str
    contributions: tuple[ContextContribution, ...]
    runs: tuple[ContributorRun, ...]
    specs: Mapping[str, ContributorSpec] = field(repr=False)
    protocol_version: str = PROTOCOL_VERSION


_registry: dict[str, ContributorSpec] = {}
_diagnostics: deque[dict[str, object]] = deque(maxlen=50)


def register(spec: ContributorSpec) -> None:
    contributor_id = str(spec.contributor_id or "")
    if not CONTRIBUTOR_ID_PATTERN.fullmatch(contributor_id):
        raise ValueError("contributor_id_invalid")
    if contributor_id in _registry:
        raise ValueError("contributor_id_duplicate")
    if len(_registry) >= MAX_CONTRIBUTORS:
        raise ValueError("contributor_limit_exceeded")
    if not spec.allowed_kinds or not spec.allowed_kinds <= ALLOWED_KINDS:
        raise ValueError("contributor_kinds_invalid")
    if not spec.allowed_privacy or not spec.allowed_privacy <= ALLOWED_PRIVACY:
        raise ValueError("contributor_privacy_invalid")
    if not 1 <= int(spec.timeout_ms) <= MAX_TIMEOUT_MS:
        raise ValueError("contributor_timeout_invalid")
    _registry[contributor_id] = spec


def unregister(contributor_id: str) -> None:
    _registry.pop(str(contributor_id), None)


def is_enabled(contributor_id: str) -> bool:
    # Registration is not user consent to disclose the current query.
    return db.get_setting(_setting_key(contributor_id), "0") == "1"


def set_enabled(contributor_id: str, enabled: bool) -> dict[str, object]:
    if contributor_id not in _registry:
        raise KeyError(contributor_id)
    db.set_setting(_setting_key(contributor_id), "1" if enabled else "0")
    return contributor_public(_registry[contributor_id])


def contributor_public(spec: ContributorSpec) -> dict[str, object]:
    return {
        "contributor_id": spec.contributor_id,
        "version": str(spec.version),
        "enabled": is_enabled(spec.contributor_id),
        "allowed_kinds": sorted(spec.allowed_kinds),
        "allowed_privacy": sorted(spec.allowed_privacy),
        "timeout_ms": int(spec.timeout_ms),
    }


def diagnostics() -> dict[str, object]:
    """Return registration and recent outcome metadata without payload bodies."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "contributors": [contributor_public(spec) for spec in _registry.values()],
        "recent_collections": list(_diagnostics),
    }


async def collect(request: ContributionRequest) -> CollectionBatch:
    specs = dict(_registry)
    runs_and_items = await asyncio.gather(*(
        _run(spec, request) for spec in specs.values()
    )) if specs else []
    runs = tuple(item[0] for item in runs_and_items)
    contributions = tuple(
        contribution
        for _run_result, items in runs_and_items
        for contribution in items
    )
    batch = CollectionBatch(
        request_id=request.request_id,
        contributions=contributions,
        runs=runs,
        specs=specs,
    )
    _diagnostics.appendleft({
        "request_id": request.request_id,
        "created_at": request.now,
        "candidate_count": len(contributions),
        "accepted_count": None,
        "rejected_count": None,
        "runs": [run_public(run) for run in runs],
    })
    return batch


def record_governance(
    request_id: str, *, accepted_count: int, rejected_counts: Mapping[str, int],
) -> None:
    for event in _diagnostics:
        if event.get("request_id") != request_id:
            continue
        event["accepted_count"] = max(0, int(accepted_count))
        event["rejected_count"] = sum(max(0, int(value)) for value in rejected_counts.values())
        event["rejected_reason_counts"] = {
            str(key): max(0, int(value)) for key, value in rejected_counts.items()
        }
        return


def run_public(run: ContributorRun) -> dict[str, object]:
    return {
        "contributor_id": run.contributor_id,
        "status": run.status,
        "elapsed_ms": run.elapsed_ms,
        "candidate_count": run.candidate_count,
        "reason_code": run.reason_code,
    }


def payload_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _run(
    spec: ContributorSpec, request: ContributionRequest,
) -> tuple[ContributorRun, tuple[ContextContribution, ...]]:
    started = time.perf_counter()
    if not is_enabled(spec.contributor_id):
        return ContributorRun(spec.contributor_id, "disabled", 0, 0), ()
    try:
        result = await asyncio.wait_for(
            _invoke(spec.handler, request), timeout=spec.timeout_ms / 1_000,
        )
        if not isinstance(result, Sequence) or isinstance(result, (str, bytes, bytearray)):
            raise TypeError("contributor_result_invalid")
        items = tuple(result[:MAX_CONTRIBUTIONS_PER_SOURCE])
        if not all(isinstance(item, ContextContribution) for item in items):
            raise TypeError("contributor_result_invalid")
        elapsed = int((time.perf_counter() - started) * 1_000)
        return ContributorRun(spec.contributor_id, "ok", elapsed, len(items)), items
    except asyncio.TimeoutError:
        elapsed = int((time.perf_counter() - started) * 1_000)
        return ContributorRun(spec.contributor_id, "timeout", elapsed, 0, "contributor_timeout"), ()
    except Exception:  # noqa: BLE001 - isolation boundary for third-party code
        elapsed = int((time.perf_counter() - started) * 1_000)
        return ContributorRun(spec.contributor_id, "error", elapsed, 0, "contributor_error"), ()


async def _invoke(handler: Handler, request: ContributionRequest) -> Sequence[ContextContribution]:
    if inspect.iscoroutinefunction(handler):
        return await handler(request)
    result = await asyncio.to_thread(handler, request)
    if inspect.isawaitable(result):
        return await result
    return result


def _setting_key(contributor_id: str) -> str:
    return f"cie_context_contributor:{contributor_id}"


def _reset_for_tests() -> None:
    _registry.clear()
    _diagnostics.clear()
