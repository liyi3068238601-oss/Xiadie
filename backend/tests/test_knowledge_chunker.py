"""F.4 稳定切片协议的确定性、结构边界与定位测试。"""
import hashlib
import json

import pytest

from app import knowledge_chunker, knowledge_parser


def _chunks(text: str, extension: str = ".md") -> list[dict]:
    payload = knowledge_parser.parse(text.encode("utf-8"), extension=extension)
    return knowledge_chunker.chunk_artifact(payload)


def test_markdown_heading_paths_and_locators_are_exact_and_deterministic():
    text = "# 顶层\n介绍\n## 子节\n中文。English.\n\n- 条目一\n- 条目二\n# 末章\n结束"
    first = _chunks(text)
    second = _chunks(text)

    assert first == second
    assert [json.loads(item["heading_path_json"]) for item in first] == [
        ["顶层"], ["顶层", "子节"], ["顶层", "子节"], ["末章"],
    ]
    assert [item["chunk_kind"] for item in first] == ["heading", "heading", "list", "heading"]
    for ordinal, chunk in enumerate(first):
        assert chunk["ordinal"] == ordinal
        assert chunk["content"] == text[chunk["char_start"]:chunk["char_end"]]
        assert chunk["content_sha256"] == hashlib.sha256(chunk["content"].encode()).hexdigest()
        assert chunk["page_start"] is None and chunk["page_end"] is None


def test_txt_uses_paragraph_boundaries_without_inventing_heading_or_page():
    text = "  短行列表一\n短行列表二\n\nMixed 中文 and English paragraph.\n\n最后一段"
    chunks = _chunks(text, ".txt")

    assert len(chunks) == 1
    assert chunks[0]["paragraph_start"] == 1 and chunks[0]["paragraph_end"] == 3
    assert chunks[0]["heading_path_json"] == "[]"
    assert chunks[0]["content"] == text


def test_long_paragraph_prefers_sentence_boundaries_then_stays_below_hard_limit():
    text = ("甲" * 790) + "。" + ("乙" * 790) + "！" + ("丙" * 790)
    chunks = _chunks(text, ".txt")

    assert len(chunks) == 3
    assert chunks[0]["content"].endswith("。")
    assert chunks[1]["content"].endswith("！")
    assert all(0 < len(item["content"]) <= knowledge_chunker.MAX_CHARS for item in chunks)
    assert all(left["char_end"] <= right["char_start"] for left, right in zip(chunks, chunks[1:]))


def test_unbroken_text_has_deterministic_hard_boundary_fallback():
    text = "A" * 2501
    chunks = _chunks(text, ".txt")
    assert [len(item["content"]) for item in chunks] == [1200, 1200, 101]
    assert "".join(item["content"] for item in chunks) == text


def test_chunking_checks_cooperative_cancellation_inside_loop():
    payload = knowledge_parser.parse((("一段。" * 400) + "\n\n尾声").encode(), extension=".txt")
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        return calls >= 2

    with pytest.raises(knowledge_chunker.ChunkingCancelled):
        knowledge_chunker.chunk_artifact(payload, should_cancel=cancelled)


def test_tampered_parse_artifact_contract_is_rejected():
    payload = knowledge_parser.parse(b"valid", extension=".txt")
    payload["normalized_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="parse_artifact_invalid"):
        knowledge_chunker.chunk_artifact(payload)
