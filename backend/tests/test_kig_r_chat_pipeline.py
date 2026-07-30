from dataclasses import replace

from fastapi.testclient import TestClient

from app import (
    db, kig_pipeline as pipeline, kig_retrieval as retrieval, kig_sources, llm,
)
from app.main import app

client = TestClient(app, headers={"X-Xiadie-Token": "test-token-with-at-least-thirty-two-bytes"})


def _candidate(
    text: str, *, source="history", source_kind="message", version: str | None = None,
    privacy="private", occurred_at: float | None = None,
):
    now = occurred_at or db.now()
    session_id, message_id = db.new_id(), db.new_id()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO sessions(id,title,archived,created_at,updated_at) VALUES(?,?,?,?,?)",
            (session_id, "KIG-R", 0, now, now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (message_id, session_id, "assistant", text, now),
        )
        conn.commit()
    finally:
        conn.close()
    ref = kig_sources.registry.resolve("message", message_id)
    candidate = retrieval._candidate(
        source="history", ref=ref, excerpt=text, lexical_score=1.0,
        vector_score=None, occurred_at=now, authority="recorded_conversation",
        metadata={"version": version} if version else {},
    )
    if source == "history" and source_kind == "message" and privacy == "private":
        return candidate
    return replace(candidate, source=source, source_type=source_kind, privacy_scope=privacy)


def _batch(*items):
    return retrieval.RetrievalBatch(
        candidates=tuple(items), diagnostics={
            source: {"candidate_count": sum(item.source == source for item in items)}
            for source in {item.source for item in items}
        }, failed_sources=(), lexical_fallback_sources=(),
    )


def test_pipeline_applies_only_deterministic_freshness_and_keeps_model_shadow(monkeypatch):
    old = _candidate("星河 API 版本 1.0", version="1.0", occurred_at=db.now() - 100)
    new = _candidate("星河 API 版本 2.0", version="2.0", occurred_at=db.now())
    monkeypatch.setattr(retrieval, "retrieve", lambda _request: _batch(old, new))
    result = pipeline.prepare_for_chat(
        query="综合多个来源比较星河版本", source_message_id=db.new_id(),
        session_id=db.new_id(), provider={"execution_location": "local"},
        recall_mode="explicit",
    )
    assert result is not None
    assert result.protocol_version == "kig-retrieval-governance-v1"
    assert result.plan.bypassed_model is True
    assert result.deterministic_relation_count >= 1
    assert result.freshness.states[old.candidate_id] == "superseded"
    assert old.candidate_id not in result.selected_candidate_ids
    assert new.candidate_id in result.selected_candidate_ids
    assert [item.source_id for item in result.bundle.selected_evidence] == [new.source_id]


def test_pipeline_persists_body_free_deterministic_relations_after_chat_boundary(monkeypatch):
    old = _candidate("星河服务版本 1.1", version="1.1")
    new = _candidate("星河服务版本 1.2", version="1.2")
    monkeypatch.setattr(retrieval, "retrieve", lambda _request: _batch(old, new))
    result = pipeline.prepare_for_chat(
        query="综合多个来源比较星河版本", source_message_id=db.new_id(),
        session_id=db.new_id(), provider={"execution_location": "local"},
        recall_mode="explicit",
    )
    assert result and pipeline.persist_deterministic_relations(result) >= 1
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM kig_version_relations WHERE older_source_id=? AND newer_source_id=?",
            (old.source_id, new.source_id),
        ).fetchone()
        dependencies = conn.execute(
            "SELECT * FROM derived_dependencies WHERE derived_kind='version_relation' "
            "AND derived_id=?", (row["id"],),
        ).fetchall()
    finally:
        conn.close()
    assert row["decision_source"] == "deterministic" and row["status"] == "confirmed"
    assert len(dependencies) == 2
    assert not hasattr(result.bundle.selected_evidence[0], "body")


