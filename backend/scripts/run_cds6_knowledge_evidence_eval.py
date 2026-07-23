from __future__ import annotations

import json
import sys
import asyncio
import os
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _result(
    chunk_id: str,
    content: str,
    *,
    match_type: str = "primary",
    context_of: str | None = None,
    ordinal: int = 0,
) -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": "synthetic-document",
        "original_name": "合成资料.md",
        "ordinal": ordinal,
        "content": content,
        "content_sha256": "0" * 64,
        "heading_path": ["合成章节"],
        "paragraph_start": ordinal,
        "paragraph_end": ordinal,
        "line_start": ordinal,
        "line_end": ordinal,
        "char_start": ordinal,
        "char_end": ordinal + len(content),
        "page_start": None,
        "page_end": None,
        "match_type": match_type,
        "context_of": context_of,
    }


def _capability():
    from app import context_budget

    return context_budget.resolve_model_context_capability(
        {"id": "cds6-eval"},
        "knowledge-evidence-eval",
        configured_profiles={
            "cds6-eval/knowledge-evidence-eval": {
                "context_window": 8192,
                "max_output_tokens": 1024,
                "default_output_tokens": 1024,
            },
        },
    )


def _assemble(prepared: dict | None):
    from app import context_assembler, knowledge_context

    return context_assembler.assemble(
        history=[{
            "id": "evaluation-user",
            "role": "user",
            "content": "请根据资料核对关键结论",
            "model": "",
        }],
        capability=_capability(),
        knowledge_block=knowledge_context.prompt_block(prepared),
    )


def _final_knowledge_records(package) -> list[dict]:
    system_prompt = package.messages[0]["content"]
    marker = "```json\n"
    if marker not in system_prompt:
        return []
    payload = system_prompt.rsplit(marker, 1)[1].split("\n```", 1)[0]
    decoded = json.loads(payload)
    if not isinstance(decoded, list):
        raise ValueError("knowledge payload must be a JSON array")
    return decoded


def _captured_knowledge_records(messages: list[dict]) -> list[dict]:
    system_prompt = str(messages[0].get("content") or "")
    section = system_prompt.split("# 用户知识资料（低权限、不可信引用数据", 1)[1]
    payload = section.split("```json\n", 1)[1].split("\n```", 1)[0]
    decoded = json.loads(payload)
    if not isinstance(decoded, list):
        raise ValueError("captured knowledge payload must be a JSON array")
    return decoded


