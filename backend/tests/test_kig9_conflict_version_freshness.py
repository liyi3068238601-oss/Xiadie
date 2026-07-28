import asyncio
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import db, kig_governance as governance, kig_retrieval as retrieval, kig_sources, llm
from app.main import app

client = TestClient(app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"})


def _candidate(
    text: str, *, version: str | None = None, occurred_at: float | None = None,
    authority: str = "user_statement",
):
    now = db.now()
    session_id, message_id = db.new_id(), db.new_id()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO sessions(id,title,archived,created_at,updated_at) VALUES(?,?,?,?,?)",
            (session_id, "KIG version", 0, now, now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (message_id, session_id, "user", text, occurred_at or now),
        )
        conn.commit()
    finally:
        conn.close()
    ref = kig_sources.registry.resolve("message", message_id)
    return retrieval._candidate(
        source="history", ref=ref, excerpt=text, lexical_score=1.0,
        vector_score=None, occurred_at=occurred_at or now, authority=authority,
        metadata={"version": version} if version else {},
    )


def _governed(candidate):
    return governance.adapt_candidate(candidate)


def test_schema_76_version_governance_is_body_free_and_revisioned():
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "80"
        relation_columns = {row["name"] for row in conn.execute(
            "PRAGMA table_info(kig_version_relations)"
        )}
        source_columns = {row["name"] for row in conn.execute(
            "PRAGMA table_info(kig_source_governance)"
        )}
    finally:
        conn.close()
    assert {"relation", "decision_source", "requires_confirmation", "relation_revision"} <= relation_columns
    assert {"authority_level", "scope_json", "user_confirmed", "governance_revision"} <= source_columns
    assert not ({"content", "body", "excerpt", "source_body"} & (relation_columns | source_columns))


def test_hash_and_semver_rules_precede_semantic_judgement():
    first = _candidate("相同设计 v1.0", version="1.0")
    second = _candidate("相同设计 v1.0", version="1.0")
    duplicate = governance.deterministic_relation(_governed(first), _governed(second))
    assert duplicate and duplicate.relation == "exact_duplicate"
    assert duplicate.reason_codes == ("same_content_hash",)

    old = _candidate("星河 API 版本 1.2", version="1.2")
    new = _candidate("星河 API 版本 2.0", version="2.0")
    relation = governance.deterministic_relation(_governed(new), _governed(old))
    assert relation and relation.relation == "supersedes"
    assert relation.older_id == old.candidate_id and relation.newer_id == new.candidate_id
    assert relation.reason_codes == ("semantic_version_order",)

    unrelated_old = _candidate("Alpha API 版本 1.0", version="1.0")
    unrelated_new = _candidate("Beta API 版本 2.0", version="2.0")
    assert governance.deterministic_relation(
        _governed(unrelated_old), _governed(unrelated_new),
    ) is None


def test_distinct_time_or_conditions_are_compatible_not_conflicts():
    morning = _candidate("早上喜欢咖啡")
    evening = _candidate("晚上不喜欢咖啡")
    governance.upsert_source_governance(
        kig_sources.registry.resolve("message", morning.source_id),
        authority_level="imported_source", scope={"qualifiers": ["morning"]},
    )
    governance.upsert_source_governance(
        kig_sources.registry.resolve("message", evening.source_id),
        authority_level="imported_source", scope={"qualifiers": ["evening"]},
    )
    relation = governance.deterministic_relation(_governed(morning), _governed(evening))
    assert relation and relation.relation == "compatible_with_conditions"
    assert relation.requires_confirmation is False


def test_recency_alone_never_claims_newer_is_correct_without_same_scope():
    older = _candidate("当前使用 Electron", occurred_at=db.now() - 10_000)
    newer = _candidate("未来评估 Tauri", occurred_at=db.now())
    assert governance.deterministic_relation(_governed(older), _governed(newer)) is None


def test_user_correction_and_confirmed_authority_win_precedence():
    ordinary = _candidate("项目使用旧方案", occurred_at=db.now())
    correction = _candidate("用户纠正：项目使用新方案", occurred_at=db.now() - 100)
    governance.upsert_source_governance(
        kig_sources.registry.resolve("message", correction.source_id),
        authority_level="user_correction", scope={"topic": "project-plan"},
        user_confirmed=True,
    )
    corrected = _governed(correction)
    normal = _governed(ordinary)
    assert corrected.authority_priority > normal.authority_priority
    relation = governance.VersionRelationResult(
        action="select", selected_ids=(correction.candidate_id,), relation="contradicts",
        older_id=ordinary.candidate_id, newer_id=correction.candidate_id,
        scope_terms=("project-plan",), reason_codes=("semantic_relation",),
        confidence_band="high", requires_confirmation=False, proposal_only=True,
    )
    assessment = governance.assess_freshness((normal, corrected), (relation,))
    assert assessment.preferred_ids[0] == correction.candidate_id


def test_high_impact_conflict_cannot_bypass_confirmation():
    left, right = _candidate("生产环境允许删除"), _candidate("生产环境禁止删除")
    payload = governance.build_pair_input(
        left, right, request_id=db.new_id(), query="生产环境删除权限冲突",
    )
    unsafe = governance.VersionRelationResult(
        action="select", selected_ids=(right.candidate_id,), relation="contradicts",
        older_id=left.candidate_id, newer_id=right.candidate_id, scope_terms=(),
        reason_codes=("semantic_relation",), confidence_band="high",
        requires_confirmation=False, proposal_only=True,
    )
    with pytest.raises(Exception) as caught:
        governance.validate_result(payload, unsafe)
    assert getattr(caught.value, "code", "") == "confirmation_required"
    safe = replace(unsafe, requires_confirmation=True)
    governance.validate_result(payload, safe)
    assessment = governance.assess_freshness(payload.sources, (safe,))
    assert assessment.confirmation_required_pairs == ((left.candidate_id, right.candidate_id),)


def test_llm_semantic_relation_remains_shadow_proposal(monkeypatch):
    left, right = _candidate("星河采用方案甲"), _candidate("星河采用方案乙")
    payload = governance.build_pair_input(
        left, right, request_id=db.new_id(), query="比较两个星河方案",
    )
    result = governance.VersionRelationResult(
        action="select", selected_ids=(right.candidate_id,), relation="divergent_branch",
        older_id=left.candidate_id, newer_id=right.candidate_id, scope_terms=("星河",),
        reason_codes=("semantic_relation",), confidence_band="medium",
        requires_confirmation=False, proposal_only=True,
    )

    async def complete(*_args, **_kwargs):
        return {"text": json.dumps({
            **result.__dict__, "selected_ids": list(result.selected_ids),
            "scope_terms": list(result.scope_terms), "reason_codes": list(result.reason_codes),
        }, ensure_ascii=False), "latency_ms": 1, "prompt_tokens": 10, "completion_tokens": 10}

    monkeypatch.setattr(llm, "complete_json", complete)
    outcome = asyncio.run(governance.propose_semantic_relation(
        payload, provider={
            "id": "mock", "execution_location": "local", "location_revision": 1,
        }, model="xiadie-mock", remote_authorized=True,
    ))
    assert outcome["model_called"] is True
    assert outcome["proposal"].relation == "divergent_branch"
    assert outcome["proposal"].proposal_only is True
    assert outcome["outcome"]["application_allowed"] is False


def test_relation_persistence_requires_user_resolution_and_revision_match():
    left, right = _candidate("生产配置 A"), _candidate("生产配置 B")
    payload = governance.build_pair_input(
        left, right, request_id=db.new_id(), query="生产配置冲突",
    )
    proposal = governance.VersionRelationResult(
        action="select", selected_ids=(right.candidate_id,), relation="contradicts",
        older_id=left.candidate_id, newer_id=right.candidate_id, scope_terms=("production",),
        reason_codes=("semantic_relation",), confidence_band="high",
        requires_confirmation=True, proposal_only=True,
    )
    stored = governance.persist_relation(proposal, payload)
    assert stored["status"] == "proposed" and stored["requires_confirmation"] == 1
    conn = db.connect()
    try:
        dependencies = conn.execute(
            "SELECT * FROM derived_dependencies WHERE derived_kind='version_relation' "
            "AND derived_id=?", (stored["id"],),
        ).fetchall()
    finally:
        conn.close()
    assert len(dependencies) == 2 and all(row["dependency_status"] == "active" for row in dependencies)
    with pytest.raises(ValueError, match="relation_conflict"):
        governance.resolve_relation(stored["id"], accept=True, expected_revision=999)
    confirmed = governance.resolve_relation(
        stored["id"], accept=True, expected_revision=stored["relation_revision"],
    )
    assert confirmed["status"] == "confirmed"
    assert confirmed["decision_source"] == "user_confirmed"
    assert confirmed["relation_revision"] == stored["relation_revision"] + 1


def test_expiry_and_partial_supersession_are_visible_freshness_states():
    old = _candidate("旧政策", occurred_at=db.now() - 1_000)
    new = _candidate("新政策", occurred_at=db.now())
    governance.upsert_source_governance(
        kig_sources.registry.resolve("message", old.source_id),
        authority_level="imported_source", applicable_to=db.now() - 10,
    )
    sources = (_governed(old), _governed(new))
    relation = governance.VersionRelationResult(
        action="select", selected_ids=(new.candidate_id,), relation="partially_supersedes",
        older_id=old.candidate_id, newer_id=new.candidate_id, scope_terms=(),
        reason_codes=("authority_and_date_order",), confidence_band="medium",
        requires_confirmation=False, proposal_only=True,
    )
    assessment = governance.assess_freshness(sources, (relation,))
    assert assessment.states[old.candidate_id] == "expired"
    assert new.candidate_id in assessment.preferred_ids


def test_governance_snapshot_is_ignored_after_owner_source_changes():
    candidate = _candidate("原始版本 1.0")
    ref = kig_sources.registry.resolve("message", candidate.source_id)
    governance.upsert_source_governance(
        ref, authority_level="user_confirmed_authoritative", version_label="1.0",
        user_confirmed=True,
    )
    assert governance.source_governance("message", candidate.source_id) is not None
    conn = db.connect()
    try:
        conn.execute("UPDATE messages SET content='来源发生变化' WHERE id=?", (candidate.source_id,))
        conn.commit()
    finally:
        conn.close()
    assert governance.source_governance("message", candidate.source_id) is None


def test_governance_api_requires_explicit_user_confirmation_and_resolves_conflicts():
    candidate = _candidate("用户权威版本 3.0")
    denied = client.put(
        f"/api/kig/governance/sources/message/{candidate.source_id}",
        json={
            "authority_level": "user_confirmed_authoritative", "scope": {},
            "version_label": "3.0", "user_confirmed": False,
        },
    )
    assert denied.status_code == 409
    accepted = client.put(
        f"/api/kig/governance/sources/message/{candidate.source_id}",
        json={
            "authority_level": "user_confirmed_authoritative",
            "scope": {"topic": "api"}, "version_label": "3.0",
            "user_confirmed": True,
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["governance"]["user_confirmed"] == 1

    left, right = _candidate("生产方案甲"), _candidate("生产方案乙")
    payload = governance.build_pair_input(
        left, right, request_id=db.new_id(), query="生产方案冲突",
    )
    relation = governance.persist_relation(governance.VersionRelationResult(
        action="select", selected_ids=(right.candidate_id,), relation="divergent_branch",
        older_id=left.candidate_id, newer_id=right.candidate_id, scope_terms=(),
        reason_codes=("semantic_relation",), confidence_band="medium",
        requires_confirmation=True, proposal_only=True,
    ), payload)
    pending = client.get("/api/kig/governance/version-relations?status=proposed")
    assert pending.status_code == 200
    assert relation["id"] in {item["id"] for item in pending.json()["relations"]}
    resolved = client.post(
        f"/api/kig/governance/version-relations/{relation['id']}/resolve",
        json={"accept": False, "expected_revision": relation["relation_revision"]},
    )
    assert resolved.status_code == 200
    assert resolved.json()["relation"]["status"] == "rejected"
