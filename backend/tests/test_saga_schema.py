"""Saga D.1 数据地基与数据库约束。"""
import json
import sqlite3

import pytest

from app import db

db.init_db()


def _episode(conn: sqlite3.Connection, episode_id: str, at: float) -> None:
    conn.execute(
        "INSERT INTO memory_episodes("
        "id,title,summary,start_at,end_at,status,source,created_at,updated_at"
        ") VALUES(?,?,?,?,?,'active','automatic',?,?)",
        (episode_id, f"经历 {episode_id}", "有来源的经历摘要", at, at + 60, at, at),
    )


def _saga(conn: sqlite3.Connection, saga_id: str, start: float = 100.0) -> None:
    conn.execute(
        "INSERT INTO memory_sagas("
        "id,title,summary,theme,start_at,end_at,source_episode_ids_json,created_at,updated_at"
        ") VALUES(?,?,?,?,?,?,?,?,?)",
        (saga_id, "共同完善记忆系统", "我们在不同日期持续完善记忆系统。", "project", start,
         start + 86400, json.dumps(["episode-a", "episode-b"]), start, start),
    )


def test_saga_schema_has_traceability_and_lifecycle_fields():
    conn = db.connect()
    try:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()["value"]
        assert version == "71"
        tables = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'memory_saga%'"
            ).fetchall()
        }
        assert tables == {
            "memory_sagas", "memory_saga_episodes", "memory_saga_entities",
            "memory_saga_events",
        }
        worker_tables = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name IN ('saga_consolidator_runs','saga_consolidator_events')"
            ).fetchall()
        }
        assert worker_tables == {"saga_consolidator_runs", "saga_consolidator_events"}
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(memory_sagas)").fetchall()
        }
        assert {
            "theme", "status", "grouping_fingerprint", "policy_version",
            "source_episode_ids_json", "source_hash", "summary_status",
            "summary_protocol_version", "summary_evidence_json", "completion_reason",
            "completed_at", "archived_at", "tombstoned_at", "correction_note",
            "corrected_at", "current_stage", "completion_evidence_episode_ids_json",
            "lifecycle_policy_version", "revision",
        } <= columns
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table'"
            " AND name='saga_relationship_delta_suggestions'"
        ).fetchone()
    finally:
        conn.close()


def test_schema_18_upgrades_existing_episode_and_entity_without_rewriting_them():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(
            "CREATE TABLE memory_episodes(id TEXT PRIMARY KEY, title TEXT NOT NULL);"
            "CREATE TABLE memory_entities(id TEXT PRIMARY KEY, name TEXT NOT NULL);"
            "INSERT INTO memory_episodes VALUES('legacy-episode','旧库经历');"
            "INSERT INTO memory_entities VALUES('legacy-entity','旧库实体');"
        )
        migration = next(sql for version, sql in db.MIGRATIONS if version == 18)
        conn.executescript(migration)
        assert conn.execute(
            "SELECT title FROM memory_episodes WHERE id='legacy-episode'"
        ).fetchone()["title"] == "旧库经历"
        assert conn.execute(
            "SELECT name FROM memory_entities WHERE id='legacy-entity'"
        ).fetchone()["name"] == "旧库实体"
        conn.execute(
            "INSERT INTO memory_sagas(id,title,summary,start_at,end_at,created_at,updated_at)"
            " VALUES('new-saga','长期故事','旧数据可以成为来源',1,2,2,2)"
        )
        conn.execute(
            "INSERT INTO memory_saga_episodes(saga_id,episode_id,position,added_at)"
            " VALUES('new-saga','legacy-episode',0,2)"
        )
        conn.execute(
            "INSERT INTO memory_saga_entities(saga_id,entity_id,created_at,updated_at)"
            " VALUES('new-saga','legacy-entity',2,2)"
        )
    finally:
        conn.close()


def test_saga_constraints_reject_invalid_state_range_and_duplicate_source():
    conn = db.connect()
    stamp = db.now()
    suffix = db.new_id()
    try:
        _episode(conn, f"episode-a-{suffix}", stamp)
        _episode(conn, f"episode-b-{suffix}", stamp + 86400)
        _saga(conn, f"saga-a-{suffix}", stamp)
        _saga(conn, f"saga-b-{suffix}", stamp)
        conn.execute(
            "INSERT INTO memory_saga_episodes(saga_id,episode_id,position,role,added_at)"
            " VALUES(?,?,?,?,?)",
            (f"saga-a-{suffix}", f"episode-a-{suffix}", 0, "anchor", stamp),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO memory_saga_episodes(saga_id,episode_id,position,added_at)"
                " VALUES(?,?,?,?)",
                (f"saga-b-{suffix}", f"episode-a-{suffix}", 0, stamp),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE memory_sagas SET status='paused' WHERE id=?",
                (f"saga-a-{suffix}",),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE memory_sagas SET end_at=start_at-1 WHERE id=?",
                (f"saga-a-{suffix}",),
            )
        conn.rollback()
    finally:
        conn.close()


