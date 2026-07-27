from __future__ import annotations

import hashlib

import pytest

from app import db, life_events, self_timeline


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture(autouse=True)
def clean_timeline():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM self_timeline_entries")
        conn.execute("DELETE FROM diary_entry_sources")
        conn.execute("DELETE FROM diary_entry_revisions")
        conn.execute("DELETE FROM diary_entries")
        conn.execute("DELETE FROM continuity_threads")
        conn.execute("DELETE FROM life_event_candidates")
        conn.execute("DELETE FROM life_schedule_replacements")
        conn.execute("DELETE FROM life_schedule_segments")
        conn.execute("DELETE FROM life_schedules")
        conn.execute("DELETE FROM personal_goal_events")
        conn.execute("DELETE FROM personal_goal_sources")
        conn.execute("DELETE FROM personal_goals")
        conn.execute("DELETE FROM life_event_audit_events")
        conn.execute("DELETE FROM life_event_sources")
        conn.execute("DELETE FROM life_event_revisions")
        conn.execute("DELETE FROM life_events")
        conn.execute("DELETE FROM tool_logs")
        conn.commit()
    finally:
        conn.close()


def _insert(layer: str, *, source_type="life_event", source_id=None, summary=None):
    source_id = source_id or layer
    summary = summary or f"{layer} synthetic"
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO self_timeline_entries(id,source_type,source_id,source_revision,world_layer,"
            "source_status,occurred_at,summary,source_locator,content_hash,indexed_at) "
            "VALUES(?,?,?,?,?,'active',?,?,?,?,?)",
            (db.new_id(), source_type, source_id, "1", layer, 100.0, summary,
             f"/source/{source_id}", _hash(summary), 100.0),
        )
        conn.commit()
    finally:
        conn.close()


def test_schema_71_adds_unified_provenance_projection():
    conn = db.connect()
    try:
        version = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='self_timeline_entries'").fetchone()[0]
    finally:
        conn.close()
    assert version == "73"
    for source_type in (
        "life_event", "diary_entry", "schedule_segment", "tool_run",
        "proactive_delivery", "personal_goal",
    ):
        assert source_type in sql


def test_epistemic_expression_v1_keeps_all_layers_distinct():
    cases = {
        "planned": "原本打算",
        "simulated": "在自己的日程里",
        "inferred": "大概按原来的节奏",
        "observed": "根据留下的记录",
        "performed": "确实完成",
    }
    for layer, prefix in cases.items():
        item = {
            "world_layer": layer, "summary": "合成记录",
            "source_type": "tool_run" if layer == "performed" else "life_event",
        }
        assert self_timeline.epistemic_expression(item).startswith(prefix)


def test_refresh_indexes_planned_event_as_planned_and_done_tool_as_performed():
    source = life_events.SourceRef("system_observation", "plan", "1", _hash("plan"))
    planned = life_events.create_event(
        event_kind="activity", world_layer="planned", summary="去散步", source_refs=(source,),
        idempotency_key=life_events.make_idempotency_key(
            event_kind="activity", source_refs=(source,), semantic_key="plan",
        ),
    )[0]
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO tool_logs(id,tool,risk_level,status,summary,created_at) VALUES(?,?,?,?,?,?)",
            ("tool-1", "local", "S0", "done", "保存了本地文件", 200.0),
        )
        conn.commit()
    finally:
        conn.close()
    assert self_timeline.refresh(now=300.0) == 2
    items = self_timeline.search("你最近做过什么")
    by_source = {(item["source_type"], item["source_id"]): item for item in items}
    assert by_source[("life_event", planned["id"])]["world_layer"] == "planned"
    assert by_source[("tool_run", "tool-1")]["world_layer"] == "performed"


def test_context_injects_only_for_relevant_question_and_is_bounded():
    for index in range(10):
        _insert("observed", source_id=f"event-{index}", summary="合成生活记录 " + ("x" * 300))
    assert self_timeline.context_block("帮我写一段代码") == ""
    block = self_timeline.context_block("你最近做过什么")
    assert block.startswith("[SelfTimeline / epistemic-expression-v1]")
    assert block.count("\n-") <= 5 and len(block) <= 1_200


def test_no_record_is_explicit_and_never_fabricates():
    assert self_timeline.context_block("你今天做过什么") == (
        "[SelfTimeline] 没有可靠记录；不要编造角色做过的事情。"
    )


def test_source_locator_and_projection_deletion_are_available():
    _insert("observed", source_id="deletable")
    item = self_timeline.search("你经历了什么")[0]
    assert item["source_locator"] == "/source/deletable"
    assert self_timeline.delete_projection(source_type="life_event", source_id="deletable") == 1
    assert self_timeline.search("你经历了什么") == []
