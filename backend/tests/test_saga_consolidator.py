"""Saga D.4 任务状态机、原子创建/追加与周级懒调度。"""
import asyncio
import json

import pytest

from app import db, episode_summary, memory, saga_consolidator, sagas

db.init_db()


@pytest.fixture(autouse=True)
def clean_d4_objects():
    conn = db.connect()
    try:
        before_fragments = {row["id"] for row in conn.execute("SELECT id FROM memory_fragments")}
        before_episodes = {row["id"] for row in conn.execute("SELECT id FROM memory_episodes")}
        before_entities = {row["id"] for row in conn.execute("SELECT id FROM memory_entities")}
        old_last = db.get_setting("last_saga_consolidator_run", "")
        conn.execute("DELETE FROM saga_consolidator_runs")
        conn.execute("DELETE FROM saga_candidate_summary_events")
        conn.execute("DELETE FROM saga_group_candidates")
        conn.execute("DELETE FROM memory_sagas")
        conn.commit()
    finally:
        conn.close()
    yield
    conn = db.connect()
    try:
        conn.execute("DELETE FROM saga_consolidator_runs")
        conn.execute("DELETE FROM saga_candidate_summary_events")
        conn.execute("DELETE FROM saga_group_candidates")
        conn.execute("DELETE FROM memory_sagas")
        episode_ids = [row["id"] for row in conn.execute("SELECT id FROM memory_episodes")
                       if row["id"] not in before_episodes]
        if episode_ids:
            marks = ",".join("?" for _ in episode_ids)
            conn.execute(f"DELETE FROM memory_episodes WHERE id IN ({marks})", episode_ids)
        fragment_ids = [row["id"] for row in conn.execute("SELECT id FROM memory_fragments")
                        if row["id"] not in before_fragments]
        if fragment_ids:
            marks = ",".join("?" for _ in fragment_ids)
            conn.execute(f"DELETE FROM memory_fragment_entities WHERE fragment_id IN ({marks})",
                         fragment_ids)
            conn.execute(f"DELETE FROM memory_fragments WHERE id IN ({marks})", fragment_ids)
        entity_ids = [row["id"] for row in conn.execute("SELECT id FROM memory_entities")
                      if row["id"] not in before_entities]
        if entity_ids:
            marks = ",".join("?" for _ in entity_ids)
            conn.execute(f"DELETE FROM memory_entities WHERE id IN ({marks})", entity_ids)
        conn.commit()
    finally:
        conn.close()
    db.set_setting("last_saga_consolidator_run", old_last)


def _entity() -> str:
    eid, now = db.new_id(), db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO memory_entities(id,name,entity_type,status,source,created_at,updated_at)"
            " VALUES(?,?,'project','active','manual',?,?)",
            (eid, f"Saga项目-{eid}", now, now),
        )
        conn.commit()
        return eid
    finally:
        conn.close()


def _episode(summary: str, stamp: float, entity_id: str) -> str:
    fragment = memory.create_memory("L1", summary)
    conn = db.connect()
    try:
        conn.execute("UPDATE memory_fragments SET created_at=?,updated_at=? WHERE id=?",
                     (stamp, stamp, fragment["id"]))
        source = dict(conn.execute("SELECT * FROM memory_fragments WHERE id=?",
                                   (fragment["id"],)).fetchone())
        eid = db.new_id()
        conn.execute(
            "INSERT INTO memory_episodes("
            "id,title,summary,start_at,end_at,status,source,source_fragment_ids_json,source_hash,"
            "summary_status,summary_protocol_version,summary_evidence_json,created_at,updated_at)"
            " VALUES(?,?,?,?,?,'active','automatic',?,?, 'extractive_fallback',"
            "'episode-extractive-v1',?,?,?)",
            (eid, "Saga项目进展", summary, stamp, stamp + 60, json.dumps([fragment["id"]]),
             episode_summary.source_hash([source]), json.dumps([fragment["id"]]), stamp, stamp),
        )
        conn.execute("INSERT INTO memory_episode_fragments VALUES(?,?,0,?)",
                     (eid, fragment["id"], stamp))
        conn.execute("INSERT INTO memory_episode_entities VALUES(?,?,'involves',?)",
                     (eid, entity_id, stamp))
        conn.commit()
        return eid
    finally:
        conn.close()


