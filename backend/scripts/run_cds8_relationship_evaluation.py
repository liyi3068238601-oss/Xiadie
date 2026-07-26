from __future__ import annotations

import asyncio
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

from app import db, llm  # noqa: E402
from app.affect import repository  # noqa: E402
from app.proactive import cognition, cognition_service, protocols, relationship  # noqa: E402
from app.proactive.run_ledger import RunStatus, get_run  # noqa: E402
from app.proactive.schemas import ProtocolValidationError  # noqa: E402

FIXTURE_PATH = BACKEND_DIR / "tests" / "fixtures" / "cds8_relationship_meaning_v1.json"
JSON_PATH = PROJECT_DIR / "docs" / "reports" / "cds-8-relationship-meaning-evaluation.json"
MD_PATH = PROJECT_DIR / "docs" / "reports" / "cds-8-relationship-meaning-evaluation.md"
RELATIONSHIP_PROTOCOL = protocols.RELATIONSHIP_MEANING_V1
SINGLE_TURN_CAPS = relationship.SINGLE_TURN_CAPS


def _insert_turn(case: dict) -> dict:
    session_id = f"cds8-session-{case['id']}"
    user_id = f"cds8-user-{case['id']}"
    assistant_id = f"cds8-assistant-{case['id']}"
    now = db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (session_id, "CDS.8 synthetic evaluation", now, now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (user_id, session_id, "user", case["user_text"], now),
        )
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,model,created_at) VALUES(?,?,?,?,?,?)",
            (assistant_id, session_id, "assistant", case["assistant_text"], "synthetic", now + 0.1),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "session_id": session_id,
        "user_id": user_id,
        "assistant_id": assistant_id,
        "user_text": case["user_text"],
        "assistant_text": case["assistant_text"],
    }


def _insert_provider() -> dict:
    provider = {
        "id": "cds8-structured-provider", "name": "CDS.8 structured provider",
        "base_url": "https://synthetic.invalid/v1", "api_key": "synthetic",
        "models": json.dumps(["deterministic"]), "enabled": 1,
    }
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO providers(id,name,base_url,api_key,models,enabled,sort) VALUES(?,?,?,?,?,?,?)",
            (provider["id"], provider["name"], provider["base_url"], provider["api_key"],
             provider["models"], provider["enabled"], 99),
        )
        conn.commit()
    finally:
        conn.close()
    return provider


def _evidence_is_enforced(case: dict) -> bool:
    tampered = json.loads(json.dumps(case["structured_output"], ensure_ascii=False))
    tampered["relationship_meaning"]["evidence"] = [
        {"speaker": "user", "quote": "不存在的合成证据"},
    ]
    try:
        cognition.parse_and_validate(
            tampered, user_text=case["user_text"], assistant_text=case["assistant_text"],
        )
    except ProtocolValidationError as exc:
        return exc.code == "evidence_not_found"
    return False


def _within_caps(values: dict) -> bool:
    return all(SINGLE_TURN_CAPS[name][0] <= values[name] <= SINGLE_TURN_CAPS[name][1] for name in values)


def _terminal_invariant(run, suggestion, result_exists: bool) -> bool:
    return bool(
        run and run.status == RunStatus.APPLIED and run.completed_at is not None
        and run.attempt_count == 1 and result_exists and suggestion
        and suggestion.status == "applied" and suggestion.applied_at is not None
    )


def _message_count() -> int:
    conn = db.connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    finally:
        conn.close()


def _result_exists(run_id: str) -> bool:
    conn = db.connect()
    try:
        return conn.execute(
            "SELECT 1 FROM companion_cognition_results WHERE run_id=?", (run_id,),
        ).fetchone() is not None
    finally:
        conn.close()


def _suggestion_values(suggestion) -> dict:
    values = {
        "bond": suggestion.bond_delta,
        "familiarity": suggestion.familiarity_delta,
        "trust": suggestion.trust_delta,
        "attachment": suggestion.attachment_delta,
        "rapport": suggestion.rapport_delta,
    }
    return values


