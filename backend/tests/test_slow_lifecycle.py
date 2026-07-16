"""Archivist E.5：Episode/Saga 慢生命周期与来源链保护。"""
import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import (
    db, episode_summary, episodes, main, memory, saga_consolidator, saga_lifecycle,
    saga_summary, sagas, slow_lifecycle,
)

db.init_db()
client = TestClient(
    main.app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"}
)


@pytest.fixture(autouse=True)
def clean_slow_lifecycle_records():
    conn = db.connect()
    try:
        before = {
            table: {row["id"] for row in conn.execute(f"SELECT id FROM {table}")}
            for table in ("memory_fragments", "memory_episodes", "memory_sagas",
                          "saga_group_candidates", "saga_consolidator_runs")
        }
    finally:
        conn.close()
    yield
    conn = db.connect()
    try:
        for table in ("saga_consolidator_runs", "saga_group_candidates", "memory_sagas",
                      "memory_episodes"):
            current = {row["id"] for row in conn.execute(f"SELECT id FROM {table}")}
            created = current - before[table]
            if created:
                marks = ",".join("?" for _ in created)
                conn.execute(f"DELETE FROM {table} WHERE id IN ({marks})", list(created))
        fragments = {row["id"] for row in conn.execute("SELECT id FROM memory_fragments")}
        created = fragments - before["memory_fragments"]
        for fragment_id in created:
            conn.execute("DELETE FROM memory_recall_events WHERE fragment_id=?", (fragment_id,))
            conn.execute("DELETE FROM memory_lifecycle_events WHERE fragment_id=?", (fragment_id,))
            conn.execute("DELETE FROM memory_fragments WHERE id=?", (fragment_id,))
        conn.commit()
    finally:
        conn.close()


def _episode(label: str, stamp: float, *, significance: int = 4) -> tuple[dict, dict]:
    fragment = memory.create_memory("L1", f"慢生命周期来源 {label} {db.new_id()}")
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET created_at=?,updated_at=?,importance=0.2 WHERE id=?",
            (stamp, stamp, fragment["id"]),
        )
        source = dict(conn.execute(
            "SELECT * FROM memory_fragments WHERE id=?", (fragment["id"],)
        ).fetchone())
        episode_id = db.new_id()
        conn.execute(
            "INSERT INTO memory_episodes("
            "id,title,summary,start_at,end_at,significance,status,source,"
            "source_fragment_ids_json,source_hash,summary_status,summary_protocol_version,"
            "summary_evidence_json,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,'active','automatic',?,?,'extractive_fallback',"
            "'episode-extractive-v1',?,?,?)",
            (episode_id, label, source["content"], stamp, stamp + 60, significance,
             json.dumps([fragment["id"]]), episode_summary.source_hash([source]),
             json.dumps([fragment["id"]]), stamp, stamp),
        )
        conn.execute(
            "INSERT INTO memory_episode_fragments(episode_id,fragment_id,position,created_at)"
            " VALUES(?,?,0,?)", (episode_id, fragment["id"], stamp),
        )
        conn.commit()
        episode = dict(conn.execute(
            "SELECT * FROM memory_episodes WHERE id=?", (episode_id,)
        ).fetchone())
        return episode, source
    finally:
        conn.close()


def _saga(episodes: list[dict], *, status: str = "completed", significance: int = 4,
          completed_at: float | None = None) -> dict:
    now = completed_at if completed_at is not None else db.now()
    saga_id = db.new_id()
    conn = db.connect()
    try:
        ids = [item["id"] for item in episodes]
        source_hash = saga_summary.source_hash(sagas._load_candidate_episodes(conn, ids))
        conn.execute(
            "INSERT INTO memory_sagas("
            "id,title,summary,start_at,end_at,significance,status,source,source_episode_ids_json,"
            "source_hash,summary_status,summary_protocol_version,summary_evidence_json,"
            "completion_reason,completed_at,completion_revision,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,'automatic',?,?,'extractive_fallback','saga-summary-v1',"
            "?,'grounded',?,?,?,?)",
            (saga_id, "慢生命周期 Saga", "两个阶段形成的长期经历",
             min(item["start_at"] for item in episodes), max(item["end_at"] for item in episodes),
             significance, status, json.dumps(ids), source_hash, json.dumps(ids),
             now if status == "completed" else None, 0 if status == "completed" else None,
             now, now),
        )
        for position, episode in enumerate(episodes):
            conn.execute(
                "INSERT INTO memory_saga_episodes(saga_id,episode_id,position,role,added_at)"
                " VALUES(?,?,?,?,?)",
                (saga_id, episode["id"], position,
                 "anchor" if position == 0 else "resolution", now),
            )
        conn.commit()
        return dict(conn.execute("SELECT * FROM memory_sagas WHERE id=?", (saga_id,)).fetchone())
    finally:
        conn.close()