def _candidate(episode_ids: list[str], *, mode: str = "create", target: str | None = None) -> dict:
    cid, now = db.new_id(), db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO saga_group_candidates("
            "id,grouping_fingerprint,status,episode_ids_json,shared_entity_ids_json,"
            "entity_score,text_score,time_score,coherence_score,total_score,score_details_json,"
            "policy_version,first_seen_at,last_evaluated_at,expires_at,application_mode,target_saga_id)"
            " VALUES(?,?,'qualified',?,'[]',1,0.8,0.8,0.8,0.85,'{}',?,?,?,?,?,?)",
            (cid, sagas.grouping_fingerprint(episode_ids), json.dumps(episode_ids),
             sagas.POLICY_VERSION, now, now, now + 1000, mode, target),
        )
        conn.commit()
    finally:
        conn.close()
    assert sagas.record_summary_fallback(cid, "summary_model_unavailable")
    return sagas.get_group_candidate(cid)


def _running_run(key: str) -> dict:
    run = saga_consolidator.enqueue(trigger="manual", request_key=key)
    claimed = saga_consolidator._claim_next()
    assert claimed and claimed["id"] == run["id"]
    return claimed


def test_enqueue_cancel_and_weekly_schedule_are_idempotent():
    first = saga_consolidator.enqueue(trigger="manual", request_key="same")
    second = saga_consolidator.enqueue(trigger="manual", request_key="same")
    assert first["id"] == second["id"]
    assert [event["action"] for event in second["events"]] == ["enqueued"]
    assert saga_consolidator.cancel(first["id"])["status"] == "cancelled"

    db.set_setting("last_saga_consolidator_run", "0")
    weekly = saga_consolidator.enqueue_weekly(now=2_000_000_000)
    repeated = saga_consolidator.enqueue_weekly(now=2_000_000_001)
    assert weekly and repeated and weekly["id"] == repeated["id"]
    db.set_setting("last_saga_consolidator_run", "2000000000")
    assert saga_consolidator.enqueue_weekly(now=2_000_000_100) is None


def test_atomic_create_preserves_order_entities_audit_and_run_terminal():
    entity = _entity()
    episode_ids = [_episode("我们开始 Saga 项目", 1_900_000_000, entity),
                   _episode("我们继续 Saga 项目", 1_900_086_400, entity)]
    candidate = _candidate(episode_ids)
    run = _running_run("create")
    applied = sagas.apply_candidates_for_run(run["id"], [candidate["id"]])
    assert len(applied) == 1
    saga_id = applied[0]["id"]
    assert applied[0]["current_stage"]
    conn = db.connect()
    try:
        links = conn.execute(
            "SELECT episode_id FROM memory_saga_episodes WHERE saga_id=? ORDER BY position",
            (saga_id,),
        ).fetchall()
        assert [row["episode_id"] for row in links] == episode_ids
        assert conn.execute("SELECT COUNT(*) c FROM memory_saga_entities WHERE saga_id=?",
                            (saga_id,)).fetchone()["c"] == 1
        assert conn.execute("SELECT action FROM memory_saga_events WHERE saga_id=?",
                            (saga_id,)).fetchone()["action"] == "created"
    finally:
        conn.close()
    finished = saga_consolidator.get_run(run["id"])
    assert finished["status"] == "applied" and finished["result_saga_ids"] == [saga_id]
    duplicate = _running_run("duplicate")
    assert sagas.apply_candidates_for_run(duplicate["id"], [candidate["id"]]) == []
    assert saga_consolidator.get_run(duplicate["id"])["status"] == "skipped"


