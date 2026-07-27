"""LIFE.13 conservative compaction for rebuildable and runtime-only records."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from . import db

POLICY_VERSION = "life-retention-v1"
AUTHORITATIVE_TABLES = (
    "life_events", "life_event_revisions", "life_event_sources",
    "diary_entries", "diary_entry_revisions", "diary_entry_sources",
    "important_dates", "important_date_sources", "personal_goals", "personal_goal_sources",
)


@dataclass(frozen=True)
class CompactionResult:
    policy_version: str
    dry_run: bool
    cutoff: float
    stale_event_candidates: int
    catchup_requests: int
    orphan_exit_snapshots: int
    runtime_events: int
    authoritative_before: dict[str, int]
    authoritative_after: dict[str, int]


def _counts(conn, tables: tuple[str, ...]) -> dict[str, int]:
    return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}


def compact_derived(*, cutoff: float, dry_run: bool = True,
                    keep_runtime_events: int = 32) -> dict[str, Any]:
    """Compact only reconstructible candidates and runtime metadata.

    Source-owned events, diary revisions, dates and confirmed goals are never
    touched. The latest exit snapshot and the newest runtime events are kept so
    restart and anomaly diagnostics remain useful.
    """
    if cutoff <= 0 or keep_runtime_events < 1:
        raise ValueError("invalid life retention boundary")
    conn = db.connect()
    try:
        before = _counts(conn, AUTHORITATIVE_TABLES)
        stale_candidates = conn.execute(
            "SELECT COUNT(*) FROM life_event_candidates WHERE status IN ('materialized','rejected') "
            "AND updated_at<?", (cutoff,),
        ).fetchone()[0]
        catchups = conn.execute(
            "SELECT COUNT(*) FROM life_catchup_requests WHERE status IN ('applied','skipped') "
            "AND COALESCE(completed_at,created_at)<?", (cutoff,),
        ).fetchone()[0]
        orphan_snapshots = conn.execute(
            "SELECT COUNT(*) FROM life_exit_snapshots s WHERE s.created_at<? "
            "AND s.id!=(SELECT id FROM life_exit_snapshots ORDER BY exited_at DESC,id DESC LIMIT 1) "
            "AND NOT EXISTS(SELECT 1 FROM life_catchup_requests r WHERE r.exit_snapshot_id=s.id "
            "AND NOT (r.status IN ('applied','skipped') AND COALESCE(r.completed_at,r.created_at)<?))",
            (cutoff, cutoff),
        ).fetchone()[0]
        runtime_events = conn.execute(
            "SELECT COUNT(*) FROM life_runtime_events WHERE created_at<? AND id NOT IN "
            "(SELECT id FROM life_runtime_events ORDER BY created_at DESC,id DESC LIMIT ?)",
            (cutoff, keep_runtime_events),
        ).fetchone()[0]
        if not dry_run:
            conn.execute(
                "DELETE FROM life_event_candidates WHERE status IN ('materialized','rejected') AND updated_at<?",
                (cutoff,),
            )
            conn.execute(
                "DELETE FROM life_catchup_requests WHERE status IN ('applied','skipped') "
                "AND COALESCE(completed_at,created_at)<?", (cutoff,),
            )
            conn.execute(
                "DELETE FROM life_exit_snapshots WHERE created_at<? "
                "AND id!=(SELECT id FROM life_exit_snapshots ORDER BY exited_at DESC,id DESC LIMIT 1) "
                "AND NOT EXISTS(SELECT 1 FROM life_catchup_requests r WHERE r.exit_snapshot_id=life_exit_snapshots.id)",
                (cutoff,),
            )
            conn.execute(
                "DELETE FROM life_runtime_events WHERE created_at<? AND id NOT IN "
                "(SELECT id FROM life_runtime_events ORDER BY created_at DESC,id DESC LIMIT ?)",
                (cutoff, keep_runtime_events),
            )
            conn.commit()
        after = _counts(conn, AUTHORITATIVE_TABLES)
        if before != after:
            conn.rollback()
            raise RuntimeError("authoritative LIFE records changed during compaction")
        return asdict(CompactionResult(
            policy_version=POLICY_VERSION, dry_run=dry_run, cutoff=cutoff,
            stale_event_candidates=stale_candidates, catchup_requests=catchups,
            orphan_exit_snapshots=orphan_snapshots, runtime_events=runtime_events,
            authoritative_before=before, authoritative_after=after,
        ))
    finally:
        conn.close()
