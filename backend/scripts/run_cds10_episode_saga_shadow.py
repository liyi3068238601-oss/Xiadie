from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import replace
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import cognitive_decision as cds  # noqa: E402
from app import db, entities, episode_summary, episodes, memory, sagas  # noqa: E402
from app import episode_saga_shadow as shadow  # noqa: E402
from app import episode_saga_shadow_oracle as oracle  # noqa: E402

FIXTURE_PATH = BACKEND_DIR / "tests" / "fixtures" / "cds10_episode_saga_shadow_v1.json"
QUALITY_PATH = BACKEND_DIR / "tests" / "fixtures" / "cds10_episode_saga_quality_v1.json"
JSON_PATH = PROJECT_DIR / "docs" / "reports" / "cds-10-episode-saga-shadow.json"
MD_PATH = PROJECT_DIR / "docs" / "reports" / "cds-10-episode-saga-shadow.md"
LEDGER_TABLES = frozenset({"decision_runs", "decision_run_events"})
MEM_DOMAIN_PREFIXES = ("memory_", "episode_", "saga_")
EVALUATION_NOW = 2_300_000_000.0


def payload_from_case(case: dict):
    values = case["input"]
    if case["decision_kind"] == shadow.EPISODE_DECISION_KIND:
        return shadow.EpisodeBoundaryInput(
            candidate_ids=tuple(values["candidate_ids"]),
            same_goal=values["same_goal"], causal_chain=values["causal_chain"],
            turning_point_ids=tuple(values["turning_point_ids"]),
            outcome_present=values["outcome_present"],
            projected_confidence=values["projected_confidence"],
        )
    return shadow.SagaTransitionInput(
        candidate_ids=tuple(values["candidate_ids"]),
        target_saga_id=values["target_saga_id"], target_status=values["target_status"],
        transition_hint=values["transition_hint"], evidence_origin=values["evidence_origin"],
        projected_confidence=values["projected_confidence"],
    )


def _with_synthetic_bindings(payload):
    source_kind = "memory_fragment" if isinstance(payload, shadow.EpisodeBoundaryInput) else "memory_episode"
    bindings = tuple(shadow.NarrativeSourceBinding(
        source_id=item, source_kind=source_kind, revision="synthetic-v1",
        content_hash=hashlib.sha256(f"{source_kind}:{item}".encode()).hexdigest(),
    ) for item in payload.candidate_ids)
    provenance = shadow.NarrativeCandidateProvenance(
        candidate_id=f"candidate:{payload.candidate_ids[0]}",
        candidate_kind=(
            "memory_episode_candidate"
            if isinstance(payload, shadow.EpisodeBoundaryInput)
            else "saga_group_candidate"
        ),
        status=("pending" if isinstance(payload, shadow.EpisodeBoundaryInput) else "qualified"),
        policy_version="synthetic-candidate-v1",
        content_hash=hashlib.sha256(
            f"candidate:{':'.join(payload.candidate_ids)}".encode()
        ).hexdigest(),
    )
    if isinstance(payload, shadow.EpisodeBoundaryInput):
        return replace(payload, source_bindings=bindings, candidate_provenance=provenance)
    target_binding = None
    if payload.target_saga_id:
        target_binding = shadow.NarrativeSourceBinding(
            source_id=payload.target_saga_id, source_kind="memory_saga", revision="synthetic-v1",
            content_hash=hashlib.sha256(f"memory_saga:{payload.target_saga_id}".encode()).hexdigest(),
        )
    return replace(
        payload, source_bindings=bindings, target_binding=target_binding,
        candidate_provenance=provenance,
    )


def _table_snapshot(names_filter) -> dict[str, tuple[int, str]]:
    conn = db.connect()
    try:
        names = [
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall() if names_filter(row["name"])
        ]
        result = {}
        for name in names:
            rows = [tuple(row) for row in conn.execute(f'SELECT * FROM "{name}"').fetchall()]
            encoded = json.dumps(rows, ensure_ascii=False, default=str)
            result[name] = (len(rows), hashlib.sha256(encoded.encode()).hexdigest())
        return result
    finally:
        conn.close()


def _schema_version() -> int:
    conn = db.connect()
    try:
        return int(conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()["value"])
    finally:
        conn.close()


