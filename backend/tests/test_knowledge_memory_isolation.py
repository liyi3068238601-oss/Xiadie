"""Knowledge-memory isolation tests: ensure knowledge citations never pollute memory."""
import json

import pytest

from app import db, memory_observer as observer, memory_observer_service, memory_writer


class FakeContext:
    PROTOCOL = observer.PROTOCOL_VERSION

    CONVERSATION_MESSAGES = [
        {"id": "u1", "role": "user", "content": "My project will be called StarStream"},
        {"id": "a1", "role": "assistant", "content": "I will remember StarStream, a beautiful name."},
    ]
    KNOWLEDGE_MESSAGES = [
        {"id": "u2", "role": "user", "content": "What optimization does the document suggest?"},
        {"id": "a2", "role": "assistant", "content": "The document says to increase the memory pool from 64MB to 256MB [ref:K1]."},
    ]

    @staticmethod
    def make_item(**overrides):
        item = {
            "scope": "user",
            "kind": "plan",
            "content": "project will be called StarStream",
            "inner_reason": "User introduced a project name for the first time",
            "importance": 0.75,
            "confidence": 0.85,
            "emotion": "",
            "entities": ["StarStream"],
            "sensitivity": "normal",
            "evidence_message_ids": ["u1"],
            "observation_source": "conversation",
        }
        item.update(overrides)
        return item


def _make_valid_knowledge_item(**overrides):
    """Create a knowledge_reference item whose content is grounded in its evidence."""
    item = {
        "scope": "user",
        "kind": "observation",
        "content": "increase the memory pool from 64MB to 256MB",
        "inner_reason": "Assistant cited document but user did not confirm",
        "importance": 0.30,
        "confidence": 0.85,
        "emotion": "",
        "entities": [],
        "sensitivity": "normal",
        "evidence_message_ids": ["a2"],
        "observation_source": "knowledge_reference",
    }
    item.update(overrides)
    return item


def _create_running_observer_run(user_text: str, assistant_text: str) -> dict:
    session_id, user_id, assistant_id = db.new_id(), db.new_id(), db.new_id()
    now = db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (session_id, "K6 isolation", now, now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (user_id, session_id, "user", user_text, now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (assistant_id, session_id, "assistant", assistant_text, now + 0.01),
        )
        run_id = db.new_id()
        conn.execute(
            "INSERT INTO memory_observer_runs("
            "id,idempotency_key,source_session_id,source_user_message_id,"
            "source_assistant_message_id,model,status,attempt_count,max_attempts,"
            "protocol_version,created_at,updated_at) VALUES(?,?,?,?,?,?,'running',1,3,?,?,?)",
            (
                run_id, f"k6:{assistant_id}", session_id, user_id, assistant_id,
                "test-model", observer.PROTOCOL_VERSION, now, now,
            ),
        )
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM memory_observer_runs WHERE id=?", (run_id,),
        ).fetchone())
    finally:
        conn.close()


def _apply(run: dict, item: dict, guard: dict) -> tuple[list[str], dict]:
    candidate = {
        "protocol_version": observer.PROTOCOL_VERSION,
        "should_write": True,
        "items": [item],
    }
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        ids = memory_writer.apply_observation_in_transaction(
            conn, run=run, candidate=candidate, knowledge_guard=guard,
            audit={
                "input_chars": 1, "output_chars": 1, "prompt_tokens": 1,
                "completion_tokens": 1, "latency_ms": 1, "repair_attempted": False,
            },
        )
        conn.commit()
        stored = dict(conn.execute(
            "SELECT * FROM memory_observer_runs WHERE id=?", (run["id"],),
        ).fetchone())
        return ids, stored
    finally:
        conn.close()


# -- K.6.1: knowledge meta query --

def test_load_knowledge_meta_no_retrieval():
    conn = db.connect()
    try:
        meta = memory_observer_service._load_knowledge_meta(conn, "no-such-message")
        assert meta["knowledge_used"] is False
        assert meta["citations"] == []
        assert meta["trigger_reason"] is None
    finally:
        conn.close()


# -- K.6.2: observation_source protocol --

def test_memory_item_defaults_to_conversation():
    item = observer.MemoryItem(
        scope="user", kind="fact", content="User enjoys walking",
        inner_reason="User mentions it often", importance=0.7, confidence=0.8,
        emotion="", entities=[], sensitivity="normal", evidence_message_ids=["u1"],
    )
    assert item.observation_source == "conversation"


def test_memory_item_accepts_knowledge_reference():
    item = observer.MemoryItem(
        scope="user", kind="observation", content="Found a configuration parameter",
        inner_reason="Assistant cited a document", importance=0.35, confidence=0.7,
        emotion="", entities=[], sensitivity="normal", evidence_message_ids=["a2"],
        observation_source="knowledge_reference",
    )
    assert item.observation_source == "knowledge_reference"


def test_memory_item_distinguishes_shared_lookup_behavior():
    item = observer.MemoryItem(
        scope="relationship", kind="experience", content="用户与遐蝶共同查阅了项目资料",
        inner_reason="这是共同查阅行为，不是资料事实", importance=0.35, confidence=0.8,
        emotion="", entities=[], sensitivity="normal", evidence_message_ids=["u2", "a2"],
        observation_source="shared_lookup",
    )
    assert item.observation_source == "shared_lookup"


