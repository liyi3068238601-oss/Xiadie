import pytest
from fastapi.testclient import TestClient

from app import db, entities, episodes, memory
from app.main import app


client = TestClient(
    app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"}
)


@pytest.fixture(autouse=True)
def clean_scored_candidates():
    conn = db.connect()
    try:
        before_fragment_ids = {
            row["id"] for row in conn.execute("SELECT id FROM memory_fragments").fetchall()
        }
        before_episode_ids = {
            row["id"] for row in conn.execute("SELECT id FROM memory_episodes").fetchall()
        }
        conn.execute("DELETE FROM episode_group_candidates")
        conn.execute(
            "DELETE FROM memory_episode_candidate_fragments WHERE candidate_id IN ("
            "SELECT id FROM memory_episode_candidates WHERE policy_version=?)",
            (episodes.GROUP_POLICY_VERSION,),
        )
        conn.execute(
            "DELETE FROM memory_episode_candidates WHERE policy_version=?",
            (episodes.GROUP_POLICY_VERSION,),
        )
        conn.commit()
    finally:
        conn.close()
    yield
    conn = db.connect()
    try:
        new_fragment_ids = [
            row["id"] for row in conn.execute("SELECT id FROM memory_fragments").fetchall()
            if row["id"] not in before_fragment_ids
        ]
        new_episode_ids = [
            row["id"] for row in conn.execute("SELECT id FROM memory_episodes").fetchall()
            if row["id"] not in before_episode_ids
        ]
        if new_episode_ids:
            placeholders = ",".join("?" for _ in new_episode_ids)
            conn.execute(
                f"DELETE FROM memory_episodes WHERE id IN ({placeholders})", new_episode_ids
            )
        conn.execute("DELETE FROM episode_group_candidates")
        conn.execute(
            "DELETE FROM memory_episode_candidate_fragments WHERE candidate_id IN ("
            "SELECT id FROM memory_episode_candidates WHERE policy_version=?)",
            (episodes.GROUP_POLICY_VERSION,),
        )
        conn.execute(
            "DELETE FROM memory_episode_candidates WHERE policy_version=?",
            (episodes.GROUP_POLICY_VERSION,),
        )
        if new_fragment_ids:
            placeholders = ",".join("?" for _ in new_fragment_ids)
            conn.execute(
                f"DELETE FROM memory_fragment_entities WHERE fragment_id IN ({placeholders})",
                new_fragment_ids,
            )
            conn.execute(
                f"DELETE FROM memory_fragments WHERE id IN ({placeholders})",
                new_fragment_ids,
            )
        conn.commit()
    finally:
        conn.close()


def _entity(name: str | None = None) -> dict:
    return entities.create_entity(name or f"评分实体-{db.new_id()}", "event")


