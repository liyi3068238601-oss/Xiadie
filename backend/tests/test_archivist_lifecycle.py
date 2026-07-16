"""Archivist E.3 Fragment 精确状态转换、恢复、审计与 FTS 同步。"""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import archivist, db, memory
from app.main import app

db.init_db()
client = TestClient(
    app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"}
)


@pytest.fixture
def fragment():
    item = memory.create_memory("L1", f"lifecyclemarker{db.new_id()}")
    yield item
    conn = db.connect()
    try:
        conn.execute("DELETE FROM memory_recall_events WHERE fragment_id=?", (item["id"],))
        conn.execute("DELETE FROM memory_lifecycle_events WHERE fragment_id=?", (item["id"],))
        conn.execute("DELETE FROM memory_fragments WHERE id=?", (item["id"],))
        conn.commit()
    finally:
        conn.close()


def _set_low_value(fragment_id: str, *, created: float, status: str = "active",
                   cooling_since: float | None = None, updated: float | None = None) -> None:
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET status=?,importance=0,confidence=0,scope='world',"
            "kind='observation',recall_count=0,last_recalled_at=NULL,created_at=?,updated_at=?,"
            "cooling_since=?,frozen_at=NULL,lifecycle_revision=0 WHERE id=?",
            (status, created, updated if updated is not None else created,
             cooling_since, fragment_id),
        )
        conn.commit()
    finally:
        conn.close()


def _row(fragment_id: str) -> dict:
    conn = db.connect()
    try:
        return dict(conn.execute(
            "SELECT * FROM memory_fragments WHERE id=?", (fragment_id,)
        ).fetchone())
    finally:
        conn.close()


def _fts_hits(text: str) -> int:
    conn = db.connect()
    try:
        return conn.execute(
            "SELECT COUNT(*) count FROM memory_fragments_fts"
            " WHERE memory_fragments_fts MATCH ?", (f'"{text}"',),
        ).fetchone()["count"]
    finally:
        conn.close()


