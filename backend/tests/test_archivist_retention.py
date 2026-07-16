"""Archivist E.2 真实召回、保留评分与跨层保护验收。"""
import json
from concurrent.futures import ThreadPoolExecutor

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
    item = memory.create_memory("L1", f"retention-fragment-{db.new_id()}")
    yield item
    conn = db.connect()
    try:
        conn.execute("DELETE FROM memory_recall_events WHERE fragment_id=?", (item["id"],))
        conn.execute("DELETE FROM memory_lifecycle_events WHERE fragment_id=?", (item["id"],))
        conn.execute("DELETE FROM memory_fragments WHERE id=?", (item["id"],))
        conn.commit()
    finally:
        conn.close()


def _fragment_state(fragment_id: str) -> dict:
    conn = db.connect()
    try:
        return dict(conn.execute(
            "SELECT * FROM memory_fragments WHERE id=?", (fragment_id,)
        ).fetchone())
    finally:
        conn.close()


def test_context_key_is_stable_for_same_turn_and_rejects_missing_identity():
    assert archivist.recall_context_key("session-a", "message-a") == (
        "chat:session-a:message-a"
    )
    assert archivist.recall_context_key("session-a", "message-a") == (
        archivist.recall_context_key("session-a", "message-a")
    )
    with pytest.raises(ValueError):
        archivist.recall_context_key("session-a", "")


def test_search_does_not_count_and_injection_is_atomic_and_idempotent(fragment):
    assert memory.search_memories(fragment["content"])
    assert _fragment_state(fragment["id"])["recall_count"] == 0
    first = archivist.record_injected_memories(
        [fragment, fragment], context_key="chat:s:m1", source_session_id=None,
        injected_at=1_900_000_000,
    )
    repeated = archivist.record_injected_memories(
        [fragment], context_key="chat:s:m1", source_session_id=None,
        injected_at=1_900_000_010,
    )
    state = _fragment_state(fragment["id"])
    assert first == [fragment["id"]] and repeated == []
    assert state["recall_count"] == 1
    assert state["last_recalled_at"] == 1_900_000_000


def test_concurrent_same_turn_injection_creates_exactly_one_count(fragment):
    def inject():
        return archivist.record_injected_memories(
            [fragment], context_key="chat:concurrent:message", source_session_id=None,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _index: inject(), range(4)))
    assert sum(len(result) for result in results) == 1
    assert _fragment_state(fragment["id"])["recall_count"] == 1
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT COUNT(*) count FROM memory_recall_events WHERE fragment_id=?",
            (fragment["id"],),
        ).fetchone()["count"] == 1
    finally:
        conn.close()