def build_report(fixture: dict) -> dict:
    original_data_dir, original_db_path = db.DATA_DIR, db.DB_PATH
    evaluation_dir = tempfile.mkdtemp(prefix="xiadie-cds10-run-")
    db.DATA_DIR = evaluation_dir
    db.DB_PATH = os.path.join(evaluation_dir, "xiadie.db")
    outcomes = []
    try:
        db.init_db()
        schema_version = _schema_version()
        mem_before = _table_snapshot(lambda name: name.startswith(MEM_DOMAIN_PREFIXES))
        ledger_before = _table_snapshot(lambda name: name in LEDGER_TABLES)
        for case in fixture["cases"]:
            payload = _with_synthetic_bindings(payload_from_case(case))
            source = shadow.input_source_snapshots(payload)
            candidates = shadow.candidate_refs(payload.source_bindings)
            definition = cds.REGISTRY.get(case["decision_kind"])
            header = cds.build_header(
                decision_kind=case["decision_kind"],
                policy_version=definition.output_schema_version,
                request_id=f"cds10:{case['id']}", mode=cds.DecisionMode.SHADOW,
                source_snapshot=source,
            )
            result = shadow.episode_fallback(payload) if case["decision_kind"] == shadow.EPISODE_DECISION_KIND else shadow.saga_fallback(payload)
            definition.validator(payload, result)
            run, _ = cds.create_run(header, payload, candidates, now=EVALUATION_NOW)
            runtime = cds.evaluate_output(
                run.id, header, payload, json.dumps(result.__dict__, ensure_ascii=False),
                current_snapshot=source,
            )
            proposal = result.proposed_action if isinstance(result, shadow.EpisodeBoundaryProposal) else result.proposed_transition
            outcomes.append({
                "case_id": case["id"], "group": case["group"],
                "decision_kind": case["decision_kind"], "proposal": proposal,
                "proposal_exact": proposal == case["expected"]["proposal"],
                "candidate_subset": set(result.selected_ids) <= set(payload.candidate_ids),
                "low_confidence_selected": payload.projected_confidence == "low" and bool(result.selected_ids),
                "merge_execution_allowed": bool(getattr(result, "high_impact", False) and result.execution_allowed),
                "safety_violations": list(oracle.safety_violations(case["decision_kind"], payload, result)),
                "application_allowed": runtime["application_allowed"],
            })
        mem_after = _table_snapshot(lambda name: name.startswith(MEM_DOMAIN_PREFIXES))
        ledger_after = _table_snapshot(lambda name: name in LEDGER_TABLES)
    finally:
        db.DATA_DIR, db.DB_PATH = original_data_dir, original_db_path
        shutil.rmtree(evaluation_dir, ignore_errors=True)
    changed_mem = sorted(name for name in set(mem_before) | set(mem_after) if mem_before.get(name) != mem_after.get(name))
    changed_ledger = sorted(name for name in set(ledger_before) | set(ledger_after) if ledger_before.get(name) != ledger_after.get(name))
    total = len(outcomes)
    quality = evaluate_quality_corpus(json.loads(QUALITY_PATH.read_text(encoding="utf-8")))
    return {
        "report_version": "cds10-episode-saga-shadow-report-v1",
        "protocol_version": fixture["protocol_version"],
        "synthetic_only": True, "contains_user_data": False,
        "fixture_sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
        "quality_corpus_sha256": hashlib.sha256(QUALITY_PATH.read_bytes()).hexdigest(),
        "sample_count": total,
        "rule_template_count": len({case["group"] for case in fixture["cases"]}),
        "decision_kind_counts": dict(sorted(Counter(row["decision_kind"] for row in outcomes).items())),
        "proposal_exact_rate": sum(row["proposal_exact"] for row in outcomes) / total,
        "candidate_subset_rate": sum(row["candidate_subset"] for row in outcomes) / total,
        "low_confidence_selection_rate": sum(row["low_confidence_selected"] for row in outcomes) / total,
        "merge_execution_rate": sum(row["merge_execution_allowed"] for row in outcomes) / total,
        "application_allowed_rate": sum(row["application_allowed"] for row in outcomes) / total,
        "safety_violation_count": sum(len(row["safety_violations"]) for row in outcomes),
        "oracle_version": oracle.ORACLE_VERSION,
        "shadow_ledger_write_count": len(changed_ledger),
        "changed_shadow_ledger_tables": changed_ledger,
        "mem_domain_write_count": len(changed_mem),
        "changed_mem_domain_tables": changed_mem,
        "schema_version": schema_version, "schema_changed": schema_version != 62,
        "fallback_kind": "pure_deterministic", "application_owner": "mem",
        "quality_corpus_role": quality["corpus_role"],
        "quality_metrics": quality,
        "outcomes": outcomes,
    }


