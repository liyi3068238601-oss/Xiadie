"""D.6 总验收：从 Fragment 到 Saga 终态的完整正式数据链。"""
import json

import pytest

from app import (
    db, entities, episode_consolidator, episodes, memory,
    saga_consolidator, saga_lifecycle, sagas,
)

db.init_db()


@pytest.fixture(autouse=True)
def clean_end_to_end_records():
    conn = db.connect()
    try:
        before = {
            table: {row["id"] for row in conn.execute(f"SELECT id FROM {table}")}
            for table in (
                "memory_fragments", "memory_entities", "memory_episode_candidates",
                "memory_episodes", "episode_consolidator_runs", "memory_sagas",
                "saga_group_candidates", "saga_consolidator_runs",
            )
        }
    finally:
        conn.close()
    yield
    conn = db.connect()
    try:
        # Parent deletions cascade through timeline, events, source links and suggestions.
        for table in (
            "saga_consolidator_runs", "saga_group_candidates", "memory_sagas",
            "episode_consolidator_runs", "memory_episode_candidates", "memory_episodes",
        ):
            current = {row["id"] for row in conn.execute(f"SELECT id FROM {table}")}
            created = current - before[table]
            if created:
                marks = ",".join("?" for _ in created)
                conn.execute(f"DELETE FROM {table} WHERE id IN ({marks})", list(created))
        created_fragments = {
            row["id"] for row in conn.execute("SELECT id FROM memory_fragments")
        } - before["memory_fragments"]
        if created_fragments:
            marks = ",".join("?" for _ in created_fragments)
            conn.execute(
                f"DELETE FROM memory_fragment_entities WHERE fragment_id IN ({marks})",
                list(created_fragments),
            )
            conn.execute(
                f"DELETE FROM memory_fragments WHERE id IN ({marks})", list(created_fragments)
            )
        created_entities = {
            row["id"] for row in conn.execute("SELECT id FROM memory_entities")
        } - before["memory_entities"]
        if created_entities:
            marks = ",".join("?" for _ in created_entities)
            conn.execute(
                f"DELETE FROM memory_entities WHERE id IN ({marks})", list(created_entities)
            )
        conn.commit()
    finally:
        conn.close()


def _running_episode_run(key: str) -> dict:
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


