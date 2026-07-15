import pytest

from app import db, entities, episode_consolidator, episode_summary, episodes, memory


@pytest.fixture(autouse=True)
def clean_application_records():
    conn = db.connect()
    try:
        before = {
            "fragments": {row["id"] for row in conn.execute("SELECT id FROM memory_fragments")},
            "entities": {row["id"] for row in conn.execute("SELECT id FROM memory_entities")},
            "episodes": {row["id"] for row in conn.execute("SELECT id FROM memory_episodes")},
            "candidates": {
                row["id"] for row in conn.execute("SELECT id FROM memory_episode_candidates")
            },
            "runs": {row["id"] for row in conn.execute("SELECT id FROM episode_consolidator_runs")},
        }
    finally:
        conn.close()
    yield
    conn = db.connect()
    try:
        for table, key in (
            ("episode_consolidator_runs", "runs"),
            ("memory_episodes", "episodes"),
            ("memory_episode_candidates", "candidates"),
        ):
            current = {row["id"] for row in conn.execute(f"SELECT id FROM {table}")}
            new_ids = current - before[key]
            if new_ids:
                placeholders = ",".join("?" for _ in new_ids)
                conn.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", list(new_ids))
        new_fragments = {
            row["id"] for row in conn.execute("SELECT id FROM memory_fragments")
        } - before["fragments"]
        if new_fragments:
            placeholders = ",".join("?" for _ in new_fragments)
            conn.execute(
                f"DELETE FROM memory_fragment_entities WHERE fragment_id IN ({placeholders})",
                list(new_fragments),
            )
            conn.execute(
                f"DELETE FROM memory_fragments WHERE id IN ({placeholders})", list(new_fragments)
            )
        new_entities = {
            row["id"] for row in conn.execute("SELECT id FROM memory_entities")
        } - before["entities"]
        if new_entities:
            placeholders = ",".join("?" for _ in new_entities)
            conn.execute(
                f"DELETE FROM memory_entities WHERE id IN ({placeholders})", list(new_entities)
            )
        conn.execute("DELETE FROM episode_group_candidates")
        conn.commit()
    finally:
        conn.close()


def _candidate() -> dict:
    now = db.now()
    name = f"原子经历-{db.new_id()}"
    entity = entities.create_entity(name, "event")
    ids = []
    for index, suffix in enumerate(("共同经历开始", "共同经历完成")):
        fragment = memory.create_memory("L1", f"{name}{suffix}")
        assert entities.link_fragment(entity["id"], fragment["id"], source="test")
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE memory_fragments SET created_at=?,updated_at=?,emotion='joy',"
                "scope='relationship',kind='experience' WHERE id=?",
                (now + index, now + index, fragment["id"]),
            )
            conn.commit()
        finally:
            conn.close()
        ids.append(fragment["id"])
    return next(
        item for item in episodes.generate_candidates(now=now + 2)
        if [fragment["id"] for fragment in item["fragments"]] == ids
    )


