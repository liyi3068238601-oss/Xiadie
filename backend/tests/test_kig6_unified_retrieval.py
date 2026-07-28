import asyncio
from dataclasses import replace

import pytest

from app import (
    db, kig_retrieval as retrieval, kig_sources, knowledge, knowledge_embeddings,
    knowledge_worker, life_events, lore, memory, self_timeline,
)


def _memory_candidate(text="星河记忆", *, occurred_at=None):
    item = memory.create_memory("L1", text)
    ref = kig_sources.registry.resolve("memory_fragment", item["id"])
    return retrieval._candidate(
        source="memory", ref=ref, excerpt=text, lexical_score=0.8,
        vector_score=None, occurred_at=occurred_at or db.now(),
        authority="user_memory", metadata={"tags": ("星河",)},
    )


def test_retrieval_candidate_has_provenance_scores_status_and_locator():
    candidate = _memory_candidate()
    retrieval.validate_candidate(candidate)
    assert candidate.source_type == "memory_fragment"
    assert candidate.source_status == "active" and candidate.locator.startswith("memory://")
    assert len(candidate.source_hash) == 64 and len(candidate.excerpt_hash) == 64
    assert 0 <= candidate.lexical_score <= 1 and 0 <= candidate.recency <= 1
    assert candidate.freshness_state == "current"


def test_source_failure_isolated_and_limits_are_per_source():
    candidate = _memory_candidate()

    def good(_request, _limit):
        return [candidate, replace(candidate, candidate_id="second")], {"retrieval_mode": "fts"}

    def failed(_request, _limit):
        raise RuntimeError("secret provider detail must not escape")

    batch = retrieval.retrieve(
        retrieval.RetrievalRequest(
            query="星河", selected_sources=("memory", "task"),
            per_source_limits={"memory": 1, "task": 2},
        ),
        adapters={"memory": good, "task": failed},
    )
    assert len(batch.candidates) == 1 and batch.candidates[0].source == "memory"
    assert batch.failed_sources == ("task",)
    assert batch.diagnostics["task"] == {
        "status": "failed", "error_code": "source_recall_failed", "count": 0,
    }
    assert "secret" not in str(batch.diagnostics)


def test_adapter_cannot_return_an_unselected_source():
    memory_candidate = _memory_candidate()
    batch = retrieval.retrieve(
        retrieval.RetrievalRequest(query="星河", selected_sources=("task",)),
        adapters={"task": lambda *_args: ([memory_candidate], {})},
    )
    assert batch.candidates == () and batch.failed_sources == ("task",)
    assert batch.diagnostics["task"]["error_code"] == "source_recall_failed"


def test_metadata_date_version_status_source_and_tag_filters_are_hard_gates():
    now = db.now()
    candidate = _memory_candidate(occurred_at=now)
    base = retrieval.RetrievalRequest(
        query="星河", selected_sources=("memory",),
        filters=retrieval.RetrievalFilters(
            source_ids=(candidate.source_id,), versions=(candidate.source_revision,),
            statuses=("active",), tags=("星河",), date_from=now - 1, date_to=now + 1,
        ),
    )
    adapter = {"memory": lambda *_args: ([candidate], {})}
    assert retrieval.retrieve(base, adapters=adapter).candidates == (candidate,)
    rejected = replace(base, filters=replace(base.filters, versions=("old",)))
    assert retrieval.retrieve(rejected, adapters=adapter).candidates == ()
    rejected = replace(base, filters=replace(base.filters, date_from=now + 2, date_to=now + 3))
    assert retrieval.retrieve(rejected, adapters=adapter).candidates == ()


def test_deduplication_and_round_robin_preserve_source_diversity():
    first = _memory_candidate("相同文本")
    duplicate = replace(first, candidate_id="duplicate")
    other = replace(first, candidate_id="other", excerpt="不同文本",
                    excerpt_hash=retrieval.hashlib.sha256("不同文本".encode()).hexdigest())
    deduplicated = retrieval._deduplicate([first, duplicate, other])
    assert len(deduplicated) == 2
    assert {item.excerpt for item in deduplicated} == {"相同文本", "不同文本"}
    knowledge_items = [replace(first, source="knowledge", candidate_id=f"k{i}") for i in range(3)]
    memory_items = [replace(first, candidate_id=f"m{i}") for i in range(2)]
    selected = retrieval._diverse_round_robin(
        {"knowledge": knowledge_items, "memory": memory_items},
        ("knowledge", "memory"), 4,
    )
    assert [item.candidate_id for item in selected] == ["k0", "m0", "k1", "m1"]