def test_saga_source_links_and_events_preserve_auditable_order():
    conn = db.connect()
    stamp = db.now()
    suffix = db.new_id()
    saga_id = f"saga-{suffix}"
    episode_ids = [f"episode-a-{suffix}", f"episode-b-{suffix}"]
    try:
        for position, episode_id in enumerate(episode_ids):
            _episode(conn, episode_id, stamp + position * 86400)
        _saga(conn, saga_id, stamp)
        for position, episode_id in enumerate(episode_ids):
            conn.execute(
                "INSERT INTO memory_saga_episodes(saga_id,episode_id,position,role,added_at)"
                " VALUES(?,?,?,?,?)",
                (saga_id, episode_id, position,
                 "anchor" if position == 0 else "development", stamp + position),
            )
        conn.execute(
            "INSERT INTO memory_saga_events("
            "id,saga_id,action,after_json,reason_code,source,created_at"
            ") VALUES(?,?,?,?,?,?,?)",
            (db.new_id(), saga_id, "created", json.dumps({"episode_ids": episode_ids}),
             "qualified_group", "consolidator", stamp),
        )
        ordered = conn.execute(
            "SELECT episode_id,role FROM memory_saga_episodes"
            " WHERE saga_id=? AND removed_at IS NULL ORDER BY position",
            (saga_id,),
        ).fetchall()
        assert [row["episode_id"] for row in ordered] == episode_ids
        event = conn.execute(
            "SELECT action,reason_code,source FROM memory_saga_events WHERE saga_id=?",
            (saga_id,),
        ).fetchone()
        assert dict(event) == {
            "action": "created", "reason_code": "qualified_group", "source": "consolidator",
        }
        conn.rollback()
    finally:
        conn.close()


def test_saga_candidate_schema_rejects_invalid_status_score_and_fingerprint_reuse():
    conn = db.connect()
    stamp = db.now()
    values = (
        "candidate-a", "fingerprint-a", "observing", '["episode-a","episode-b"]',
        "[]", 0.5, 0.5, 0.5, 0.5, 0.5, "saga-group-v1", stamp, stamp, stamp + 1,
    )
    statement = (
        "INSERT INTO saga_group_candidates("
        "id,grouping_fingerprint,status,episode_ids_json,shared_entity_ids_json,"
        "entity_score,text_score,time_score,coherence_score,total_score,policy_version,"
        "first_seen_at,last_evaluated_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    )
    try:
        columns = {
            row["name"] for row in conn.execute(
                "PRAGMA table_info(saga_group_candidates)"
            ).fetchall()
        }
        assert {
            "episode_ids_json", "shared_entity_ids_json", "entity_score", "text_score",
            "time_score", "coherence_score", "total_score", "score_details_json",
            "policy_version", "conflict_reason", "evaluation_count", "expires_at",
            "title", "summary", "theme", "current_stage", "lifecycle_signal",
            "summary_status", "summary_protocol_version", "summary_provider_id",
            "summary_model", "summary_evidence_episode_ids_json",
            "completion_evidence_episode_ids_json", "summary_warnings_json",
            "summary_error_code", "summary_source_hash", "summary_prompt_tokens",
            "summary_completion_tokens", "summary_repair_attempted",
            "application_mode", "target_saga_id", "application_attempt_count",
            "application_error_code", "last_application_at",
        } <= columns
        conn.execute(statement, values)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO saga_candidate_summary_events("
                "id,candidate_id,action,created_at) VALUES('bad-event','candidate-a','raw_saved',?)",
                (stamp,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(statement, ("candidate-b", *values[1:]))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(statement, (
                "candidate-c", "fingerprint-c", "invalid", *values[3:]
            ))
        invalid_score = list(values)
        invalid_score[0:2] = ["candidate-d", "fingerprint-d"]
        invalid_score[5] = 1.1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(statement, invalid_score)
        conn.rollback()
    finally:
        conn.close()
