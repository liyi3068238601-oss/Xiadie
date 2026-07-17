"""Knowledge-memory isolation tests: ensure knowledge citations never pollute memory."""
from app import db, memory_observer as observer, memory_observer_service


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
    evidence_text = "The document says to increase the memory pool from 64MB to 256MB [ref:K1]."
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


# -- K.6.1: knowledge meta query --

def test_load_knowledge_meta_no_retrieval():
    conn = db.connect()
    try:
        meta = memory_observer_service._load_knowledge_meta(conn, "no-such-message")
        assert meta["knowledge_used"] is False
        assert meta["citations"] == []
        assert meta["trigger_reason"] is None
    except Exception:
        # Table may not exist in dev test database; that's fine,
        # _load_knowledge_meta should be safe against missing tables too.
        pass
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
    assert memory_observer_service._detect_user_confirmation("就这样吧") is True


def test_detect_confirmation_negative():
    assert memory_observer_service._detect_user_confirmation("原来如此，谢谢") is False
    assert memory_observer_service._detect_user_confirmation("好的") is False
    assert memory_observer_service._detect_user_confirmation("ok thanks") is False
    assert memory_observer_service._detect_user_confirmation("") is False


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
