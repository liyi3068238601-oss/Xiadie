"""Run KIG.0 synthetic retrieval and ownership baseline in an isolated database."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import platform
import statistics
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
FIXTURE_PATH = BACKEND_DIR / "tests" / "fixtures" / "kig0_evaluation_v1.json"
JSON_PATH = PROJECT_DIR / "docs" / "reports" / "kig-0-baseline.json"
MARKDOWN_PATH = PROJECT_DIR / "docs" / "reports" / "kig-0-construction-baseline.md"
PREDECESSOR_SHA = "f16d80ab0d2457065dc65d7d284d3cbf3584f5ee"


def _distribution(values: list[float]) -> dict:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "average": 0.0, "p50": 0.0, "p90": 0.0, "max": 0.0}

    def percentile(ratio: float) -> float:
        position = (len(ordered) - 1) * ratio
        low, high = math.floor(position), math.ceil(position)
        if low == high:
            return ordered[low]
        return ordered[low] * (high - position) + ordered[high] * (position - low)

    return {
        "count": len(ordered),
        "average": round(statistics.fmean(ordered), 6),
        "p50": round(percentile(0.50), 6),
        "p90": round(percentile(0.90), 6),
        "max": round(ordered[-1], 6),
    }


def evaluate_fixture(fixture: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix="xiadie-kig0-") as data_dir:
        os.environ["XIADIE_DATA_DIR"] = data_dir
        if str(BACKEND_DIR) not in sys.path:
            sys.path.insert(0, str(BACKEND_DIR))
        from app import db, knowledge, knowledge_context, knowledge_search, knowledge_worker, memory
        from app import cognitive_decision, context_assembler, specialty_contracts

        db.init_db()
        document_map: dict[str, str] = {}
        for item in fixture["documents"]:
            imported = knowledge.import_file(
                item["title"] + ".md", "text/markdown", item["text"].encode("utf-8"),
            )
            document_map[item["id"]] = imported["document"]["id"]
            asyncio.run(knowledge_worker.process_due(limit=3))
        for item in fixture["memories"]:
            memory.create_memory("L2", item["content"], tags="kig0 synthetic", source="kig0_fixture")

        reverse_documents = {value: key for key, value in document_map.items()}
        outcomes: list[dict] = []
        for case in fixture["cases"]:
            started = time.perf_counter()
            found = knowledge_search.hybrid_search(
                case["query"], limit=8, context_window=0, max_chars=8_000,
            )
            knowledge_ms = (time.perf_counter() - started) * 1000
            actual_documents = {
                reverse_documents.get(item["document_id"], "unknown")
                for item in found["results"]
            }
            expected_documents = set(case["expected_documents"])
            knowledge_hit = expected_documents <= actual_documents
            memory_hit = True
            memory_ms = 0.0
            if case["expected_memory_marker"]:
                started = time.perf_counter()
                memories = memory.search_memories(case["query"])
                memory_ms = (time.perf_counter() - started) * 1000
                memory_hit = any(
                    case["expected_memory_marker"] in item["content"] for item in memories
                )
            prepared = knowledge_context._prepare_results(
                query=case["query"], reason="kig0_baseline", results=found["results"],
                candidate_count=found["result_count"], token_budget=7_000, max_results=8,
                lore_text="", memory_text="", source_mode="explicit",
            )
            normalized, used = knowledge_context.validate_citations(
                "合成结论 [资料:K1]；伪造来源 [资料:K999]。", prepared,
            )
            citation_valid = bool(
                used and "[资料:K1]" in normalized and "[资料引用无效]" in normalized
            )
            outcomes.append({
                "case_id": case["id"], "category": case["category"],
                "knowledge_hit": knowledge_hit, "memory_hit": memory_hit,
                "citation_valid": citation_valid,
                "candidate_count": found["result_count"],
                "knowledge_tokens": prepared["knowledge_tokens"],
                "knowledge_latency_ms": round(knowledge_ms, 6),
                "memory_latency_ms": round(memory_ms, 6),
            })

        categories = Counter(item["category"] for item in outcomes)
        per_category = {}
        for category, count in sorted(categories.items()):
            rows = [item for item in outcomes if item["category"] == category]
            per_category[category] = {
                "cases": count,
                "knowledge_recall_rate": sum(item["knowledge_hit"] for item in rows) / count,
                "memory_recall_rate": (
                    sum(item["memory_hit"] for item in rows) / count
                    if category == "knowledge_memory" else None
                ),
            }
        conn = db.connect()
        try:
            schema_version = int(conn.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0])
            kig_tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND "
                "(name LIKE 'kig_%' OR name LIKE 'pwm_%' OR name IN ('derived_dependencies','source_refs'))"
            )]
        finally:
            conn.close()

        return {
            "report_version": "kig-construction-baseline-v1",
            "construction_baseline": {
                "repository": "liyi3068238601-oss/Xiadie",
                "predecessor_pr": 3,
                "base_branch": "main",
                "base_commit_sha": PREDECESSOR_SHA,
                "schema_version": schema_version,
                "next_schema_version": schema_version + 1,
                "frozen_protocols": [
                    cognitive_decision.PROTOCOL_VERSION,
                    cognitive_decision.REGISTRY_VERSION,
                    specialty_contracts.CONTRACT_VERSION,
                    context_assembler.PACKAGE_PROTOCOL_VERSION,
                    knowledge_search.SEARCH_PROTOCOL_VERSION,
                    "eap-decision-run-adapter-v1", "life-adapter-v1",
                ],
                "test_baseline": {
                    "backend": "2428 passed, 1 warning", "frontend": "50 passed",
                    "vite_modules": 190, "electron_contracts": 3,
                },
                "plan_version": "KIG v0.3", "recorded_at": "2026-07-27",
            },
            "fixture_sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
            "environment": {"python": platform.python_version(), "platform": platform.platform()},
            "privacy": {"synthetic_only": True, "contains_user_data": False, "provider_calls": 0},
            "scenario_counts": dict(sorted(categories.items())),
            "metrics": {
                "knowledge_recall_rate": sum(item["knowledge_hit"] for item in outcomes) / len(outcomes),
                "cross_memory_recall_rate": sum(
                    item["memory_hit"] for item in outcomes if item["category"] == "knowledge_memory"
                ) / categories["knowledge_memory"],
                "citation_allowlist_accuracy": sum(item["citation_valid"] for item in outcomes) / len(outcomes),
                "knowledge_latency_ms": _distribution([item["knowledge_latency_ms"] for item in outcomes]),
                "memory_latency_ms": _distribution([
                    item["memory_latency_ms"] for item in outcomes if item["category"] == "knowledge_memory"
                ]),
                "knowledge_tokens": _distribution([float(item["knowledge_tokens"]) for item in outcomes]),
                "unified_cross_source_evidence_rate": 0.0,
            },
            "per_category": per_category,
            "capability_matrix": [
                {"capability": "knowledge_import_parse_chunk", "state": "x", "owner": "Knowledge", "evidence": "knowledge.py/knowledge_parser.py/knowledge_chunker.py/knowledge_worker.py"},
                {"capability": "knowledge_fts_dense_search_v2", "state": "x", "owner": "Knowledge", "evidence": "knowledge_search.py/knowledge_embeddings.py"},
                {"capability": "knowledge_citation_locator_delete_grants", "state": "x", "owner": "Knowledge", "evidence": "knowledge_context.py/knowledge_cleanup.py/knowledge_grants.py"},
                {"capability": "context_hard_budget", "state": "arrow", "owner": "CTX", "evidence": "context_assembler.py/context_budget.py"},
                {"capability": "fragment_episode_saga", "state": "arrow", "owner": "MEM", "evidence": "memory.py/episodes.py/sagas.py"},
                {"capability": "life_authoritative_ledger", "state": "arrow", "owner": "LIFE", "evidence": "life_events.py/self_timeline.py"},
                {"capability": "task_toolrun_sources", "state": "partial", "owner": "Task/ToolRegistry", "evidence": "tasks and tool_logs exist; ToolRegistry not implemented", "existing": "Task CRUD 与已完成 tool_logs 可查询", "gap": "尚无正式 ToolRegistry 或来源 adapter", "minimal_delta": "KIG 只读并验证既有行，未来 ToolRegistry 继续拥有写入权", "rollback": "移除 KIG adapter，不修改 Task/ToolRun 权威行"},
                {"capability": "lore_readonly_sections", "state": "arrow", "owner": "Lore", "evidence": "lore.py"},
                {"capability": "unified_source_ref_registry", "state": "missing", "owner": "KIG", "evidence": "no KIG tables at Schema 71"},
                {"capability": "cross_source_query_plan_evidence", "state": "partial", "owner": "KIG", "evidence": "parallel CTX blocks exist; no unified candidate/evidence contract", "existing": "CTX 可为 Knowledge、Memory、History、Life 与 Lore 并列分配预算", "gap": "尚无统一 candidate、QueryPlan、EvidenceLink 或支持度校验", "minimal_delta": "新增 KIG 信封和 adapter，同时保留 CTX 最终预算所有权", "rollback": "关闭 KIG bundle，继续既有 CTX 并列装配"},
                {"capability": "pwm_projection", "state": "missing", "owner": "KIG", "evidence": "no pwm_ tables at Schema 71"},
                {"capability": "web_result_live_adapter", "state": "not_applicable", "owner": "Future ToolRegistry", "evidence": "compatibility slot only"},
            ],
            "kig_tables_at_baseline": kig_tables,
            "responsibility_conflicts": [],
            "authority_order": [
                "current user instruction", "CODEX_PROJECT_CONTEXT.md", "XIADIE_LONG_TERM_ROADMAP.md",
                "accepted ADR", "SPECIALTY_OWNERSHIP_AND_CONTRACT_MATRIX.md", "KIG plan",
            ],
            "ownership_boundaries": {
                "Knowledge": "documents, chunks, import, parsing, indexes, citations, grants and deletion",
                "CTX": "final ContextPackage assembly and hard token budget",
                "MEM": "fragments, episodes, sagas and memory_entities",
                "EAP": "affect, relationship, proactive candidate, delivery and feedback",
                "LIFE": "life events, schedules, goals, dates, diary and self timeline",
                "Task/ToolRegistry": "tasks and real external execution evidence",
                "KIG": "source adapters, derived evidence/version governance and rebuildable PWM projection",
            },
            "failure_modes": [
                "no_unified_source_adapter_registry", "no_cross_source_retrieval_candidate",
                "no_query_plan_protocol", "no_cross_source_evidence_link",
                "no_version_freshness_governance", "no_pwm_projection",
            ],
            "outcomes": outcomes,
        }


def render_markdown(report: dict) -> str:
    base, metrics = report["construction_baseline"], report["metrics"]
    lines = [
        "# KIG.0 ConstructionBaseline 与能力审计", "",
        f"- predecessor：LIFE PR #3 merge `{base['base_commit_sha']}`",
        f"- Schema：{base['schema_version']}；KIG 首个可用迁移：{base['next_schema_version']}",
        f"- 冻结测试基线：后端 `{base['test_baseline']['backend']}`；前端 `{base['test_baseline']['frontend']}`；Vite {base['test_baseline']['vite_modules']} modules；Electron contract {base['test_baseline']['electron_contracts']} 项",
        f"- 合成固定集：60 条；fixture SHA-256 `{report['fixture_sha256']}`；真实 Provider 调用 0",
        "", "## 60 场景当前基线", "",
        "| 场景 | 数量 | Knowledge 召回率 | Memory 召回率 |", "|---|---:|---:|---:|",
    ]
    for category, row in report["per_category"].items():
        memory_rate = "—" if row["memory_recall_rate"] is None else f"{row['memory_recall_rate']:.2%}"
        lines.append(f"| `{category}` | {row['cases']} | {row['knowledge_recall_rate']:.2%} | {memory_rate} |")
    lines += [
        "", "## 指标", "",
        f"- Knowledge 召回率：{metrics['knowledge_recall_rate']:.2%}。",
        f"- Knowledge+Memory 双源分别召回率：{metrics['cross_memory_recall_rate']:.2%}。",
        f"- 现有 Knowledge citation allowlist 准确率：{metrics['citation_allowlist_accuracy']:.2%}。",
        f"- Knowledge 延迟 P50/P90：{metrics['knowledge_latency_ms']['p50']:.3f}/{metrics['knowledge_latency_ms']['p90']:.3f} ms。",
        f"- Memory 延迟 P50/P90：{metrics['memory_latency_ms']['p50']:.3f}/{metrics['memory_latency_ms']['p90']:.3f} ms。",
        f"- Knowledge 注入 token 平均/P90：{metrics['knowledge_tokens']['average']:.1f}/{metrics['knowledge_tokens']['p90']:.1f}。",
        "- 跨源统一 Evidence 支持率：0%；当前只能由 CTX 并列装配，不能冒充 KIG 已实现。",
        "", "## 能力矩阵", "",
        "| 能力 | 状态 | 唯一所有者 | 代码证据 |", "|---|---|---|---|",
    ]
    markers = {"x": "[x]", "partial": "[~]", "missing": "[ ]", "arrow": "[→]", "not_applicable": "[-]"}
    for item in report["capability_matrix"]:
        lines.append(f"| `{item['capability']}` | {markers[item['state']]} | {item['owner']} | `{item['evidence']}` |")
    partial = [item for item in report["capability_matrix"] if item["state"] == "partial"]
    lines += ["", "## `[~]` 最小补差与回滚", ""]
    for item in partial:
        lines += [
            f"### `{item['capability']}`", "",
            f"- 已有：{item['existing']}。",
            f"- 缺失：{item['gap']}。",
            f"- 最小补差：{item['minimal_delta']}。",
            f"- 回滚：{item['rollback']}。", "",
        ]
    lines += [
        "", "## 审计结论", "",
        "- KnowledgeDocument、Chunk、导入、解析、删除、引用和 search v2 已完整存在，KIG 必须复用，禁止重建第二主链。",
        "- CTX、MEM、LIFE 与 Lore 保持单一写入者；KIG 只读来源并持久化最小派生依赖。",
        "- Task 与 tool_logs 可作为来源，但正式 ToolRegistry 尚未施工，KIG v1 只能验证已有 ToolRun 行。",
        "- 缺口集中在统一 SourceRef、跨源候选/证据、QueryPlan、版本/新鲜度与 PWM 派生投影。",
        "- `web_result` 只保留兼容位；KIG v1 不联网搜索、不抓取网页、不注册研究执行器。",
        "", "## 回滚", "",
        "KIG.0 仅新增合成 fixture、审计脚本、测试、报告和 ADR，不创建 Schema 72 或生产写路径；可整提交回滚且不影响用户数据。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    report = evaluate_fixture(fixture)
    JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MARKDOWN_PATH.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(JSON_PATH), "markdown": str(MARKDOWN_PATH), "metrics": report["metrics"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
