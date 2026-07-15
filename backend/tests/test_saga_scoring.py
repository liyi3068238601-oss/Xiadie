"""Saga D.2 纯本地候选预筛、评分、幂等和冲突边界。"""
from datetime import datetime, timedelta

import pytest

from app import db, sagas

db.init_db()


@pytest.fixture(autouse=True)
def clean_saga_scoring_objects():
    conn = db.connect()
    try:
        before_sagas = {row["id"] for row in conn.execute("SELECT id FROM memory_sagas")}
        before_episodes = {row["id"] for row in conn.execute("SELECT id FROM memory_episodes")}
        before_entities = {row["id"] for row in conn.execute("SELECT id FROM memory_entities")}
        conn.execute("DELETE FROM saga_group_candidates")
        conn.commit()
    finally:
        conn.close()
    yield
    conn = db.connect()
    try:
        conn.execute("DELETE FROM saga_group_candidates")
        new_sagas = [
            row["id"] for row in conn.execute("SELECT id FROM memory_sagas")
            if row["id"] not in before_sagas
        ]
        if new_sagas:
            placeholders = ",".join("?" for _ in new_sagas)
            conn.execute(f"DELETE FROM memory_sagas WHERE id IN ({placeholders})", new_sagas)
        new_episodes = [
            row["id"] for row in conn.execute("SELECT id FROM memory_episodes")
            if row["id"] not in before_episodes
        ]
        if new_episodes:
            placeholders = ",".join("?" for _ in new_episodes)
            conn.execute(
                f"DELETE FROM memory_episodes WHERE id IN ({placeholders})", new_episodes
            )
        new_entities = [
            row["id"] for row in conn.execute("SELECT id FROM memory_entities")
            if row["id"] not in before_entities
        ]
        if new_entities:
            placeholders = ",".join("?" for _ in new_entities)
            conn.execute(
                f"DELETE FROM memory_entities WHERE id IN ({placeholders})", new_entities
            )
        conn.commit()
    finally:
        conn.close()


def _midday(day_offset: int = 0) -> float:
    base = datetime(2026, 2, 1, 12, 0, 0)
    return (base + timedelta(days=day_offset)).timestamp()


def _entity(name: str = "记忆系统项目") -> str:
    entity_id = db.new_id()
    stamp = db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO memory_entities("
            "id,name,entity_type,status,source,created_at,updated_at"
            ") VALUES(?,?,'project','active','manual',?,?)",
            (entity_id, f"{name}-{entity_id}", stamp, stamp),
        )
        conn.commit()
        return entity_id
    finally:
        conn.close()


def _episode(
    title: str, summary: str, day: int, entity_id: str | None = None,
    *, status: str = "active", confidence: float = 0.85,
) -> dict:
    episode_id = db.new_id()
    start = _midday(day)
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO memory_episodes("
            "id,title,summary,start_at,end_at,confidence,status,source,"
            "source_fragment_ids_json,source_hash,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,'automatic','[]',?, ?,?)",
            (
                episode_id, title, summary, start, start + 3600, confidence, status,
                f"source-{episode_id}", start, start,
            ),
        )
        if entity_id:
            conn.execute(
                "INSERT INTO memory_episode_entities(episode_id,entity_id,created_at)"
                " VALUES(?,?,?)",
                (episode_id, entity_id, start),
            )
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM memory_episodes WHERE id=?", (episode_id,)
        ).fetchone())
    finally:
        conn.close()


def test_weight_formula_and_policy_fingerprint_are_stable():
    assert sagas.combine_scores(1.0, 0.2, 0.5, 0.375) == {
        "entity": 1.0, "text": 0.2, "time": 0.5, "coherence": 0.375,
        "total": 0.52,
    }
    left = sagas.grouping_fingerprint(["episode-b", "episode-a"])
    right = sagas.grouping_fingerprint(["episode-a", "episode-b", "episode-a"])
    assert left == right and len(left) == 64
    with pytest.raises(ValueError, match="至少需要两个"):
        sagas.grouping_fingerprint(["episode-a"])


def test_group_requires_two_episodes_cross_day_and_bounded_gaps():
    template = {"title": "持续项目", "summary": "我们继续完善同一个记忆系统", "confidence": 0.8}
    first = {"id": "first", "start_at": _midday(0), "end_at": _midday(0) + 60, **template}
    same_day = {"id": "same", "start_at": _midday(0) + 3600, "end_at": _midday(0) + 3660, **template}
    with pytest.raises(ValueError, match="两个自然日"):
        sagas.assess_group([first, same_day], {"first": {"e"}, "same": {"e"}})
    too_far = {"id": "far", "start_at": _midday(61), "end_at": _midday(61) + 60, **template}
    with pytest.raises(ValueError, match="间隔不能超过 60 天"):
        sagas.assess_group([first, too_far], {"first": {"e"}, "far": {"e"}})
    with pytest.raises(ValueError, match="2 到 12"):
        sagas.assess_group([first], {"first": {"e"}})
    thirteen = [
        {"id": f"episode-{index}", "start_at": _midday(index),
         "end_at": _midday(index) + 60, **template}
        for index in range(13)
    ]
    with pytest.raises(ValueError, match="2 到 12"):
        sagas.assess_group(thirteen, {item["id"]: {"e"} for item in thirteen})


