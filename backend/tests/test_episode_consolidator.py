import pytest
import asyncio

from app import db, episode_consolidator, memory
from app.main import app
from fastapi.testclient import TestClient


client = TestClient(
    app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"}
)


@pytest.fixture(autouse=True)
def clean_consolidator_runs():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM episode_consolidator_runs")
        conn.commit()
    finally:
        conn.close()
    yield
    conn = db.connect()
    try:
        conn.execute("DELETE FROM episode_consolidator_runs")
        conn.commit()
    finally:
        conn.close()


def test_enqueue_is_idempotent_and_audited():
    first = client.post(
        "/api/episode-consolidator/runs",
        json={"trigger": "idle", "request_key": "same-idle-window"},
    )
    second = client.post(
        "/api/episode-consolidator/runs",
        json={"trigger": "idle", "request_key": "same-idle-window"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["status"] == "queued"
    assert first.json()["input_fragment_ids"] == []
    assert [event["action"] for event in second.json()["events"]] == ["enqueued"]


def test_queued_run_can_be_cancelled_once_and_remains_auditable():
    run = client.post(
        "/api/episode-consolidator/runs",
        json={"trigger": "manual", "request_key": "cancel-queued"},
    ).json()
    cancelled = client.post(
        f"/api/episode-consolidator/runs/{run['id']}/cancel"
    ).json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["finished_at"] is not None
    assert [event["after_status"] for event in cancelled["events"]] == [
        "queued", "cancelled",
    ]
    repeated = client.post(
        f"/api/episode-consolidator/runs/{run['id']}/cancel"
    ).json()
    assert len(repeated["events"]) == 2


def test_running_run_uses_cooperative_cancel_and_terminal_is_protected():
    run = episode_consolidator.enqueue(trigger="startup", request_key="running-cancel")
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE episode_consolidator_runs SET status='running',started_at=?,updated_at=?"
            " WHERE id=?",
            (db.now(), db.now(), run["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    requested = episode_consolidator.cancel(run["id"])
    assert requested["status"] == "cancel_requested"
    assert requested["finished_at"] is None
    assert requested["events"][-1]["action"] == "cancel_requested"

    terminal = episode_consolidator.enqueue(trigger="fragment", request_key="terminal-run")
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE episode_consolidator_runs SET status='applied',finished_at=?,updated_at=?"
            " WHERE id=?",
            (db.now(), db.now(), terminal["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ValueError, match="已结束"):
        episode_consolidator.cancel(terminal["id"])


def test_run_api_validates_trigger_and_missing_ids():
    assert client.post(
        "/api/episode-consolidator/runs", json={"trigger": "timer"}
    ).status_code == 400
    assert client.get("/api/episode-consolidator/runs/missing").status_code == 404
    assert client.post(
        "/api/episode-consolidator/runs/missing/cancel"
    ).status_code == 404


def test_worker_claims_and_audits_empty_and_created_passes(monkeypatch):
    empty = episode_consolidator.enqueue(trigger="manual", request_key="empty-pass")
    monkeypatch.setattr(episode_consolidator.episodes, "generate_candidates", lambda: [])
    assert asyncio.run(episode_consolidator.process_due(limit=1)) == 1
    finished = episode_consolidator.get_run(empty["id"])
    assert finished["status"] == "skipped"
    assert finished["attempt_count"] == 1
    assert [event["action"] for event in finished["events"]] == [
        "enqueued", "claimed", "processed",
    ]

    created = episode_consolidator.enqueue(trigger="manual", request_key="created-pass")
    monkeypatch.setattr(
        episode_consolidator.episodes, "generate_candidates", lambda: [{"id": "legacy-group"}]
    )
    assert asyncio.run(episode_consolidator.process_due(limit=1)) == 1
    finished = episode_consolidator.get_run(created["id"])
    assert finished["status"] == "applied"
    assert finished["group_count"] == 1
    assert finished["events"][-1]["reason_code"] == "legacy_candidates_created"


def test_worker_retries_then_exhausts_without_touching_fragments(monkeypatch):
    run = episode_consolidator.enqueue(trigger="manual", request_key="failing-pass")
    before_count = len(memory.list_memories())

    def fail():
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(episode_consolidator.episodes, "generate_candidates", fail)
    assert asyncio.run(episode_consolidator.process_due(limit=1)) == 1
    retry = episode_consolidator.get_run(run["id"])
    assert retry["status"] == "recovery_pending"
    assert retry["next_attempt_at"] is not None

    conn = db.connect()
    try:
        conn.execute(
            "UPDATE episode_consolidator_runs SET attempt_count=max_attempts-1,next_attempt_at=0"
            " WHERE id=?",
            (run["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    assert asyncio.run(episode_consolidator.process_due(limit=1)) == 1
    exhausted = episode_consolidator.get_run(run["id"])
    assert exhausted["status"] == "exhausted"
    assert exhausted["finished_at"] is not None
    assert len(memory.list_memories()) == before_count


def test_stale_recovery_and_recovery_pending_cancel_are_audited():
    run = episode_consolidator.enqueue(trigger="startup", request_key="stale-run")
    conn = db.connect()
    try:
        old = db.now() - episode_consolidator.RUNNING_STALE_SECONDS - 1
        conn.execute(
            "UPDATE episode_consolidator_runs SET status='running',attempt_count=1,updated_at=?"
            " WHERE id=?",
            (old, run["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    assert episode_consolidator.recover_stale_runs() == 1
    recovered = episode_consolidator.get_run(run["id"])
    assert recovered["status"] == "recovery_pending"
    assert recovered["events"][-1]["action"] == "recovery_scheduled"
    cancelled = episode_consolidator.cancel(run["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["events"][-1]["before_status"] == "recovery_pending"


def test_stale_cancel_request_finishes_cancelled():
    run = episode_consolidator.enqueue(trigger="manual", request_key="stale-cancel")
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE episode_consolidator_runs SET status='running',attempt_count=1 WHERE id=?",
            (run["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    assert episode_consolidator.cancel(run["id"])["status"] == "cancel_requested"
    conn = db.connect()
    try:
        old = db.now() - episode_consolidator.RUNNING_STALE_SECONDS - 1
        conn.execute(
            "UPDATE episode_consolidator_runs SET updated_at=? WHERE id=?", (old, run["id"])
        )
        conn.commit()
    finally:
        conn.close()
    assert episode_consolidator.recover_stale_runs() == 1
    assert episode_consolidator.get_run(run["id"])["status"] == "cancelled"


def test_fragment_and_idle_triggers_have_stable_idempotency_keys():
    fragment = episode_consolidator.enqueue_for_fragments(["b", "a", "a"])
    repeated = episode_consolidator.enqueue_for_fragments(["a", "b"])
    assert fragment["id"] == repeated["id"]
    assert fragment["input_fragment_ids"] == ["a", "b"]
    assert fragment["events"][0]["metadata"]["fragment_count"] == 2

    first_idle = episode_consolidator.enqueue_idle(now=900)
    second_idle = episode_consolidator.enqueue_idle(now=901)
    assert first_idle["id"] == second_idle["id"]
    assert first_idle["trigger"] == "idle"


def test_fragment_write_only_enqueues_after_commit():
    item = memory.create_memory("L1", "C2 热路径只保存 Fragment，不同步整理 Episode")
    runs = episode_consolidator.list_runs(limit=20)
    queued = next(run for run in runs if item["id"] in run["input_fragment_ids"])
    assert queued["trigger"] == "fragment"
    assert queued["status"] == "queued"
    assert all(
        item["id"] not in {fragment["id"] for fragment in candidate["fragments"]}
        for candidate in episode_consolidator.episodes.list_candidates()
    )


def test_start_worker_enqueues_startup_and_stops_cleanly(monkeypatch):
    async def no_work(*, limit=3):
        return 0

    monkeypatch.setattr(episode_consolidator, "process_due", no_work)

    async def scenario():
        await episode_consolidator.start_worker()
        await asyncio.sleep(0)
        await episode_consolidator.stop_worker()

    asyncio.run(scenario())
    assert any(run["trigger"] == "startup" for run in episode_consolidator.list_runs())
