"""Evaluate the frozen CIE.0 fallback against a synthetic fixed set."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import statistics
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
FIXTURE_PATH = BACKEND_DIR / "tests" / "fixtures" / "cie0_interaction_v1.json"
JSON_PATH = PROJECT_DIR / "docs" / "reports" / "cie-0-baseline.json"
MARKDOWN_PATH = PROJECT_DIR / "docs" / "reports" / "cie-0-construction-baseline.md"
PREDECESSOR_SHA = "b436e9f8876f8926ac90df3562edbeef3f085413"
PROVIDER_SAMPLES = 3


def _percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * ratio
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


async def _measure_provider_latency() -> dict:
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    from app import llm
    from app.main import _current_model

    provider, model = _current_model()
    if provider is None or provider.get("id") == "mock" or not provider.get("base_url"):
        raise RuntimeError("a configured non-mock provider is required")
    values: list[float] = []
    for index in range(1, PROVIDER_SAMPLES + 1):
        started = time.perf_counter()
        first_token_ms: float | None = None
        async for chunk in llm.stream_chat(
            provider,
            model,
            [{"role": "user", "content": f"只回复一个汉字：好。合成延迟样本 {index}。"}],
            max_tokens=256,
        ):
            if chunk and first_token_ms is None:
                first_token_ms = (time.perf_counter() - started) * 1_000
        if first_token_ms is None:
            raise RuntimeError("provider stream completed without a text delta")
        values.append(first_token_ms)
    return {
        "value": round(statistics.median(values), 3),
        "p50": round(_percentile(values, 0.50), 3),
        "p95": round(_percentile(values, 0.95), 3),
        "samples": len(values),
        "provider_id": str(provider["id"]),
        "model": model,
        "scope": "direct configured provider stream; synthetic prompt; no chat persistence",
    }


def evaluate(fixture: dict) -> dict:
    continuous_messages = [
        message
        for scenario in fixture["continuous"]
        for message in scenario["messages"]
    ]
    unique_message_ids = {message["message_id"] for message in continuous_messages}
    sequence_valid = all(
        [message["sequence"] for message in scenario["messages"]]
        == list(range(1, scenario["rounds"] + 1))
        for scenario in fixture["continuous"]
    )
    text_attachments = [item for item in fixture["attachments"] if item["kind"] == "text"]
    image_attachments = [item for item in fixture["attachments"] if item["kind"] == "image"]
    return {
        "report_version": "cie-construction-baseline-v1",
        "construction_baseline": {
            "repository": "liyi3068238601-oss/Xiadie",
            "predecessor_pr": 4,
            "base_branch": "main",
            "base_commit_sha": PREDECESSOR_SHA,
            "schema_version": 80,
            "next_schema_version_provisional": 81,
            "cie0_uses_migration": False,
            "plan_version": "CIE v0.2",
            "recorded_at": "2026-07-28",
            "test_baseline": {
                "backend": "2560 passed, 1 warning",
                "frontend": "52 passed",
                "vite_modules": 190,
                "electron_contracts": 3,
            },
        },
        "fixture_sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
        "privacy": {"synthetic_only": True, "contains_user_data": False, "provider_calls": 0},
        "fixed_set": {
            "continuous_rounds": [item["rounds"] for item in fixture["continuous"]],
            "continuous_messages": len(continuous_messages),
            "interruption_cases": len(fixture["interruption"]),
            "text_attachment_cases": len(text_attachments),
            "image_attachment_cases": len(image_attachments),
            "rhythm_cases": len(fixture["rhythm"]),
            "contribution_cases": len(fixture["contributions"]),
        },
        "metrics": {
            "serialized_fallback_send_success_rate": 1.0 if sequence_valid else 0.0,
            "first_token_latency_ms": {
                "value": None,
                "scope": "requires live provider run; not fabricated by synthetic fixture",
            },
            "active_generation_cancel_support_rate": 0.0,
            "duplicate_reply_rate": 0.0 if len(unique_message_ids) == len(continuous_messages) else 1.0,
            "third_party_body_leakage_rate": 0.0,
            "text_attachment_support_rate": sum(item["expected_current_support"] for item in text_attachments) / len(text_attachments),
            "native_image_support_rate": sum(item["expected_current_support"] for item in image_attachments) / len(image_attachments),
            "reply_semantic_rewrite_rate": 0.0,
        },
        "current_capability": {
            "turn_ingress_buffer": False,
            "active_generation_cancel": False,
            "native_image_transport": False,
            "client_rhythm_state_machine": False,
            "context_contribution_v1": False,
        },
        "fallback_contract": {
            "turn_mode": "single_message",
            "generation_mode": "single_generation",
            "transport": "text_sse",
            "attachment_mode": "local_text_extraction",
            "feature_flag": "cie_enabled",
            "feature_flag_default": False,
        },
    }


def render_markdown(report: dict) -> str:
    base = report["construction_baseline"]
    fixed = report["fixed_set"]
    metrics = report["metrics"]
    latency = metrics["first_token_latency_ms"]
    if latency["value"] is None:
        latency_line = "- 首 token 延迟：尚未实测；使用 `--measure-provider` 通过同一入口补测。"
    else:
        latency_line = (
            f"- 首 token 延迟：P50 {latency['p50']:.3f} ms，P95 {latency['p95']:.3f} ms，"
            f"{latency['samples']} 次；`{latency['provider_id']}/{latency['model']}` 直连合成短提示，"
            "不经过聊天持久化。"
        )
    return "\n".join([
        "# CIE.0 ConstructionBaseline 与交互固定评测集",
        "",
        f"- predecessor：KIG PR #4 merge `{base['base_commit_sha']}`。",
        f"- Schema：{base['schema_version']}；CIE.0 不占迁移，81 仅为后续暂定候选。",
        f"- 冻结测试基线：后端 `{base['test_baseline']['backend']}`；前端 `{base['test_baseline']['frontend']}`；Vite {base['test_baseline']['vite_modules']} modules；Electron contract {base['test_baseline']['electron_contracts']} 项。",
        f"- 合成固定集：连续轮数 {fixed['continuous_rounds']}，共 {fixed['continuous_messages']} 条消息；打断 {fixed['interruption_cases']}、附件 {fixed['text_attachment_cases'] + fixed['image_attachment_cases']}、节奏 {fixed['rhythm_cases']}、第三方贡献 {fixed['contribution_cases']} 条。",
        f"- fixture SHA-256：`{report['fixture_sha256']}`；真实用户数据 0；Provider 调用 0。",
        "",
        "## 当前基线指标",
        "",
        f"- 串行 fallback 发送成功率：{metrics['serialized_fallback_send_success_rate']:.2%}（合成契约，不代表网络可用率）。",
        latency_line,
        f"- 活动生成取消支持率：{metrics['active_generation_cancel_support_rate']:.2%}。",
        f"- 合成重复回复率：{metrics['duplicate_reply_rate']:.2%}。",
        f"- 第三方正文泄漏率：{metrics['third_party_body_leakage_rate']:.2%}；当前没有贡献接口，因此是 fail-closed 基线，不代表 CIE.5 已实现。",
        f"- 文本附件支持率：{metrics['text_attachment_support_rate']:.2%}；原生图片支持率：{metrics['native_image_support_rate']:.2%}。",
        f"- 当前 SSE 原文路径语义改写率：{metrics['reply_semantic_rewrite_rate']:.2%}。",
        "",
        "## 冻结 fallback 与开关",
        "",
        "唯一开关为 `cie_enabled`，默认关闭。CIE.0 不把开关接入聊天热路径，因此关闭状态严格保持当前单消息、单生成、纯文本 SSE 与本地文本附件解析路径；开启也尚不宣称任何 CIE.1+ 能力。",
        "",
        "## 缺口结论",
        "",
        "消息积累、活动生成取消、原生图片、客户端节奏状态机和 `context-contribution-v1` 均为未实现。上述 0% 是施工输入，不是质量失败；任一后续能力仍必须在失败时回到本报告冻结的 fallback。",
        "",
        "## 回滚",
        "",
        "CIE.0 只新增合成 fixture、离线报告、默认关闭的单一设置模块、测试和文档；无 Schema 迁移、无聊天热路径改动、无用户数据写入，可整提交回滚。",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--measure-provider",
        action="store_true",
        help="refresh first-token latency through the configured non-mock provider",
    )
    args = parser.parse_args()
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    report = evaluate(fixture)
    if args.measure_provider:
        report["metrics"]["first_token_latency_ms"] = asyncio.run(_measure_provider_latency())
    elif JSON_PATH.exists():
        previous = json.loads(JSON_PATH.read_text(encoding="utf-8"))
        measured = previous.get("metrics", {}).get("first_token_latency_ms", {})
        if measured.get("value") is not None:
            report["metrics"]["first_token_latency_ms"] = measured
    JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MARKDOWN_PATH.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(JSON_PATH), "markdown": str(MARKDOWN_PATH), "metrics": report["metrics"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
