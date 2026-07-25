from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import db  # noqa: E402
from app import cognitive_decision as cds  # noqa: E402
from app import memory  # noqa: E402
from app import memory_shadow_oracle as oracle  # noqa: E402
from app import memory_shadow_proposals as proposals  # noqa: E402

FIXTURE_PATH = BACKEND_DIR / "tests" / "fixtures" / "cds9_memory_shadow_v1.json"
JSON_PATH = PROJECT_DIR / "docs" / "reports" / "cds-9-memory-shadow.json"
MD_PATH = PROJECT_DIR / "docs" / "reports" / "cds-9-memory-shadow.md"
LEDGER_TABLES = frozenset({"decision_runs", "decision_run_events"})
MEM_DOMAIN_PREFIXES = ("memory_",)
MEM_DOMAIN_TABLES = frozenset({"episode_summaries", "saga_summaries"})
EVALUATION_NOW = 2_300_000_000.0


def _create_fragment(case: dict, role: str) -> str:
    group = case["group"]
    topic = case["scenario"]["topic"]
    marker = case["id"]
    if group in {
        "user_supersedes_automatic", "automatic_cannot_supersede_user",
        "injection_cannot_supersede_user", "user_confirmed_newer_wins",
        "observed_newer_wins", "user_correction",
    }:
        content = f"用户喜欢{topic}{marker}" if role == "older" else f"用户不喜欢{topic}{marker}"
    elif group == "conditional_difference":
        content = f"用户在工作日选择{topic}{marker}" if role == "older" else f"用户在周末选择{topic}{marker}"
    else:
        content = f"用户喜欢{topic}{marker}"
    item = memory.create_memory("L1", content)
    return item["id"]


def _origin_source(origin: str) -> str:
    return {
        "user_confirmed": "user_confirmed_fact",
        "observed": "conversation",
        "automatic": "shared_lookup",
        "system_injected": "knowledge_reference",
    }[origin]


def _prepare_case(case: dict):
    values = case["input"]
    if case["decision_kind"] == proposals.CONFLICT_DECISION_KIND:
        older_id, newer_id = _create_fragment(case, "older"), _create_fragment(case, "newer")
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE memory_fragments SET observation_source=?,created_at=? WHERE id=?",
                (_origin_source(values["older_origin"]), EVALUATION_NOW - 2, older_id),
            )
            conn.execute(
                "UPDATE memory_fragments SET observation_source=?,created_at=?,kind=? WHERE id=?",
                (_origin_source(values["newer_origin"]), EVALUATION_NOW - 1,
                 "correction" if values["relation_hint"] == "correction" else "preference", newer_id),
            )
            conn.commit()
        finally:
            conn.close()
        payload = proposals.build_conflict_input(
            older_id, newer_id, condition_changed=values["condition_changed"],
        )
        if payload.relation_hint != values["relation_hint"]:
            raise ValueError(f"fixture relation does not match adapter: {case['id']}")
        expected = {
            **case["expected"],
            "superseded_id": older_id if case["expected"]["superseded_id"] else None,
        }
        return payload, expected
    fragment_id = _create_fragment(case, "fragment")
    age_days = case["scenario"]["age_days"] if values["retention_band"] == "low" else 1
    created = EVALUATION_NOW - age_days * 86_400
    cooling_since = EVALUATION_NOW - 40 * 86_400 if values["status"] == "cooling" else None
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET observation_source=?,status=?,importance=?,confidence=?,"
            "layer=?,kind='observation',created_at=?,updated_at=?,cooling_since=? WHERE id=?",
            (_origin_source(values["origin"]), values["status"],
             0.9 if values["retention_band"] == "high" else 0.0,
             1.0 if values["retention_band"] == "high" else 0.0,
             "L0" if values["protected"] else "L1", created,
             cooling_since if cooling_since is not None else created, cooling_since, fragment_id),
        )
        conn.commit()
    finally:
        conn.close()
    return proposals.build_retention_input(fragment_id, now=EVALUATION_NOW), case["expected"]