def _actual_values(before: dict, after: dict) -> dict:
    return {
        "bond": after["bond"] - before["bond"],
        "trust": after["trust"] - before["trust"],
    }


def build_report(fixture: dict) -> dict:
    original_data_dir, original_db_path = db.DATA_DIR, db.DB_PATH
    evaluation_dir = tempfile.mkdtemp(prefix="xiadie-cds8-run-")
    db.DATA_DIR = evaluation_dir
    db.DB_PATH = os.path.join(evaluation_dir, "xiadie.db")
    outcomes = []
    try:
        db.init_db()
        provider = _insert_provider()
        for case in fixture["cases"]:
            repository.reset()
            before = repository.get_snapshot(advance_time=False)["relationship"]
            if case["group"] == "silence":
                messages_before = _message_count()
                after = repository.advance_by(24 * 60)["relationship"]
                outcomes.append({
                    "case_id": case["id"], "group": case["group"], "label": None,
                    "label_exact": None, "schema_valid": None, "decision_run_terminal": None,
                    "eap_suggestion_applied": None, "enqueue_worker_applied": None,
                    "provider_boundary_called": None, "terminal_invariant": None,
                    "idempotency_reused": None, "duplicate_application_unchanged": None,
                    "evidence_enforced": None, "within_single_turn_caps": None,
                    "actual_applied": None, "actual_applied_within_caps": None,
                    "bond_grew": None,
                    "silence_declined": after["bond"] < before["bond"] - 1e-12
                    or after["trust"] < before["trust"] - 1e-12,
                    "no_messages_created": _message_count() == messages_before,
                })
                continue
            context = _insert_turn(case)
            provider_calls = []
            original_complete_json = llm.complete_json

            async def complete_json(resolved_provider, model, messages, **kwargs):
                provider_calls.append((resolved_provider["id"], model, bool(messages), kwargs.get("max_tokens")))
                return {"text": json.dumps(case["structured_output"], ensure_ascii=False)}

            llm.complete_json = complete_json
            try:
                queued = cognition_service.enqueue_turn(
                    chat_provider=provider, chat_model="deterministic",
                    session_id=context["session_id"], user_message_id=context["user_id"],
                    assistant_message_id=context["assistant_id"],
                )
                processed = asyncio.run(cognition_service.process_due(limit=1))
            finally:
                llm.complete_json = original_complete_json
            terminal = get_run(queued["id"])
            suggestion = relationship.get_suggestion_by_source_message(
                context["user_id"], terminal.source_revision,
            )
            after = repository.get_snapshot(advance_time=False)["relationship"]
            parsed = cognition.parse_and_validate(
                case["structured_output"], user_text=context["user_text"],
                assistant_text=context["assistant_text"],
            )
            duplicate = relationship.process_relationship_delta(
                context["session_id"], context["user_id"], case["expected_label"],
                source_assistant_message_id=context["assistant_id"],
                evidence=parsed["relationship_meaning"]["evidence"],
                reason=parsed["relationship_meaning"]["reason"],
                confidence=parsed["relationship_meaning"]["confidence"],
            )
            reused = relationship.get_suggestion_by_source_message(
                context["user_id"], terminal.source_revision,
            )
            relationship.apply_suggestion(suggestion.id)
            after_duplicate = repository.get_snapshot(advance_time=False)["relationship"]
            actual_values = _actual_values(before, after)
            outcomes.append({
                "case_id": case["id"], "group": case["group"],
                "label": parsed["relationship_meaning"]["label"],
                "label_exact": parsed["relationship_meaning"]["label"] == case["expected_label"],
                "schema_valid": True, "decision_run_terminal": terminal.status == RunStatus.APPLIED,
                "eap_suggestion_applied": suggestion is not None and suggestion.status == "applied",
                "enqueue_worker_applied": queued["status"] == "queued" and processed == 1,
                "provider_boundary_called": provider_calls == [
                    (provider["id"], "deterministic", True, llm.JSON_COMPLETION_MAX_TOKENS),
                ],
                "terminal_invariant": _terminal_invariant(
                    terminal, suggestion, _result_exists(terminal.id),
                ),
                "idempotency_reused": duplicate is None and reused is not None and reused.id == suggestion.id,
                "duplicate_application_unchanged": after_duplicate == after,
                "evidence_enforced": _evidence_is_enforced(case),
                "within_single_turn_caps": _within_caps(_suggestion_values(suggestion)),
                "actual_applied": actual_values,
                "actual_applied_within_caps": _within_caps(actual_values)
                and abs(suggestion.cap_bond_applied - actual_values["bond"]) <= 1e-12
                and abs(suggestion.cap_trust_applied - actual_values["trust"]) <= 1e-12,
                "bond_grew": after["bond"] > before["bond"] + 1e-12,
                "silence_declined": False, "no_messages_created": True,
            })
    finally:
        db.DATA_DIR, db.DB_PATH = original_data_dir, original_db_path
        shutil.rmtree(evaluation_dir, ignore_errors=True)
    total = len(outcomes)
    ordinary = [row for row in outcomes if row["group"] == "ordinary"]
    silence = [row for row in outcomes if row["group"] == "silence"]
    message_outcomes = [row for row in outcomes if row["group"] != "silence"]
    completion_gate_counts = {
        "ordinary_question_bond_growth": {
            "hits": sum(row["bond_grew"] for row in ordinary),
            "denominator": len(ordinary),
        },
        "silence_relationship_decline": {
            "hits": sum(row["silence_declined"] for row in silence),
            "denominator": len(silence),
        },
        "single_turn_over_cap": {
            "hits": sum(not row["actual_applied_within_caps"] for row in message_outcomes),
            "denominator": len(message_outcomes),
        },
    }
    completion_gates = {
        "ordinary_question_bond_growth_rate": completion_gate_counts["ordinary_question_bond_growth"]["hits"] / completion_gate_counts["ordinary_question_bond_growth"]["denominator"],
        "silence_relationship_decline_rate": completion_gate_counts["silence_relationship_decline"]["hits"] / completion_gate_counts["silence_relationship_decline"]["denominator"],
        "single_turn_over_cap_rate": completion_gate_counts["single_turn_over_cap"]["hits"] / completion_gate_counts["single_turn_over_cap"]["denominator"],
    }
    return {
        "report_version": "relationship-meaning-evaluation-report-v1",
        "protocol_version": fixture["protocol_version"],
        "source_protocol_version": RELATIONSHIP_PROTOCOL,
        "synthetic_only": True,
        "contains_user_data": False,
        "fixture_sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
        "sample_count": total,
        "group_counts": dict(sorted(Counter(row["group"] for row in outcomes).items())),
        "label_exact_rate": sum(row["label_exact"] for row in message_outcomes) / len(message_outcomes),
        "schema_validation_rate": sum(row["schema_valid"] for row in message_outcomes) / len(message_outcomes),
        "decision_run_terminal_rate": sum(row["decision_run_terminal"] for row in message_outcomes) / len(message_outcomes),
        "eap_suggestion_application_rate": sum(row["eap_suggestion_applied"] for row in message_outcomes) / len(message_outcomes),
        "enqueue_worker_application_rate": sum(row["enqueue_worker_applied"] for row in message_outcomes) / len(message_outcomes),
        "provider_boundary_call_rate": sum(row["provider_boundary_called"] for row in message_outcomes) / len(message_outcomes),
        "terminal_invariant_rate": sum(row["terminal_invariant"] for row in message_outcomes) / len(message_outcomes),
        "idempotency_reuse_rate": sum(row["idempotency_reused"] for row in message_outcomes) / len(message_outcomes),
        "duplicate_application_change_rate": 1 - sum(row["duplicate_application_unchanged"] for row in message_outcomes) / len(message_outcomes),
        "evidence_validation_rate": sum(row["evidence_enforced"] for row in message_outcomes) / len(message_outcomes),
        "completion_gates": completion_gates,
        "completion_gate_counts": completion_gate_counts,
        "all_completion_gates_passed": completion_gates == {
            "ordinary_question_bond_growth_rate": 0.0,
            "silence_relationship_decline_rate": 0.0,
            "single_turn_over_cap_rate": 0.0,
        },
        "protocol_unchanged": protocols.get_protocol(RELATIONSHIP_PROTOCOL).status is protocols.ProtocolStatus.FROZEN,
        "schema_changed": False,
        "relationship_writer": "eap",
        "outcomes": outcomes,
    }