def test_current_pair_relation_is_not_hidden_by_unrelated_newer_rows():
    from app import kig_governance

    old = _candidate("星河服务版本 1.0", version="1.0")
    new = _candidate("星河服务版本 2.0", version="2.0")
    governed = (kig_governance.adapt_candidate(old), kig_governance.adapt_candidate(new))
    relation = kig_governance.deterministic_relation(*governed)
    assert relation is not None
    payload = kig_governance.VersionRelationInput(
        candidate_ids=(governed[0].candidate_id, governed[1].candidate_id),
        request_id=db.new_id(), query="deterministic version governance",
        sources=governed, impact_level="medium",
    )
    stored = kig_governance.persist_relation(relation, payload)

    conn = db.connect()
    try:
        conn.execute(
            "UPDATE kig_version_relations SET updated_at=1 WHERE id=?", (stored["id"],),
        )
        for index in range(201):
            conn.execute(
                "INSERT INTO kig_version_relations("
                "id,older_source_kind,older_source_id,older_source_revision,older_source_hash,"
                "newer_source_kind,newer_source_id,newer_source_revision,newer_source_hash,"
                "relation,scope_json,confidence,evidence_refs_json,decision_source,impact_level,"
                "requires_confirmation,status,relation_revision,created_at,updated_at,confirmed_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    db.new_id(), "message", f"unrelated-old-{index}", "1", "0" * 64,
                    "message", f"unrelated-new-{index}", "1", "1" * 64,
                    "supersedes", "{}", 1.0, "[]", "deterministic", "medium",
                    0, "confirmed", 1, index + 2, index + 2, index + 2,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    applied, proposed = pipeline._persisted_relations(governed)  # noqa: SLF001
    assert proposed == []
    assert len(applied) == 1
    assert applied[0].older_id == governed[0].candidate_id
    assert applied[0].newer_id == governed[1].candidate_id


def test_distinct_conditions_remain_both_visible_and_do_not_create_conflict(monkeypatch):
    morning = _candidate("早上喜欢咖啡")
    evening = _candidate("晚上不喜欢咖啡")
    from app import kig_governance
    kig_governance.upsert_source_governance(
        kig_sources.registry.resolve("message", morning.source_id),
        authority_level="imported_source", scope={"qualifiers": ["morning"]},
    )
    kig_governance.upsert_source_governance(
        kig_sources.registry.resolve("message", evening.source_id),
        authority_level="imported_source", scope={"qualifiers": ["evening"]},
    )
    monkeypatch.setattr(retrieval, "retrieve", lambda _request: _batch(morning, evening))
    result = pipeline.prepare_for_chat(
        query="综合所有来源比较咖啡习惯", source_message_id=db.new_id(),
        session_id=db.new_id(), provider={"execution_location": "local"},
        recall_mode="explicit",
    )
    assert result and set(result.selected_candidate_ids) == {morning.candidate_id, evening.candidate_id}
    assert not result.freshness.conflict_pairs
    assert "version_conflict_unresolved" not in result.bundle.conflict_notes


def test_disabled_and_ambiguous_queries_preserve_existing_chat_behavior(monkeypatch):
    calls = 0

    def retrieve(_request):
        nonlocal calls
        calls += 1
        return _batch()

    monkeypatch.setattr(retrieval, "retrieve", retrieve)
    assert pipeline.prepare_for_chat(
        query="综合所有来源", source_message_id=db.new_id(), session_id=db.new_id(),
        provider={"execution_location": "local"}, recall_mode="off",
    ) is None
    assert pipeline.prepare_for_chat(
        query="上次说的那个", source_message_id=db.new_id(), session_id=db.new_id(),
        provider={"execution_location": "local"}, recall_mode="explicit",
    ) is None
    assert calls == 0


def test_casual_chat_invitation_does_not_enter_retrieval(monkeypatch):
    calls = 0

    def retrieve(_request):
        nonlocal calls
        calls += 1
        raise AssertionError("casual companionship chat must not retrieve evidence")

    monkeypatch.setattr(retrieval, "retrieve", retrieve)
    assert pipeline.prepare_for_chat(
        query="今天想聊点什么？", source_message_id=db.new_id(), session_id=db.new_id(),
        provider={"execution_location": "local"}, recall_mode="smart",
    ) is None
    assert calls == 0


def test_remote_task_body_is_never_admitted_without_explicit_setting(monkeypatch):
    task = _candidate(
        "高风险工具执行结果", source="task", source_kind="tool_run", privacy="private",
    )
    monkeypatch.setattr(retrieval, "retrieve", lambda _request: _batch(task))
    db.set_setting("kig_remote_task_evidence", "0")
    result = pipeline.prepare_for_chat(
        query="综合所有来源", source_message_id=db.new_id(), session_id=db.new_id(),
        provider={"execution_location": "remote"}, recall_mode="explicit",
    )
    # Query planner removes task from enabled remote sources before retrieval;
    # a malicious adapter result is still filtered at the candidate boundary.
    assert result is not None
    assert not result.batch.candidates
    assert not result.bundle.selected_evidence


def test_remote_transfer_rejects_unknown_or_malformed_privacy_scopes():
    unknown_message = _candidate("未知隐私消息", privacy="highly_sensitive")
    malformed_knowledge = _candidate(
        "错误隐私资料", source="knowledge", source_kind="knowledge_chunk",
        privacy="normal:remote_allowed:unexpected",
    )
    public_lore = _candidate(
        "公开设定", source="lore", source_kind="lore_section", privacy="public",
    )
    filtered = pipeline._filter_transfer(  # noqa: SLF001
        _batch(unknown_message, malformed_knowledge, public_lore),
        {"execution_location": "remote"},
    )
    assert filtered.candidates == (public_lore,)


def test_confirmed_reverse_relation_overrides_deterministic_pair_direction():
    from app import kig_governance

    old = _candidate("星河 API 服务版本 1.0", version="1.0", occurred_at=db.now() - 100)
    new = _candidate("星河 API 服务版本 2.0", version="2.0", occurred_at=db.now())
    governed = (kig_governance.adapt_candidate(old), kig_governance.adapt_candidate(new))
    deterministic = kig_governance.deterministic_relation(*governed)
    assert deterministic is not None
    confirmed_reverse = replace(
        deterministic, older_id=new.candidate_id, newer_id=old.candidate_id,
        selected_ids=(old.candidate_id,), reason_codes=("semantic_relation",),
    )
    combined = pipeline._deduplicate_relations(  # noqa: SLF001
        [deterministic, confirmed_reverse],
    )
    assert combined == [confirmed_reverse]
    freshness = kig_governance.assess_freshness(governed, combined)
    assert freshness.states[new.candidate_id] == "superseded"
    assert freshness.states[old.candidate_id] == "current"


def test_latest_persisted_confirmation_wins_for_an_unordered_source_pair():
    from app import kig_governance

    old = _candidate("星河 API 服务版本 1.0", version="1.0", occurred_at=db.now() - 100)
    new = _candidate("星河 API 服务版本 2.0", version="2.0", occurred_at=db.now())
    governed = (kig_governance.adapt_candidate(old), kig_governance.adapt_candidate(new))
    deterministic = kig_governance.deterministic_relation(*governed)
    assert deterministic is not None
    payload = kig_governance.VersionRelationInput(
        candidate_ids=(old.candidate_id, new.candidate_id), request_id=db.new_id(),
        query="版本确认", sources=governed, impact_level="medium",
    )
    kig_governance.persist_relation(deterministic, payload)
    reverse_proposal = replace(
        deterministic, older_id=new.candidate_id, newer_id=old.candidate_id,
        selected_ids=(old.candidate_id,), reason_codes=("semantic_relation",),
        requires_confirmation=True, proposal_only=True,
    )
    stored = kig_governance.persist_relation(reverse_proposal, payload)
    kig_governance.resolve_relation(
        stored["id"], accept=True, expected_revision=stored["relation_revision"],
    )

    persisted, proposed = pipeline._persisted_relations(governed)  # noqa: SLF001
    combined = pipeline._deduplicate_relations([deterministic, *persisted])  # noqa: SLF001
    assert proposed == [] and len(combined) == 1
    assert combined[0].older_id == new.candidate_id
    assert combined[0].newer_id == old.candidate_id


def test_kig_never_broadens_owner_authorized_knowledge_chunks():
    allowed = _candidate(
        "已授权资料", source="knowledge", source_kind="knowledge_chunk",
        privacy="normal:remote_allowed",
    )
    denied = _candidate(
        "仅限本机资料", source="knowledge", source_kind="knowledge_chunk",
        privacy="normal:local_only",
    )
    batch = pipeline._filter_knowledge_authorization(  # noqa: SLF001
        _batch(allowed, denied), frozenset({allowed.source_id}),
    )
    assert tuple(item.source_id for item in batch.candidates) == (allowed.source_id,)
    assert not pipeline._filter_knowledge_authorization(  # noqa: SLF001
        _batch(allowed, denied), frozenset(),
    ).candidates


def test_legacy_knowledge_filter_never_broadens_authorized_chunk_set():
    prepared = {
        "results": [{"chunk_id": "k1"}, {"chunk_id": "k2"}],
        "evidence_windows": [],
    }
    assert pipeline.filter_knowledge_prepared(prepared, None) is prepared


def test_real_chat_routes_bundle_through_ctx_validates_and_persists_source_strip(monkeypatch):
    source = _candidate("星河当前采用 Electron")
    monkeypatch.setattr(retrieval, "retrieve", lambda _request: _batch(source))
    captured = {}

    async def stream(_provider, _model, messages, **_kwargs):
        captured["system"] = messages[0]["content"]
        yield "星河当前采用 Electron。[来源:E1]"

    monkeypatch.setattr(llm, "stream_chat", stream)
    session = client.post("/api/sessions", json={}).json()
    with client.stream("POST", "/api/chat", json={
        "session_id": session["id"], "content": "综合所有来源，星河采用什么",
    }) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200 and "event: done" in body
    assert "跨来源证据" in captured["system"] and "[来源:E1]" in captured["system"]
    assistant = client.get(f"/api/sessions/{session['id']}/messages").json()[-1]
    assert assistant["content"] == "星河当前采用 Electron。[来源:E1]"
    assert len(assistant["evidence_links"]) == 1
    opened = client.get(
        f"/api/kig/evidence-links/{assistant['evidence_links'][0]['id']}"
    ).json()
    assert opened["available"] is True and opened["content"] == "星河当前采用 Electron"
    conn = db.connect()
    try:
        bundle = conn.execute(
            "SELECT * FROM kig_retrieval_bundles WHERE assistant_message_id=?",
            (assistant["id"],),
        ).fetchone()
        segments = conn.execute(
            "SELECT * FROM kig_answer_claim_segments WHERE assistant_message_id=?",
            (assistant["id"],),
        ).fetchall()
    finally:
        conn.close()
    assert bundle and bundle["protocol_version"] == "knowledge-retrieval-bundle-v1"
    assert segments and segments[0]["support_state"] == "supported"


def test_unresolved_semantic_conflict_cannot_leave_a_certain_answer(monkeypatch):
    source = _candidate("星河当前采用 Electron")
    monkeypatch.setattr(retrieval, "retrieve", lambda _request: _batch(source))

    async def stream(*_args, **_kwargs):
        yield "星河当前采用 Electron。[来源:E1]"

    monkeypatch.setattr(llm, "stream_chat", stream)
    session = client.post("/api/sessions", json={}).json()
    with client.stream("POST", "/api/chat", json={
        "session_id": session["id"], "content": "所有来源存在冲突，以哪个为准",
    }) as response:
        assert response.status_code == 200
        _ = "".join(response.iter_text())
    assistant = client.get(f"/api/sessions/{session['id']}/messages").json()[-1]
    assert assistant["content"].startswith("现有来源存在冲突：")
    conn = db.connect()
    try:
        segment = conn.execute(
            "SELECT support_state,uncertainty_consistent FROM kig_answer_claim_segments "
            "WHERE assistant_message_id=?", (assistant["id"],),
        ).fetchone()
    finally:
        conn.close()
    assert tuple(segment) == ("conflicted", 0)
