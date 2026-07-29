"""Read-only LIFE2 WorldBook r1 loader, source gate, and deterministic recall."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from threading import RLock
from typing import Mapping

from . import db

WORLD_BOOK_PATH = Path(__file__).with_name("knowledge") / "xiadie_worldbook_r1.json"
PROTOCOL_VERSION = "worldbook-r1"
SOURCE_GATE_VERSION = "worldbook-source-gate-v1"
ROLLOUT_KEY = "life.worldbook_r1.rollout_mode"
ROLLOUT_MODES = ("off", "shadow", "active")
MAX_SECTIONS = 3
MAX_CHARS = 3600
PRODUCTION_SOURCE_STATUSES = frozenset({"verified_a"})
SHADOW_SOURCE_STATUSES = frozenset({"verified_a", "candidate_b", "local_candidate"})
LORE_PREAMBLE = "以下仅是原作背景资料，不得据此把当前用户认定为开拓者、原作人物或继承相同关系。"

_CACHE_LOCK = RLock()
_CACHE: dict[tuple[str, str, str], tuple[dict[str, object], ...]] = {}


class WorldBookResourceError(ValueError):
    pass


@dataclass(frozen=True)
class WorldBookRecall:
    content: str
    candidate_content: str
    rollout_mode: str
    selected_r1: bool
    manifest_hash: str
    source_gate_version: str
    entry_ids: tuple[str, ...]
    candidate_entry_ids: tuple[str, ...]
    revisions: tuple[str, ...]
    candidate_revisions: tuple[str, ...]
    truncated: bool
    fallback_reason: str | None

    def public_meta(self) -> dict[str, object]:
        return {
            "worldbook_protocol_version": PROTOCOL_VERSION,
            "worldbook_rollout_mode": self.rollout_mode,
            "worldbook_r1_selected": self.selected_r1,
            "worldbook_manifest_hash": self.manifest_hash,
            "worldbook_source_gate_version": self.source_gate_version,
            "worldbook_entry_ids": list(self.entry_ids),
            "worldbook_candidate_entry_ids": list(self.candidate_entry_ids),
            "worldbook_revisions": list(self.revisions),
            "worldbook_candidate_revisions": list(self.candidate_revisions),
            "worldbook_truncated": self.truncated,
            "worldbook_fallback_reason": self.fallback_reason,
        }


def retrieve_for_request(
    query: str, *, legacy_content: str, rollout_mode: str | None = None,
    max_sections: int = MAX_SECTIONS, max_chars: int = MAX_CHARS,
) -> WorldBookRecall:
    rollout = rollout_mode or db.get_setting(ROLLOUT_KEY, "off")
    if rollout not in ROLLOUT_MODES:
        rollout = "off"
    try:
        raw = WORLD_BOOK_PATH.read_bytes()
        manifest_hash = hashlib.sha256(raw).hexdigest()
        entries = _load_entries(raw, manifest_hash, rollout)
        candidates, truncated = _retrieve(
            query, entries, allowed=SHADOW_SOURCE_STATUSES,
            max_sections=max_sections, max_chars=max_chars,
        )
        production, prod_truncated = _retrieve(
            query, entries, allowed=PRODUCTION_SOURCE_STATUSES,
            max_sections=max_sections, max_chars=max_chars,
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return WorldBookRecall(
            content=legacy_content, candidate_content="", rollout_mode=rollout,
            selected_r1=False, manifest_hash="", source_gate_version=SOURCE_GATE_VERSION,
            entry_ids=(), candidate_entry_ids=(), revisions=(), candidate_revisions=(),
            truncated=False, fallback_reason="worldbook_resource_invalid",
        )
    candidate_content = _render(candidates)
    candidate_ids = tuple(str(item["entry_id"]) for item in candidates)
    candidate_revisions = tuple(str(item["revision"]) for item in candidates)
    if rollout != "active":
        return WorldBookRecall(
            content=legacy_content, candidate_content=candidate_content, rollout_mode=rollout,
            selected_r1=False, manifest_hash=manifest_hash,
            source_gate_version=SOURCE_GATE_VERSION, entry_ids=(),
            candidate_entry_ids=candidate_ids, revisions=(),
            candidate_revisions=candidate_revisions, truncated=truncated,
            fallback_reason="worldbook_rollout_inactive",
        )
    # Active is still fail-closed: B/local candidates can never replace legacy Lore.
    if not production:
        return WorldBookRecall(
            content=legacy_content, candidate_content=candidate_content, rollout_mode=rollout,
            selected_r1=False, manifest_hash=manifest_hash,
            source_gate_version=SOURCE_GATE_VERSION, entry_ids=(),
            candidate_entry_ids=candidate_ids, revisions=(),
            candidate_revisions=candidate_revisions, truncated=truncated,
            fallback_reason="worldbook_no_verified_source",
        )
    return WorldBookRecall(
        content=_render(production), candidate_content=candidate_content, rollout_mode=rollout,
        selected_r1=True, manifest_hash=manifest_hash,
        source_gate_version=SOURCE_GATE_VERSION,
        entry_ids=tuple(str(item["entry_id"]) for item in production),
        candidate_entry_ids=candidate_ids,
        revisions=tuple(str(item["revision"]) for item in production),
        candidate_revisions=candidate_revisions,
        truncated=prod_truncated, fallback_reason=None,
    )


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _load_entries(raw: bytes, manifest_hash: str, rollout: str) -> tuple[dict[str, object], ...]:
    key = (manifest_hash, SOURCE_GATE_VERSION, rollout)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise WorldBookResourceError("worldbook_protocol_invalid")
    if payload.get("source_gate_version") != SOURCE_GATE_VERSION:
        raise WorldBookResourceError("worldbook_source_gate_invalid")
    rows = payload.get("entries")
    if not isinstance(rows, list) or payload.get("entry_count") != len(rows):
        raise WorldBookResourceError("worldbook_entry_count_invalid")
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != payload.get("entries_sha256"):
        raise WorldBookResourceError("worldbook_manifest_hash_invalid")
    ids = {str(item.get("entry_id")) for item in rows if isinstance(item, dict)}
    if len(ids) != len(rows):
        raise WorldBookResourceError("worldbook_entry_id_invalid")
    validated: list[dict[str, object]] = []
    for item in rows:
        _validate_entry(item, ids)
        validated.append(dict(item))
    result = tuple(validated)
    with _CACHE_LOCK:
        _CACHE[key] = result
        # The current request holds its tuple; old immutable namespaces can be discarded.
        for stale in tuple(_CACHE):
            if stale != key:
                _CACHE.pop(stale, None)
    return result


def _validate_entry(item: Mapping[str, object], ids: set[str]) -> None:
    entry_id = item.get("entry_id")
    body = item.get("body")
    if not isinstance(entry_id, str) or not re.fullmatch(r"[a-z0-9_]+", entry_id):
        raise WorldBookResourceError("worldbook_entry_id_invalid")
    if not isinstance(body, str) or not body:
        raise WorldBookResourceError("worldbook_body_invalid")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if item.get("body_sha256") != digest or item.get("revision") != f"r1-{digest[:16]}":
        raise WorldBookResourceError("worldbook_body_hash_invalid")
    if item.get("source_status") not in SHADOW_SOURCE_STATUSES:
        raise WorldBookResourceError("worldbook_source_status_invalid")
    if item.get("always_on") is not False:
        raise WorldBookResourceError("worldbook_always_on_forbidden")
    if not isinstance(item.get("priority"), int) or not 0 <= int(item["priority"]) <= 100:
        raise WorldBookResourceError("worldbook_priority_invalid")
    for field in ("triggers", "aliases", "related_entry_ids", "source_refs"):
        if not isinstance(item.get(field), list) or not all(isinstance(value, str) for value in item[field]):
            raise WorldBookResourceError(f"worldbook_{field}_invalid")
    if not set(item["related_entry_ids"]).issubset(ids):
        raise WorldBookResourceError("worldbook_related_entry_invalid")


def _retrieve(
    query: str, entries: tuple[dict[str, object], ...], *, allowed: frozenset[str],
    max_sections: int, max_chars: int,
) -> tuple[list[dict[str, object]], bool]:
    clean_query = _fold(query)
    if not clean_query or max_sections <= 0 or max_chars <= 0:
        return [], False
    by_id = {str(item["entry_id"]): item for item in entries}
    explicit: list[dict[str, object]] = []
    for item in entries:
        if item["source_status"] not in allowed:
            continue
        terms = list(item["triggers"]) + list(item["aliases"])
        if any(_fold(str(term)) in clean_query for term in terms if _fold(str(term))):
            explicit.append(item)
    explicit.sort(key=lambda item: (-int(item["priority"]), str(item["entry_id"])))
    ordered: list[dict[str, object]] = []
    seen: set[str] = set()
    for root in explicit:
        root_id = str(root["entry_id"])
        if root_id not in seen:
            ordered.append(root)
            seen.add(root_id)
        related = [
            by_id[entry_id] for entry_id in root["related_entry_ids"]
            if entry_id in by_id and by_id[entry_id]["source_status"] in allowed
        ]
        related.sort(key=lambda item: (-int(item["priority"]), str(item["entry_id"])))
        for item in related[:2]:
            entry_id = str(item["entry_id"])
            if entry_id not in seen:
                ordered.append(item)
                seen.add(entry_id)
    selected: list[dict[str, object]] = []
    used = 0
    content_limit = max(0, min(max_chars, MAX_CHARS) - len(LORE_PREAMBLE) - 2)
    truncated = False
    for item in ordered:
        if len(selected) >= min(max_sections, MAX_SECTIONS):
            truncated = True
            break
        rendered = _render_entry(item)
        remaining = content_limit - used
        if remaining <= 0:
            truncated = True
            break
        if len(rendered) > remaining:
            if selected:
                truncated = True
                break
            item = dict(item)
            item["body"] = str(item["body"])[: max(0, remaining - len(f"## {item['entry_id']}\n"))]
            truncated = True
        selected.append(item)
        used += len(_render_entry(item)) + (2 if len(selected) > 1 else 0)
    return selected, truncated


def _render_entry(item: Mapping[str, object]) -> str:
    return f"## {item['entry_id']}\n{item['body']}"


def _render(entries: list[dict[str, object]]) -> str:
    if not entries:
        return ""
    return LORE_PREAMBLE + "\n\n" + "\n\n".join(_render_entry(item) for item in entries)


def _fold(value: str) -> str:
    return re.sub(r"\s+", "", value.casefold())