def evaluate_quality_corpus(corpus: dict) -> dict:
    rows = []
    for case in corpus["cases"]:
        predicted = _evaluate_raw_narrative(case)
        rows.append((case["label"], predicted))
    labels = sorted({value for pair in rows for value in pair})
    per_label = {}
    for label in labels:
        true_positive = sum(expected == predicted == label for expected, predicted in rows)
        false_positive = sum(expected != label and predicted == label for expected, predicted in rows)
        false_negative = sum(expected == label and predicted != label for expected, predicted in rows)
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        per_label[label] = {
            "support": sum(expected == label for expected, _ in rows),
            "precision": precision, "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        }
    return {
        "corpus_role": corpus["corpus_role"],
        "candidate_path": corpus["candidate_path"],
        "label_authorship": corpus["label_authorship"],
        "promotion_evidence_eligible": False,
        "sample_count": len(rows),
        "correct_count": sum(expected == predicted for expected, predicted in rows),
        "error_count": sum(expected != predicted for expected, predicted in rows),
        "accuracy": sum(expected == predicted for expected, predicted in rows) / len(rows),
        "macro_precision": sum(item["precision"] for item in per_label.values()) / len(per_label),
        "macro_recall": sum(item["recall"] for item in per_label.values()) / len(per_label),
        "macro_f1": sum(item["f1"] for item in per_label.values()) / len(per_label),
        "per_label": per_label,
    }


def _evaluate_raw_narrative(case: dict) -> str:
    original_data_dir, original_db_path = db.DATA_DIR, db.DB_PATH
    evaluation_dir = tempfile.mkdtemp(prefix="xiadie-cds10-quality-")
    db.DATA_DIR = evaluation_dir
    db.DB_PATH = os.path.join(evaluation_dir, "xiadie.db")
    try:
        db.init_db()
        narratives = case["raw_narrative"]["members"]
        entity_id = None
        if case["raw_narrative"]["shared_active_entity"]:
            entity_id = entities.create_entity(f"质量语料实体-{case['id']}", "project")["id"]
        if case["decision_kind"] == shadow.EPISODE_DECISION_KIND:
            for index, narrative in enumerate(narratives):
                fragment = memory.create_memory("L1", narrative)
                conn = db.connect()
                try:
                    stamp = EVALUATION_NOW - 3600 + index * 600
                    conn.execute(
                        "UPDATE memory_fragments SET created_at=?,updated_at=? WHERE id=?",
                        (stamp, stamp, fragment["id"]),
                    )
                    conn.commit()
                finally:
                    conn.close()
                if entity_id:
                    entities.link_fragment(entity_id, fragment["id"], source="evaluation")
            candidates = episodes.generate_candidates(now=EVALUATION_NOW)
            if not candidates:
                return "skip"
            payload = shadow.build_episode_input(candidates[0]["id"])
            return shadow.episode_fallback(payload).proposed_action
        episode_ids = []
        for index, narrative in enumerate(narratives):
            episode_ids.append(_insert_quality_episode(narrative, index, entity_id))
        candidates = sagas.generate_candidates(now=EVALUATION_NOW)
        if not candidates:
            return "skip"
        payload = shadow.build_saga_input(candidates[0]["id"])
        return shadow.saga_fallback(payload).proposed_transition
    finally:
        db.DATA_DIR, db.DB_PATH = original_data_dir, original_db_path
        shutil.rmtree(evaluation_dir, ignore_errors=True)


def _insert_quality_episode(narrative: str, index: int, entity_id: str | None) -> str:
    fragment = memory.create_memory("L1", narrative)
    stamp = EVALUATION_NOW - (2 - index) * 86_400
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE memory_fragments SET created_at=?,updated_at=? WHERE id=?",
            (stamp, stamp, fragment["id"]),
        )
        source = dict(conn.execute(
            "SELECT * FROM memory_fragments WHERE id=?", (fragment["id"],)
        ).fetchone())
        episode_id = db.new_id()
        conn.execute(
            "INSERT INTO memory_episodes("
            "id,title,summary,start_at,end_at,status,source,source_fragment_ids_json,source_hash,"
            "summary_status,summary_protocol_version,summary_evidence_json,created_at,updated_at)"
            " VALUES(?,?,?,?,?,'active','automatic',?,?,'extractive_fallback',"
            "'episode-extractive-v1',?,?,?)",
            (
                episode_id, narrative[:80], narrative, stamp, stamp + 60,
                json.dumps([fragment["id"]]), episode_summary.source_hash([source]),
                json.dumps([fragment["id"]]), stamp, stamp,
            ),
        )
        conn.execute(
            "INSERT INTO memory_episode_fragments VALUES(?,?,0,?)",
            (episode_id, fragment["id"], stamp),
        )
        if entity_id:
            conn.execute(
                "INSERT INTO memory_episode_entities(episode_id,entity_id,created_at) VALUES(?,?,?)",
                (episode_id, entity_id, stamp),
            )
        conn.commit()
        return episode_id
    finally:
        conn.close()


