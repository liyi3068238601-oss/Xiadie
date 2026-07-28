import asyncio
import hashlib
import json

import pytest

from app import cognitive_decision as cds, db, knowledge, knowledge_boundary_shadow as boundary
from app import knowledge_chunker, knowledge_management, knowledge_parser, knowledge_search, knowledge_worker


def _chunks(text: str) -> list[dict]:
    return knowledge_chunker.chunk_artifact(knowledge_parser.parse(text.encode(), extension=".md"))


def test_structure_quality_set_preserves_heading_list_table_code_and_prose():
    text = (
        "# Definition\nA widget is a bounded object.\n\n"
        "- first step\n- second step\n\n"
        "| name | value |\n|---|---|\n| alpha | 1 |\n\n"
        "```python\ndef build():\n    return 'exact'\n```\n\n"
        "Final warning: never remove the source hash."
    )
    chunks = _chunks(text)
    kinds = [item["chunk_kind"] for item in chunks]
    assert kinds == ["heading", "list", "table", "code", "prose"]
    assert "".join(item["content"] for item in chunks).replace("\n\n", "") == text.replace("\n\n", "")
    for index, item in enumerate(chunks):
        assert item["content"] == text[item["char_start"]:item["char_end"]]
        assert item["content_sha256"] == hashlib.sha256(item["content"].encode()).hexdigest()
        assert item["previous_ordinal"] == (index - 1 if index else None)
        assert item["next_ordinal"] == (index + 1 if index + 1 < len(chunks) else None)


def test_normal_sized_code_and_table_blocks_are_not_cut_in_the_middle():
    code = "```python\n" + "\n".join(f"value_{i} = {i}" for i in range(100)) + "\n```"
    table = "| a | b |\n|---|---|\n" + "\n".join(f"| {i} | {i*i} |" for i in range(80))
    code_chunks = _chunks(code)
    table_chunks = _chunks(table)
    assert len(code_chunks) == 1 and code_chunks[0]["chunk_kind"] == "code"
    assert len(table_chunks) == 1 and table_chunks[0]["chunk_kind"] == "table"
    assert code_chunks[0]["content"] == code and table_chunks[0]["content"] == table


def test_boundary_model_can_only_select_safe_offsets_and_never_rewrite_raw_text():
    text = "alpha paragraph\n\nbeta paragraph\n\ngamma paragraph"
    offsets = tuple(index + 2 for index in range(len(text) - 1) if text[index:index + 2] == "\n\n")
    payload = boundary.BoundaryProposalInput(
        candidate_ids=boundary.candidate_ids(offsets), source_id="synthetic", source_revision="1",
        source_hash=hashlib.sha256(text.encode()).hexdigest(), raw_text_length=len(text),
        deterministic_cut_offsets=offsets,
    )
    fallback = boundary.deterministic_fallback(payload)
    boundary.validate(payload, fallback)
    assert "".join(boundary.apply_exact_slices(text, fallback)) == text
    forged = boundary.BoundaryProposalResult(**{
        **fallback.__dict__, "selected_ids": ("cut:3",), "cut_offsets": (3,),
        "reason_codes": ("model_boundary_subset",),
    })
    with pytest.raises(cds.DecisionProtocolError) as caught:
        boundary.validate(payload, forged)
    assert caught.value.code == "invented_boundary"


def test_invalid_model_boundary_output_falls_back_to_exact_deterministic_slices():
    text = "first\n\nsecond"
    offsets = (7,)
    payload = boundary.BoundaryProposalInput(
        boundary.candidate_ids(offsets), "synthetic", "1", hashlib.sha256(text.encode()).hexdigest(),
        len(text), offsets,
    )
    source = (cds.SourceSnapshot("knowledge_document", "synthetic", "1", payload.source_hash),)
    header = cds.build_header(
        decision_kind=boundary.DECISION_KIND, policy_version=boundary.POLICY_VERSION,
        request_id="boundary-fallback", mode=cds.DecisionMode.SHADOW, source_snapshot=source,
    )
    run, _ = cds.create_run(header, payload, boundary.candidates(payload))
    outcome = cds.evaluate_output(run.id, header, payload, "{not-json", current_snapshot=source)
    assert outcome["fallback_used"] is True and outcome["application_allowed"] is False
    fallback = boundary.deterministic_fallback(payload)
    assert "".join(boundary.apply_exact_slices(text, fallback)) == text


def test_index_v2_atomic_rebuild_keeps_raw_hash_and_legacy_index_is_compatible():
    raw = b"# KIG4\nDefinition remains exact.\n\n```py\nprint('exact')\n```"
    imported = knowledge.import_file(f"kig4-{db.new_id()}.md", "text/markdown", raw)
    document_id = imported["document"]["id"]
    assert asyncio.run(knowledge_worker.process_due(limit=3)) == 3
    conn = db.connect()
    try:
        before = dict(conn.execute("SELECT * FROM knowledge_documents WHERE id=?", (document_id,)).fetchone())
    finally:
        conn.close()
    assert before["content_sha256"] == hashlib.sha256(raw).hexdigest()
    assert before["index_version"] == knowledge_search.INDEX_VERSION
    assert {row["chunk_kind"] for row in knowledge_worker.chunks_for_document(document_id)} == {"heading", "code"}

    run = knowledge_management.enqueue_reindex(document_id)
    assert knowledge_search.search("Definition", document_ids=[document_id])["result_count"] == 1
    assert asyncio.run(knowledge_worker.process_due(limit=3)) == 3
    conn = db.connect()
    try:
        after = dict(conn.execute("SELECT * FROM knowledge_documents WHERE id=?", (document_id,)).fetchone())
        conn.execute("UPDATE knowledge_documents SET index_version='knowledge-fts-terms-v1' WHERE id=?",
                     (document_id,))
        conn.commit()
    finally:
        conn.close()
    assert run["trigger"] == "reindex" and after["content_sha256"] == before["content_sha256"]
    assert after["active_index_revision"] == before["active_index_revision"] + 1
    assert knowledge.storage_path_for(after).read_bytes() == raw
    assert knowledge_search.search("Definition", document_ids=[document_id])["result_count"] == 1
