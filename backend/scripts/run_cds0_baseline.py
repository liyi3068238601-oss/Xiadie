"""Run CDS.0 against frozen deterministic production functions.

The corpus is synthetic. Reports contain IDs, labels, counts, versions and timings,
not fixture text or user data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import archivist, context_assembler, context_budget, history_recall, knowledge_recall  # noqa: E402
from app.proactive import cognition, presence, protocols  # noqa: E402

DEFAULT_FIXTURE = BACKEND_DIR / "tests" / "fixtures" / "cds0_evaluation_v1.json"
DEFAULT_JSON = PROJECT_DIR / "docs" / "reports" / "cds-0-legacy-baseline.json"
DEFAULT_MARKDOWN = PROJECT_DIR / "docs" / "reports" / "cds-0-legacy-baseline.md"
REPORT_VERSION = "cognitive-decision-baseline-report-v1"
CONSTRUCTION_BASE_COMMIT = "6b8aa47134f8a9a55131c73bb1148e6912421c4f"


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("protocol_version") != "cognitive-decision-eval-v1":
        raise ValueError("CDS evaluation protocol mismatch")
    if payload.get("synthetic_only") is not True or payload.get("contains_user_data") is not False:
        raise ValueError("CDS baseline only accepts synthetic fixtures")
    if payload.get("scenario_count") != len(payload.get("cases", [])):
        raise ValueError("CDS scenario count mismatch")
    return payload


def _knowledge(case: dict) -> tuple[list[str], int, dict]:
    has_results = bool(case["input"]["has_results"])

    def search_fn(_query: str, **_kwargs) -> dict:
        results = []
        if has_results:
            results.append({
                "document_id": "doc-synthetic",
                "duplicate_document_ids": ["doc-synthetic"],
                "original_name": "星港项目规范.md",
                "heading_path": ["删除规则"],
                "content": "合成评测内容",
                "match_type": "primary",
                "fts_position": 1,
                "dense_position": None,
                "vector_score": None,
                "tags": ["星港项目"],
            })
        return {
            "results": results, "retrieval_mode": "fts", "vector_available": False,
            "vector_error_code": None, "diagnostics": {},
        }

    def policy_fn(ids: set[str]) -> dict[str, dict]:
        return {
            value: {"transmission_policy": "remote_allowed", "policy_revision": 1}
            for value in ids
        }

    decision = knowledge_recall.evaluate(
        case["input"]["text"],
        {"id": "cds-baseline", "execution_location": "local", "location_revision": 1},
        search_fn=search_fn,
        policy_fn=policy_fn,
    )
    selected = ["knowledge"] if decision["action"] in {"retrieve", "ask"} else []
    return selected, int(decision.get("natural_tokens") or 0), {
        "action": decision["action"], "reason_code": decision["reason_code"],
        "confidence_band": decision["confidence_band"],
    }


def _context(case: dict) -> tuple[list[str], int, dict]:
    values = {
        name: (f"{name}内容" * int(case["input"]["units"]))
        if name in case["input"]["present_components"] else ""
        for name in context_assembler.OPTIONAL_COMPONENT_SHARES
    }
    bounded = context_assembler._bounded_components(  # noqa: SLF001 - frozen baseline probe
        int(case["input"]["total_budget"]), **values,
    )
    selected = [name for name, value in bounded.items() if value]
    tokens = sum(context_budget.estimate_tokens(value) for value in bounded.values())
    return selected, tokens, {"component_tokens": {
        name: context_budget.estimate_tokens(value) for name, value in bounded.items()
    }}


def _retention(case: dict) -> tuple[list[str], int, dict]:
    item = dict(case["input"])
    now = float(item.pop("now"))
    relationship = float(item.pop("relationship", 0.0))
    in_active_saga = bool(item.pop("in_active_saga", False))
    duplicate_penalty = float(item.pop("duplicate_penalty", 0.0))
    reasons = archivist.protection_reasons(item)
    score = archivist.retention_score(
        item, now=now, relationship=relationship,
        in_active_saga=in_active_saga, duplicate_penalty=duplicate_penalty,
    )
    if reasons or score["score"] >= archivist.COOLING_SCORE_THRESHOLD:
        selected = ["retain"]
    elif score["score"] >= archivist.FROZEN_SCORE_THRESHOLD:
        selected = ["cool"]
    else:
        selected = ["freeze"]
    return selected, 0, {"score": score["score"], "protection_reasons": reasons}


def evaluate_case(case: dict) -> dict:
    started = time.perf_counter_ns()
    track = case["track"]
    if track == "presence":
        signal = presence.detect_presence_signals(case["input"]["text"])
        selected, tokens, detail = [signal.user_status], 0, {
            "open_thread": signal.open_thread,
        }
    elif track == "relationship_fallback":
        fallback = cognition.unknown_fallback()["relationship_meaning"]
        selected, tokens, detail = [fallback["label"]], 0, {
            "confidence": fallback["confidence"], "fallback": True,
        }
    elif track == "knowledge_gate":
        selected, tokens, detail = _knowledge(case)
    elif track == "history_intent":
        explicit = bool(history_recall._EXPLICIT_RECALL.search(case["input"]["text"]))  # noqa: SLF001
        selected, tokens, detail = (["cross_session_history"] if explicit else []), 0, {
            "explicit_recall": explicit,
        }
    elif track == "context_fixed_budget":
        selected, tokens, detail = _context(case)
    elif track == "memory_retention":
        selected, tokens, detail = _retention(case)
    else:
        raise ValueError(f"unknown CDS baseline track: {track}")
    latency_ms = (time.perf_counter_ns() - started) / 1_000_000
    expected = case["expected"]
    actual = set(selected)
    must = set(expected["must_select"])
    forbidden = set(expected["forbidden_select"])
    false_negative = sorted(must - actual)
    false_positive = sorted(actual & forbidden)
    return {
        "case_id": case["id"], "track": track, "group": case["group"],
        "selected": sorted(actual), "false_negative": false_negative,
        "false_positive": false_positive, "exact_match": not false_negative and not false_positive,
        "latency_ms": round(latency_ms, 6), "estimated_tokens": tokens,
        "detail": detail,
    }


def _percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * ratio
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def _metrics(rows: list[dict]) -> dict:
    count = len(rows)
    fp = sum(len(row["false_positive"]) for row in rows)
    fn = sum(len(row["false_negative"]) for row in rows)
    fp_cases = sum(bool(row["false_positive"]) for row in rows)
    fn_cases = sum(bool(row["false_negative"]) for row in rows)
    latencies = [float(row["latency_ms"]) for row in rows]
    tokens = [int(row["estimated_tokens"]) for row in rows]
    return {
        "sample_count": count,
        "exact_match_rate": round(sum(row["exact_match"] for row in rows) / count, 6),
        "false_positive_selections": fp,
        "false_negative_selections": fn,
        "false_positive_case_rate": round(fp_cases / count, 6),
        "false_negative_case_rate": round(fn_cases / count, 6),
        "latency_ms": {
            "average": round(statistics.fmean(latencies), 6),
            "p50": round(_percentile(latencies, 0.50), 6),
            "p95": round(_percentile(latencies, 0.95), 6),
            "max": round(max(latencies), 6),
        },
        "estimated_tokens": {
            "total": sum(tokens), "average": round(statistics.fmean(tokens), 3),
            "max": max(tokens),
        },
    }


def build_report(fixture: dict, fixture_path: Path, *, base_commit: str) -> dict:
    outcomes = [evaluate_case(case) for case in fixture["cases"]]
    by_track: dict[str, list[dict]] = defaultdict(list)
    for row in outcomes:
        by_track[row["track"]].append(row)
    return {
        "report_version": REPORT_VERSION,
        "evaluation_protocol_version": fixture["protocol_version"],
        "synthetic_only": True,
        "contains_user_data": False,
        "construction_baseline": {
            "repository": "liyi3068238601-oss/Xiadie",
            "predecessor_pr": 1,
            "base_branch": "main",
            "base_commit_sha": base_commit,
            "schema_version": 60,
            "test_baseline": "937 passed, 1 warning",
            "plan_version": "CDS v0.3",
            "recorded_at": "2026-07-22",
        },
        "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "algorithm_versions": {
            "presence": protocols.CONVERSATION_PRESENCE_V2,
            "relationship_fallback": cognition.PROTOCOL_VERSION,
            "knowledge_gate": knowledge_recall.PROTOCOL_VERSION,
            "knowledge_threshold": knowledge_recall.knowledge_recall_thresholds.THRESHOLD_VERSION,
            "history_index": history_recall.INDEX_VERSION,
            "history_score": history_recall.SCORE_VERSION,
            "context_package": context_assembler.PACKAGE_PROTOCOL_VERSION,
            "context_budget": context_budget.BUDGET_PROTOCOL_VERSION,
            "context_estimator": context_budget.ESTIMATOR_VERSION,
            "memory_retention": archivist.RETENTION_POLICY_VERSION,
        },
        "label_counts": {
            "must_select": sum(len(case["expected"]["must_select"]) for case in fixture["cases"]),
            "may_select": sum(len(case["expected"]["may_select"]) for case in fixture["cases"]),
            "forbidden_select": sum(len(case["expected"]["forbidden_select"]) for case in fixture["cases"]),
        },
        "overall": _metrics(outcomes),
        "tracks": {track: _metrics(rows) for track, rows in sorted(by_track.items())},
        "failure_groups": dict(sorted(Counter(
            f"{row['track']}:{row['group']}" for row in outcomes if not row["exact_match"]
        ).items())),
        "outcomes": outcomes,
    }


def render_markdown(report: dict) -> str:
    baseline = report["construction_baseline"]
    lines = [
        "# CDS.0 旧算法离线基线报告", "",
        f"- 固定提交：`{baseline['base_commit_sha']}`（PR #{baseline['predecessor_pr']} 合并后的 `main`）",
        f"- Schema：{baseline['schema_version']}；测试基线：`{baseline['test_baseline']}`",
        f"- 评测协议：`{report['evaluation_protocol_version']}`；fixture SHA-256：`{report['fixture_sha256']}`",
        f"- 样本：{report['overall']['sample_count']} 条纯合成场景，不含用户数据；不调用真实 Provider",
        "", "## 总体指标", "",
        "| 样本 | 精确匹配 | 误选 case | 漏选 case | 平均延迟 ms | P95 ms | 估算 token 总量 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        f"| {report['overall']['sample_count']} | {report['overall']['exact_match_rate']:.2%} | "
        f"{report['overall']['false_positive_case_rate']:.2%} | {report['overall']['false_negative_case_rate']:.2%} | "
        f"{report['overall']['latency_ms']['average']:.6f} | {report['overall']['latency_ms']['p95']:.6f} | "
        f"{report['overall']['estimated_tokens']['total']} |",
        "", "## 分轨指标", "",
        "| 轨道 | 样本 | 精确匹配 | 误选数 | 漏选数 | 平均延迟 ms | 平均 token |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for track, metrics in report["tracks"].items():
        lines.append(
            f"| `{track}` | {metrics['sample_count']} | {metrics['exact_match_rate']:.2%} | "
            f"{metrics['false_positive_selections']} | {metrics['false_negative_selections']} | "
            f"{metrics['latency_ms']['average']:.6f} | {metrics['estimated_tokens']['average']} |"
        )
    lines += [
        "", "## 结论", "",
        "- 本报告冻结的是现有确定性/保守回退行为，不把它包装成 CDS 新算法。",
        "- Presence、Knowledge、History、CTX、Relationship fallback 与 Archivist 均直接调用当前生产纯函数或门控函数。",
        "- Relationship 无可用模型时必然回退 `ordinary_exchange`；语义事件的漏选是已知基线，不在 CDS.0 修复。",
        "- CTX v1 固定比例可能在受限预算下保留语义无关组件；CDS.7 只可先做 Shadow proposal，不能直接改冻结装配器。",
        "- 所有误选/漏选留给后续 CDS 阶段配对比较；本阶段不改变聊天、数据库或任何冻结协议。",
        "", "逐样本结果、版本和无正文诊断见同名 JSON。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--base-commit", default=CONSTRUCTION_BASE_COMMIT)
    args = parser.parse_args()
    fixture = _load(args.fixture)
    report = build_report(fixture, args.fixture, base_commit=args.base_commit)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown_output.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({
        "json": str(args.json_output), "markdown": str(args.markdown_output),
        "sample_count": report["overall"]["sample_count"], "overall": report["overall"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