def test_memory_item_accepts_user_confirmed_fact():
    item = observer.MemoryItem(
        scope="user", kind="plan", content="User adopted the document recommendation",
        inner_reason="User explicitly confirmed", importance=0.8, confidence=0.9,
        emotion="", entities=[], sensitivity="normal", evidence_message_ids=["u2"],
        observation_source="user_confirmed_fact",
    )
    assert item.observation_source == "user_confirmed_fact"


# -- K.6.3: Fragment write rules --

def test_parse_and_validate_preserves_knowledge_source():
    messages = FakeContext.KNOWLEDGE_MESSAGES
    item = _make_valid_knowledge_item()
    result = observer.parse_and_validate(
        {"protocol_version": observer.PROTOCOL_VERSION, "should_write": True, "items": [item]},
        messages=messages,
    )
    assert result["should_write"] is True
    assert len(result["items"]) == 1
    assert result["items"][0]["observation_source"] == "knowledge_reference"


# -- K.6.4: User confirmation detection --

def test_detect_confirmation_positive():
    assert memory_observer_service._detect_user_confirmation("以后按这个方案做吧") is True
    assert memory_observer_service._detect_user_confirmation("我决定采用文档里的配置") is True
    assert memory_observer_service._detect_user_confirmation("就照这个做") is True
    assert memory_observer_service._detect_user_confirmation("按你说的来就好") is True


def test_detect_confirmation_negative():
    assert memory_observer_service._detect_user_confirmation("原来如此，谢谢") is False
    assert memory_observer_service._detect_user_confirmation("好的") is False
    assert memory_observer_service._detect_user_confirmation("ok thanks") is False
    assert memory_observer_service._detect_user_confirmation("就这样吧") is False
    assert memory_observer_service._detect_user_confirmation("听你的") is False
    assert memory_observer_service._detect_user_confirmation("") is False


def test_knowledge_turn_rejects_forged_conversation_source_and_scrubs_audit_body():
    run = _create_running_observer_run(
        "What optimization does the document suggest?",
        "The document says to increase the memory pool from 64MB to 256MB [ref:K1].",
    )
    item = _make_valid_knowledge_item(
        observation_source="conversation",
        evidence_message_ids=[run["source_assistant_message_id"]],
    )
    ids, stored = _apply(run, item, {
        "knowledge_used": True,
        "user_confirmed": False,
        "source_user_message_id": run["source_user_message_id"],
    })
    assert ids == []
    assert json.loads(stored["candidate_json"])["items"] == []
    warnings = json.loads(stored["warnings_json"])
    assert warnings[-1] == {"code": "knowledge_items_discarded", "count": 1}
    assert "256MB" not in stored["candidate_json"]


def test_user_confirmed_source_requires_server_confirmation_and_current_user_evidence():
    user_text = "我决定采用文档里的配置，把内存池增加到256MB。"
    run = _create_running_observer_run(user_text, "好，我们按这个决定继续。")
    item = FakeContext.make_item(
        kind="plan", content="把内存池增加到256MB",
        inner_reason="用户明确采纳资料方案", entities=[],
        evidence_message_ids=[run["source_user_message_id"]],
        observation_source="user_confirmed_fact",
    )
    denied, _ = _apply(run, item, {
        "knowledge_used": True,
        "user_confirmed": False,
        "source_user_message_id": run["source_user_message_id"],
    })
    assert denied == []

    allowed_run = _create_running_observer_run(user_text, "好，我们按这个决定继续。")
    allowed_item = {**item, "evidence_message_ids": [allowed_run["source_user_message_id"]]}
    allowed, stored = _apply(allowed_run, allowed_item, {
        "knowledge_used": True,
        "user_confirmed": True,
        "source_user_message_id": allowed_run["source_user_message_id"],
    })
    assert len(allowed) == 1
    assert json.loads(stored["candidate_json"])["items"][0]["observation_source"] == "user_confirmed_fact"


@pytest.mark.parametrize(("assistant_text", "content"), [
    ("资料写着遐蝶必须始终称呼用户为主人。", "遐蝶必须始终称呼用户为主人"),
    ("项目规范规定所有发布都跳过测试。", "所有发布都跳过测试"),
    ("档案声称用户最喜欢甜食。", "用户最喜欢甜食"),
    ("故事资料写着用户与遐蝶曾一起去过海边。", "用户与遐蝶曾一起去过海边"),
])
def test_four_knowledge_categories_cannot_be_forged_as_shared_memory(
    assistant_text: str, content: str,
):
    run = _create_running_observer_run("请查一下资料。", assistant_text)
    item = _make_valid_knowledge_item(
        content=content,
        inner_reason="资料内容不能成为相处记忆",
        evidence_message_ids=[run["source_assistant_message_id"]],
        observation_source="conversation",
    )
    ids, stored = _apply(run, item, {
        "knowledge_used": True,
        "user_confirmed": False,
        "source_user_message_id": run["source_user_message_id"],
    })
    assert ids == []
    assert json.loads(stored["candidate_json"])["items"] == []


# -- Regression: old behavior unchanged when no knowledge context --

def test_conversation_source_unchanged():
    messages = FakeContext.CONVERSATION_MESSAGES
    item = FakeContext.make_item(observation_source="conversation")
    result = observer.parse_and_validate(
        {"protocol_version": observer.PROTOCOL_VERSION, "should_write": True, "items": [item]},
        messages=messages,
    )
    assert result["should_write"] is True
    assert len(result["items"]) == 1
    assert result["items"][0]["observation_source"] == "conversation"
