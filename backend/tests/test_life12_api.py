from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from app import db, diary, life_events
from app.life_events import SourceRef
from app.main import app

client = TestClient(app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"})


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture(autouse=True)
def reset_mode():
    db.set_setting("life_continuity_mode", "continuous_simulated")
    yield
    db.set_setting("life_continuity_mode", "continuous_simulated")


def _event() -> dict:
    item, _ = life_events.create_event(
        event_kind="activity", world_layer="simulated", summary="读完了一章",
        source_refs=(SourceRef("user_statement", db.new_id(), "1", _sha("event")),),
        idempotency_key="life12:" + db.new_id(),
    )
    return item


def test_life_settings_default_on_and_support_pause_disable():
    assert client.get("/api/life/settings").json()["mode"] == "continuous_simulated"
    assert client.patch("/api/life/settings", json={"mode": "paused"}).json()["mode"] == "paused"
    assert client.patch("/api/life/settings", json={"mode": "disabled"}).json()["mode"] == "disabled"
    assert client.patch("/api/life/settings", json={"mode": "unknown"}).status_code == 400


def test_goal_create_edit_pause_and_delete_are_revision_guarded():
    created = client.post("/api/life/goals", json={"title": "慢慢读完一本书", "priority": 3})
    assert created.status_code == 200
    goal = created.json()
    assert goal["status"] == "active"
    edited = client.patch(f"/api/life/goals/{goal['id']}", json={
        "expected_revision": goal["revision"], "title": "读完这本书", "status": "paused",
    })
    assert edited.status_code == 200
    goal = edited.json()
    assert goal["title"] == "读完这本书" and goal["status"] == "paused"
    stale = client.delete(f"/api/life/goals/{goal['id']}?expected_revision=1")
    assert stale.status_code == 409
    deleted = client.delete(
        f"/api/life/goals/{goal['id']}?expected_revision={goal['revision']}"
    )
    assert deleted.json()["status"] == "revoked"


def test_important_date_create_edit_and_delete():
    created = client.post("/api/life/dates", json={
        "label": "第一次见面的日子", "recurrence": "yearly_solar",
        "date_month": 7, "date_day": 26, "timezone_id": "Asia/Shanghai",
        "celebration_policy": "natural",
    })
    assert created.status_code == 200
    item = created.json()
    assert item["status"] == "active"
    edited = client.patch(f"/api/life/dates/{item['id']}", json={
        "expected_revision": item["revision"], "label": "共同纪念日",
        "celebration_policy": "day_only",
    }).json()
    assert edited["label"] == "共同纪念日" and edited["celebration_policy"] == "day_only"
    deleted = client.delete(
        f"/api/life/dates/{item['id']}?expected_revision={edited['revision']}"
    ).json()
    assert deleted["status"] == "revoked"


def test_diary_edit_delete_export_and_body_free_diagnostics():
    event = _event()
    item = diary.create_entry(
        entry_date="2026-07-26", title="今天", body="一段只应出现在日记与显式导出中的正文",
        source_kind="life_event", source_id=event["id"], source_revision=str(event["revision"]),
        source_hash=_sha("diary"), share_policy="private",
    )
    listed = client.get("/api/life/diary").json()["items"]
    assert any(row["id"] == item["id"] for row in listed)
    edited = client.patch(f"/api/life/diary/{item['id']}", json={
        "expected_revision": item["revision"], "title": "新的标题", "body": "新的私密正文",
    }).json()
    diagnostics_text = client.get("/api/life/diagnostics").text
    assert "新的私密正文" not in diagnostics_text and "新的标题" not in diagnostics_text
    exported = client.get("/api/life/export").json()
    assert any(row["body"] == "新的私密正文" for row in exported["diary"])
    deleted = client.delete(
        f"/api/life/diary/{item['id']}?expected_revision={edited['revision']}"
    ).json()
    assert deleted["status"] == "revoked"


def test_rebuild_and_diagnostics_expose_versions_not_internal_reasons():
    rebuilt = client.post("/api/life/rebuild")
    assert rebuilt.status_code == 200
    diagnostics = client.get("/api/life/diagnostics").json()
    assert diagnostics["schema_version"] == "71"
    assert "state_algorithm" in diagnostics and "counts" in diagnostics
    assert all(set(item) == {"source_type", "source_id", "source_revision", "source_status"}
               for item in diagnostics["sources"])
