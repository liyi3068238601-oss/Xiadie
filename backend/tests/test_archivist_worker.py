"""Archivist E.4 worker、懒调度、预算、恢复和审计 API。"""
import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import archivist, archivist_worker, db, main, memory

db.init_db()
client = TestClient(
    main.app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"}
)


@pytest.fixture(autouse=True)
def clean_worker_objects():
    conn = db.connect()
    try:
        old_last = db.get_setting("last_archivist_run", "")
        existing_enabled = {
            row["id"]: row["enabled"] for row in conn.execute(
                "SELECT id,enabled FROM memory_fragments"
            )
        }
        conn.execute("UPDATE memory_fragments SET enabled=0")
        conn.execute("DELETE FROM archivist_runs")
        conn.commit()
    finally:
        conn.close()
    yield
    conn = db.connect()
    try:
        conn.execute("DELETE FROM archivist_runs")
        for fragment_id, enabled in existing_enabled.items():
            conn.execute(
                "UPDATE memory_fragments SET enabled=? WHERE id=?", (enabled, fragment_id)
            )
        new_ids = [
            row["id"] for row in conn.execute("SELECT id FROM memory_fragments")
            if row["id"] not in existing_enabled
        ]
        for fragment_id in new_ids:
            conn.execute("DELETE FROM memory_recall_events WHERE fragment_id=?", (fragment_id,))
            conn.execute("DELETE FROM memory_lifecycle_events WHERE fragment_id=?", (fragment_id,))
            conn.execute("DELETE FROM memory_fragments WHERE id=?", (fragment_id,))
        conn.commit()
    finally:
        conn.close()
    db.set_setting("last_archivist_run", old_last)


def _old_fragment(label: str, *, age_days: int = 200) -> dict:
    item = memory.create_memory("L1", f"Archivist worker {label} {db.new_id()}")
    old = db.now() - age_days * 86_400
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET created_at=?,updated_at=?,importance=0.05,"
            "confidence=0.05,scope='world',kind='observation',enabled=1 WHERE id=?",
            (old, old, item["id"]),
        )
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM memory_fragments WHERE id=?", (item["id"],)
        ).fetchone())
    finally:
        conn.close()


def test_schema_25_adds_bounded_run_and_body_free_event_ledgers():
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()["value"] == "55"
        tables = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name IN ('archivist_runs','archivist_run_events')"
            )
        }
        assert tables == {"archivist_runs", "archivist_run_events"}
        event_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(archivist_run_events)")
        }
        assert not ({"content", "summary", "tags", "raw_output"} & event_columns)
        fragment_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(memory_fragments)")
        }
        assert "last_archivist_evaluated_at" in fragment_columns
        with pytest.raises(sqlite3.IntegrityError):
            now = db.now()
            conn.execute(
                "INSERT INTO archivist_runs("
                "id,idempotency_key,trigger,status,policy_version,max_attempts,scan_budget,"
                "transition_budget,runtime_budget_ms,created_at,updated_at)"
                " VALUES(?,?,'manual','queued','test',3,201,1,1000,?,?)",
                (db.new_id(), db.new_id(), now, now),
            )
    finally:
        conn.close()


def test_schema_25_migrates_a_schema_24_database_without_fragment_changes():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.executescript(
            "CREATE TABLE memory_fragments("
            "id TEXT PRIMARY KEY,content TEXT NOT NULL,status TEXT NOT NULL,enabled INTEGER NOT NULL);"
            "INSERT INTO memory_fragments VALUES('legacy','旧记忆保持不变','active',1);"
        )
        migration = next(sql for version, sql in db.MIGRATIONS if version == 25)
        conn.executescript(migration)
        assert conn.execute(
            "SELECT content FROM memory_fragments WHERE id='legacy'"
        ).fetchone()["content"] == "旧记忆保持不变"
        tables = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'archivist_%'"
            )
        }
        assert tables == {"archivist_runs", "archivist_run_events"}
    finally:
        conn.close()


def test_startup_and_idle_schedule_only_once_per_twenty_hour_window():
    db.set_setting("last_archivist_run", "0")
    first = archivist_worker.enqueue_if_due(now=2_000_000_000, trigger="startup")
    repeated = archivist_worker.enqueue_if_due(now=2_000_000_001, trigger="startup")
    assert first and repeated and first["id"] == repeated["id"]
    db.set_setting("last_archivist_run", "2000000000")
    assert archivist_worker.enqueue_if_due(
        now=2_000_000_000 + archivist_worker.MAINTENANCE_INTERVAL_SECONDS - 1,
        trigger="idle",
    ) is None
    next_run = archivist_worker.enqueue_if_due(
        now=2_000_000_000 + archivist_worker.MAINTENANCE_INTERVAL_SECONDS,
        trigger="idle",
    )
    assert next_run and next_run["trigger"] == "idle"


def test_worker_processes_oldest_first_and_stops_at_transition_budget():
    oldest = _old_fragment("oldest", age_days=300)
    newer = _old_fragment("newer", age_days=200)
    run = archivist_worker.enqueue(
        trigger="manual", request_key="budget", scan_budget=2, transition_budget=1,
    )
    assert asyncio.run(archivist_worker.process_due()) == 1
    finished = archivist_worker.get_run(run["id"])
    assert finished["status"] == "completed"
    assert finished["scanned_count"] == 1 and finished["transitioned_count"] == 1
    assert finished["events"][-1]["reason_code"] == "transition_budget_reached"
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT status FROM memory_fragments WHERE id=?", (oldest["id"],)
        ).fetchone()["status"] == "cooling"
        assert conn.execute(
            "SELECT status FROM memory_fragments WHERE id=?", (newer["id"],)
        ).fetchone()["status"] == "active"
    finally:
        conn.close()


