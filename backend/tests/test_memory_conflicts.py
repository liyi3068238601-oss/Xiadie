"""E.6 冲突关系：保守预筛、可追溯处置及生命周期详情。"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db, entities, memory, memory_conflicts
from app.main import app

client = TestClient(
    app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"}
)


@pytest.fixture
def conflict_objects():
    marker = db.new_id()
    entity = entities.create_entity(f"冲突实体{marker}", "concept")
    fragment_ids: list[str] = []

    def make(content: str, *, scope: str = "user", kind: str = "preference") -> dict:
        item = memory.create_memory("L1", content)
        fragment_ids.append(item["id"])
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE memory_fragments SET scope=?,kind=?,created_at=created_at+? WHERE id=?",
                (scope, kind, len(fragment_ids), item["id"]),
            )
            conn.commit()
        finally:
            conn.close()
        entities.link_fragment(entity["id"], item["id"], source="test")
        return item

    yield entity, make
    conn = db.connect()
    try:
        conn.execute("DELETE FROM memory_entities WHERE id=?", (entity["id"],))
        for fragment_id in fragment_ids:
            conn.execute("DELETE FROM memory_fragments WHERE id=?", (fragment_id,))
        conn.commit()
    finally:
        conn.close()


def test_schema_27_relation_audit_has_no_memory_body():
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()["value"] == "39"
        relation_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(memory_fragment_relations)")
        }
        assert {"detector_version", "model_version", "confidence", "rule_code"} <= relation_columns
        event_columns = {
            row["name"] for row in conn.execute(
                "PRAGMA table_info(memory_fragment_relation_events)"
            )
        }
        assert not ({"content", "summary", "raw_output"} & event_columns)
        with pytest.raises(sqlite3.IntegrityError):
            now = db.now()
            conn.execute(
                "INSERT INTO memory_fragment_relations VALUES(?,?,?,?,?,'active',1,'r','v',NULL,?,?)",
                (db.new_id(), "same", "same", "missing", "superseded", now, now),
            )
    finally:
        conn.close()


def test_explicit_negation_creates_directional_superseded_without_mutation(conflict_objects):
    _entity, make = conflict_objects
    older = make("用户喜欢咖啡")
    newer = make("用户不喜欢咖啡")
    before = {item["id"]: (item["content"], item["status"]) for item in memory.list_memories()}

    result = memory_conflicts.scan_conflicts(limit=50)
    relations = memory_conflicts.list_relations(status="active")
    relation = next(item for item in relations if item["source_fragment_id"] == older["id"])

    assert result["superseded_count"] == 1
    assert relation["target_fragment_id"] == newer["id"]
    assert relation["relation_type"] == "superseded"
    assert relation["model_version"] is None
    assert relation["events"][0]["source"] == "archivist"
    after = {item["id"]: (item["content"], item["status"]) for item in memory.list_memories()}
    assert after[older["id"]] == before[older["id"]]
    assert after[newer["id"]] == before[newer["id"]]
    assert memory_conflicts.scan_conflicts(limit=50)["created_count"] == 0


def test_scope_kind_prefilter_and_user_disposition(conflict_objects):
    _entity, make = conflict_objects
    make("用户计划周末去公园", scope="user", kind="plan")
    make("用户计划周末去公园散步", scope="world", kind="plan")
    make("用户计划周末去公园散步", scope="user", kind="experience")
    first = make("用户计划周末去公园", scope="relationship", kind="plan")
    second = make("用户计划周末去公园散步", scope="relationship", kind="plan")

    result = memory_conflicts.scan_conflicts(limit=50)
    assert result["possible_conflict_count"] == 1
    relation = next(
        item for item in memory_conflicts.list_relations(status="active")
        if item["source_fragment_id"] == first["id"] and item["target_fragment_id"] == second["id"]
    )
    response = client.post(
        f"/api/memory-relations/{relation['id']}/status",
        json={"status": "dismissed", "reason": "两条计划并不矛盾"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "dismissed"
    assert response.json()["events"][-1]["source"] == "user"


def test_lifecycle_detail_includes_score_events_and_relations(conflict_objects):
    _entity, make = conflict_objects
    older = make("用户喜欢红茶")
    make("用户不喜欢红茶")
    memory_conflicts.scan_conflicts(limit=50)

    response = client.get(f"/api/memories/{older['id']}/lifecycle")
    assert response.status_code == 200
    body = response.json()
    assert body["fragment"]["id"] == older["id"]
    assert body["evaluation"]["fragment_id"] == older["id"]
    assert "components" in body["evaluation"]
    assert body["relations"][0]["relation_type"] == "superseded"


def test_normalization_similarity_edge_inputs_are_predictable():
    assert memory_conflicts._normalize(" 123-45 ") == "12345"
    assert memory_conflicts._similarity("甲", "乙") == 0.0
    assert memory_conflicts._similarity("甲", "甲") == 1.0
    assert memory_conflicts._classify("like tea", "don't like tea")[0] == "possible_conflict"
