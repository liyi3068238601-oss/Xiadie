"""Synthetic, body-free KIG-R safety and integration acceptance.

This runner never reads the production database and never calls a provider. The
separate KIG.7 model-family quality report is an explicit release dependency.
"""
from __future__ import annotations

import json
import os
import runpy
from dataclasses import replace
from pathlib import Path
import shutil
import sys
import tempfile

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

PROJECT_DIR = BACKEND_DIR.parent
JSON_PATH = PROJECT_DIR / "docs" / "reports" / "kig-r-acceptance.json"
MARKDOWN_PATH = PROJECT_DIR / "docs" / "reports" / "kig-r-acceptance.md"
MODEL_QUALITY_PATH = PROJECT_DIR / "docs" / "reports" / "kig-7-model-quality.json"
MODEL_EVAL_RUNNER = BACKEND_DIR / "scripts" / "run_kig7_model_eval.py"
CASE_COUNT = 10


def _load_isolated_app() -> str:
    """Load app modules only after isolating the executable acceptance process."""
    temp_dir = tempfile.mkdtemp(prefix="xiadie-kig-r-")
    os.environ["XIADIE_DATA_DIR"] = temp_dir
    os.environ["XIADIE_API_TOKEN"] = "synthetic-kig-r-token-at-least-thirty-two-bytes"
    global db, kig_evidence, kig_governance, kig_pipeline, kig_reranker  # noqa: PLW0603
    global kig_retrieval, kig_sources  # noqa: PLW0603
    from app import (  # noqa: PLC0415
        db as _db, kig_evidence as _kig_evidence,
        kig_governance as _kig_governance, kig_pipeline as _kig_pipeline,
        kig_reranker as _kig_reranker, kig_retrieval as _kig_retrieval,
        kig_sources as _kig_sources,
    )
    db, kig_evidence = _db, _kig_evidence
    kig_governance, kig_pipeline = _kig_governance, _kig_pipeline
    kig_reranker, kig_retrieval, kig_sources = _kig_reranker, _kig_retrieval, _kig_sources
    return temp_dir


def _message_candidate(text: str, *, occurred_at: float | None = None):
    now = occurred_at or db.now()
    session_id, message_id = db.new_id(), db.new_id()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO sessions(id,title,archived,created_at,updated_at) VALUES(?,?,?,?,?)",
            (session_id, "synthetic KIG-R", 0, now, now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (message_id, session_id, "assistant", text, now),
        )
        conn.commit()
    finally:
        conn.close()
    ref = kig_sources.registry.resolve("message", message_id)
    return kig_retrieval._candidate(
        source="history", ref=ref, excerpt=text, lexical_score=1.0,
        vector_score=None, occurred_at=now, authority="recorded_conversation",
    )


def _tool_candidate(text: str):
    tool_id = db.new_id()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO tool_logs(id,tool,risk_level,status,summary,created_at) VALUES(?,?,?,?,?,?)",
            (tool_id, "synthetic.tool", "S1", "done", text, db.now()),
        )
        conn.commit()
    finally:
        conn.close()
    ref = kig_sources.registry.resolve("tool_run", tool_id)
    return kig_retrieval._candidate(
        source="task", ref=ref, excerpt=text, lexical_score=1.0,
        vector_score=None, occurred_at=db.now(), authority="tool_result",
    )


def _batch(*items):
    return kig_retrieval.RetrievalBatch(
        candidates=tuple(items), diagnostics={}, failed_sources=(),
        lexical_fallback_sources=(),
    )