def test_incremental_append_updates_atomically_and_failure_keeps_old_snapshot():
    entity = _entity()
    first = _episode("我们开始 Saga 项目", 1_910_000_000, entity)
    second = _episode("我们继续 Saga 项目", 1_910_086_400, entity)
    created = _candidate([first, second])
    run = _running_run("initial")
    saga = sagas.apply_candidates_for_run(run["id"], [created["id"]])[0]
    third = _episode("我们完成 Saga 项目新阶段", 1_910_172_800, entity)
    generated = sagas.generate_candidates(now=1_910_172_900)
    assert generated, str([
        (item["status"], item["episode_ids"], item.get("application_mode"),
         item.get("target_saga_id"), item["score_details"])
        for status in ("qualified", "observing", "conflicted")
        for item in sagas.list_group_candidates(status)
    ])
    append = next(
        item for item in generated
        if set(item["episode_ids"]) == {first, second, third}
    )
    assert append["application_mode"] == "append"
    assert append["target_saga_id"] == saga["id"]
    assert sagas.record_summary_fallback(append["id"], "summary_model_unavailable")
    append = sagas.get_group_candidate(append["id"])
    run = _running_run("append")
    updated = sagas.apply_candidates_for_run(run["id"], [append["id"]])[0]
    assert json.loads(updated["source_episode_ids_json"]) == [first, second, third]
    old_summary, old_hash = updated["summary"], updated["source_hash"]

    fourth = _episode("我们继续 Saga 项目下一阶段", 1_910_259_200, entity)
    failing = _candidate([first, second, third, fourth], mode="append", target=saga["id"])
    conn = db.connect()
    try:
        conn.execute("UPDATE memory_episodes SET summary=?,summary_status='user_edited',"
                     "corrected_at=?,updated_at=? WHERE id=?",
                     ("来源在应用前改变", db.now(), db.now(), fourth))
        conn.commit()
    finally:
        conn.close()
    run = _running_run("append-fail")
    with pytest.raises(sagas.SagaApplyError, match="来源"):
        sagas.apply_candidates_for_run(run["id"], [failing["id"]])
    conn = db.connect()
    try:
        current = conn.execute("SELECT * FROM memory_sagas WHERE id=?", (saga["id"],)).fetchone()
        assert current["summary"] == old_summary and current["source_hash"] == old_hash
        assert conn.execute("SELECT COUNT(*) c FROM memory_saga_episodes WHERE saga_id=?",
                            (saga["id"],)).fetchone()["c"] == 3
    finally:
        conn.close()


def test_worker_empty_retry_recovery_and_cooperative_cancel(monkeypatch):
    run = saga_consolidator.enqueue(trigger="manual", request_key="empty")
    monkeypatch.setattr(saga_consolidator.sagas, "generate_candidates", lambda: [])
    monkeypatch.setattr(saga_consolidator.sagas, "qualified_candidates", lambda _limit: [])
    assert asyncio.run(saga_consolidator.process_due(limit=1)) == 1
    assert saga_consolidator.get_run(run["id"])["status"] == "skipped"

    failing = saga_consolidator.enqueue(trigger="manual", request_key="retry")
    monkeypatch.setattr(
        saga_consolidator.sagas, "generate_candidates",
        lambda: (_ for _ in ()).throw(RuntimeError("synthetic")),
    )
    assert asyncio.run(saga_consolidator.process_due(limit=1)) == 1
    assert saga_consolidator.get_run(failing["id"])["status"] == "recovery_pending"
    conn = db.connect()
    try:
        old = db.now() - saga_consolidator.RUNNING_STALE_SECONDS - 1
        conn.execute("UPDATE saga_consolidator_runs SET status='running',updated_at=? WHERE id=?",
                     (old, failing["id"]))
        conn.commit()
    finally:
        conn.close()
    assert saga_consolidator.cancel(failing["id"])["status"] == "cancel_requested"
    conn = db.connect()
    try:
        conn.execute("UPDATE saga_consolidator_runs SET updated_at=? WHERE id=?",
                     (old, failing["id"]))
        conn.commit()
    finally:
        conn.close()
    assert saga_consolidator.recover_stale_runs() == 1
    assert saga_consolidator.get_run(failing["id"])["status"] == "cancelled"

    interrupted = _running_run("shutdown")
    saga_consolidator._mark_interrupted(interrupted)
    recovered = saga_consolidator.get_run(interrupted["id"])
    assert recovered["status"] == "recovery_pending"
    assert recovered["error_code"] == "worker_stopped"