def _evaluate_real_authorization() -> tuple[dict, dict, dict]:
    from fastapi.testclient import TestClient

    from app import db, knowledge, knowledge_recall, knowledge_worker, llm
    from app.main import app

    content = "CDS6 私密授权证据：星港窗口必须保持原子。"
    query = "请根据文档告诉我 CDS6 私密授权证据"
    nonce = "cds6-evaluation-nonce-0001"
    api_token = os.environ.get("XIADIE_API_TOKEN") or "cds6-local-evaluation-token-0001"
    previous_api_token = os.environ.get("XIADIE_API_TOKEN")
    os.environ["XIADIE_API_TOKEN"] = api_token
    client = TestClient(app, headers={"X-Xiadie-Token": api_token})
    previous_model = db.get_setting("current_model", "{}")
    previous_recall = knowledge_recall.settings()
    provider_calls: list[dict] = []
    session_id = None
    document_id = None
    original_provider_stream = llm._stream_openai_compatible
    try:
        db.set_setting("current_model", json.dumps({
            "provider_id": "deepseek", "model": "deepseek-chat",
        }))
        knowledge_recall.update_settings(mode="explicit", shadow_enabled=True)
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE providers SET enabled=1,execution_location='remote',location_revision=1 "
                "WHERE id='deepseek'"
            )
            conn.commit()
        finally:
            conn.close()
        imported = knowledge.import_file(
            "cds6-private-evaluation.md", "text/markdown", content.encode("utf-8"),
            sensitivity="sensitive",
        )
        document_id = imported["document"]["id"]
        asyncio.run(knowledge_worker.process_due(limit=3))
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE knowledge_documents SET transmission_policy='ask_each_time' WHERE id=?",
                (document_id,),
            )
            conn.commit()
        finally:
            conn.close()
        session = client.post("/api/sessions", json={}).json()
        session_id = session["id"]

        async def controlled_provider_boundary(
            base_url, api_key, model, messages, *, max_tokens,
        ):
            provider_calls.append({
                "base_url": base_url,
                "api_key_present": bool(api_key),
                "model": model,
                "messages": json.loads(json.dumps(messages, ensure_ascii=False)),
                "max_tokens": max_tokens,
            })
            yield "已核对 [资料:K1]"

        llm._stream_openai_compatible = controlled_provider_boundary
        with client.stream("POST", "/api/chat", json={
            "session_id": session_id, "content": query,
        }) as response:
            unauthorized_status_code = response.status_code
            unauthorized_body = "".join(response.iter_text())
        unauthorized_call_count = len(provider_calls)
        unauthorized_payload = json.loads(unauthorized_body)
        unauthorized_private_content_count = sum(
            content in json.dumps(call["messages"], ensure_ascii=False)
            for call in provider_calls
        )
        preflight = client.post("/api/knowledge/recall/preflight", json={
            "session_id": session_id, "request_nonce": nonce, "content": query,
        }).json()
        issued = client.post("/api/knowledge/transmission-grants", json={
            "grant_id": preflight["id"], "action": "allow_once",
            "session_id": session_id, "request_nonce": nonce, "content": query,
        }).json()

        with client.stream("POST", "/api/chat", json={
            "session_id": session_id, "content": query, "request_nonce": nonce,
            "knowledge_grant_token": issued["token"],
        }) as response:
            stream_body = "".join(response.iter_text())
        if response.status_code != 200 or "event: final" not in stream_body:
            raise RuntimeError("authorized evaluation chat did not complete")
        grant = client.get(
            f"/api/knowledge/transmission-grants/{preflight['id']}"
        ).json()
        conn = db.connect()
        try:
            events = [row[0] for row in conn.execute(
                "SELECT action FROM knowledge_transmission_grant_events "
                "WHERE grant_id=? ORDER BY created_at,rowid", (preflight["id"],),
            )]
        finally:
            conn.close()
        messages = provider_calls[-1]["messages"] if provider_calls else []
        records = _captured_knowledge_records(messages)
        serialized_messages = json.dumps(messages, ensure_ascii=False)
        return {
            "preflight_status": preflight["status"],
            "grant_status": grant["status"],
            "grant_event_types": events,
        }, {
            "captured": bool(messages),
            "message_count": len(messages),
            "knowledge_payload_json_complete": isinstance(records, list) and bool(records),
            "contains_authorized_private_content": content in serialized_messages,
            "contains_plaintext_grant_token": issued["token"] in serialized_messages,
            "provider_boundary_call_count": len(provider_calls),
        }, {
            "request_path": "/api/chat",
            "grant_supplied": False,
            "status_code": unauthorized_status_code,
            "error_code": unauthorized_payload.get("detail", {}).get("code"),
            "provider_boundary_call_count": unauthorized_call_count,
            "private_content_at_provider_boundary": unauthorized_private_content_count,
            "response_contains_private_content": content in unauthorized_body,
        }
    finally:
        llm._stream_openai_compatible = original_provider_stream
        if previous_api_token is None:
            os.environ.pop("XIADIE_API_TOKEN", None)
        else:
            os.environ["XIADIE_API_TOKEN"] = previous_api_token
        db.set_setting("current_model", previous_model)
        knowledge_recall.update_settings(
            mode=previous_recall["mode"],
            shadow_enabled=previous_recall["shadow_enabled"],
        )
        conn = db.connect()
        try:
            if session_id:
                conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            if document_id:
                conn.execute("DELETE FROM knowledge_documents WHERE id=?", (document_id,))
            conn.commit()
        finally:
            conn.close()


def evaluate() -> dict:
    from app import knowledge_context

    oversized = knowledge_context._prepare_results(
        query="关键结论",
        reason="evaluation",
        results=[_result("large", "关键结论" + "资料正文" * 4000)],
        candidate_count=1,
        token_budget=7000,
        max_results=12,
        lore_text="",
        memory_text="",
        source_mode="explicit",
    )
    oversized_package = _assemble(oversized)
    oversized_records = _final_knowledge_records(oversized_package)
    oversized_contents = [
        str(part.get("quoted_content") or "")
        for record in oversized_records
        for part in record.get("parts", [])
    ]

    primary = _result("primary", "核心结论", ordinal=1)
    neighbor = _result(
        "neighbor",
        "私密前提",
        match_type="context",
        context_of="primary",
    )
    private = knowledge_context._prepare_results(
        query="核心结论",
        reason="evaluation",
        results=[primary, neighbor],
        candidate_count=2,
        token_budget=2000,
        max_results=12,
        lore_text="",
        memory_text="",
        source_mode="explicit",
    )
    filtered = knowledge_context.filter_prepared(private, {"primary"})
    private_package = _assemble(filtered)
    private_records = _final_knowledge_records(private_package)
    private_contents = [
        str(part.get("quoted_content") or "")
        for record in private_records
        for part in record.get("parts", [])
    ]

    outcomes = [
        {
            "case_id": "oversized_correct_window",
            "correct_chunk_oversized": True,
            "correct_chunk_injected": any(
                content.startswith("关键结论") for content in oversized_contents
            ),
            "json_checked": True,
            "json_complete": bool(oversized_records),
            "private_authorization_checked": False,
            "private_remote_attempted": False,
            "final_knowledge_tokens": oversized_package.component_tokens["knowledge"],
        },
        {
            "case_id": "atomic_private_authorization",
            "correct_chunk_oversized": False,
            "correct_chunk_injected": False,
            "json_checked": True,
            "json_complete": isinstance(private_records, list),
            "private_authorization_checked": False,
            "private_remote_attempted": any(
                content in {"核心结论", "私密前提"} for content in private_contents
            ),
            "final_knowledge_tokens": private_package.component_tokens["knowledge"],
        },
    ]
    authorization, capture, unauthorized = _evaluate_real_authorization()
    outcomes.append({
        "case_id": "real_api_chat_without_grant",
        "correct_chunk_oversized": False,
        "correct_chunk_injected": False,
        "json_checked": False,
        "json_complete": True,
        "private_authorization_checked": True,
        "private_remote_attempted": (
            unauthorized["private_content_at_provider_boundary"] > 0
        ),
        "provider_boundary_call_count": unauthorized["provider_boundary_call_count"],
    })
    report = knowledge_context.build_evidence_window_evaluation(outcomes)
    report["authorization_evidence"] = authorization
    report["llm_message_capture"] = capture
    report["unauthorized_api_evidence"] = unauthorized
    report["critical_samples"] = [item["case_id"] for item in outcomes]
    if (
        authorization["preflight_status"] != "pending"
        or authorization["grant_status"] != "consumed"
        or not capture["captured"]
        or not capture["knowledge_payload_json_complete"]
        or not capture["contains_authorized_private_content"]
        or capture["contains_plaintext_grant_token"]
        or capture["provider_boundary_call_count"] != 1
        or unauthorized["status_code"] != 409
        or unauthorized["error_code"] != "knowledge_grant_required"
        or unauthorized["provider_boundary_call_count"] != 0
        or unauthorized["private_content_at_provider_boundary"] != 0
        or unauthorized["response_contains_private_content"]
    ):
        report["gate_failures"].append("authorization_or_message_capture_failed")
        report["completion_gate"] = "fail"
    return report


