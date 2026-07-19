"""K.8 知识审计生命周期：过期清理不得破坏仍可见消息的引用。"""
from __future__ import annotations

import hashlib

from . import db

RETENTION_POLICY_VERSION = "knowledge-audit-retention-v1"
RETENTION_RECALL_DECISIONS = 90
RETENTION_GRANTS = 30
RETENTION_CHAT_RETRIEVALS = 180
CLEANUP_LIMIT = 100
EMPTY_QUERY_SHA256 = hashlib.sha256(b"").hexdigest()


def policy() -> dict:
    return {
        "policy_version": RETENTION_POLICY_VERSION,
        "recall_decisions_days": RETENTION_RECALL_DECISIONS,
        "terminal_grants_days": RETENTION_GRANTS,
        "retrieval_metadata_days": RETENTION_CHAT_RETRIEVALS,
        "citations": "message_lifetime",
        "document_bodies_in_audit": False,
        "expired_cited_retrieval_behavior": "minimize_metadata_keep_citation",
    }


def stats() -> dict:
    conn = db.connect()
    try:
        counts = {}
        for key, table in (
            ("recall_decisions", "knowledge_recall_decisions"),
            ("grants", "knowledge_transmission_grants"),
            ("retrievals", "knowledge_chat_retrievals"),
            ("citations", "knowledge_message_citations"),
        ):
            counts[key] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        counts["minimized_retrievals"] = int(conn.execute(
            "SELECT COUNT(*) FROM knowledge_chat_retrievals WHERE audit_state='minimized'"
        ).fetchone()[0])
        counts["knowledge_candidates_isolated"] = int(conn.execute(
            "SELECT COALESCE(SUM(CAST(json_extract(w.value,'$.count') AS INTEGER)),0)"
            " FROM memory_observer_runs r,json_each(CASE WHEN json_valid(r.warnings_json)"
            " THEN r.warnings_json ELSE '[]' END) w"
            " WHERE json_extract(w.value,'$.code')='knowledge_memory_write_blocked'"
        ).fetchone()[0])
        return {**policy(), "counts": counts}
    finally:
        conn.close()


def run_once(*, now: float | None = None) -> int:
    at = db.now() if now is None else float(now)
    changed = 0
    changed += _cleanup_table(
        "knowledge_transmission_grants", at - RETENTION_GRANTS * 86400,
        "AND status IN ('consumed','expired','revoked','denied')",
    )
    changed += _cleanup_table(
        "knowledge_recall_decisions", at - RETENTION_RECALL_DECISIONS * 86400,
        "AND NOT EXISTS (SELECT 1 FROM knowledge_transmission_grants g"
        " WHERE g.recall_decision_id=knowledge_recall_decisions.id"
        " AND g.status IN ('pending','issued'))",
    )
    changed += _cleanup_retrievals(at - RETENTION_CHAT_RETRIEVALS * 86400, at)
    return changed


def _cleanup_table(table: str, cutoff: float, extra_where: str) -> int:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table}"
            f" WHERE created_at < ? {extra_where} ORDER BY created_at,id LIMIT ?)",
            (cutoff, CLEANUP_LIMIT),
        )
        conn.commit()
        return cursor.rowcount
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _cleanup_retrievals(cutoff: float, now: float) -> int:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        deleted = conn.execute(
            "DELETE FROM knowledge_chat_retrievals WHERE id IN ("
            "SELECT r.id FROM knowledge_chat_retrievals r WHERE r.created_at<?"
            " AND NOT EXISTS (SELECT 1 FROM knowledge_message_citations c"
            " WHERE c.retrieval_id=r.id) ORDER BY r.created_at,r.id LIMIT ?)",
            (cutoff, CLEANUP_LIMIT),
        ).rowcount
        minimized = conn.execute(
            "UPDATE knowledge_chat_retrievals SET audit_state='minimized',minimized_at=?,"
            "user_message_id=NULL,trigger_reason='retained_citation',query_sha256=?,"
            "candidate_count=0,injected_count=0,knowledge_tokens=0,lore_tokens=0,memory_tokens=0"
            " WHERE id IN (SELECT r.id FROM knowledge_chat_retrievals r"
            " WHERE r.created_at<? AND r.audit_state='active'"
            " AND EXISTS (SELECT 1 FROM knowledge_message_citations c"
            " WHERE c.retrieval_id=r.id) ORDER BY r.created_at,r.id LIMIT ?)",
            (now, EMPTY_QUERY_SHA256, cutoff, CLEANUP_LIMIT),
        ).rowcount
        conn.commit()
        return deleted + minimized
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