def build_report() -> dict:
    db.init_db()
    counters = {
        "forged_source_ref_accepted": 0,
        "invented_citation_clickable": 0,
        "stale_citation_clickable": 0,
        "unsupported_citation_clickable": 0,
        "unconfirmed_high_impact_relation_accepted": 0,
        "unauthorized_remote_tool_excerpt": 0,
        "unauthorized_knowledge_excerpt": 0,
        "unknown_privacy_scope_excerpt": 0,
        "conflicting_confirmed_pair_applied": 0,
        "conditional_false_conflict": 0,
        "recency_only_supersession": 0,
        "shadow_proposal_active": 0,
        "deterministic_fallback_empty": 0,
    }
    for index in range(CASE_COUNT):
        base = _message_candidate(f"星河系统 {index} 当前使用 Electron 版本 1.0")
        ref = kig_sources.registry.resolve("message", base.source_id)
        forged = kig_sources.SourceRef(**{**ref.to_dict(), "locator": ref.locator + "/forged"})
        try:
            kig_sources.validate_ref(forged)
            counters["forged_source_ref_accepted"] += 1
        except kig_sources.SourceRefError:
            pass

        bundle = kig_evidence.build_bundle(
            query="综合所有来源比较版本", request_id=db.new_id(),
            selected_sources=("history", "memory"), batch=_batch(base),
        )
        invented = kig_evidence.validate_answer("伪造结论。[来源:E99]", bundle)
        counters["invented_citation_clickable"] += sum(
            link.validation_status == "active" for link in invented.links
        )
        unsupported = kig_evidence.validate_answer(
            f"星河系统 {index} 当前使用 Tauri 版本 2.0。[来源:E1]", bundle,
        )
        counters["unsupported_citation_clickable"] += sum(
            link.validation_status == "active" for link in unsupported.links
        )
        conn = db.connect()
        try:
            conn.execute("UPDATE messages SET content=? WHERE id=?",
                         (f"source changed {index}", base.source_id))
            conn.commit()
        finally:
            conn.close()
        stale = kig_evidence.validate_answer("旧结论。[来源:E1]", bundle)
        counters["stale_citation_clickable"] += sum(
            link.validation_status == "active" for link in stale.links
        )

        left = _message_candidate(f"生产环境 {index} 允许删除")
        right = _message_candidate(f"生产环境 {index} 禁止删除")
        payload = kig_governance.build_pair_input(
            left, right, request_id=db.new_id(), query="生产环境删除权限冲突",
        )
        unsafe = kig_governance.VersionRelationResult(
            action="select", selected_ids=(right.candidate_id,), relation="contradicts",
            older_id=left.candidate_id, newer_id=right.candidate_id, scope_terms=(),
            reason_codes=("semantic_relation",), confidence_band="high",
            requires_confirmation=False, proposal_only=True,
        )
        try:
            kig_governance.validate_result(payload, unsafe)
            counters["unconfirmed_high_impact_relation_accepted"] += 1
        except Exception:
            pass

        tool = _tool_candidate(f"private tool result {index}")
        filtered = kig_pipeline._filter_transfer(  # noqa: SLF001 - acceptance boundary probe
            _batch(tool), {"execution_location": "remote"},
        )
        counters["unauthorized_remote_tool_excerpt"] += len(filtered.candidates)

        denied_knowledge = replace(
            base, source="knowledge", source_type="knowledge_chunk",
            privacy_scope="normal:local_only",
        )
        knowledge_filtered = kig_pipeline._filter_knowledge_authorization(  # noqa: SLF001
            _batch(denied_knowledge), frozenset(),
        )
        counters["unauthorized_knowledge_excerpt"] += len(knowledge_filtered.candidates)

        unknown_privacy = replace(base, privacy_scope="highly_sensitive")
        privacy_filtered = kig_pipeline._filter_transfer(  # noqa: SLF001
            _batch(unknown_privacy), {"execution_location": "remote"},
        )
        counters["unknown_privacy_scope_excerpt"] += len(privacy_filtered.candidates)

        forward = kig_governance.VersionRelationResult(
            action="select", selected_ids=(right.candidate_id,), relation="supersedes",
            older_id=left.candidate_id, newer_id=right.candidate_id, scope_terms=(),
            reason_codes=("deterministic_relation",), confidence_band="high",
            requires_confirmation=False, proposal_only=False,
        )
        confirmed_reverse = replace(
            forward, selected_ids=(left.candidate_id,), older_id=right.candidate_id,
            newer_id=left.candidate_id, reason_codes=("semantic_relation",),
            proposal_only=True,
        )
        pair_relations = kig_pipeline._deduplicate_relations(  # noqa: SLF001
            [forward, confirmed_reverse],
        )
        pair_freshness = kig_governance.assess_freshness(
            (kig_governance.adapt_candidate(left), kig_governance.adapt_candidate(right)),
            pair_relations,
        )
        counters["conflicting_confirmed_pair_applied"] += int(
            len(pair_relations) != 1
            or pair_freshness.states[left.candidate_id] != "current"
            or pair_freshness.states[right.candidate_id] != "superseded"
        )

        morning = _message_candidate(f"项目 {index} 早上喜欢咖啡")
        evening = _message_candidate(f"项目 {index} 晚上不喜欢咖啡")
        kig_governance.upsert_source_governance(
            kig_sources.registry.resolve("message", morning.source_id),
            authority_level="imported_source", scope={
                "topic": f"coffee-{index}", "qualifiers": ["morning"],
            },
        )
        kig_governance.upsert_source_governance(
            kig_sources.registry.resolve("message", evening.source_id),
            authority_level="imported_source", scope={
                "topic": f"coffee-{index}", "qualifiers": ["evening"],
            },
        )
        conditional = kig_governance.deterministic_relation(
            kig_governance.adapt_candidate(morning), kig_governance.adapt_candidate(evening),
        )
        counters["conditional_false_conflict"] += int(
            conditional is None or conditional.relation != "compatible_with_conditions"
        )

        old_topic = _message_candidate(f"项目 {index} 当前用 Electron", occurred_at=db.now() - 10_000)
        future_topic = _message_candidate(f"其他项目 {index} 未来评估 Tauri", occurred_at=db.now())
        counters["recency_only_supersession"] += int(
            kig_governance.deterministic_relation(
                kig_governance.adapt_candidate(old_topic),
                kig_governance.adapt_candidate(future_topic),
            ) is not None
        )
        fallback_input = kig_reranker.adapt(
            _batch(left, right), request_id=db.new_id(), query="比较方案", max_selected=2,
        )
        fallback = kig_reranker.deterministic_fusion(fallback_input)
        counters["shadow_proposal_active"] += int(fallback.proposal_only is not True)
        counters["deterministic_fallback_empty"] += int(not fallback.selected_ids)

    denominators = {name: CASE_COUNT for name in counters}
    rates = {name + "_rate": counters[name] / CASE_COUNT for name in counters}
    safety_failures = [name for name, count in counters.items() if count != 0]
    quality_report = None
    try:
        quality_report = json.loads(MODEL_QUALITY_PATH.read_text(encoding="utf-8"))
        eval_namespace = runpy.run_path(str(MODEL_EVAL_RUNNER))
        model_certified = kig_reranker.certification_matches(
            quality_report, provider_id=str(quality_report.get("provider_id") or ""),
            model=str(quality_report.get("model") or ""),
            eval_dataset_hash=eval_namespace["_dataset_hash"](),
        )
    except (OSError, ValueError, KeyError, TypeError):
        model_certified = False
    safety_passed = not safety_failures
    report = {
        "protocol_version": "kig-r-acceptance-v1",
        "release_protocol": kig_pipeline.PROTOCOL_VERSION,
        "schema_version": 76,
        "synthetic_only": True,
        "contains_user_data": False,
        "case_count": CASE_COUNT,
        "denominators": denominators,
        "counts": counters,
        "rates": rates,
        "safety_gate": "pass" if safety_passed else "fail",
        "safety_failures": safety_failures,
        "model_quality_gate": "pass" if model_certified else "fail",
        "model_certification_key": (
            (quality_report or {}).get("certification", {}).get("certification_key")
            if model_certified else None
        ),
        "certified_provider_id": (quality_report or {}).get("provider_id") if model_certified else None,
        "certified_model": (quality_report or {}).get("model") if model_certified else None,
        "release_gate": "pass" if safety_passed and model_certified else "pending_model_quality",
    }
    return report