def test_active_to_cooling_requires_both_fourteen_days_and_low_score(fragment):
    now = 2_100_000_000.0
    _set_low_value(fragment["id"], created=now - 13 * 86_400)
    early = archivist.assess_and_transition(fragment["id"], now=now)
    assert early["changed"] is False and early["reason_code"] == "cooling_minimum_age"
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET created_at=?,updated_at=? WHERE id=?",
            (now - 14 * 86_400, now - 14 * 86_400, fragment["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    changed = archivist.assess_and_transition(fragment["id"], now=now)
    assert changed["changed"] is True
    assert changed["fragment"]["status"] == "cooling"
    assert changed["fragment"]["cooling_since"] == now
    assert changed["fragment"]["lifecycle_revision"] == 1
    event = archivist.list_lifecycle_events(fragment["id"])[0]
    assert (event["from_status"], event["to_status"]) == ("active", "cooling")
    assert event["reason_code"] == "retention_below_cooling"
    assert event["score_components"]["importance"] == 0


def test_high_retention_and_ninety_day_stable_boundary_remain_active(fragment):
    now = 2_105_000_000.0
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET created_at=?,updated_at=?,importance=1,confidence=1,"
            "scope='relationship',kind='preference' WHERE id=?",
            (now - 90 * 86_400, now - 90 * 86_400, fragment["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    result = archivist.assess_and_transition(fragment["id"], now=now)
    assert result["changed"] is False
    assert result["fragment"]["status"] == "active"
    assert "stable_boundary" in result["protection_reasons"]


def test_active_episode_source_blocks_cooling_and_saga_anchor_is_explicit(fragment):
    now = 2_110_000_000.0
    _set_low_value(fragment["id"], created=now - 200 * 86_400)
    episode_id, saga_id = db.new_id(), db.new_id()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO memory_episodes("
            "id,title,summary,start_at,end_at,status,source,created_at,updated_at)"
            " VALUES(?,?,?,?,?,'active','automatic',?,?)",
            (episode_id, "重要经历", "仍在进行的重要经历", now - 100, now, now, now),
        )
        conn.execute("INSERT INTO memory_episode_fragments VALUES(?,?,0,?)",
                     (episode_id, fragment["id"], now))
        conn.commit()
        blocked = archivist.assess_and_transition(fragment["id"], now=now)
        assert blocked["reason_code"] == "active_episode_source"
        conn.execute(
            "INSERT INTO memory_sagas("
            "id,title,summary,theme,current_stage,start_at,end_at,status,source,"
            "source_episode_ids_json,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,'active','automatic',?,?,?)",
            (saga_id, "长期故事", "仍在继续", "重要经历", "继续", now - 100, now,
             json.dumps([episode_id]), now, now),
        )
        conn.execute(
            "INSERT INTO memory_saga_episodes VALUES(?,?,0,'anchor',?,NULL)",
            (saga_id, episode_id, now),
        )
        conn.commit()
        protected = archivist.assess_and_transition(fragment["id"], now=now)
        assert protected["reason_code"] == "protected_fragment"
        assert "active_saga_anchor" in protected["protection_reasons"]
    finally:
        conn.execute("DELETE FROM memory_sagas WHERE id=?", (saga_id,))
        conn.execute("DELETE FROM memory_episodes WHERE id=?", (episode_id,))
        conn.commit()
        conn.close()


def test_cooling_to_frozen_requires_extra_thirty_days_and_removes_fts(fragment):
    now = 2_120_000_000.0
    content = fragment["content"]
    _set_low_value(
        fragment["id"], created=now - 200 * 86_400, status="cooling",
        cooling_since=now - 29 * 86_400, updated=now - 29 * 86_400,
    )
    assert _fts_hits(content) == 1
    early = archivist.assess_and_transition(fragment["id"], now=now)
    assert early["reason_code"] == "frozen_minimum_age"
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET cooling_since=?,updated_at=? WHERE id=?",
            (now - 30 * 86_400, now - 30 * 86_400, fragment["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    frozen = archivist.assess_and_transition(fragment["id"], now=now)
    assert frozen["fragment"]["status"] == "frozen"
    assert frozen["fragment"]["frozen_at"] == now
    assert _fts_hits(content) == 0


def test_modified_during_cooling_and_active_episode_block_freezing(fragment):
    now = 2_130_000_000.0
    cooling = now - 31 * 86_400
    _set_low_value(
        fragment["id"], created=now - 200 * 86_400, status="cooling",
        cooling_since=cooling, updated=cooling + 1,
    )
    assert archivist.assess_and_transition(fragment["id"], now=now)[
        "reason_code"
    ] == "modified_during_cooling"
    conn = db.connect()
    episode_id = db.new_id()
    try:
        conn.execute("UPDATE memory_fragments SET updated_at=? WHERE id=?",
                     (cooling, fragment["id"]))
        conn.execute(
            "INSERT INTO memory_episodes("
            "id,title,summary,start_at,end_at,status,source,created_at,updated_at)"
            " VALUES(?,?,?,?,?,'active','automatic',?,?)",
            (episode_id, "新证据", "冷却期间出现新证据", cooling, now, now, now),
        )
        conn.execute("INSERT INTO memory_episode_fragments VALUES(?,?,0,?)",
                     (episode_id, fragment["id"], now))
        conn.commit()
        assert archivist.assess_and_transition(fragment["id"], now=now)[
            "reason_code"
        ] == "active_episode_source"
    finally:
        conn.execute("DELETE FROM memory_episodes WHERE id=?", (episode_id,))
        conn.commit()
        conn.close()


def test_user_and_new_evidence_recovery_clear_times_and_write_distinct_events(fragment):
    now = 2_140_000_000.0
    _set_low_value(
        fragment["id"], created=now - 200 * 86_400, status="cooling",
        cooling_since=now - 40 * 86_400, updated=now - 40 * 86_400,
    )
    restored = archivist.reactivate_fragment(
        fragment["id"], trigger="new_evidence", now=now,
    )
    assert restored["status"] == "active" and restored["cooling_since"] is None
    assert archivist.list_lifecycle_events(fragment["id"])[-1][
        "reason_code"
    ] == "reactivated_by_new_evidence"
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET status='cooling',cooling_since=?,updated_at=?"
            " WHERE id=?", (now + 1, now + 1, fragment["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    restored = archivist.reactivate_fragment(
        fragment["id"], trigger="user", reason="我仍需要它",
        expected_revision=1, now=now + 2,
    )
    assert restored["lifecycle_revision"] == 2
    event = archivist.list_lifecycle_events(fragment["id"])[-1]
    assert event["reason_code"] == "reactivated_by_user"
    assert event["score_components"]["metadata"]["reason"] == "我仍需要它"


def test_strong_actual_recall_restores_frozen_index_and_counts_once(fragment):
    now = db.now()
    content = fragment["content"]
    _set_low_value(
        fragment["id"], created=now - 300 * 86_400, status="cooling",
        cooling_since=now - 40 * 86_400, updated=now - 40 * 86_400,
    )
    frozen = archivist.assess_and_transition(fragment["id"], now=now)
    assert frozen["fragment"]["status"] == "frozen" and _fts_hits(content) == 0
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET importance=0.9,confidence=1 WHERE id=?",
            (fragment["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    candidates = archivist.find_reactivation_candidates(content, now=now + 1)
    candidate = next(item for item in candidates if item["id"] == fragment["id"])
    recorded = archivist.record_injected_memories(
        [candidate], context_key="chat:restore:one", source_session_id=None,
        injected_at=now + 1,
    )
    assert recorded == [fragment["id"]]
    state = _row(fragment["id"])
    assert state["status"] == "active" and state["recall_count"] == 1
    assert _fts_hits(content) == 1
    assert archivist.list_lifecycle_events(fragment["id"])[-1][
        "reason_code"
    ] == "reactivated_by_recall"


def test_lifecycle_transaction_rolls_back_if_event_revision_conflicts(fragment):
    now = 2_150_000_000.0
    _set_low_value(fragment["id"], created=now - 200 * 86_400)
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO memory_lifecycle_events("
            "id,fragment_id,revision,from_status,to_status,retention_score,reason_code,"
            "source,policy_version,created_at) VALUES(?,?,1,'active','cooling',0.1,"
            "'synthetic','test','fragment-retention-v1',?)",
            (db.new_id(), fragment["id"], now - 1),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(sqlite3.IntegrityError):
        archivist.assess_and_transition(fragment["id"], now=now)
    state = _row(fragment["id"])
    assert state["status"] == "active" and state["lifecycle_revision"] == 0


def test_tombstone_is_terminal_and_automatic_assessment_never_changes_it(fragment):
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET status='tombstone',enabled=0 WHERE id=?",
            (fragment["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    result = archivist.assess_and_transition(fragment["id"], now=db.now() + 999 * 86_400)
    assert result["changed"] is False and result["fragment"]["status"] == "tombstone"
    with pytest.raises(archivist.ArchivistLifecycleError, match="不可恢复"):
        archivist.reactivate_fragment(fragment["id"], trigger="user")


def test_user_delete_atomically_removes_fts_and_writes_tombstone_event(fragment):
    content = fragment["content"]
    assert _fts_hits(content) == 1
    assert memory.delete_memory(fragment["id"])
    state = _row(fragment["id"])
    assert state["status"] == "tombstone" and state["fts_indexed"] == 0
    assert _fts_hits(content) == 0
    event = archivist.list_lifecycle_events(fragment["id"])[-1]
    assert event["to_status"] == "tombstone"
    assert event["reason_code"] == "deleted_by_user" and event["source"] == "user"


def test_privacy_clear_removes_body_sources_and_old_audit_content(fragment):
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET tags='private-tag',inner_reason='private reason',"
            "evidence_message_ids='[\"message-secret\"]' WHERE id=?", (fragment["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    response = client.delete(f"/api/memories/{fragment['id']}?privacy=true")
    assert response.status_code == 200 and response.json()["privacy_cleared"] is True
    state = _row(fragment["id"])
    assert state["status"] == "tombstone" and state["content"] == "" and state["tags"] == ""
    assert state["inner_reason"] == "" and state["evidence_message_ids"] == "[]"
    assert state["source_session_id"] is None and state["source_message_id"] is None
    conn = db.connect()
    try:
        events = conn.execute(
            "SELECT before_json,after_json FROM memory_events"
            " WHERE object_type='fragment' AND object_id=?", (fragment["id"],),
        ).fetchall()
        serialized = "".join((row["before_json"] or "") + (row["after_json"] or "") for row in events)
        assert "private-tag" not in serialized
        assert "private reason" not in serialized
        assert "message-secret" not in serialized
        assert fragment["content"] not in serialized
    finally:
        conn.close()


def test_privacy_clear_still_scrubs_an_existing_tombstone(fragment):
    assert memory.delete_memory(fragment["id"])
    assert _row(fragment["id"])["content"] == fragment["content"]
    assert memory.delete_memory(fragment["id"], privacy=True)
    state = _row(fragment["id"])
    assert state["status"] == "tombstone" and state["content"] == ""
    assert archivist.list_lifecycle_events(fragment["id"])[-1]["reason_code"] == (
        "privacy_cleared_by_user"
    )


def test_user_lifecycle_api_restores_with_revision_and_patch_cannot_bypass(fragment):
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET status='cooling',cooling_since=?,"
            "lifecycle_revision=3 WHERE id=?", (db.now(), fragment["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    rejected = client.patch(
        f"/api/memories/{fragment['id']}", json={"status": "active"}
    )
    assert rejected.status_code == 400
    conflict = client.post(
        f"/api/memories/{fragment['id']}/lifecycle",
        json={"target_status": "active", "expected_revision": 2},
    )
    assert conflict.status_code == 409
    restored = client.post(
        f"/api/memories/{fragment['id']}/lifecycle",
        json={
            "target_status": "active", "reason": "继续使用", "expected_revision": 3,
        },
    )
    assert restored.status_code == 200
    assert restored.json()["status"] == "active"
