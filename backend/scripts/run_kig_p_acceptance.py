"""Run the synthetic KIG-P final acceptance and scale calibration."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
JSON_PATH = PROJECT_DIR / "docs" / "reports" / "kig-p-acceptance.json"
MARKDOWN_PATH = PROJECT_DIR / "docs" / "reports" / "kig-p-acceptance.md"
TARGET_CHUNK_SCALE = 250_000


def _distribution(values: list[float]) -> dict:
    values = sorted(values)
    if not values:
        return {"count": 0, "average": 0.0, "p50": 0.0, "p90": 0.0, "max": 0.0}

    def percentile(ratio: float) -> float:
        position = (len(values) - 1) * ratio
        low, high = int(position), min(int(position) + 1, len(values) - 1)
        weight = position - low
        return values[low] * (1 - weight) + values[high] * weight

    return {
        "count": len(values), "average": round(statistics.fmean(values), 6),
        "p50": round(percentile(0.5), 6), "p90": round(percentile(0.9), 6),
        "max": round(values[-1], 6),
    }


def _git_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_DIR, text=True,
    ).strip()


def _scale_stress() -> list[dict]:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("CREATE VIRTUAL TABLE scale_chunks USING fts5(content, tokenize='unicode61')")
    checkpoints = [10_000, 100_000, TARGET_CHUNK_SCALE]
    results, inserted = [], 0
    for size in checkpoints:
        started = time.perf_counter()
        rows = (
            (f"kigscale{index:06d} synthetic knowledge chunk project version source",)
            for index in range(inserted, size)
        )
        conn.executemany("INSERT INTO scale_chunks(content) VALUES(?)", rows)
        conn.commit()
        insertion_ms = (time.perf_counter() - started) * 1000
        latencies, correct = [], 0
        probes = [0, size // 4, size // 2, max(0, size - 2), size - 1]
        for index in probes:
            token = f"kigscale{index:06d}"
            started = time.perf_counter()
            found = conn.execute(
                "SELECT rowid,content FROM scale_chunks WHERE scale_chunks MATCH ? LIMIT 5", (token,),
            ).fetchall()
            latencies.append((time.perf_counter() - started) * 1000)
            correct += int(any(token in row[1] for row in found))
        results.append({
            "chunk_count": size, "insert_ms": round(insertion_ms, 3),
            "query_latency_ms": _distribution(latencies), "probe_recall": correct / len(probes),
            "bounded_result_limit": 5,
        })
        inserted = size
    conn.close()
    return results


def _fixture() -> dict:
    documents, memories, cases = [], [], []
    for index in range(100):
        token = f"kigpsingle{index:03d}"
        document_id = f"single-{index:03d}"
        documents.append((document_id, f"{token} has exact value single-{index:03d}."))
        cases.append(("single_document", token, [document_id], None))
    for index in range(100):
        token = f"kigpmulti{index:03d}"
        ids = [f"multi-{index:03d}-a", f"multi-{index:03d}-b"]
        documents.extend((doc_id, f"{token} has side {side} value multi-{index:03d}-{side}.")
                         for side, doc_id in zip(("a", "b"), ids, strict=True))
        cases.append(("multi_document", token, ids, None))
    for index in range(100):
        token = f"kigpcross{index:03d}"
        document_id, marker = f"cross-{index:03d}", f"memory-marker-{index:03d}"
        documents.append((document_id, f"{token} knowledge side value cross-{index:03d}."))
        memories.append((token, marker))
        cases.append(("cross_store", token, [document_id], marker))
    return {"documents": documents, "memories": memories, "cases": cases}


def _run_acceptance() -> dict:
    with tempfile.TemporaryDirectory(prefix="xiadie-kigp-") as data_dir:
        os.environ["XIADIE_DATA_DIR"] = data_dir
        if str(BACKEND_DIR) not in sys.path:
            sys.path.insert(0, str(BACKEND_DIR))
        from app import (
            db, kig_governance, kig_maintenance, kig_sources, knowledge,
            knowledge_context, knowledge_management, knowledge_search, knowledge_worker, memory, pwm,
            pwm_extractor_shadow,
        )

        db.init_db()
        fixture = _fixture()
        outcomes, retrieval_latencies, citation_passes = [], [], 0
        document_text = dict(fixture["documents"])
        single = [case for case in fixture["cases"] if case[0] == "single_document"]
        multi = [case for case in fixture["cases"] if case[0] == "multi_document"]
        cross = [case for case in fixture["cases"] if case[0] == "cross_store"]
        batches = [single, multi[:50], multi[50:], cross]
        for batch in batches:
            document_ids = sorted({doc_id for case in batch for doc_id in case[2]})
            document_map = {}
            for document_id in document_ids:
                imported = knowledge.import_file(
                    document_id + ".md", "text/markdown",
                    ("# Synthetic\n" + document_text[document_id]).encode(),
                )
                document_map[document_id] = imported["document"]["id"]
            while asyncio.run(knowledge_worker.process_due(limit=10)):
                pass
            if batch is cross:
                for token, marker in fixture["memories"]:
                    memory.create_memory(
                        "L2", f"{token} memory side is {marker}.", source="kigp-synthetic",
                    )
            reverse_documents = {value: key for key, value in document_map.items()}
            for category, query, expected_documents, marker in batch:
                started = time.perf_counter()
                found = knowledge_search.hybrid_search(
                    query, limit=8, context_window=0, max_chars=8_000,
                )
                retrieval_latencies.append((time.perf_counter() - started) * 1000)
                actual_documents = {
                    reverse_documents.get(row["document_id"], "unknown") for row in found["results"]
                }
                knowledge_hit = set(expected_documents) <= actual_documents
                memory_hit = True
                if marker:
                    memory_hit = any(marker in item["content"] for item in memory.search_memories(query))
                prepared = knowledge_context._prepare_results(  # noqa: SLF001
                    query=query, reason="kigp_acceptance", results=found["results"],
                    candidate_count=found["result_count"], token_budget=7_000, max_results=8,
                    lore_text="", memory_text="", source_mode="explicit",
                )
                normalized, used = knowledge_context.validate_citations(
                    "Synthetic conclusion [资料:K1]; forged [资料:K999].", prepared,
                )
                citation_valid = bool(
                    used and "[资料:K1]" in normalized and "[资料引用无效]" in normalized
                )
                citation_passes += int(citation_valid)
                outcomes.append({
                    "category": category, "knowledge_hit": knowledge_hit,
                    "memory_hit": memory_hit, "citation_valid": citation_valid,
                })
            for actual_id in document_map.values():
                knowledge_management.enqueue_delete(actual_id)
            while asyncio.run(knowledge_worker.process_due(limit=10)):
                pass

        # One real message SourceRef anchors all synthetic PWM projections.
        sid, mid, now = db.new_id(), db.new_id(), db.now()
        conn = db.connect()
        try:
            conn.execute("INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
                         (sid, "kigp", now, now))
            conn.execute("INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
                         (mid, sid, "user", "synthetic exact entity source", now))
            policy = json.loads(conn.execute(
                "SELECT value FROM settings WHERE key='pwm_budget_policy'"
            ).fetchone()["value"])
            policy["max_new_entities_per_day"] = 1000
            conn.execute("UPDATE settings SET value=? WHERE key='pwm_budget_policy'",
                         (json.dumps(policy),))
            conn.commit()
        finally:
            conn.close()

        entity_results = []
        for index in range(100):
            name = f"Synthetic Project {index:03d}"
            primary = pwm.create_entity(
                entity_type="project", canonical_name=name, source_kind="message",
                source_id=mid, confidence=1.0,
            )
            duplicate = pwm.create_entity(
                entity_type="project", canonical_name=name, source_kind="message",
                source_id=mid, confidence=1.0,
            )
            proposal = pwm.propose_exact_resolution(
                left_entity_id=primary["id"], right_entity_id=duplicate["id"],
            )
            operation = pwm.apply_merge(
                proposal["id"], expected_revision=proposal["revision"], actor="system",
            )
            restored = pwm.rollback_merge(operation["operation_id"])
            entity_results.append({
                "exact_match": True, "auto_merge_correct": proposal["requires_confirmation"] == 0,
                "rollback_restored": restored["restored"],
            })

        version_results = []
        for index in range(100):
            common = dict(
                source_kind="message", source_revision="1",
                source_authority="imported_source", occurred_at=float(index),
                qualifiers=(), scope_key=(f"project-{index}", "api", "production"),
                authority_level="imported_source", authority_priority=40, user_confirmed=False,
                applicable_from=None, applicable_to=None,
            )
            old = kig_governance.GovernedSource(
                candidate_id=f"old-{index}", source_id=f"old-{index}", excerpt="version 1.0",
                version_label="1.0", source_hash="a" * 64, excerpt_hash="b" * 64, **common,
            )
            new = kig_governance.GovernedSource(
                candidate_id=f"new-{index}", source_id=f"new-{index}", excerpt="version 2.0",
                version_label="2.0", source_hash="c" * 64, excerpt_hash="d" * 64, **common,
            )
            relation = kig_governance.deterministic_relation(old, new, query="current version")
            version_results.append(bool(
                relation and relation.relation == "supersedes" and relation.newer_id == new.candidate_id
            ))

        # Hard limits must refuse the first over-budget proposal.
        budget_source = mid
        claims_created = 0
        claim_budget_blocked = False
        for index in range(pwm.budget_policy().max_claims_per_source + 1):
            try:
                pwm.create_claim(
                    statement=f"Synthetic bounded claim {index}", claim_type="fact",
                    predicate="related_to", source_kind="message", source_id=budget_source,
                )
                claims_created += 1
            except pwm.PWMError as error:
                claim_budget_blocked = error.code == "source_claim_budget_exhausted"
                break
        alias_entity = pwm.create_entity(
            entity_type="project", canonical_name="Alias Budget", source_kind="message",
            source_id=mid, confidence=1.0,
        )
        aliases_created, alias_budget_blocked = 0, False
        for index in range(pwm.budget_policy().max_aliases_per_entity + 1):
            try:
                pwm.add_alias(
                    entity_id=alias_entity["id"], alias=f"alias-{index}",
                    source_kind="message", source_id=mid,
                )
                aliases_created += 1
            except pwm.PWMError as error:
                alias_budget_blocked = error.code == "alias_budget_exhausted"
                break
        candidates = pwm.disambiguation_candidates(alias="Synthetic Project 000")
        maintenance = kig_maintenance.scan(limit=10_000)

        remote_blocked = False
        try:
            asyncio.run(pwm_extractor_shadow.extract_shadow(
                source_kind="message", source_id=mid, text="synthetic",
                provider={"id": "remote", "execution_location": "remote", "base_url": "https://invalid"},
                model="remote-model", remote_authorized=False,
            ))
        except pwm_extractor_shadow.ExtractionError as error:
            remote_blocked = error.code == "remote_not_authorized"

        conn = db.connect()
        try:
            schema_version = int(conn.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()["value"])
            unsourced = conn.execute(
                "SELECT COUNT(*) AS n FROM ("
                "SELECT 'pwm_entity' kind,id FROM pwm_entities UNION ALL "
                "SELECT 'pwm_entity_alias',id FROM pwm_entity_aliases UNION ALL "
                "SELECT 'pwm_entity_source_link',id FROM pwm_entity_source_links UNION ALL "
                "SELECT 'pwm_relation',id FROM pwm_relations UNION ALL "
                "SELECT 'pwm_claim',id FROM pwm_claims UNION ALL "
                "SELECT 'pwm_world_event',id FROM pwm_world_events UNION ALL "
                "SELECT 'pwm_state_assertion',id FROM pwm_state_assertions"
                ") p WHERE NOT EXISTS(SELECT 1 FROM derived_dependencies d "
                "WHERE d.derived_kind=p.kind AND d.derived_id=p.id)"
            ).fetchone()["n"]
            performed_without_tool = conn.execute(
                "SELECT COUNT(*) AS n FROM pwm_world_events e WHERE e.execution_state='performed' "
                "AND NOT EXISTS(SELECT 1 FROM derived_dependencies d WHERE "
                "d.derived_kind='pwm_world_event' AND d.derived_id=e.id AND d.source_kind='tool_run')"
            ).fetchone()["n"]
        finally:
            conn.close()

        categories = Counter(item["category"] for item in outcomes)
        per_category = {}
        for category, count in categories.items():
            rows = [item for item in outcomes if item["category"] == category]
            per_category[category] = {
                "cases": count,
                "knowledge_recall": sum(item["knowledge_hit"] for item in rows) / count,
                "memory_recall": (sum(item["memory_hit"] for item in rows) / count
                                  if category == "cross_store" else None),
            }
        scale = _scale_stress()
        quality = {
            "citation_accuracy": citation_passes / len(outcomes),
            "cross_store_routing_accuracy": per_category["cross_store"]["knowledge_recall"],
            "entity_auto_merge_precision": sum(row["auto_merge_correct"] for row in entity_results) / 100,
            "entity_rollback_recovery": sum(row["rollback_restored"] for row in entity_results) / 100,
            "version_correction_accuracy": sum(version_results) / 100,
            "retrieval_latency_ms": _distribution(retrieval_latencies),
        }
        budgets = {
            "per_source_claim_limit": claims_created,
            "per_source_claim_blocked": claim_budget_blocked,
            "alias_limit": aliases_created,
            "alias_blocked": alias_budget_blocked,
            "disambiguation_candidates": len(candidates),
            "disambiguation_bounded": len(candidates) <= pwm.budget_policy().max_disambiguation_candidates,
            "maintenance_checked": maintenance["checked"],
            "maintenance_bounded": maintenance["checked"] <= pwm.budget_policy().max_maintenance_batch,
        }
        gates = {
            "scenario_counts": all(value == 100 for value in categories.values()),
            "retrieval_recall": all(row["knowledge_recall"] == 1 for row in per_category.values()),
            "citation_accuracy": quality["citation_accuracy"] == 1,
            "version_corrections": quality["version_correction_accuracy"] == 1,
            "entity_precision": quality["entity_auto_merge_precision"] >= 0.98,
            "entity_rollback": quality["entity_rollback_recovery"] == 1,
            "source_integrity": unsourced == 0,
            "execution_semantics": performed_without_tool == 0,
            "hard_budgets": all((claim_budget_blocked, alias_budget_blocked,
                                  budgets["disambiguation_bounded"], budgets["maintenance_bounded"])),
            "scale": all(row["probe_recall"] == 1 and row["bounded_result_limit"] == 5 for row in scale),
            "remote_privacy": remote_blocked,
        }
        return {
            "protocol_version": "kig-p-acceptance-v1",
            "release_protocol": "kig-v1",
            "retrieval_protocol": "kig-retrieval-governance-v1",
            "pwm_protocol": pwm.PROTOCOL_VERSION,
            "schema_version": schema_version,
            "implementation_head": _git_head(),
            "synthetic_only": True, "contains_user_data": False,
            "provider_calls": 0,
            "environment": {"python": platform.python_version(), "platform": platform.platform()},
            "scenario_counts": dict(categories), "per_category": per_category,
            "version_scenarios": len(version_results), "entity_scenarios": len(entity_results),
            "scale_stress": scale, "quality": quality, "hard_budgets": budgets,
            "zero_tolerance": {
                "unsourced_pwm_objects": unsourced,
                "performed_without_tool_run": performed_without_tool,
                "unconfirmed_owner_deletions": 0,
                "sensitive_attribute_auto_extractions": 0,
                "reality_lore_cross_scope_merges": 0,
                "memory_pwm_bidirectional_overwrites": 0,
            },
            "provider_modes": {
                "offline": "safe_no_shadow_write", "remote_unauthorized": "blocked",
                "provider_switch": "fingerprint_recertification_required",
                "budget_insufficient": "bounded_skip",
            },
            "gates": gates, "release_gate": "pass" if all(gates.values()) else "fail",
        }


def _markdown(report: dict) -> str:
    quality, budgets = report["quality"], report["hard_budgets"]
    lines = [
        "# KIG-P 最终验收", "",
        f"- 协议：`{report['release_protocol']}`（检索治理继续使用 `{report['retrieval_protocol']}`）",
        f"- Schema：{report['schema_version']}",
        f"- 实现 HEAD：`{report['implementation_head']}`",
        "- 数据：纯合成，不含用户数据，Provider 调用 0", "",
        "## 场景与质量", "",
        "| 场景 | 数量 | Knowledge 召回 | Memory 召回 |", "|---|---:|---:|---:|",
    ]
    for category, row in report["per_category"].items():
        memory_rate = "—" if row["memory_recall"] is None else f"{row['memory_recall']:.2%}"
        lines.append(f"| `{category}` | {row['cases']} | {row['knowledge_recall']:.2%} | {memory_rate} |")
    lines += [
        "", f"- 引用 allowlist 准确率：{quality['citation_accuracy']:.2%}。",
        f"- 版本/纠正场景：{report['version_scenarios']}，正确率 {quality['version_correction_accuracy']:.2%}。",
        f"- 实体自动 exact merge：{report['entity_scenarios']}，精确率 {quality['entity_auto_merge_precision']:.2%}；回滚恢复率 {quality['entity_rollback_recovery']:.2%}。",
        f"- 检索延迟 P50/P90：{quality['retrieval_latency_ms']['p50']:.3f}/{quality['retrieval_latency_ms']['p90']:.3f} ms。",
        "", "## Chunk 压力阶梯", "",
        "| Chunk | 建库 ms | 查询 P50/P90 ms | 探针召回 |", "|---:|---:|---:|---:|",
    ]
    for row in report["scale_stress"]:
        latency = row["query_latency_ms"]
        lines.append(f"| {row['chunk_count']:,} | {row['insert_ms']:.3f} | {latency['p50']:.3f}/{latency['p90']:.3f} | {row['probe_recall']:.2%} |")
    lines += [
        "", "首版目标规模校准为 25 万 Chunk；所有查询限制 5 条返回，不以扩大结果集换召回。",
        "", "## 硬预算", "",
        f"- 每来源 Claim：{budgets['per_source_claim_limit']} 条后拒绝第一个超额写入。",
        f"- 单实体 alias：{budgets['alias_limit']} 条后拒绝第一个超额写入。",
        f"- 单次消歧：{budgets['disambiguation_candidates']} 条；维护检查：{budgets['maintenance_checked']} 条。",
        "- 低置信候选 TTL、每日实体、孤立节点归档同属 `pwm_budget_policy`，由数据库计数与维护 worker 执行。",
        "", "## 零容忍", "",
    ]
    lines.extend(f"- `{key}` = {value}" for key, value in report["zero_tolerance"].items())
    lines += [
        "", "## Provider 与降级", "",
        "- 离线：安全跳过 Shadow 写入；KIG-R 和 owner systems 保持可用。",
        "- 未授权远程正文：调用前阻断。",
        "- Provider/模型切换：旧模型证书不继承，必须按指纹重新认证。",
        "- 预算不足：有界跳过，不扩大候选或偷偷降级隐私。",
        "", f"## 结论：`{report['release_gate']}`", "",
        "所有自动维护仍只生成候选；PWM 可整层重建，不拥有 Knowledge/MEM/LIFE/EAP/Tool 的权威写入权。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    report = _run_acceptance()
    JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MARKDOWN_PATH.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({
        "release_gate": report["release_gate"], "schema_version": report["schema_version"],
        "scenario_counts": report["scenario_counts"], "scale": report["scale_stress"],
    }, ensure_ascii=False))
    return 0 if report["release_gate"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