def test_injection_rechecks_active_enabled_normal_state(fragment):
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET status='frozen',frozen_at=? WHERE id=?",
            (db.now(), fragment["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    assert archivist.record_injected_memories(
        [fragment], context_key="chat:s:frozen", source_session_id=None
    ) == []
    assert _fragment_state(fragment["id"])["recall_count"] == 0


def test_retention_components_cover_zero_saturation_and_clamp_boundaries():
    now = 2_000_000_000.0
    low = archivist.retention_score(
        {
            "importance": 0, "recall_count": 0, "created_at": now - 400 * 86_400,
            "confidence": 0,
        },
        now=now, relationship=0, in_active_saga=False, duplicate_penalty=1,
    )
    high = archivist.retention_score(
        {
            "importance": 1, "recall_count": 10_000, "created_at": now,
            "last_recalled_at": now + 100, "confidence": 1,
        },
        now=now, relationship=1, in_active_saga=True, duplicate_penalty=0,
    )
    assert low["score"] == 0
    assert low["components"]["duplicate_penalty"] == archivist.MAX_DUPLICATE_PENALTY
    assert high["score"] == 1
    assert high["components"] == {
        "importance": 1.0, "recall_strength": 1.0, "recency": 1.0,
        "relationship_significance": 1.0, "active_saga_bonus": 1.0,
        "confidence": 1.0, "duplicate_penalty": 0.0,
    }
    assert sum(high["contributions"].values()) == 1


def test_recall_and_recency_are_bounded_monotonic_and_use_created_at_fallback():
    assert archivist.recall_strength(0) == 0
    assert 0 < archivist.recall_strength(1) < archivist.recall_strength(10) < 1
    assert archivist.recall_strength(20) == archivist.recall_strength(10_000) == 1
    now = 2_000_000_000.0
    assert archivist.recency_strength(
        last_recalled_at=None, created_at=now, now=now
    ) == 1
    assert archivist.recency_strength(
        last_recalled_at=None, created_at=now - 180 * 86_400, now=now
    ) == 0
    assert archivist.recency_strength(
        last_recalled_at=now - 10, created_at=now - 400 * 86_400, now=now
    ) > 0.99


def test_protection_reasons_are_explicit_and_do_not_read_instant_emotion():
    base = {"layer": "L1", "status": "active", "importance": 0.9}
    assert archivist.protection_reasons({**base, "kind": "preference"}) == [
        "stable_boundary"
    ]
    assert archivist.protection_reasons({**base, "kind": "correction"}) == [
        "current_correction"
    ]
    assert archivist.protection_reasons({**base, "kind": "plan"}) == [
        "unfinished_plan"
    ]
    emotional = archivist.retention_score(
        {**base, "kind": "observation", "emotion": "intense", "created_at": 1},
        now=100, relationship=0,
    )
    neutral = archivist.retention_score(
        {**base, "kind": "observation", "emotion": "", "created_at": 1},
        now=100, relationship=0,
    )
    assert emotional == neutral


def test_cross_layer_snapshot_finds_episode_active_saga_and_anchor(fragment):
    stamp = db.now()
    episode_id, saga_id = db.new_id(), db.new_id()
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET scope='relationship',kind='experience' WHERE id=?",
            (fragment["id"],),
        )
        conn.execute(
            "INSERT INTO memory_episodes("
            "id,title,summary,start_at,end_at,status,source,created_at,updated_at)"
            " VALUES(?,?,?,?,?,'active','automatic',?,?)",
            (episode_id, "共同经历", "共同经历持续发展", stamp, stamp + 60, stamp, stamp),
        )
        conn.execute(
            "INSERT INTO memory_episode_fragments VALUES(?,?,0,?)",
            (episode_id, fragment["id"], stamp),
        )
        conn.execute(
            "INSERT INTO memory_sagas("
            "id,title,summary,theme,current_stage,start_at,end_at,status,source,"
            "source_episode_ids_json,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,'active','automatic',?,?,?)",
            (
                saga_id, "共同故事", "共同经历持续发展", "共同经历", "持续发展",
                stamp, stamp + 60, json.dumps([episode_id]), stamp, stamp,
            ),
        )
        conn.execute(
            "INSERT INTO memory_saga_episodes("
            "saga_id,episode_id,position,role,added_at) VALUES(?,?,0,'anchor',?)",
            (saga_id, episode_id, stamp),
        )
        conn.commit()
        result = archivist.evaluate_fragments([fragment["id"]], now=stamp)[0]
        assert result["dependency_flags"] == {
            "in_episode": True, "in_active_episode": True, "in_active_saga": True,
            "is_active_saga_anchor": True,
        }
        assert result["components"]["relationship_significance"] == 1
        assert result["components"]["active_saga_bonus"] == 1
        assert "active_saga_anchor" in result["protection_reasons"]
    finally:
        conn.execute("DELETE FROM memory_sagas WHERE id=?", (saga_id,))
        conn.execute("DELETE FROM memory_episodes WHERE id=?", (episode_id,))
        conn.commit()
        conn.close()


def test_real_chat_counts_once_and_regenerate_reuses_the_same_turn():
    marker = f"retentionchatmarker{db.new_id()}"
    fragment = memory.create_memory("L1", marker)
    session = client.post("/api/sessions", json={}).json()
    previous_model = db.get_setting("current_model", "")
    db.set_setting("current_model", json.dumps({"provider_id": "mock", "model": "xiadie-mock"}))
    try:
        with client.stream(
            "POST", "/api/chat", json={"session_id": session["id"], "content": marker}
        ) as response:
            "".join(response.iter_text())
        first = _fragment_state(fragment["id"])
        assert first["recall_count"] == 1
        conn = db.connect()
        try:
            user_id = conn.execute(
                "SELECT id FROM messages WHERE session_id=? AND role='user'",
                (session["id"],),
            ).fetchone()["id"]
            event = conn.execute(
                "SELECT * FROM memory_recall_events WHERE fragment_id=?",
                (fragment["id"],),
            ).fetchone()
            assert event["context_key"] == archivist.recall_context_key(session["id"], user_id)
        finally:
            conn.close()

        with client.stream(
            "POST", "/api/chat",
            json={"session_id": session["id"], "content": marker, "regenerate": True},
        ) as response:
            "".join(response.iter_text())
        assert _fragment_state(fragment["id"])["recall_count"] == 1

        with client.stream(
            "POST", "/api/chat", json={"session_id": session["id"], "content": marker}
        ) as response:
            "".join(response.iter_text())
        assert _fragment_state(fragment["id"])["recall_count"] == 2
    finally:
        db.set_setting("current_model", previous_model)
        conn = db.connect()
        try:
            conn.execute("DELETE FROM memory_recall_events WHERE fragment_id=?", (fragment["id"],))
            conn.execute("DELETE FROM memory_fragments WHERE id=?", (fragment["id"],))
            conn.execute("DELETE FROM sessions WHERE id=?", (session["id"],))
            conn.commit()
        finally:
            conn.close()