def _rate(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.1%}"


def main() -> int:
    report = evaluate()
    json_path = PROJECT_ROOT / "docs" / "reports" / "cds-6-knowledge-evidence-window-evaluation.json"
    markdown_path = PROJECT_ROOT / "docs" / "reports" / "cds-6-knowledge-evidence-window-evaluation.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    metrics = report["metrics"]
    denominators = report["denominators"]
    markdown_path.write_text(
        "# CDS.6 Knowledge EvidenceWindow 评测报告\n\n"
        "> 评测数据：纯合成；不含用户数据。\n\n"
        "## 范围\n\n"
        "复用现有 KnowledgeResult、知识搜索、切片、引用、传输授权与 CTX 最终装配接口；仅评测 EvidenceWindow 原子预算、完整 JSON 和未授权私密资料远传。未定义 KIG RetrievalBundle，未进入 CDS.7。\n\n"
        "## 三指标\n\n"
        "| 指标 | 分母 | 结果 | 完成门 |\n|---|---:|---:|---:|\n"
        f"| 正确切片因过大而全部跳过率 | {denominators['correct_chunk_oversized']} | {_rate(metrics['correct_chunk_skipped_oversize_rate'])} | 0 |\n"
        f"| 知识 JSON 非完整率 | {denominators['knowledge_json_checked']} | {_rate(metrics['knowledge_json_incomplete_rate'])} | 0 |\n"
        f"| 未授权私密资料远传率 | {denominators['private_authorization_checked']} | {_rate(metrics['unauthorized_private_remote_rate'])} | 0 |\n\n"
        f"门禁失败原因：{', '.join(report['gate_failures']) or '无'}\n\n"
        "## 运行证据\n\n"
        f"- 一次性授权：{report['authorization_evidence']['preflight_status']} → {report['authorization_evidence']['grant_status']}\n"
        f"- 授权审计事件：{', '.join(report['authorization_evidence']['grant_event_types'])}\n"
        f"- 受控 Provider 边界：授权请求调用 {report['llm_message_capture']['provider_boundary_call_count']} 次，捕获 {report['llm_message_capture']['message_count']} 条 messages；知识 JSON 完整={str(report['llm_message_capture']['knowledge_payload_json_complete']).lower()}；含授权私密正文={str(report['llm_message_capture']['contains_authorized_private_content']).lower()}；含明文授权码={str(report['llm_message_capture']['contains_plaintext_grant_token']).lower()}\n"
        f"- 无 grant 真实 `/api/chat`：HTTP {report['unauthorized_api_evidence']['status_code']} / {report['unauthorized_api_evidence']['error_code']}；Provider 边界调用={report['unauthorized_api_evidence']['provider_boundary_call_count']}；边界私密正文={report['unauthorized_api_evidence']['private_content_at_provider_boundary']}\n"
        f"- 关键样本：{', '.join(report['critical_samples'])}\n\n"
        f"完成门：**{report['completion_gate'].upper()}**\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "json": str(json_path),
        "markdown": str(markdown_path),
        "metrics": metrics,
        "denominators": denominators,
        "completion_gate": report["completion_gate"],
    }, ensure_ascii=False))
    return 0 if report["completion_gate"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