def _running_saga_run(key: str) -> dict:
    run = saga_consolidator.enqueue(trigger="manual", request_key=key)
    conn = db.connect()
    try:
        now = db.now()
        conn.execute(
            "UPDATE saga_consolidator_runs SET status='running',attempt_count=1,"
            "started_at=?,updated_at=? WHERE id=?", (now, now, run["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    return run


def _formal_episode(shared_entity_id: str, text: str, stamp: float, key: str) -> dict:
    fragment_ids = []
    for index in range(2):
        fragment = memory.create_memory("L1", f"{text}（记录{index + 1}）")
        assert entities.link_fragment(shared_entity_id, fragment["id"], source="test")
        conn = db.connect()
        try:
            moment = stamp + index * 60
            conn.execute(
                "UPDATE memory_fragments SET created_at=?,updated_at=?,emotion='joy',"
                "scope='relationship',kind='experience' WHERE id=?",
                (moment, moment, fragment["id"]),
            )
            conn.commit()
        finally:
            conn.close()
        fragment_ids.append(fragment["id"])
    candidate = next(
        item for item in episodes.generate_candidates(now=stamp + 120)
        if [fragment["id"] for fragment in item["fragments"]] == fragment_ids
    )
    applied = episodes.apply_candidates_for_run(
        _running_episode_run(key)["id"], [candidate["id"]]
    )
    assert len(applied) == 1
    return applied[0]


def _qualified_saga_candidate(episode_ids: list[str], now: float) -> dict:
    generated = sagas.generate_candidates(now=now)
    candidate = next(
        item for item in generated if item["episode_ids"] == episode_ids
    )
    assert candidate["status"] == "qualified"
    assert sagas.record_summary_fallback(candidate["id"], "integration_model_unavailable")
    return sagas.get_group_candidate(candidate["id"])


def _validate_completed_summary(candidate: dict) -> dict:
    sources = candidate["episodes"]
    raw = {
        "protocol_version": "saga-summary-v1",
        "title": "长期项目故事",
        "theme": "长期项目",
        "current_stage": sources[-1]["summary"],
        "current_stage_episode_ids": [sources[-1]["id"]],
        "claims": [
            {
                "text": source["summary"],
                "episode_ids": [source["id"]],
                "role": "anchor" if index == 0 else (
                    "resolution" if index == len(sources) - 1 else "development"
                ),
            }
            for index, source in enumerate(sources)
        ],
        "lifecycle_signal": "completed",
        "completion_evidence_episode_ids": [sources[-1]["id"]],
    }
    return sagas.apply_model_summary(
        candidate["id"], raw, provider_id="integration-provider", model="summary-model",
        prompt_tokens=100, completion_tokens=60, repair_attempted=False,
        expected_source_hash=candidate["current_source_hash"],
    )


def test_fragment_to_episode_to_saga_complete_recover_correct_and_tombstone():
    shared = entities.create_entity(f"长期项目-{db.new_id()}", "project")
    base = 2_200_000_000.0
    first = _formal_episode(shared["id"], "我们开始长期项目", base, "e2e-episode-1")
    second = _formal_episode(
        shared["id"], "我们继续推进长期项目", base + 10 * 86_400, "e2e-episode-2"
    )

    create_candidate = _qualified_saga_candidate(
        [first["id"], second["id"]], base + 10 * 86_400 + 180
    )
    created = sagas.apply_candidates_for_run(
        _running_saga_run("e2e-saga-create")["id"], [create_candidate["id"]]
    )[0]
    assert json.loads(created["source_episode_ids_json"]) == [first["id"], second["id"]]
    assert saga_lifecycle.get_saga(created["id"])["entities"][0]["entity_id"] == shared["id"]

    completed_episode = _formal_episode(
        shared["id"], "长期项目已经完成", base + 20 * 86_400, "e2e-episode-3"
    )
    append_candidate = _qualified_saga_candidate(
        [first["id"], second["id"], completed_episode["id"]],
        base + 20 * 86_400 + 180,
    )
    append_candidate = _validate_completed_summary(append_candidate)
    appended = sagas.apply_candidates_for_run(
        _running_saga_run("e2e-saga-append")["id"], [append_candidate["id"]]
    )[0]
    detail = saga_lifecycle.get_saga(appended["id"])
    assert detail["status"] == "completed"
    assert detail["completion_evidence_episode_ids"] == [completed_episode["id"]]
    assert detail["relationship_suggestions"][0]["status"] == "proposed"

    detail = saga_lifecycle.transition(
        detail["id"], "active", reason="用户开启下一阶段",
        expected_revision=detail["revision"],
    )
    assert detail["relationship_suggestions"][0]["status"] == "revoked"
    replacement = _formal_episode(
        shared["id"], "长期项目调整方案后继续推进",
        base + 30 * 86_400, "e2e-episode-4",
    )
    detail = saga_lifecycle.correct_sources(
        detail["id"], [first["id"], replacement["id"]],
        note="完成记录属于旧阶段，改用当前进展",
        expected_revision=detail["revision"],
    )
    assert detail["summary_status"] == "extractive_fallback"
    assert detail["source_episode_ids"] == [first["id"], replacement["id"]]
    assert any(
        item["episode_id"] == completed_episode["id"] and item["removed_at"]
        for item in detail["timeline"]
    )

    tombstone = saga_lifecycle.transition(
        detail["id"], "tombstone", reason="用户永久删除测试故事",
        expected_revision=detail["revision"],
    )
    assert tombstone["status"] == "tombstone"
    with pytest.raises(saga_lifecycle.SagaLifecycleError, match="不可恢复"):
        saga_lifecycle.transition(
            tombstone["id"], "active", reason="不应恢复",
            expected_revision=tombstone["revision"],
        )