def _table_snapshot(names_filter) -> dict[str, tuple[int, str]]:
    conn = db.connect()
    try:
        names = [
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            if names_filter(row["name"])
        ]
        result = {}
        for name in names:
            rows = [tuple(row) for row in conn.execute(f'SELECT * FROM "{name}"').fetchall()]
            encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str)
            result[name] = (len(rows), hashlib.sha256(encoded.encode("utf-8")).hexdigest())
        return result
    finally:
        conn.close()


def _schema_version() -> int:
    conn = db.connect()
    try:
        row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        return int(row["value"])
    finally:
        conn.close()


def build_report(fixture: dict) -> dict:
    original_data_dir, original_db_path = db.DATA_DIR, db.DB_PATH
    evaluation_dir = tempfile.mkdtemp(prefix="xiadie-cds9-run-")
    db.DATA_DIR = evaluation_dir
    db.DB_PATH = os.path.join(evaluation_dir, "xiadie.db")
    outcomes = []
    try:
        db.init_db()
        schema_version = _schema_version()
        prepared = [(case, *_prepare_case(case)) for case in fixture["cases"]]
        mem_before = _table_snapshot(
            lambda name: name.startswith(MEM_DOMAIN_PREFIXES) or name in MEM_DOMAIN_TABLES
        )
        ledger_before = _table_snapshot(lambda name: name in LEDGER_TABLES)
        for case, payload, expected in prepared:
            bindings = payload.fragment_bindings
            source = proposals.source_snapshots(bindings)
            candidates = proposals.candidate_refs(bindings)
            definition = cds.REGISTRY.get(case["decision_kind"])
            header = cds.build_header(
                decision_kind=case["decision_kind"], policy_version=definition.output_schema_version,
                request_id=f"cds9:{case['id']}", mode=cds.DecisionMode.SHADOW,
                source_snapshot=source,
            )
            if case["decision_kind"] == proposals.CONFLICT_DECISION_KIND:
                result = proposals.conflict_fallback(payload)
                proposals.validate_conflict(payload, result)
                exact = (
                    result.relation_type == expected["relation_type"]
                    and result.superseded_id == expected["superseded_id"]
                )
                weak_override = (
                    payload.older_origin == "user_confirmed"
                    and payload.newer_origin in {"automatic", "system_injected"}
                    and result.relation_type == "supersedes"
                )
                injection_recovery = False
                proposal_value = result.relation_type
            else:
                result = proposals.retention_fallback(payload)
                proposals.validate_retention(payload, result)
                exact = (
                    result.proposed_action == expected["proposed_action"]
                    and result.recovery_allowed is expected["recovery_allowed"]
                )
                weak_override = False
                injection_recovery = payload.injection_only and result.recovery_allowed
                proposal_value = result.proposed_action
            run, _ = cds.create_run(header, payload, candidates, now=EVALUATION_NOW)
            violations = oracle.safety_violations(case["decision_kind"], payload, result)
            current_source = proposals.source_snapshots(
                proposals.load_fragment_bindings(payload.candidate_ids)
            )
            runtime_outcome = cds.evaluate_output(
                run.id, header, payload, json.dumps(result.__dict__, ensure_ascii=False),
                current_snapshot=current_source,
            )
            outcomes.append({
                "case_id": case["id"],
                "group": case["group"],
                "decision_kind": case["decision_kind"],
                "proposal_value": proposal_value,
                "proposal_exact": exact,
                "weak_source_override": weak_override,
                "injection_recovery": injection_recovery,
                "tombstone_proposed": result.tombstone_allowed,
                "advisory_only": result.advisory_only,
                "safety_violations": list(violations),
                "application_allowed": runtime_outcome["application_allowed"],
            })
        mem_after = _table_snapshot(
            lambda name: name.startswith(MEM_DOMAIN_PREFIXES) or name in MEM_DOMAIN_TABLES
        )
        ledger_after = _table_snapshot(lambda name: name in LEDGER_TABLES)
    finally:
        db.DATA_DIR, db.DB_PATH = original_data_dir, original_db_path
        shutil.rmtree(evaluation_dir, ignore_errors=True)
    changed_mem_tables = sorted(
        name for name in set(mem_before) | set(mem_after) if mem_before.get(name) != mem_after.get(name)
    )
    changed_ledger_tables = sorted(
        name for name in set(ledger_before) | set(ledger_after)
        if ledger_before.get(name) != ledger_after.get(name)
    )
    total = len(outcomes)
    return {
        "report_version": "cds9-memory-shadow-report-v1",
        "protocol_version": fixture["protocol_version"],
        "synthetic_only": True,
        "contains_user_data": False,
        "fixture_sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
        "sample_count": total,
        "rule_template_count": len({case["group"] for case in fixture["cases"]}),
        "decision_kind_counts": dict(sorted(Counter(row["decision_kind"] for row in outcomes).items())),
        "group_counts": dict(sorted(Counter(row["group"] for row in outcomes).items())),
        "proposal_exact_rate": sum(row["proposal_exact"] for row in outcomes) / total,
        "weak_source_override_rate": sum(row["weak_source_override"] for row in outcomes) / total,
        "injection_recovery_rate": sum(row["injection_recovery"] for row in outcomes) / total,
        "tombstone_proposal_rate": sum(row["tombstone_proposed"] for row in outcomes) / total,
        "advisory_only_rate": sum(row["advisory_only"] for row in outcomes) / total,
        "safety_violation_count": sum(len(row["safety_violations"]) for row in outcomes),
        "oracle_version": oracle.ORACLE_VERSION,
        "shadow_ledger_write_count": len(changed_ledger_tables),
        "changed_shadow_ledger_tables": changed_ledger_tables,
        "mem_domain_write_count": len(changed_mem_tables),
        "changed_mem_domain_tables": changed_mem_tables,
        "schema_version": schema_version,
        "schema_changed": schema_version != 62,
        "fallback_kind": "pure_deterministic",
        "application_owner": "mem",
        "outcomes": outcomes,
    }


