from __future__ import annotations

import hashlib

import pytest

from app import db, diary, important_dates, life_events, life_sharing, personal_goals
from app.life_events import SourceRef
from app.proactive import expression, life_adapter, orchestrator


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture(autouse=True)
def clean_life_share_rows():
    db.init_db()
    conn = db.connect()
    try:
        for table in (
            "proactive_deliveries", "expression_plans", "proactive_intensity_plans",
            "proactive_decisions", "proactive_candidate_claims", "proactive_runtime_sagas",
            "proactive_runtime_sources", "proactive_candidates", "life_proactive_seeds",
            "contact_episodes", "diary_entry_sources", "diary_entry_revisions", "diary_entries",
            "important_date_sources", "important_date_events", "important_dates",
            "personal_goal_sources", "personal_goal_events", "personal_goals",
            "life_event_sources", "life_event_audit_events", "life_event_revisions", "life_events",
            "messages", "sessions",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()
    db.set_setting("proactive_local_delivery_enabled", "0")
    db.set_setting("proactive_enabled", "1")
    yield


def _session() -> str:
    session_id = db.new_id()
    now = db.now()
    conn = db.connect()
    try:
        conn.execute("INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
                     (session_id, "life11", now, now))
        conn.commit()
    finally:
        conn.close()
    return session_id


def _event(summary: str = "完成了今天的阅读", *, world_layer: str = "simulated") -> dict:
    item, _ = life_events.create_event(
        event_kind="activity", world_layer=world_layer, summary=summary, attributes={},
        source_refs=(SourceRef("user_statement", db.new_id(), "1", _sha(summary)),),
        idempotency_key="life11:" + db.new_id(),
    )
    return item


def test_life_source_only_creates_revision_bound_seed_and_eap_source():
    session_id, event = _session(), _event()
    result = life_sharing.propose_share(
        session_id=session_id,
        request=life_sharing.ShareRequest("life_event", event["id"]),
    )
    assert result["status"] == "queued" and len(result["source_hash"]) == 64
    seed = life_adapter.get_seed(result["seed_id"])
    assert seed.seed_kind == "life_share" and seed.source_revision == str(event["revision"])
    source = next(row for row in orchestrator.list_runtime_sources() if row["id"] == result["runtime_source_id"])
    assert source["source_kind"] == "life_seed" and source["candidate_id"] is None
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM proactive_deliveries").fetchone()[0] == 0
    finally:
        conn.close()


def test_planned_or_repeated_event_never_becomes_another_share():
    session_id = _session()
    planned = _event("准备明天散步", world_layer="planned")
    with pytest.raises(life_sharing.LifeShareError) as blocked:
        life_sharing.propose_share(
            session_id=session_id, request=life_sharing.ShareRequest("life_event", planned["id"]),
        )
    assert blocked.value.code == "planned_not_shareable"
    event = _event()
    first = life_sharing.propose_share(
        session_id=session_id, request=life_sharing.ShareRequest("life_event", event["id"]),
    )
    second = life_sharing.propose_share(
        session_id=session_id, request=life_sharing.ShareRequest("life_event", event["id"]),
    )
    assert first["status"] == "queued"
    assert second == {"status": "duplicate", "reason_code": "life_source_already_shared"}


def test_diary_date_and_goal_boundaries_are_enforced_without_body_leakage():
    session_id, event = _session(), _event()
    private = diary.create_entry(
        entry_date="2026-07-26", title="私密标题", body="绝不能出现在 seed 的正文秘密",
        source_kind="life_event", source_id=event["id"], source_revision=str(event["revision"]),
        source_hash=_sha("diary-source"), share_policy="private",
    )
    with pytest.raises(life_sharing.LifeShareError) as blocked:
        life_sharing.propose_share(
            session_id=session_id, request=life_sharing.ShareRequest("diary_entry", private["id"]),
        )
    assert blocked.value.code == "diary_boundary_blocks_share"

    natural = diary.create_entry(
        entry_date="2026-07-26", title="可以分享的标题", body="仍不应进入 seed 的日记正文",
        source_kind="life_event", source_id=event["id"], source_revision=str(event["revision"]),
        source_hash=_sha("diary-source-2"), share_policy="natural",
    )
    shared = life_sharing.propose_share(
        session_id=session_id, request=life_sharing.ShareRequest("diary_entry", natural["id"]),
    )
    seed = life_adapter.get_seed(shared["seed_id"])
    assert seed.source_event_summary == "可以分享的标题"
    assert "日记正文" not in seed.source_event_summary

    date_item = important_dates.create_candidate(
        label="静默纪念日", recurrence="yearly_solar", date_year=None, date_month=7, date_day=26,
        timezone_id="Asia/Shanghai", confidence=1, source_kind="manual", source_id=db.new_id(),
        source_revision="1", source_hash=_sha("date"), celebration_policy="none",
    )
    date_item = important_dates.confirm(
        date_item["id"], expected_revision=1, date_year=None, date_month=7, date_day=26,
    )
    with pytest.raises(life_sharing.LifeShareError) as date_blocked:
        life_sharing.propose_share(
            session_id=session_id, request=life_sharing.ShareRequest("important_date", date_item["id"]),
        )
    assert date_blocked.value.code == "date_boundary_blocks_share"

    goal = personal_goals.create_candidate(
        title="尚未确认的目标", priority=2, confidence=.5, source_kind="persona",
        source_id=db.new_id(), source_revision="1", source_hash=_sha("goal"),
    )
    with pytest.raises(life_sharing.LifeShareError) as goal_blocked:
        life_sharing.propose_share(
            session_id=session_id, request=life_sharing.ShareRequest("personal_goal", goal["id"]),
        )
    assert goal_blocked.value.code == "source_unavailable"


def test_long_offline_batch_queues_one_representative_item_only():
    session_id = _session()
    requests = [life_sharing.ShareRequest("life_event", _event(f"事件 {index}")["id"]) for index in range(5)]
    results = life_sharing.propose_batch(
        session_id=session_id, requests=requests, offline_seconds=30 * 24 * 3600,
    )
    assert sum(item["status"] == "queued" for item in results) == 1
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM proactive_runtime_sources").fetchone()[0] == 1
    finally:
        conn.close()


def test_eap_builds_intensity_and_expression_plan_without_relationship_change():
    session_id, event = _session(), _event()
    queued = life_sharing.propose_share(
        session_id=session_id, request=life_sharing.ShareRequest("life_event", event["id"]),
    )
    conn = db.connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO relationship_state(id,bond,trust,interaction_count,updated_at) "
            "VALUES(1,.25,.25,0,?)", (db.now(),),
        )
        conn.commit()
        before = tuple(conn.execute("SELECT bond,trust FROM relationship_state WHERE id=1").fetchone())
    finally:
        conn.close()
    assert orchestrator.process_due(now=db.now(), worker_id="life11") == 2
    seed = life_adapter.get_seed(queued["seed_id"])
    conn = db.connect()
    try:
        decision_id = conn.execute(
            "SELECT id FROM proactive_decisions WHERE candidate_id=?", (seed.consumed_candidate_id,),
        ).fetchone()[0]
        after = tuple(conn.execute("SELECT bond,trust FROM relationship_state WHERE id=1").fetchone())
    finally:
        conn.close()
    plan = expression.get_expression_plan_by_decision(decision_id)
    assert plan is not None
    assert not any((plan.modifies_facts, plan.modifies_safety, plan.modifies_tool_results,
                    plan.modifies_permissions, plan.modifies_user_boundary))
    assert before == after