def test_scan_budget_rotates_past_previously_evaluated_protected_fragments():
    first = _old_fragment("protected-first", age_days=300)
    second = _old_fragment("protected-second", age_days=200)
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET layer='L0' WHERE id IN (?,?)",
            (first["id"], second["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    one = archivist_worker.enqueue(
        trigger="manual", request_key="rotate-1", scan_budget=1, transition_budget=1,
    )
    assert asyncio.run(archivist_worker.process_due()) == 1
    assert archivist_worker.get_run(one["id"])["scanned_count"] == 1
    two = archivist_worker.enqueue(
        trigger="manual", request_key="rotate-2", scan_budget=1, transition_budget=1,
    )
    assert asyncio.run(archivist_worker.process_due()) == 1
    assert archivist_worker.get_run(two["id"])["scanned_count"] == 1
    conn = db.connect()
    try:
        stamps = {
            row["id"]: row["last_archivist_evaluated_at"] for row in conn.execute(
                "SELECT id,last_archivist_evaluated_at FROM memory_fragments WHERE id IN (?,?)",
                (first["id"], second["id"]),
            )
        }
        assert stamps[first["id"]] is not None and stamps[second["id"]] is not None
    finally:
        conn.close()


def test_zero_transition_budget_and_runtime_budget_end_without_model_calls(monkeypatch):
    _old_fragment("budget-zero")
    run = archivist_worker.enqueue(
        trigger="manual", request_key="zero", transition_budget=0, model_call_budget=0,
    )
    assert asyncio.run(archivist_worker.process_due()) == 1
    finished = archivist_worker.get_run(run["id"])
    assert finished["status"] == "completed" and finished["scanned_count"] == 0
    assert finished["model_calls_used"] == 0
    assert finished["events"][-1]["reason_code"] == "transition_budget_reached"

    second = archivist_worker.enqueue(
        trigger="manual", request_key="runtime", runtime_budget_ms=100,
    )
    monkeypatch.setattr(archivist_worker, "_runtime_exhausted", lambda *_args: True)
    assert asyncio.run(archivist_worker.process_due()) == 1
    assert archivist_worker.get_run(second["id"])["events"][-1]["reason_code"] == (
        "runtime_budget_reached"
    )


def test_failure_retry_stale_recovery_cancel_and_graceful_interruption(monkeypatch):
    _old_fragment("failure")
    run = archivist_worker.enqueue(trigger="manual", request_key="failure")
    monkeypatch.setattr(
        archivist, "assess_and_transition",
        lambda _fragment_id: (_ for _ in ()).throw(RuntimeError("synthetic")),
    )
    assert asyncio.run(archivist_worker.process_due()) == 1
    failed = archivist_worker.get_run(run["id"])
    assert failed["status"] == "recovery_pending" and failed["attempt_count"] == 1

    conn = db.connect()
    try:
        conn.execute(
            "UPDATE archivist_runs SET status='running',updated_at=? WHERE id=?",
            (db.now() - archivist_worker.RUNNING_STALE_SECONDS - 1, run["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    assert archivist_worker.cancel(run["id"])["status"] == "cancel_requested"
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE archivist_runs SET updated_at=? WHERE id=?",
            (db.now() - archivist_worker.RUNNING_STALE_SECONDS - 1, run["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    assert archivist_worker.recover_stale_runs() == 1
    assert archivist_worker.get_run(run["id"])["status"] == "cancelled"

    interrupted = archivist_worker.enqueue(trigger="manual", request_key="shutdown")
    claimed = archivist_worker._claim_next()
    assert claimed and claimed["id"] == interrupted["id"]
    archivist_worker._mark_interrupted(claimed)
    recovered = archivist_worker.get_run(interrupted["id"])
    assert recovered["status"] == "recovery_pending"
    assert recovered["error_code"] == "worker_stopped"


def test_failure_exhausts_exactly_after_three_attempts(monkeypatch):
    _old_fragment("exhaustion")
    run = archivist_worker.enqueue(trigger="manual", request_key="exhaustion")
    monkeypatch.setattr(
        archivist, "assess_and_transition",
        lambda _fragment_id: (_ for _ in ()).throw(RuntimeError("synthetic")),
    )
    for expected_attempt in range(1, archivist_worker.MAX_ATTEMPTS + 1):
        if expected_attempt > 1:
            conn = db.connect()
            try:
                conn.execute(
                    "UPDATE archivist_runs SET next_attempt_at=0 WHERE id=?", (run["id"],)
                )
                conn.commit()
            finally:
                conn.close()
        assert asyncio.run(archivist_worker.process_due()) == 1
        current = archivist_worker.get_run(run["id"])
        assert current["attempt_count"] == expected_attempt
    assert current["status"] == "exhausted" and current["next_attempt_at"] is None
    assert [event["action"] for event in current["events"]].count("claimed") == 3


def test_archivist_run_audit_api_is_idempotent_and_cancellable():
    body = {
        "trigger": "manual", "request_key": "api-idempotent",
        "scan_budget": 7, "transition_budget": 2, "runtime_budget_ms": 500,
    }
    first = client.post("/api/archivist/runs", json=body)
    second = client.post("/api/archivist/runs", json=body)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    run_id = first.json()["id"]
    assert client.get(f"/api/archivist/runs/{run_id}").status_code == 200
    assert client.get("/api/archivist/runs").json()[0]["id"] == run_id
    cancelled = client.post(f"/api/archivist/runs/{run_id}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert client.get("/api/archivist/runs/missing").status_code == 404