def test_knowledge_adapter_reuses_hybrid_search_and_lexical_fallback(monkeypatch):
    imported = knowledge.import_file(
        f"kig6-{db.new_id()}.md", "text/markdown",
        "# 星河\n星河检索主段。\n\n## 邻居\n邻居上下文。".encode(),
    )
    assert asyncio.run(knowledge_worker.process_due(limit=3)) == 3
    monkeypatch.setattr(knowledge_embeddings, "search", lambda *_args, **_kwargs: {
        "results": [], "available": False, "error_code": "embedding_offline",
    })
    batch = retrieval.retrieve(retrieval.RetrievalRequest(
        query="星河检索", selected_sources=("knowledge",),
        filters=retrieval.RetrievalFilters(document_ids=(imported["document"]["id"],)),
        per_source_limits={"knowledge": 4},
    ))
    assert batch.candidates and {item.metadata["document_id"] for item in batch.candidates} == {
        imported["document"]["id"],
    }
    assert batch.lexical_fallback_sources == ("knowledge",)
    assert batch.diagnostics["knowledge"]["retrieval_mode"] == "fts"
    assert all(item.locator.startswith("knowledge://chunks/") for item in batch.candidates)
    assert any(item.candidate_role == "neighbor" for item in batch.candidates)


def test_existing_memory_history_life_and_task_stores_adapt_without_body_copy():
    now = db.now()
    memory.create_memory("L1", "星河共同主题")
    session_id = db.new_id()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO sessions(id,title,archived,created_at,updated_at) VALUES(?,?,?,?,?)",
            (session_id, "星河会话", 0, now, now),
        )
        user_id, assistant_id = db.new_id(), db.new_id()
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (user_id, session_id, "user", "星河问题", now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (assistant_id, session_id, "assistant", "星河答复", now + 1),
        )
        tool_id = db.new_id()
        conn.execute(
            "INSERT INTO tool_logs(id,tool,risk_level,status,summary,created_at) VALUES(?,?,?,?,?,?)",
            (tool_id, "test.star", "S0", "done", "星河工具完成", now),
        )
        conn.commit()
    finally:
        conn.close()
    source = life_events.SourceRef("system_observation", db.new_id(), "1", "b" * 64)
    event, _ = life_events.create_event(
        event_kind="observation", world_layer="observed", summary="星河生活记录",
        source_refs=(source,), idempotency_key=f"kig6:{db.new_id()}",
    )
    self_timeline.refresh()
    batch = retrieval.retrieve(retrieval.RetrievalRequest(
        query="星河", selected_sources=("memory", "history", "life", "task"), total_limit=12,
    ))
    sources = {item.source for item in batch.candidates}
    assert {"memory", "history", "life", "task"} <= sources
    assert not batch.failed_sources
    assert all(item.source_status == "active" and item.locator for item in batch.candidates)
    assert any(item.source_id == event["id"] for item in batch.candidates)


def test_lore_adapter_returns_a_valid_source_locator():
    section = lore._sections()[0]
    query = section["keywords"][0] if section["keywords"] else section["title"]
    batch = retrieval.retrieve(retrieval.RetrievalRequest(
        query=query, selected_sources=("lore",),
    ))
    assert batch.candidates
    assert all(item.source == "lore" and item.locator.startswith("lore://")
               for item in batch.candidates)


@pytest.mark.parametrize("kwargs", [
    {"selected_sources": ("knowledge", "knowledge")},
    {"selected_sources": ("web",)},
    {"selected_sources": ("knowledge",), "per_source_limits": {"memory": 1}},
    {"selected_sources": ("knowledge",), "total_limit": 61},
])
def test_request_bounds_reject_invalid_sources_and_limits(kwargs):
    with pytest.raises(retrieval.RetrievalError):
        retrieval.retrieve(retrieval.RetrievalRequest(query="valid", **kwargs))