def render_markdown(report: dict) -> str:
    rows = "\n".join(
        f"| {name} | {report['denominators'][name]} | {count} |"
        for name, count in report["counts"].items()
    )
    quality_note = (
        f"模型指纹认证已通过：`{report['certified_provider_id']}` / "
        f"`{report['certified_model']}` / `{report['model_certification_key']}`。"
        if report["model_quality_gate"] == "pass" else
        "安全门不能替代 KIG.7 实配模型盲评；当前不得晋级或声称 KIG-R 已冻结。"
    )
    return f"""# KIG-R 冻结验收报告

- 协议：`{report['release_protocol']}`
- Schema：{report['schema_version']}
- 合成场景：{report['case_count']} 组（不含用户数据）
- 安全门：{report['safety_gate']}
- 模型质量门：{report['model_quality_gate']}
- 发布门：{report['release_gate']}

| 零容忍指标 | 分母 | 违规数 |
|---|---:|---:|
{rows}

安全门与模型质量门彼此独立。{quality_note} 未匹配该 Provider、模型、协议、Prompt 或固定集指纹的其他模型仍必须保持未认证 Shadow/确定性回退。
"""


def main() -> None:
    temp_dir = _load_isolated_app()
    try:
        report = build_report()
        JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        MARKDOWN_PATH.write_text(render_markdown(report), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
