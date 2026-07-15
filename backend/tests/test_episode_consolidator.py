import pytest

from app import db, episode_consolidator
from app.main import app
from fastapi.testclient import TestClient


client = TestClient(
    app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"}
)


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