def render_markdown(report: dict) -> str:
    return "\n".join([
        "# CDS.9 记忆冲突、保留与再巩固纯 Shadow 评测",
        "",
        f"- 样本：{report['sample_count']} 个纯合成变体，覆盖 {report['rule_template_count']} 个规则模板；不含用户数据，不调用 Provider。",
        f"- Fixture SHA-256：`{report['fixture_sha256']}`",
        f"- DecisionKind：冲突 {report['decision_kind_counts'][proposals.CONFLICT_DECISION_KIND]}；保留 {report['decision_kind_counts'][proposals.RETENTION_DECISION_KIND]}。",
        "",
        "## 完成门",
        "",
        "| 指标 | 结果 | 门槛 |",
        "|---|---:|---:|",
        f"| 提案精确匹配率 | {report['proposal_exact_rate']:.2%} | 100% |",
        f"| 弱来源覆盖率 | {report['weak_source_override_rate']:.2%} | 0 |",
        f"| 仅注入证据恢复率 | {report['injection_recovery_rate']:.2%} | 0 |",
        f"| tombstone 提案率 | {report['tombstone_proposal_rate']:.2%} | 0 |",
        f"| advisory_only 保持率 | {report['advisory_only_rate']:.2%} | 100% |",
        f"| Shadow 共享账本写入表数 | {report['shadow_ledger_write_count']} | >0 |",
        f"| MEM 领域表写入数 | {report['mem_domain_write_count']} | 0 |",
        "",
        "## 边界",
        "",
        f"- Schema 保持 {report['schema_version']}，未新增迁移、表或列。",
        "- memory_conflict_proposal 与 memory_retention_proposal 使用独立输入/输出 Schema，均固定 Shadow。",
        f"- 独立 oracle：`{report['oracle_version']}`，不读取 fixture expected。",
        "- fallback 复用 MEM 纯投影；Shadow 只写共享 DecisionRun 账本，MEM 仍是唯一 application owner。",
        "- 自动或系统注入来源不能覆盖用户确认；仅注入证据不能恢复 frozen 记忆。",
        "- CDS 不写 Fragment、关系、生命周期、Episode 或 Saga 正式状态，也不能产生 tombstone。",
        "",
    ])


def main() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if fixture["scenario_count"] != 280 or not fixture["synthetic_only"] or fixture["contains_user_data"]:
        raise ValueError("CDS.9 fixture boundary failed")
    report = build_report(fixture)
    JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MD_PATH.write_text(render_markdown(report), encoding="utf-8")


if __name__ == "__main__":
    main()
