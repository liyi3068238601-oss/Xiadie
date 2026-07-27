"""KIG.1 typed provenance envelopes over existing authoritative stores.

This module deliberately stores no source body.  Adapters expose only stable
identity, revision/hash, lifecycle/privacy state and an owner-system locator.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Callable

from . import db, lore

SOURCE_KINDS = frozenset({
    "knowledge_document", "knowledge_chunk", "message", "memory_fragment",
    "life_event", "tool_run", "lore_section",
})
DERIVED_KINDS = frozenset({
    "retrieval_bundle", "evidence_link", "information_item", "pwm_claim",
    "pwm_entity", "pwm_relation", "pwm_world_event", "version_relation",
    "maintenance_candidate",
})
DEPENDENCY_STATUSES = frozenset({
    "active", "stale", "missing", "revoked", "inaccessible", "unverified",
})
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class SourceRefError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SourceRef:
    source_kind: str
    source_id: str
    revision: str
    content_hash: str
    status: str
    privacy_scope: str
    locator: str

    def to_dict(self) -> dict:
        return asdict(self)


Resolver = Callable[[str], SourceRef]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_hash(value: object) -> str:
    return _sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _missing(kind: str, source_id: str) -> SourceRefError:
    return SourceRefError("source_missing", f"{kind} source {source_id!r} does not exist")


def _document(source_id: str) -> SourceRef:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT d.*,c.status AS collection_status,c.default_transmission_policy "
            "FROM knowledge_documents d JOIN knowledge_collections c ON c.id=d.collection_id "
            "WHERE d.id=?", (source_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise _missing("knowledge_document", source_id)
    status = "active" if (row["status"] == "indexed" and row["collection_status"] == "active"
                          and row["governance_status"] == "active") else "inaccessible"
    if row["status"] in {"delete_pending", "deleted"}:
        status = "revoked"
    content_hash = row["content_sha256"] or _sha256("")
    revision = (f"{row['index_version'] or 'unindexed'}:{row['active_index_revision']}:"
                f"{content_hash}:{row['policy_revision']}")
    policy = row["transmission_policy"] or row["default_transmission_policy"] or "local_only"
    privacy = f"{row['sensitivity']}:{policy}"
    return SourceRef("knowledge_document", source_id, revision, content_hash, status, privacy,
                     f"knowledge://documents/{source_id}")


def _chunk(source_id: str) -> SourceRef:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT k.id,k.document_id,k.content_sha256,k.chunker_version,d.status,d.governance_status,"
            "d.active_index_revision,d.sensitivity,"
            "d.transmission_policy,d.policy_revision,c.status AS collection_status,"
            "c.default_transmission_policy FROM knowledge_chunks k "
            "JOIN knowledge_documents d ON d.id=k.document_id "
            "JOIN knowledge_collections c ON c.id=d.collection_id WHERE k.id=?", (source_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise _missing("knowledge_chunk", source_id)
    status = "active" if (row["status"] == "indexed" and row["collection_status"] == "active"
                          and row["governance_status"] == "active") else "inaccessible"
    if row["status"] in {"delete_pending", "deleted"}:
        status = "revoked"
    policy = row["transmission_policy"] or row["default_transmission_policy"] or "local_only"
    return SourceRef(
        "knowledge_chunk", source_id,
        f"{row['chunker_version'] or 'unknown'}:{row['active_index_revision']}:"
        f"{row['content_sha256']}:{row['policy_revision']}",
        row["content_sha256"], status, f"{row['sensitivity']}:{policy}",
        f"knowledge://chunks/{source_id}",
    )


def _message(source_id: str) -> SourceRef:
    conn = db.connect()
    try:
        row = conn.execute("SELECT id,session_id,content,created_at FROM messages WHERE id=?", (source_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise _missing("message", source_id)
    content_hash = _sha256(row["content"])
    return SourceRef("message", source_id, f"{row['created_at']}:{content_hash}", content_hash,
                     "active", "private", f"conversation://sessions/{row['session_id']}/messages/{source_id}")


def _memory(source_id: str) -> SourceRef:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id,content,sensitivity,status,enabled,lifecycle_revision FROM memory_fragments WHERE id=?",
            (source_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise _missing("memory_fragment", source_id)
    content_hash = _sha256(row["content"])
    status = "active" if row["status"] == "active" and row["enabled"] else "inaccessible"
    if row["status"] == "tombstone":
        status = "revoked"
    return SourceRef("memory_fragment", source_id, f"{row['lifecycle_revision']}:{content_hash}",
                     content_hash, status, row["sensitivity"], f"memory://fragments/{source_id}")


def _life_event(source_id: str) -> SourceRef:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT e.id,e.event_kind,e.world_layer,e.lifecycle_status,e.current_revision,"
            "r.summary,r.attributes_json FROM life_events e JOIN life_event_revisions r "
            "ON r.event_id=e.id AND r.revision=e.current_revision WHERE e.id=?", (source_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise _missing("life_event", source_id)
    content_hash = _canonical_hash({
        "event_kind": row["event_kind"], "world_layer": row["world_layer"],
        "summary": row["summary"], "attributes": json.loads(row["attributes_json"]),
    })
    status = "active" if row["lifecycle_status"] == "active" else "revoked"
    return SourceRef("life_event", source_id, str(row["current_revision"]), content_hash,
                     status, "private", f"life://events/{source_id}")


def _tool_run(source_id: str) -> SourceRef:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id,tool,risk_level,status,summary,created_at FROM tool_logs WHERE id=?", (source_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise _missing("tool_run", source_id)
    content_hash = _canonical_hash({
        "tool": row["tool"], "risk_level": row["risk_level"],
        "status": row["status"], "summary": row["summary"],
    })
    status = "active" if row["status"] == "done" else "inaccessible"
    return SourceRef("tool_run", source_id, f"{row['created_at']}:{content_hash}", content_hash,
                     status, "private", f"tool://runs/{source_id}")


def _lore_section(source_id: str) -> SourceRef:
    for section in lore._sections():
        if _sha256(section["title"]) != source_id:
            continue
        content_hash = _sha256(f"## {section['title']}\n{section['body']}")
        return SourceRef("lore_section", source_id, content_hash, content_hash, "active", "public",
                         f"lore://sections/{source_id}")
    raise _missing("lore_section", source_id)


class SourceAdapterRegistry:
    def __init__(self) -> None:
        self._resolvers: dict[str, Resolver] = {}

    def register(self, source_kind: str, resolver: Resolver) -> None:
        if source_kind not in SOURCE_KINDS:
            raise SourceRefError("source_kind_invalid", "source kind is not allowlisted")
        self._resolvers[source_kind] = resolver

    def resolve(self, source_kind: str, source_id: str) -> SourceRef:
        if not source_id or source_kind not in self._resolvers:
            raise SourceRefError("source_kind_invalid", "source kind or id is invalid")
        return self._resolvers[source_kind](source_id)


registry = SourceAdapterRegistry()
for _kind, _resolver in {
    "knowledge_document": _document, "knowledge_chunk": _chunk, "message": _message,
    "memory_fragment": _memory, "life_event": _life_event, "tool_run": _tool_run,
    "lore_section": _lore_section,
}.items():
    registry.register(_kind, _resolver)


def validate_ref(source_ref: SourceRef) -> SourceRef:
    if source_ref.source_kind not in SOURCE_KINDS or not source_ref.source_id:
        raise SourceRefError("source_ref_invalid", "source identity is invalid")
    if not _HEX64.fullmatch(source_ref.content_hash):
        raise SourceRefError("source_hash_invalid", "source hash must be lowercase sha256")
    current = registry.resolve(source_ref.source_kind, source_ref.source_id)
    if source_ref != current:
        raise SourceRefError("source_ref_mismatch", "source envelope does not match authoritative metadata")
    return current


def bind_dependency(*, derived_kind: str, derived_id: str, source_ref: SourceRef) -> dict:
    if derived_kind not in DERIVED_KINDS or not derived_id:
        raise SourceRefError("derived_identity_invalid", "derived identity is not allowlisted")
    current = validate_ref(source_ref)
    now = db.now()
    conn = db.connect()
    try:
        existing = conn.execute(
            "SELECT id,created_at FROM derived_dependencies WHERE derived_kind=? AND derived_id=? "
            "AND source_kind=? AND source_id=?",
            (derived_kind, derived_id, current.source_kind, current.source_id),
        ).fetchone()
        dependency_id = existing["id"] if existing else db.new_id()
        created_at = existing["created_at"] if existing else now
        conn.execute(
            "INSERT INTO derived_dependencies(id,derived_kind,derived_id,source_kind,source_id,"
            "source_revision,source_hash,source_status_snapshot,privacy_scope,source_locator,"
            "dependency_status,checked_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(derived_kind,derived_id,source_kind,source_id) DO UPDATE SET "
            "source_revision=excluded.source_revision,source_hash=excluded.source_hash,"
            "source_status_snapshot=excluded.source_status_snapshot,privacy_scope=excluded.privacy_scope,"
            "source_locator=excluded.source_locator,dependency_status=excluded.dependency_status,"
            "checked_at=excluded.checked_at,updated_at=excluded.updated_at",
            (dependency_id, derived_kind, derived_id, current.source_kind, current.source_id,
             current.revision, current.content_hash, current.status, current.privacy_scope,
             current.locator, "active" if current.status == "active" else current.status,
             now, created_at, now),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM derived_dependencies WHERE id=?", (dependency_id,)).fetchone())
    finally:
        conn.close()


def check_dependency(dependency_id: str) -> dict:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM derived_dependencies WHERE id=?", (dependency_id,)).fetchone()
        if not row:
            raise SourceRefError("dependency_missing", "derived dependency does not exist")
        try:
            current = registry.resolve(row["source_kind"], row["source_id"])
            if current.status in {"revoked", "inaccessible"}:
                status = current.status
            elif current.revision != row["source_revision"] or current.content_hash != row["source_hash"]:
                status = "stale"
            elif current.locator != row["source_locator"] or current.privacy_scope != row["privacy_scope"]:
                status = "stale"
            else:
                status = "active"
        except SourceRefError as exc:
            status = "missing" if exc.code == "source_missing" else "unverified"
        except Exception:
            status = "unverified"
        now = db.now()
        conn.execute(
            "UPDATE derived_dependencies SET dependency_status=?,checked_at=?,updated_at=? WHERE id=?",
            (status, now, now, dependency_id),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM derived_dependencies WHERE id=?", (dependency_id,)).fetchone())
    finally:
        conn.close()


def sweep_dependencies(*, limit: int = 100) -> dict[str, int]:
    limit = max(1, min(int(limit), 500))
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id FROM derived_dependencies ORDER BY COALESCE(checked_at,0),created_at,id LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    counts = {status: 0 for status in sorted(DEPENDENCY_STATUSES)}
    for row in rows:
        result = check_dependency(row["id"])
        counts[result["dependency_status"]] += 1
    counts["checked"] = len(rows)
    return counts