def render_markdown(report: dict) -> str:
    quality = report["quality_metrics"]
    return "\n".join([
        "# CDS.10 Episode/Saga 叙事判断纯 Shadow 评测", "",
        f"- 样本：{report['sample_count']} 个纯合成场景，覆盖 {report['rule_template_count']} 个规则模板；不含用户数据，不调用 Provider。",
        f"- Fixture SHA-256：`{report['fixture_sha256']}`", "", "## 完成门", "",
        "| 指标 | 结果 | 门槛 |", "|---|---:|---:|",
        f"| 提案精确匹配率 | {report['proposal_exact_rate']:.2%} | 100% |",
        f"| 成员来自候选集合 | {report['candidate_subset_rate']:.2%} | 100% |",
        f"| 低置信度选中率 | {report['low_confidence_selection_rate']:.2%} | 0 |",
        f"| 高影响 merge 自动执行率 | {report['merge_execution_rate']:.2%} | 0 |",
        f"| Shadow application_allowed 率 | {report['application_allowed_rate']:.2%} | 0 |",
        f"| 安全违规数 | {report['safety_violation_count']} | 0 |",
        f"| MEM 领域表写入数 | {report['mem_domain_write_count']} | 0 |", "", "## 边界", "",
        f"- Schema 保持 {report['schema_version']}，未新增迁移、表或列。",
        "- EpisodeBoundaryProposal 与 SagaTransitionProposal 使用独立输入/输出 Schema，均固定 Shadow。",
        f"- 独立 oracle：`{report['oracle_version']}`，不读取 fixture expected。",
        "- adapter 只接受真实 pending/qualified 候选，复核资格并强制绑定候选及 Fragment/Episode/Saga 完整来源链 hash（含 Fragment→Episode、Episode→Saga 反向归属）；MEM 继续是唯一 application owner。",
        "- Episode 资格门检查 Fragment 未归属任何正式 Episode；Saga 资格门检查 Episode 未归属除目标 Saga 之外的任何正式 Saga；任何归属变化使来源 hash 失效。",
        "- Episode 所选边界必须连续；Saga 非 skip 提案至少包含 2 个成员。",
        "- merge_suggestion 始终 high_impact 且 execution_allowed=False；revive 仅接受 user_confirmed 来源。", "",
        "## 原始叙事回归语料", "",
        f"- 角色：`{report['quality_corpus_role']}`；SHA-256：`{report['quality_corpus_sha256']}`。",
        "- 标签：人工编写的合成标签，未经过独立评审。",
        "- 用途：只观察规则与标签在真实候选路径上的差异，不作为 Shadow→Advisory/Active 晋级证据。",
        f"- 候选路径：`{quality['candidate_path']}`；样本：{quality['sample_count']}；正确/错误：{quality['correct_count']}/{quality['error_count']}。",
        f"- Accuracy：{quality['accuracy']:.2%}。",
        f"- Macro precision / recall / F1：{quality['macro_precision']:.2%} / {quality['macro_recall']:.2%} / {quality['macro_f1']:.2%}。", "",
    ])


def main() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if fixture["scenario_count"] != 240 or not fixture["synthetic_only"] or fixture["contains_user_data"]:
        raise ValueError("CDS.10 fixture boundary failed")
    report = build_report(fixture)
    JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MD_PATH.write_text(render_markdown(report), encoding="utf-8")


if __name__ == "__main__":
    main()
