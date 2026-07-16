import json
from pathlib import Path


REQUIRED_CATEGORIES = {
    "explicit_recall",
    "natural_recall",
    "skip",
    "lexical_strong",
    "vector_strong",
    "duplicate_sources",
    "memory_conflict",
    "local_only",
    "prompt_injection",
    "source_changed",
    "provider_changed",
    "ambiguous_context",
}


def test_natural_recall_evaluation_fixture_is_anonymous_complete_and_stable():
    path = Path(__file__).parent / "fixtures" / "knowledge_recall_evaluation_v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["protocol_version"] == "knowledge-recall-eval-v1"
    assert payload["default_recall_mode"] == "explicit"
    assert payload["synthetic_only"] is True

    documents = payload["documents"]
    document_ids = {document["id"] for document in documents}
    assert len(document_ids) == len(documents)
    assert {document["policy"] for document in documents} <= {
        "remote_allowed", "ask_each_time", "local_only"
    }

    cases = payload["cases"]
    case_ids = {case["id"] for case in cases}
    assert len(case_ids) == len(cases)
    assert REQUIRED_CATEGORIES <= {case["category"] for case in cases}
    assert {case["expected_shadow_action"] for case in cases} <= {"skip", "retrieve", "ask"}
    assert all(set(case["eligible_documents"]) <= document_ids for case in cases)

    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert "api_key" not in serialized
    assert "grant_token" not in serialized
    assert "c:\\users\\" not in serialized
    assert "/users/" not in serialized