def test_shared_entity_and_theme_create_minimal_idempotent_candidate():
    entity_id = _entity()
    first = _episode("记忆系统第一阶段", "我们开始完善长期记忆系统", 0, entity_id)
    second = _episode("记忆系统第二阶段", "我们继续完善长期记忆系统", 7, entity_id)
    conn = db.connect()
    try:
        saga_count_before = conn.execute(
            "SELECT COUNT(*) c FROM memory_sagas"
        ).fetchone()["c"]
        before = {
            row["id"]: dict(row) for row in conn.execute(
                "SELECT * FROM memory_episodes WHERE id IN (?,?)", (first["id"], second["id"])
            ).fetchall()
        }
    finally:
        conn.close()
    created = sagas.generate_candidates(now=_midday(8))
    candidate = next(item for item in created if set(item["episode_ids"]) == {first["id"], second["id"]})
    assert candidate["status"] == "qualified"
    assert candidate["policy_version"] == sagas.POLICY_VERSION
    assert candidate["score_details"]["theme_gate"] is True
    assert candidate["total_score"] >= sagas.GROUP_THRESHOLD
    assert "summary" not in candidate and "title" not in candidate
    assert sagas.generate_candidates(now=_midday(8)) == []
    stored = next(
        item for item in sagas.list_group_candidates("qualified")
        if item["grouping_fingerprint"] == candidate["grouping_fingerprint"]
    )
    assert stored["evaluation_count"] == 1
    conn = db.connect()
    try:
        after = {
            row["id"]: dict(row) for row in conn.execute(
                "SELECT * FROM memory_episodes WHERE id IN (?,?)", (first["id"], second["id"])
            ).fetchall()
        }
        assert after == before
        assert conn.execute(
            "SELECT COUNT(*) c FROM memory_sagas"
        ).fetchone()["c"] == saga_count_before
    finally:
        conn.close()


def test_shared_entity_without_common_theme_stays_observing():
    entity_id = _entity("共同人物")
    first = _episode("海边散步", "潮水和晚风很安静", 0, entity_id)
    second = _episode("修复编译器", "类型检查出现全新错误", 4, entity_id)
    assert sagas.generate_candidates(now=_midday(5)) == []
    candidate = next(
        item for item in sagas.list_group_candidates("observing")
        if set(item["episode_ids"]) == {first["id"], second["id"]}
    )
    assert candidate["score_details"]["theme_gate"] is False
    assert "summary" not in candidate


def test_observing_group_can_qualify_after_episode_correction_without_new_identity():
    entity_id = _entity("共同项目")
    first = _episode("山海漫步", "海风与潮声", 0, entity_id)
    second = _episode("修复构建", "类型系统报错", 5, entity_id)
    sagas.generate_candidates(now=_midday(6))
    low = next(
        item for item in sagas.list_group_candidates("observing")
        if set(item["episode_ids"]) == {first["id"], second["id"]}
    )
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_episodes SET title=?,summary=?,updated_at=? WHERE id IN (?,?)",
            ("共同记忆系统开发", "我们继续完善共同记忆系统开发", _midday(7),
             first["id"], second["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    promoted = sagas.generate_candidates(now=_midday(7))
    candidate = next(item for item in promoted if item["id"] == low["id"])
    assert candidate["status"] == "qualified"
    assert candidate["evaluation_count"] == 2
    assert candidate["grouping_fingerprint"] == low["grouping_fingerprint"]


def test_strong_text_theme_can_qualify_without_entity():
    first = _episode("共同记忆系统开发", "完成共同记忆系统开发和来源校验", 0)
    second = _episode("共同记忆系统开发", "完成共同记忆系统开发和来源校验", 2)
    created = sagas.generate_candidates(now=_midday(3))
    candidate = next(item for item in created if set(item["episode_ids"]) == {first["id"], second["id"]})
    assert candidate["entity_score"] == 0
    assert candidate["text_score"] == 1
    assert candidate["status"] == "qualified"


def test_existing_saga_membership_records_conflict_instead_of_reusing_episode():
    entity_id = _entity()
    first = _episode("长期项目开始", "我们持续推进同一长期项目", 0, entity_id)
    second = _episode("长期项目继续", "我们持续推进同一长期项目", 3, entity_id)
    stamp = _midday(4)
    saga_id = db.new_id()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO memory_sagas(id,title,summary,start_at,end_at,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (saga_id, "已有长期项目", "已有 Saga", first["start_at"], second["end_at"], stamp, stamp),
        )
        conn.execute(
            "INSERT INTO memory_saga_episodes(saga_id,episode_id,position,added_at)"
            " VALUES(?,?,0,?)",
            (saga_id, first["id"], stamp),
        )
        conn.commit()
    finally:
        conn.close()
    assert sagas.generate_candidates(now=stamp) == []
    conflict = next(
        item for item in sagas.list_group_candidates("conflicted")
        if set(item["episode_ids"]) == {first["id"], second["id"]}
    )
    assert conflict["conflict_reason"] == "episode_already_in_saga"


def test_archived_episode_is_not_considered_and_low_candidate_expires_safely():
    entity_id = _entity()
    active = _episode("主题甲", "完全不同的内容甲", 0, entity_id)
    archived = _episode("主题甲继续", "完全不同的内容乙", 2, entity_id, status="archived")
    assert sagas.generate_candidates(now=_midday(3)) == []
    assert all(archived["id"] not in item["episode_ids"] for item in sagas.list_group_candidates())

    other = _episode("主题乙", "毫无重合的另一段话", 3, entity_id)
    sagas.generate_candidates(now=_midday(4))
    low = next(
        item for item in sagas.list_group_candidates("observing")
        if set(item["episode_ids"]) == {active["id"], other["id"]}
    )
    sagas.generate_candidates(now=low["expires_at"] + 1)
    expired = next(
        item for item in sagas.list_group_candidates("expired")
        if item["id"] == low["id"]
    )
    assert expired["episode_ids"] == low["episode_ids"]
    assert all("summary" not in item for item in (low, expired))
