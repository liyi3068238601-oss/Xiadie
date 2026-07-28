"""Run the deterministic CIE.1 envelope safety acceptance matrix."""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import turn_ingress

JSON_PATH = PROJECT_DIR / "docs" / "reports" / "cie-1-acceptance.json"
MARKDOWN_PATH = PROJECT_DIR / "docs" / "reports" / "cie-1-turn-ingress.md"
ROUND_COUNTS = (5, 20, 100, 500)


def evaluate() -> dict:
    outcomes = []
    all_input_ids: list[str] = []
    all_output_ids: list[str] = []
    cross_scope = 0
    for rounds in ROUND_COUNTS:
        entries = [turn_ingress.TurnIngressMessage.model_validate({
            "client_message_id": f"cie1_message_{rounds:03d}_{index:04d}",
            "window_id": f"window_{rounds:03d}",
            "content": f"合成消息 {rounds}/{index}",
            "attachment_ids": [f"attachment_{rounds:03d}_{index:04d}"] if index % 7 == 0 else [],
            "authorization_scope": "local_text_only",
            "queued_at_ms": rounds * 10_000 + index,
            "boundary": "idle_timeout",
        }) for index in range(1, rounds + 1)]
        output_ids: list[str] = []
        envelopes = []
        for start in range(0, len(entries), turn_ingress.MAX_MESSAGES):
            batch = entries[start:start + turn_ingress.MAX_MESSAGES]
            envelope = turn_ingress.build_envelope(f"session_{rounds:03d}", batch)
            envelopes.append(envelope)
            output_ids.extend(item.client_message_id for item in envelope.entries)
            if envelope.session_id != f"session_{rounds:03d}" or envelope.window_id != f"window_{rounds:03d}":
                cross_scope += 1
        input_ids = [item.client_message_id for item in entries]
        all_input_ids.extend(input_ids)
        all_output_ids.extend(output_ids)
        outcomes.append({
            "rounds": rounds,
            "envelopes": len(envelopes),
            "input_messages": len(input_ids),
            "output_messages": len(output_ids),
            "order_preserved": input_ids == output_ids,
            "attachments_preserved": sum(len(item.attachment_ids) for item in entries)
                == sum(len(envelope.attachment_ids) for envelope in envelopes),
        })
    input_set, output_set = set(all_input_ids), set(all_output_ids)
    return {
        "report_version": "cie-1-acceptance-v1",
        "protocol_version": turn_ingress.PROTOCOL_VERSION,
        "envelope_version": turn_ingress.ENVELOPE_VERSION,
        "synthetic_only": True,
        "schema_version": 80,
        "uses_schema_81": False,
        "window_ms": turn_ingress.DEFAULT_WINDOW_MS,
        "round_counts": list(ROUND_COUNTS),
        "metrics": {
            "input_messages": len(all_input_ids),
            "output_messages": len(all_output_ids),
            "message_loss_rate": len(input_set - output_set) / len(input_set),
            "duplicate_processing_rate": (len(all_output_ids) - len(output_set)) / len(all_output_ids),
            "cross_session_or_window_rate": cross_scope / sum(item["envelopes"] for item in outcomes),
            "order_violation_rate": sum(not item["order_preserved"] for item in outcomes) / len(outcomes),
            "attachment_scope_loss_rate": sum(not item["attachments_preserved"] for item in outcomes) / len(outcomes),
        },
        "outcomes": outcomes,
    }


def render_markdown(report: dict) -> str:
    metrics = report["metrics"]
    lines = [
        "# CIE.1 TurnIngressBuffer 验收", "",
        f"- 协议：`{report['protocol_version']}` / `{report['envelope_version']}`。",
        f"- 窗口：默认 {report['window_ms']} ms，硬范围 300～800 ms；单 envelope 上限 {turn_ingress.MAX_MESSAGES} 条。",
        f"- Schema：{report['schema_version']}；CIE.1 不需要 Schema 81。",
        f"- 固定矩阵：{report['round_counts']}，共 {metrics['input_messages']} 条纯合成消息。", "",
        "## 零容忍指标", "",
        f"- 消息丢失率：{metrics['message_loss_rate']:.2%}。",
        f"- 重复处理率：{metrics['duplicate_processing_rate']:.2%}。",
        f"- 跨会话/窗口串流率：{metrics['cross_session_or_window_rate']:.2%}。",
        f"- 顺序破坏率：{metrics['order_violation_rate']:.2%}。",
        f"- 附件授权归属丢失率：{metrics['attachment_scope_loss_rate']:.2%}。", "",
        "## 实现边界", "",
        "- 前端 `TurnIngressBuffer` 只在 `cie_enabled=1` 时使用；缺失、关闭或设置读取失败均走旧单消息路径。",
        "- 原始消息在后端分别写入现有 `messages`，附件分别绑定原消息；有序 envelope 仅用于本轮检索和生成，不持久化平行正文。",
        "- `/stop`、Ctrl/Cmd+Enter、语音结束协议位及 20 条硬上限立即封口；普通输入在最后一条后 500 ms 封口。",
        "- 当前附件范围只有 `local_text_only`；未知或混合授权范围由严格 Schema 拒绝，不静默合并。", "",
        "## 回滚", "",
        "关闭 `cie_enabled` 即回到冻结 fallback；删除 CIE.1 前后端协议、缓冲器和测试即可，无数据库迁移或用户数据转换。", "",
    ]
    return "\n".join(lines)


def main() -> int:
    report = evaluate()
    JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MARKDOWN_PATH.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"report": str(JSON_PATH), "metrics": report["metrics"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