def _fragment(
    content: str, created_at: float, entity_id: str | None, *,
    emotion: str = "neutral", scope: str = "relationship", kind: str = "experience",
) -> dict:
    item = memory.create_memory("L1", content)
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET created_at=?,updated_at=?,emotion=?,scope=?,kind=?"
            " WHERE id=?",
            (created_at, created_at, emotion, scope, kind, item["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    if entity_id:
        assert entities.link_fragment(entity_id, item["id"], source="test")
    return memory.get_memory(item["id"])


def _rewrite(fragment_ids: list[str], *, created_at: float, content: str) -> None:
    conn = db.connect()
    try:
        for fragment_id in fragment_ids:
            conn.execute(
                "UPDATE memory_fragments SET created_at=?,updated_at=?,content=?,"
                "emotion='joy',scope='relationship',kind='experience' WHERE id=?",
                (created_at, created_at, content, fragment_id),
            )
        conn.commit()
    finally:
        conn.close()


def test_weight_formula_and_exact_threshold_are_stable():
    scores = episodes.combine_scores(entity=1.0, text=0.2, time=0.0, coherence=0.5)
    assert scores == {
        "entity": 1.0, "text": 0.2, "time": 0.0, "coherence": 0.5, "total": 0.5,
    }
    assert scores["total"] >= episodes.GROUP_THRESHOLD
    assert episodes.combine_scores(1, 0.199, 0, 0.5)["total"] < episodes.GROUP_THRESHOLD


def test_group_size_and_seven_day_boundaries():
    base = 1_000_000.0
    template = {
        "content": "同一段经历", "emotion": "neutral", "scope": "relationship",
        "kind": "experience",
    }
    twenty = [
        {"id": f"f-{index}", "created_at": base + index, **template}
        for index in range(20)
    ]
    entity_map = {item["id"]: {"entity"} for item in twenty}
    assert episodes.score_group(twenty, entity_map)["total"] >= 0.5
    with pytest.raises(ValueError, match="2 到 20"):
        episodes.score_group(twenty + [{"id": "f-20", "created_at": base + 20, **template}], {
            **entity_map, "f-20": {"entity"},
        })
    edge = [
        {"id": "left", "created_at": base, **template},
        {"id": "right", "created_at": base + episodes.WINDOW_SECONDS, **template},
    ]
    assert episodes.score_group(edge, {"left": {"entity"}, "right": {"entity"}})["time"] == 0
    edge[1]["created_at"] += 0.001
    with pytest.raises(ValueError, match="7 天"):
        episodes.score_group(edge, {"left": {"entity"}, "right": {"entity"}})


def test_no_shared_entity_creates_no_group():
    now = db.now()
    first_entity, second_entity = _entity(), _entity()
    first = _fragment("完全不同的甲事件", now - 10, first_entity["id"])
    second = _fragment("完全不同的乙事件", now, second_entity["id"])
    own_ids = {first["id"], second["id"]}
    created = episodes.generate_candidates(now=now)
    assert all(
        not own_ids <= {fragment["id"] for fragment in candidate["fragments"]}
        for candidate in created
    )
    assert all(
        not own_ids <= set(group["fragment_ids"])
        for group in episodes.list_group_candidates()
    )


def test_high_score_candidate_persists_components_and_is_idempotent():
    now = db.now()
    entity = _entity()
    first = _fragment("共同旅行计划开始", now - 60, entity["id"], emotion="joy")
    second = _fragment("共同旅行计划开始执行", now, entity["id"], emotion="joy")
    created = episodes.generate_candidates(now=now)
    candidate = next(
        item for item in created
        if {fragment["id"] for fragment in item["fragments"]} == {first["id"], second["id"]}
    )
    assert candidate["policy_version"] == episodes.GROUP_POLICY_VERSION
    assert candidate["confidence"] == candidate["score_details"]["total"]
    assert candidate["confidence"] >= episodes.GROUP_THRESHOLD
    assert len(candidate["grouping_key"]) == 64
    assert episodes.generate_candidates(now=now) == []
    accepted = episodes.accept_candidate(candidate["id"])
    assert accepted is not None
    assert episodes.generate_candidates(now=now) == []
    assert episodes.list_group_candidates() == []


def test_low_score_rechecks_without_extending_expiry_then_expires_safely():
    now = db.now()
    entity = _entity()
    first = _fragment(
        "甲乙丙丁完全不同", now - episodes.WINDOW_SECONDS, entity["id"],
        emotion="sadness", scope="self", kind="fact",
    )
    second = _fragment(
        "戊己庚辛毫不相似", now, entity["id"],
        emotion="joy", scope="relationship", kind="experience",
    )
    own_ids = {first["id"], second["id"]}
    created = episodes.generate_candidates(now=now)
    assert all(
        own_ids != {fragment["id"] for fragment in candidate["fragments"]}
        for candidate in created
    )
    low = next(
        group for group in episodes.list_group_candidates()
        if set(group["fragment_ids"]) == own_ids
    )
    assert low["total_score"] < episodes.GROUP_THRESHOLD
    assert low["fragment_ids"] == [first["id"], second["id"]]
    assert "content" not in low
    original_expiry = low["expires_at"]

    episodes.generate_candidates(now=now)
    rechecked = next(
        group for group in episodes.list_group_candidates()
        if set(group["fragment_ids"]) == own_ids
    )
    assert rechecked["evaluation_count"] == 2
    assert rechecked["expires_at"] == original_expiry

    episodes.generate_candidates(now=original_expiry + 1)
    expired = next(
        group for group in episodes.list_group_candidates("expired")
        if set(group["fragment_ids"]) == own_ids
    )
    assert expired["id"] == low["id"]
    assert memory.get_memory(first["id"])["status"] == "active"
    assert memory.get_memory(second["id"])["status"] == "active"


def test_same_low_group_can_qualify_after_fact_fields_are_corrected():
    now = db.now()
    entity = _entity()
    first = _fragment(
        "低分甲内容", now - episodes.WINDOW_SECONDS, entity["id"],
        emotion="sadness", scope="self", kind="fact",
    )
    second = _fragment(
        "低分乙内容", now, entity["id"],
        emotion="joy", scope="relationship", kind="experience",
    )
    episodes.generate_candidates(now=now)
    low = episodes.list_group_candidates()[0]

    _rewrite([first["id"], second["id"]], created_at=now, content="纠正后的共同经历")
    promoted = episodes.generate_candidates(now=now)
    assert len(promoted) == 1
    qualified = episodes.list_group_candidates("qualified")[0]
    assert qualified["id"] == low["id"]
    assert qualified["promoted_candidate_id"] == promoted[0]["id"]


def test_new_fragment_supersedes_overlapping_low_group_and_maxes_at_twenty():
    now = db.now()
    entity = _entity()
    first = _fragment(
        "山川湖海无边", now - episodes.WINDOW_SECONDS, entity["id"],
        emotion="sadness", scope="self", kind="fact",
    )
    second = _fragment(
        "量子星云旋转", now, entity["id"],
        emotion="joy", scope="relationship", kind="experience",
    )
    episodes.generate_candidates(now=now)
    old = episodes.list_group_candidates()[0]
    _rewrite([first["id"], second["id"]], created_at=now, content="同一重要经历")
    third = _fragment("同一重要经历", now, entity["id"], emotion="joy")
    created = episodes.generate_candidates(now=now)
    assert len(created) == 1
    assert {fragment["id"] for fragment in created[0]["fragments"]} == {
        first["id"], second["id"], third["id"],
    }
    assert episodes.list_group_candidates("superseded")[0]["id"] == old["id"]

    other_entity = _entity()
    many = [
        _fragment(f"二十条共同经历 {index}", now + index, other_entity["id"], emotion="joy")
        for index in range(21)
    ]
    groups = episodes.generate_candidates(now=now + 30)
    selected = next(
        item for item in groups if many[0]["id"] in {fragment["id"] for fragment in item["fragments"]}
    )
    assert len(selected["fragments"]) == episodes.MAX_GROUP_SIZE


def test_low_group_read_api_is_read_only_and_validates_status():
    assert client.get("/api/episode-group-candidates?status=observing").status_code == 200
    assert client.get("/api/episode-group-candidates?status=pending").status_code == 400