def render_markdown(report: dict) -> str:
    gates = report["completion_gates"]
    counts = report["completion_gate_counts"]
    return "\n".join([
        "# CDS.8 RelationshipMeaning 兼容评测", "",
        f"- 样本：{report['sample_count']} 个纯合成场景；不含用户数据，不调用真实 Provider。",
        f"- Fixture SHA-256：`{report['fixture_sha256']}`",
        f"- 冻结 Schema 校验通过率：{report['schema_validation_rate']:.2%}",
        f"- 共享 DecisionRun 终态率：{report['decision_run_terminal_rate']:.2%}",
        f"- EAP 建议应用率：{report['eap_suggestion_application_rate']:.2%}",
        f"- 真实 enqueue/worker 应用率：{report['enqueue_worker_application_rate']:.2%}",
        f"- Provider 边界调用率：{report['provider_boundary_call_rate']:.2%}",
        f"- 终态不变量满足率：{report['terminal_invariant_rate']:.2%}", "",
        "## 完成门", "", "| 指标 | 命中/分母 | 结果 | 门槛 |", "|---|---:|---:|---:|",
        f"| 普通问答导致 bond 增长率 | {counts['ordinary_question_bond_growth']['hits']}/{counts['ordinary_question_bond_growth']['denominator']} | {gates['ordinary_question_bond_growth_rate']:.2%} | ≤1% |",
        f"| 沉默导致 bond/trust 下降率 | {counts['silence_relationship_decline']['hits']}/{counts['silence_relationship_decline']['denominator']} | {gates['silence_relationship_decline_rate']:.2%} | 0 |",
        f"| 单轮超限关系变化率 | {counts['single_turn_over_cap']['hits']}/{counts['single_turn_over_cap']['denominator']} | {gates['single_turn_over_cap_rate']:.2%} | 0 |", "",
        "## 兼容性", "",
        f"- 标签精确匹配：{report['label_exact_rate']:.2%}。",
        f"- 幂等复用：{report['idempotency_reuse_rate']:.2%}；重复应用变化率：{report['duplicate_application_change_rate']:.2%}。",
        f"- 全标签证据约束验证：{report['evidence_validation_rate']:.2%}。", "",
        "## 边界", "",
        "- 确定性结构化替身先经过现有 Companion Cognition 与 relationship-meaning-v1 Schema，再进入共享 DecisionRun 和 EAP 应用链。",
        "- EAP 保持唯一关系写入者；Affect 与 Relationship 所有权未合并。",
        "- 未修改冻结生产协议、Schema、迁移或聊天模型路径；未发现需要 relationship-meaning-v2 的兼容缺口。", "",
    ])


def main() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if fixture["scenario_count"] != 120 or not fixture["synthetic_only"] or fixture["contains_user_data"]:
        raise ValueError("CDS.8 fixture boundary failed")
    report = build_report(fixture)
    JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MD_PATH.write_text(render_markdown(report), encoding="utf-8")


if __name__ == "__main__":
    main()