def test_schema_26_migrates_schema_25_episode_rows_and_adds_four_state_audit():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.executescript(db.SCHEMA)
        for version, sql in db.MIGRATIONS:
            if version <= 25:
                conn.executescript(sql)
        conn.execute(
            "INSERT INTO memory_episodes(id,title,summary,start_at,end_at,status,created_at,updated_at)"
            " VALUES('legacy','旧经历','内容',1,2,'archived',1,2)"
        )
        migration = next(sql for version, sql in db.MIGRATIONS if version == 26)
        conn.executescript(migration)
        row = conn.execute("SELECT * FROM memory_episodes WHERE id='legacy'").fetchone()
        assert row["summary"] == "内容" and row["lifecycle_revision"] == 0
        conn.execute(
            "INSERT INTO memory_episodes(id,title,summary,start_at,end_at,status,created_at,updated_at)"
            " VALUES('completed','完成','内容',1,2,'completed',1,2)"
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_episode_six_month_maturity_and_additional_six_month_archive_boundaries():
    now = 2_000_000_000.0
    episode, _ = _episode("时间边界", now - 181 * 86_400)
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_episodes SET end_at=?,updated_at=? WHERE id=?",
            (now - 179 * 86_400, now - 179 * 86_400, episode["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    assert not slow_lifecycle.assess_episode(episode["id"], now=now)["changed"]
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_episodes SET end_at=?,updated_at=? WHERE id=?",
            (now - 180 * 86_400, now - 180 * 86_400, episode["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    matured = slow_lifecycle.assess_episode(episode["id"], now=now)
    assert matured["changed"] and matured["episode"]["status"] == "completed"
    assert not slow_lifecycle.assess_episode(
        episode["id"], now=now + 179 * 86_400
    )["changed"]
    archived = slow_lifecycle.assess_episode(
        episode["id"], now=now + 180 * 86_400
    )
    assert archived["changed"] and archived["episode"]["status"] == "archived"


def test_episode_significance_recent_recall_and_active_saga_protect_maturity():
    now = 2_000_000_000.0
    important, _ = _episode("重要经历", now - 300 * 86_400, significance=8)
    result = slow_lifecycle.assess_episode(important["id"], now=now)
    assert not result["changed"] and "high_significance" in result["protection_reasons"]

    recalled, fragment = _episode("近期召回", now - 300 * 86_400)
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET last_recalled_at=? WHERE id=?",
            (now - 2 * 86_400, fragment["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    result = slow_lifecycle.assess_episode(recalled["id"], now=now)
    assert not result["changed"] and "recent_source_recall" in result["protection_reasons"]

    linked, _ = _episode("活跃故事来源", now - 300 * 86_400)
    other, _ = _episode("活跃故事后续", now - 290 * 86_400)
    _saga([linked, other], status="active", completed_at=now - 200 * 86_400)
    result = slow_lifecycle.assess_episode(linked["id"], now=now)
    assert not result["changed"] and "active_saga_source" in result["protection_reasons"]


def test_episode_user_and_new_evidence_restore_with_revision_and_tombstone_terminal():
    now = 2_000_000_000.0
    episode, _ = _episode("恢复", now - 300 * 86_400)
    completed = slow_lifecycle.assess_episode(episode["id"], now=now)["episode"]
    with pytest.raises(slow_lifecycle.SlowLifecycleError, match="已变化"):
        slow_lifecycle.transition_episode(
            episode["id"], "active", trigger="user", expected_revision=99
        )
    active = slow_lifecycle.transition_episode(
        episode["id"], "active", trigger="new_evidence",
        expected_revision=completed["lifecycle_revision"], now=now + 1,
    )
    assert active["status"] == "active" and active["completed_at"] is None
    deleted = slow_lifecycle.transition_episode(
        episode["id"], "tombstone", trigger="user", reason="用户明确删除",
        expected_revision=active["lifecycle_revision"], now=now + 2,
    )
    assert deleted["status"] == "tombstone"
    with pytest.raises(slow_lifecycle.SlowLifecycleError, match="不可恢复"):
        slow_lifecycle.transition_episode(episode["id"], "active", trigger="user")


def test_episode_correction_is_new_evidence_and_reactivates_archived_episode():
    now = 2_000_000_000.0
    episode, _ = _episode("纠正前", now - 500 * 86_400)
    completed = slow_lifecycle.assess_episode(episode["id"], now=now)["episode"]
    archived = slow_lifecycle.assess_episode(
        episode["id"], now=now + 180 * 86_400
    )["episode"]
    corrected = episodes.correct_episode(
        episode["id"], summary="纠正后的正式经历", note="补充真实细节",
        expected_revision=archived["lifecycle_revision"],
    )
    assert corrected["status"] == "active" and corrected["archived_at"] is None
    conn = db.connect()
    try:
        event = conn.execute(
            "SELECT reason_code,source FROM memory_episode_lifecycle_events"
            " WHERE episode_id=? ORDER BY revision DESC LIMIT 1", (episode["id"],),
        ).fetchone()
        assert event["reason_code"] == "episode_reactivated_by_correction"
        assert event["source"] == "new_evidence"
    finally:
        conn.close()


def test_completed_saga_archives_only_after_twelve_stable_months_and_active_never_does():
    now = 2_000_000_000.0
    first, _ = _episode("起点", now - 500 * 86_400)
    second, _ = _episode("终点完成", now - 490 * 86_400)
    active = _saga([first, second], status="active", completed_at=now - 500 * 86_400)
    assert not slow_lifecycle.assess_saga(active["id"], now=now)["changed"]
    first, _ = _episode("归档起点", now - 500 * 86_400)
    second, _ = _episode("归档终点完成", now - 490 * 86_400)
    completed = _saga(
        [first, second], completed_at=now - 364 * 86_400
    )
    assert not slow_lifecycle.assess_saga(completed["id"], now=now)["changed"]
    conn = db.connect()
    try:
        old = now - 365 * 86_400
        conn.execute(
            "UPDATE memory_sagas SET completed_at=?,updated_at=? WHERE id=?",
            (old, old, completed["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    result = slow_lifecycle.assess_saga(completed["id"], now=now)
    assert result["changed"] and result["saga"]["status"] == "archived"
    assert result["saga"]["revision"] == completed["revision"] + 1


def test_saga_significance_recent_recall_pending_append_and_correction_block_archive():
    now = 2_000_000_000.0
    first, fragment = _episode("保护起点", now - 500 * 86_400)
    second, _ = _episode("保护终点完成", now - 490 * 86_400)
    high = _saga([first, second], significance=8, completed_at=now - 400 * 86_400)
    result = slow_lifecycle.assess_saga(high["id"], now=now)
    assert not result["changed"] and "high_significance" in result["protection_reasons"]

    first, fragment = _episode("召回保护起点", now - 500 * 86_400)
    second, _ = _episode("召回保护终点", now - 490 * 86_400)
    recalled = _saga([first, second], completed_at=now - 400 * 86_400)
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET last_recalled_at=? WHERE id=?",
            (now - 1, fragment["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    result = slow_lifecycle.assess_saga(recalled["id"], now=now)
    assert not result["changed"] and "recent_source_recall" in result["protection_reasons"]

    first, fragment = _episode("追加保护起点", now - 500 * 86_400)
    second, _ = _episode("追加保护终点", now - 490 * 86_400)
    pending = _saga([first, second], completed_at=now - 400 * 86_400)
    conn = db.connect()
    try:
        conn.execute("UPDATE memory_fragments SET last_recalled_at=NULL WHERE id=?", (fragment["id"],))
        conn.execute(
            "INSERT INTO saga_group_candidates("
            "id,grouping_fingerprint,status,episode_ids_json,shared_entity_ids_json,"
            "entity_score,text_score,time_score,coherence_score,total_score,score_details_json,"
            "policy_version,first_seen_at,last_evaluated_at,expires_at,application_mode,target_saga_id)"
                " VALUES(?,?,'qualified',?,'[]',.8,.8,.8,.8,.8,'{}','test',?,?,?, 'append',?)",
            (db.new_id(), db.new_id(), json.dumps([first["id"], second["id"]]),
             now, now, now + 1000, pending["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    result = slow_lifecycle.assess_saga(pending["id"], now=now)
    assert not result["changed"] and "pending_append_candidate" in result["protection_reasons"]

    first, _ = _episode("纠错保护起点", now - 500 * 86_400)
    second, _ = _episode("纠错保护终点", now - 490 * 86_400)
    corrected = _saga([first, second], completed_at=now - 400 * 86_400)
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_sagas SET updated_at=?,revision=revision+1 WHERE id=?",
            (now - 10, corrected["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    assert slow_lifecycle.assess_saga(corrected["id"], now=now)["reason_code"] == (
        "saga_revision_changed_since_completion"
    )


def test_frozen_fragments_keep_episode_saga_sources_and_user_can_restore_archived_saga():
    now = 2_000_000_000.0
    first, first_fragment = _episode("冻结来源起点", now - 500 * 86_400)
    second, _ = _episode("冻结来源完成", now - 490 * 86_400)
    saga = _saga([first, second], completed_at=now - 400 * 86_400)
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET status='frozen',fts_indexed=0,frozen_at=? WHERE id=?",
            (now - 100, first_fragment["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    archived = slow_lifecycle.assess_saga(saga["id"], now=now)["saga"]
    assert archived["status"] == "archived"
    detail = saga_lifecycle.get_saga(saga["id"])
    assert len(detail["timeline"]) == 2
    restored = saga_lifecycle.transition(
        saga["id"], "active", reason="用户重新开始", source="user",
        expected_revision=archived["revision"],
    )
    assert restored["status"] == "active" and restored["archived_at"] is None
    completed = saga_lifecycle.transition(
        saga["id"], "completed", reason="新阶段再次结束", source="user",
        expected_revision=restored["revision"],
    )
    conn = db.connect()
    try:
        old = now - slow_lifecycle.SAGA_ARCHIVE_DAYS * 86_400
        conn.execute(
            "UPDATE memory_sagas SET completed_at=?,updated_at=?,completion_revision=revision"
            " WHERE id=?", (old, old, saga["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    rearchived = slow_lifecycle.assess_saga(saga["id"], now=now)
    assert rearchived["changed"] and rearchived["saga"]["status"] == "archived"


def test_saga_weekly_worker_runs_slow_lifecycle_with_independent_budgets(monkeypatch):
    observed = {}
    monkeypatch.setattr(
        slow_lifecycle, "process_batch",
        lambda: {"episode_scanned": 10, "episode_changed": 1, "saga_scanned": 10,
                 "saga_changed": 1, "conflict_count": 0, "model_calls_used": 0},
    )
    monkeypatch.setattr(saga_consolidator.sagas, "generate_candidates", lambda: [])
    monkeypatch.setattr(saga_consolidator.sagas, "qualified_candidates", lambda _limit: [])
    run = saga_consolidator.enqueue(trigger="manual", request_key="slow-hook")
    assert asyncio.run(saga_consolidator.process_due(limit=1)) == 1
    finished = saga_consolidator.get_run(run["id"])
    event = next(item for item in finished["events"] if item["action"] == "slow_lifecycle_processed")
    observed.update(event["metadata"])
    assert observed["episode_scanned"] == slow_lifecycle.EPISODE_BUDGET
    assert observed["saga_scanned"] == slow_lifecycle.SAGA_BUDGET


def test_episode_lifecycle_api_restores_but_cannot_automatically_tombstone():
    now = db.now()
    episode, _ = _episode("API 恢复", now - 300 * 86_400)
    completed = slow_lifecycle.assess_episode(episode["id"], now=now)["episode"]
    response = client.post(
        f"/api/episodes/{episode['id']}/lifecycle",
        json={"target_status": "active", "reason": "继续这段经历",
              "expected_revision": completed["lifecycle_revision"]},
    )
    assert response.status_code == 200 and response.json()["status"] == "active"
    assert client.get(f"/api/episodes/{episode['id']}").status_code == 200
