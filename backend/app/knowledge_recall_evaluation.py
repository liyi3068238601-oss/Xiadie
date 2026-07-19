"""K.3 固定集评测与阈值证据汇总；运行结果只包含合成数据和数值指标。"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path

EVALUATION_PROTOCOL_VERSION = "knowledge-recall-eval-v3"
SUPPORTED_EVALUATION_PROTOCOLS = frozenset({"knowledge-recall-eval-v2", EVALUATION_PROTOCOL_VERSION})
REPORT_PROTOCOL_VERSION = "knowledge-recall-report-v1"


def load_fixture(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("protocol_version") not in SUPPORTED_EVALUATION_PROTOCOLS:
        raise ValueError("knowledge evaluation protocol mismatch")
    if payload.get("synthetic_only") is not True:
        raise ValueError("knowledge evaluation fixture must be synthetic")
    return payload


def build_report(*, fixture: dict, outcomes: list[dict], environment: dict) -> dict:
    expected_ids = {case["id"] for case in fixture["cases"]}
    if {outcome["case_id"] for outcome in outcomes} != expected_ids:
        raise ValueError("knowledge evaluation outcomes are incomplete")
    total = len(outcomes)
    action_correct = sum(row["actual_action"] == row["expected_action"] for row in outcomes)
    reason_correct = sum(row["actual_reason"] == row["expected_reason"] for row in outcomes)
    tp = sum(_positive(row["actual_action"]) and _positive(row["expected_action"]) for row in outcomes)
    fp = sum(_positive(row["actual_action"]) and not _positive(row["expected_action"]) for row in outcomes)
    fn = sum(not _positive(row["actual_action"]) and _positive(row["expected_action"]) for row in outcomes)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    relevant = [row for row in outcomes if row["expected_document_groups"]]
    retrieval_hits = sum(row["retrieval_hit"] for row in relevant)
    reciprocal_ranks = [
        1.0 / row["first_relevant_rank"] if row.get("first_relevant_rank") else 0.0
        for row in relevant
    ]
    reason_metrics: dict[str, dict] = {}
    for reason in sorted({row["actual_reason"] for row in outcomes}):
        rows = [row for row in outcomes if row["actual_reason"] == reason]
        reason_metrics[reason] = {
            "sample_count": len(rows),
            "expected_match_rate": _round(sum(
                row["actual_reason"] == row["expected_reason"] for row in rows
            ) / len(rows)),
            "action_accuracy": _round(sum(
                row["actual_action"] == row["expected_action"] for row in rows
            ) / len(rows)),
        }
    latencies = [float(row["latency_ms"]) for row in outcomes]
    rule_latencies = [float(row["latency_ms"]) for row in outcomes if row["retrieval_mode"] == "none"]
    search_latencies = [float(row["features"].get("search_ms") or 0) for row in outcomes]
    policy_latencies = [float(row["features"].get("policy_ms") or 0) for row in outcomes]
    positive_dense = [
        float(row["features"]["top_dense_score"]) for row in outcomes
        if row["expected_document_groups"] and row["features"].get("top_dense_score") is not None
    ]
    negative_dense = [
        float(row["features"]["top_dense_score"]) for row in outcomes
        if not row["expected_document_groups"] and row["features"].get("top_dense_score") is not None
    ]
    dense_floor = min(positive_dense) if positive_dense else None
    dense_ceiling = max(negative_dense) if negative_dense else None
    separable = dense_floor is not None and dense_ceiling is not None and dense_floor > dense_ceiling
    recommended_dense = (dense_floor + dense_ceiling) / 2 if separable else None
    high_auto = [
        row for row in outcomes
        if _positive(row["actual_action"]) and row.get("confidence_band") == "high"
        and row.get("recall_mode") == "smart"
    ]
    high_auto_true = sum(bool(row["expected_document_groups"]) for row in high_auto)
    high_auto_precision = high_auto_true / len(high_auto) if high_auto else 0.0
    deterministic_high_enabled = bool(
        len(positive_dense) >= 30 and len(negative_dense) >= 15
        and high_auto_precision >= 0.98
    )
    fixture_json = json.dumps(fixture, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "report_protocol_version": REPORT_PROTOCOL_VERSION,
        "evaluation_protocol_version": fixture["protocol_version"],
        "decision_protocol_version": fixture["decision_protocol_version"],
        "fixture_sha256": hashlib.sha256(fixture_json.encode("utf-8")).hexdigest(),
        "synthetic_only": True,
        "environment": environment,
        "sample_count": total,
        "metrics": {
            "action_accuracy": _round(action_correct / total),
            "reason_accuracy": _round(reason_correct / total),
            "recall_trigger_precision": _round(precision),
            "recall_trigger_recall": _round(recall),
            "recall_trigger_f1": _round(f1),
            "retrieval_case_hit_rate": _round(retrieval_hits / len(relevant)) if relevant else 0.0,
            "mean_reciprocal_rank": _round(statistics.fmean(reciprocal_ranks))
            if reciprocal_ranks else 0.0,
        },
        "reason_metrics": reason_metrics,
        "performance_ms": {
            "total": _distribution(latencies),
            "deterministic_rules": _distribution(rule_latencies),
            "search": _distribution(search_latencies),
            "policy_lookup": _distribution(policy_latencies),
        },
        "score_evidence": {
            "positive_top_dense": _distribution(positive_dense),
            "negative_top_dense": _distribution(negative_dense),
            "dense_classes_separable": separable,
            "recommended_dense_threshold": _round(recommended_dense) if recommended_dense is not None else None,
            "high_confidence_auto_sample_count": len(high_auto),
            "high_confidence_auto_precision": _round(high_auto_precision),
        },
        "threshold_decision": {
            "version": "knowledge-recall-thresholds-v2",
            "exact_term_high_min_chars": 3,
            "entity_medium_min_chars": 2,
            "semantic_auto_high_enabled": bool(
                separable and precision >= 0.90 and recall >= 0.90
                and len(positive_dense) >= 30 and len(negative_dense) >= 15
            ),
            "semantic_min_dense_score": _round(recommended_dense) if separable else None,
            "automatic_injection_enabled": deterministic_high_enabled,
            "rationale": (
                "dense_positive_negative_separated" if separable
                else "dense_score_overlap_keep_semantic_medium"
            ),
        },
        "outcomes": outcomes,
    }


def render_markdown(report: dict) -> str:
    metrics = report["metrics"]
    threshold = report["threshold_decision"]
    failures = [row for row in report["outcomes"] if not (
        row["actual_action"] == row["expected_action"]
        and row["actual_reason"] == row["expected_reason"]
        and (row["retrieval_hit"] or not row["expected_document_groups"])
    )]
    lines = [
        "# 知识自然召回固定集评测报告",
        "",
        f"- 协议：`{report['evaluation_protocol_version']}` / `{report['decision_protocol_version']}`",
        f"- 合成样本：{report['sample_count']} 条（不含用户真实对话或知识正文）",
        f"- Action accuracy：{metrics['action_accuracy']:.2%}",
        f"- Reason accuracy：{metrics['reason_accuracy']:.2%}",
        f"- Trigger precision / recall / F1：{metrics['recall_trigger_precision']:.2%} / {metrics['recall_trigger_recall']:.2%} / {metrics['recall_trigger_f1']:.2%}",
        f"- 检索命中率：{metrics['retrieval_case_hit_rate']:.2%}",
        f"- 首个相关来源 MRR：{metrics['mean_reciprocal_rank']:.4f}",
        "",
        "## 性能（毫秒）",
        "",
        "| 路径 | avg | P50 | P90 | P99 |",
        "|---|---:|---:|---:|---:|",
    ]
    for key, label in (("total", "总耗时"), ("deterministic_rules", "确定性跳过"),
                       ("search", "FTS/dense 检索"), ("policy_lookup", "策略查询")):
        row = report["performance_ms"][key]
        lines.append(f"| {label} | {row['average']} | {row['p50']} | {row['p90']} | {row['p99']} |")
    lines += [
        "",
        "## 阈值结论",
        "",
        f"- exact term 高置信最小长度：{threshold['exact_term_high_min_chars']}。",
        f"- entity 中置信最小长度：{threshold['entity_medium_min_chars']}。",
        f"- dense 自动升为 high：{'允许' if threshold['semantic_auto_high_enabled'] else '不允许'}。",
        f"- dense 建议阈值：{threshold['semantic_min_dense_score']}。",
        f"- 确定性 high 自动注入：{'允许' if threshold['automatic_injection_enabled'] else '关闭'}。",
        f"- 纯语义自动升档：{'允许' if threshold['semantic_auto_high_enabled'] else '关闭'}。",
        "",
        "## 未通过样本",
        "",
    ]
    if not failures:
        lines.append("无。")
    else:
        lines += ["| case | 期望 | 实际 | 检索命中 |", "|---|---|---|---|"]
        for row in failures:
            lines.append(
                f"| {row['case_id']} | {row['expected_action']}/{row['expected_reason']} | "
                f"{row['actual_action']}/{row['actual_reason']} | {'是' if row['retrieval_hit'] else '否'} |"
            )
    lines += [
        "", "## 可重复性", "",
        f"fixture SHA-256：`{report['fixture_sha256']}`。JSON 报告保留逐样本数值和环境版本，"
        "不保留真实查询、用户数据或知识库正文。",
    ]
    return "\n".join(lines) + "\n"


def _positive(action: str) -> bool:
    return action in {"retrieve", "ask"}


def _distribution(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "sample_count": len(ordered),
        "average": _round(statistics.fmean(ordered)) if ordered else 0.0,
        "p50": _round(_percentile(ordered, 0.50)),
        "p90": _round(_percentile(ordered, 0.90)),
        "p99": _round(_percentile(ordered, 0.99)),
        "min": _round(ordered[0]) if ordered else 0.0,
        "max": _round(ordered[-1]) if ordered else 0.0,
    }


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    position = (len(values) - 1) * ratio
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return values[lower]
    weight = position - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def _round(value: float) -> float:
    return round(float(value), 6)