def _running_run(key: str) -> dict:
    run = episode_consolidator.enqueue(trigger="manual", request_key=key)
    conn = db.connect()
    try:
        now = db.now()
        conn.execute(
            "UPDATE episode_consolidator_runs SET status='running',attempt_count=1,"
            "started_at=?,updated_at=? WHERE id=?", (now, now, run["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    return run


def _episode_count(candidate_id: str) -> int:
    conn = db.connect()
    try:
        return conn.execute(
            "SELECT COUNT(*) AS count FROM memory_episodes WHERE candidate_id=?", (candidate_id,)
        ).fetchone()["count"]
    finally:
        conn.close()


def test_atomic_application_inherits_sources_entities_summary_and_audit():
    candidate = _candidate()
    run = _running_run("atomic-success")
    applied = episodes.apply_candidates_for_run(run["id"], [candidate["id"]])
    assert len(applied) == 1
    episode = applied[0]
    source_ids = [fragment["id"] for fragment in candidate["fragments"]]
    assert episode["source"] == "consolidator_auto"
    assert episode["application_version"] == episodes.APPLICATION_VERSION
    assert episode["grouping_fingerprint"] == candidate["grouping_key"]
    assert episode["source_fragment_ids"] == source_ids
    assert [fragment["id"] for fragment in episode["fragments"]] == source_ids
    assert episode["source_hash"] == episode_summary.source_hash(candidate["fragments"])
    assert episode["summary_status"] == candidate["summary_status"]
    assert episode["summary_evidence_fragment_ids"] == candidate[
        "summary_evidence_fragment_ids"
    ]
    assert episode["entities"]
    accepted = episodes.get_candidate(candidate["id"])
    assert accepted["status"] == "accepted"
    assert accepted["application_attempt_count"] == 1
    assert accepted["application_error_code"] is None
    finished = episode_consolidator.get_run(run["id"])
    assert finished["status"] == "applied"
    assert finished["result_episode_ids"] == [episode["id"]]
    assert finished["events"][-1]["reason_code"] == "formal_episodes_created"
    assert [event["action"] for event in memory.list_events("episode", episode["id"])] == [
        "created"
    ]

    repeated = _running_run("atomic-repeat")
    assert episodes.apply_candidates_for_run(repeated["id"], [candidate["id"]]) == []
    assert episode_consolidator.get_run(repeated["id"])["status"] == "skipped"
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT COUNT(*) AS count FROM memory_episodes WHERE candidate_id=?",
            (candidate["id"],),
        ).fetchone()["count"] == 1
    finally:
        conn.close()


def test_source_change_is_rejected_then_refreshed_for_retry():
    candidate = _candidate()
    changed = candidate["fragments"][0]
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET content=?,updated_at=? WHERE id=?",
            (changed["content"] + "（已纠正）", db.now(), changed["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    run = _running_run("source-changed")
    with pytest.raises(episodes.EpisodeApplyError) as error:
        episodes.apply_candidates_for_run(run["id"], [candidate["id"]])
    assert error.value.code == "application_source_changed"
    assert episodes.get_candidate(candidate["id"])["status"] == "pending"
    assert _episode_count(candidate["id"]) == 0

    episodes.record_application_failure([candidate["id"]], error.value.code)
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_episode_candidates SET application_attempt_count=? WHERE id=?",
            (episodes.APPLICATION_MAX_ATTEMPTS, candidate["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    assert candidate["id"] not in {item["id"] for item in episodes.pending_candidates()}
    assert candidate["id"] in {
        item["id"] for item in episodes.pending_candidates(include_changed_exhausted=True)
    }
    refreshed = episodes.record_summary_fallback(candidate["id"], "application_retry_refresh")
    assert refreshed["summary_source_hash"] != candidate["summary_source_hash"]
    assert refreshed["application_attempt_count"] == 0
    assert refreshed["application_error_code"] is None
    assert candidate["id"] in {item["id"] for item in episodes.pending_candidates()}
    applied = episodes.apply_candidates_for_run(run["id"], [candidate["id"]])
    assert len(applied) == 1
    assert "已纠正" in applied[0]["summary"]
    assert episodes.get_candidate(candidate["id"])["application_attempt_count"] == 1


def test_mid_transaction_failure_rolls_back_and_same_run_can_retry(monkeypatch):
    candidate = _candidate()
    run = _running_run("atomic-rollback")
    original_event = episodes._event

    def fail_after_episode_insert(conn, object_type, object_id, action, before, after, source):
        original_event(conn, object_type, object_id, action, before, after, source)
        if object_type == "episode" and action == "created":
            raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(episodes, "_event", fail_after_episode_insert)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        episodes.apply_candidates_for_run(run["id"], [candidate["id"]])
    assert episodes.get_candidate(candidate["id"])["status"] == "pending"
    assert _episode_count(candidate["id"]) == 0
    assert episode_consolidator.get_run(run["id"])["status"] == "running"

    monkeypatch.setattr(episodes, "_event", original_event)
    assert len(episodes.apply_candidates_for_run(run["id"], [candidate["id"]])) == 1
    assert _episode_count(candidate["id"]) == 1


def test_legacy_candidate_can_still_use_manual_compatibility_path():
    candidate = _candidate()
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_episode_candidates SET summary_status='legacy_rule',"
            "summary_protocol_version='legacy',summary_source_hash='' WHERE id=?",
            (candidate["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    episode = episodes.accept_candidate(candidate["id"])
    assert episode is not None
    assert episode["source"] == "candidate_confirmed"
    assert episode["summary_status"] == "user_edited"
    assert episode["summary_protocol_version"] == "manual-v1"
    assert episode["source_hash"] == episode_summary.source_hash(episode["fragments"])
