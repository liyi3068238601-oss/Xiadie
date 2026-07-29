"""遐蝶后端：FastAPI + SQLite。

分层职责（需求第 10 节）：模型、会话、任务、记忆、工具，均保存在本地 SQLite。
不做多窗口调度、不推倒重写。此文件只负责 HTTP 接口与编排。
"""
import hashlib
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import (
    archivist, archivist_worker, cognitive_decision, cognition_calibration,
    cognition_diagnostics as cognition_diagnostic_views, cognition_runtime,
    cognition_settings, companion_state, context_assembler, context_budget,
    context_controls, context_diagnostics, conversation_summaries,
    context_contributions, conversation_summary_service, db, cie_settings,
    diary, entities, episode_consolidator, history_recall, important_dates, inner_state_projection,
    episode_summary_service, episodes, knowledge, knowledge_cleanup, knowledge_context,
    knowledge_embeddings, knowledge_grants,
    knowledge_management, knowledge_parser, knowledge_policy, knowledge_recall, knowledge_recall_service, knowledge_search,
    knowledge_worker, kig_evidence, kig_governance, kig_maintenance, kig_pipeline, kig_sources, life_catchup, life_catchup_service, life_events, life_runtime, life_schedule, llm, lore, memory, memory_conflicts, memory_shadow_proposals,
    personal_goals, persona, persona_v2, short_memo, worldbook_r1,
    saga_consolidator, saga_lifecycle, saga_summary,
    saga_summary_service, secret_store, self_timeline, slow_lifecycle, turn_ingress,
    chat_request_control, image_attachments, vision_capabilities,
)
from . import candidate_reranker_shadow  # noqa: F401
from . import presence_thread_shadow  # noqa: F401 - registers CDS.3 Shadow contract
from . import recall_planner_shadow  # noqa: F401 - registers CDS.4 Shadow contract
from . import context_planner_shadow  # noqa: F401 - registers CDS.7 Shadow contract
from . import episode_saga_shadow  # noqa: F401 - registers CDS.10 Shadow contracts
from . import life_decisions  # noqa: F401 - registers LIFE.1 Shadow contracts on CDS
from . import information_classifier_shadow  # noqa: F401 - registers KIG.3 Shadow contract
from . import knowledge_boundary_shadow  # noqa: F401 - registers KIG.4 Shadow contract
from . import kig_query_planner  # noqa: F401 - registers KIG.5 Shadow contract
from . import kig_reranker  # noqa: F401 - registers KIG.7 Shadow contract
from . import pwm_extractor_shadow  # noqa: F401 - KIG.10 bounded Shadow extraction contract
from . import memory_observer_service
from .affect import observer_service as affect_observer_service
from .proactive import presence as proactive_presence
from .proactive import settings as proactive_settings
from .proactive import cognition_service as companion_cognition_service
from .proactive import orchestrator as proactive_orchestrator
from .proactive import delivery as proactive_delivery
from .proactive import feedback as proactive_feedback
from .security import ALLOWED_ORIGINS, TOKEN_HEADER, local_api_guard
from .pwm_api import router as pwm_router

logger = logging.getLogger(__name__)


def cleanup_orphan_attachments(max_age_seconds: float = 3600) -> int:
    """清理 message_attachments 表中的孤儿数据。

    孤儿来源：用户上传附件后未发送（关闭应用、切换会话、点 × 移除），
    或 preflight 返回 pending 后用户取消授权。这些附件 message_id IS NULL，
    不会被 messages ON DELETE CASCADE 清理。

    只清理创建时间超过 max_age_seconds 的孤儿，避免清理正在上传/发送中的附件。
    返回被清理的行数。
    """
    cutoff = db.now() - max_age_seconds
    conn = db.connect()
    try:
        image_rows = conn.execute(
            "SELECT storage_path FROM message_attachments"
            " WHERE message_id IS NULL AND created_at < ? AND attachment_kind='image'",
            (cutoff,),
        ).fetchall()
        cursor = conn.execute(
            "DELETE FROM message_attachments WHERE message_id IS NULL AND created_at < ?",
            (cutoff,),
        )
        conn.commit()
        for row in image_rows:
            image_attachments.remove(row["storage_path"])
        return cursor.rowcount or 0
    finally:
        conn.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    cognition_runtime.recover_control_plane()
    await life_catchup_service.start()
    # 启动时清理上一次运行遗留的孤儿附件（message_id IS NULL 且超过 1 小时）
    cleanup_orphan_attachments()
    image_attachments.cleanup_expired()
    conversation_summaries.recover_stale_runs()
    await conversation_summary_service.start_worker()
    await affect_observer_service.start_worker()
    await companion_cognition_service.start_worker()
    await proactive_orchestrator.start_worker()
    await memory_observer_service.start_worker()
    await episode_consolidator.start_worker()
    await saga_consolidator.start_worker()
    await archivist_worker.start_worker()
    await knowledge_worker.start_worker()
    await kig_maintenance.start_worker()
    knowledge_recall_service.start_worker()
    try:
        yield
    finally:
        await life_catchup_service.stop()
        knowledge_recall_service.stop_worker()
        await kig_maintenance.stop_worker()
        await knowledge_worker.stop_worker()
        await archivist_worker.stop_worker()
        await saga_consolidator.stop_worker()
        await episode_consolidator.stop_worker()
        await memory_observer_service.stop_worker()
        await proactive_orchestrator.stop_worker()
        await companion_cognition_service.stop_worker()
        await affect_observer_service.stop_worker()
        await conversation_summary_service.stop_worker()


app = FastAPI(title="遐蝶 Agent Backend", version="0.1.0", lifespan=lifespan)
app.include_router(pwm_router)

# init 也在模块导入时执行一次，保证裸 TestClient（不走 lifespan）也有表可用。
db.init_db()

# 只允许明确的本地开发来源和 Electron file:// 来源；实际数据接口还需临时令牌。
app.add_middleware(
  CORSMiddleware,
  allow_origins=list(ALLOWED_ORIGINS),
  allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
  allow_headers=[
    "Content-Type",
    TOKEN_HEADER,
    # 知识库导入用的自定义 header，缺这些会导致 CORS 预检失败
    # （浏览器抛 TypeError: Failed to fetch）
    "X-Xiadie-Filename",
    "X-Xiadie-Collection",
    "X-Xiadie-Sensitivity",
  ],
)
app.middleware("http")(local_api_guard)


# ---------------------------------------------------------------- 基础
@app.get("/api/health")
def health() -> dict:
    # 供 Electron 判断进程是否就绪，不暴露版本、配置或运行环境。
    return {"status": "ok"}


# ---------------------------------------------------------------- 会话
class SessionIn(BaseModel):
    title: Optional[str] = None
    temporary: bool = False


@app.get("/api/sessions")
def list_sessions() -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count"
            " FROM sessions s WHERE archived = 0 ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.post("/api/sessions")
def create_session(body: SessionIn) -> dict:
    conn = db.connect()
    try:
        sid = db.new_id()
        t = db.now()
        conn.execute(
            "INSERT INTO sessions(id,title,temporary,created_at,updated_at) VALUES(?,?,?,?,?)",
            (sid, (body.title or "新对话").strip() or "新对话", int(body.temporary), t, t),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone())
    finally:
        conn.close()


@app.patch("/api/sessions/{sid}")
def update_session(sid: str, body: dict) -> dict:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        if not row:
            raise HTTPException(404, "会话不存在")
        title = body.get("title")
        archived = body.get("archived")
        if title is not None:
            conn.execute("UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                         (title.strip() or "新对话", db.now(), sid))
        if archived is not None:
            conn.execute("UPDATE sessions SET archived = ?, updated_at = ? WHERE id = ?",
                         (1 if archived else 0, db.now(), sid))
        conn.commit()
        return dict(conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone())
    finally:
        conn.close()


@app.delete("/api/sessions/{sid}")
def delete_session(sid: str) -> dict:
    conn = db.connect()
    try:
        conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/sessions/{sid}/messages")
def list_messages(sid: str) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at", (sid,)
        ).fetchall()
        messages = [_msg(r) for r in rows]
        by_message: dict[str, list[dict]] = {}
        citations = conn.execute(
            "SELECT * FROM knowledge_message_citations WHERE assistant_message_id IN "
            "(SELECT id FROM messages WHERE session_id=?) ORDER BY assistant_message_id,citation_key",
            (sid,),
        ).fetchall()
        for citation in citations:
            public = knowledge_context.citation_public(citation)
            by_message.setdefault(public["assistant_message_id"], []).append(public)
        evidence_by_message: dict[str, list[dict]] = {}
        evidence_rows = conn.execute(
            "SELECT * FROM kig_evidence_links WHERE validation_status='active' "
            "AND assistant_message_id IN "
            "(SELECT id FROM messages WHERE session_id=?) "
            "ORDER BY assistant_message_id,citation_key,id",
            (sid,),
        ).fetchall()
        for evidence_row in evidence_rows:
            public = kig_evidence.evidence_link_public(evidence_row)
            evidence_by_message.setdefault(public["assistant_message_id"], []).append(public)
        attachments_by_message: dict[str, list[dict]] = {}
        attach_rows = conn.execute(
            "SELECT id, message_id, filename, mime_type, char_count, content_sha256, created_at,"
            " attachment_kind,byte_count,pixel_width,pixel_height"
            " FROM message_attachments WHERE message_id IN"
            " (SELECT id FROM messages WHERE session_id=?) ORDER BY message_id, created_at",
            (sid,),
        ).fetchall()
        for attach in attach_rows:
            if not attach["message_id"]:
                continue
            attachments_by_message.setdefault(attach["message_id"], []).append({
                "id": attach["id"],
                "filename": attach["filename"],
                "mime_type": attach["mime_type"],
                "char_count": attach["char_count"],
                "content_preview": "",
                "content_sha256": attach["content_sha256"],
                "created_at": attach["created_at"],
                "attachment_kind": attach["attachment_kind"],
                "byte_count": attach["byte_count"],
                "pixel_width": attach["pixel_width"],
                "pixel_height": attach["pixel_height"],
            })
        for message in messages:
            message["knowledge_citations"] = by_message.get(message["id"], [])
            message["evidence_links"] = evidence_by_message.get(message["id"], [])
            message["attachments"] = attachments_by_message.get(message["id"], [])
        return messages
    finally:
        conn.close()


@app.get("/api/messages/{mid}/attachments/{aid}/content")
def get_message_attachment_content(mid: str, aid: str) -> dict:
    """返回附件全文，供前端点击查看。仅本机访问，不暴露 token。"""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id, message_id, filename, mime_type, content_text, char_count,attachment_kind"
            " FROM message_attachments WHERE id=? AND message_id=?",
            (aid, mid),
        ).fetchone()
        if not row:
            raise HTTPException(404, "附件不存在")
        if row["attachment_kind"] == "image":
            raise HTTPException(409, "图片原始字节为临时数据，不提供消息历史回读")
        return {
            "id": row["id"],
            "filename": row["filename"],
            "mime_type": row["mime_type"],
            "char_count": row["char_count"],
            "content": row["content_text"],
        }
    finally:
        conn.close()


@app.delete("/api/chat/attachments/{attachment_id}")
def delete_chat_attachment(attachment_id: str) -> dict:
    """删除未绑定的附件（message_id IS NULL）。

    用于前端用户点 × 移除 ready 附件时立即清理后端记录，避免孤儿数据。
    已绑定到消息（message_id IS NOT NULL）的附件不能通过此端点删除，
    应通过删除消息级联清理。
    """
    conn = db.connect()
    try:
        attachment = conn.execute(
            "SELECT message_id,storage_path FROM message_attachments WHERE id=?",
            (attachment_id,),
        ).fetchone()
        if attachment is None:
            raise HTTPException(404, "附件不存在")
        if attachment["message_id"] is not None:
            raise HTTPException(409, "附件已绑定到消息，不能单独删除")
        cursor = conn.execute(
            "DELETE FROM message_attachments WHERE id=? AND message_id IS NULL",
            (attachment_id,),
        )
        conn.commit()
        image_attachments.remove(attachment["storage_path"])
        return {"deleted": True}
    finally:
        conn.close()


@app.get("/api/conversation-summaries/runs")
def get_conversation_summary_runs(session_id: str | None = None,
                                  limit: int = 50) -> list[dict]:
    return conversation_summaries.list_runs(session_id=session_id, limit=limit)


@app.get("/api/conversation-summaries/runs/{run_id}")
def get_conversation_summary_run(run_id: str) -> dict:
    run = conversation_summaries.get_run(run_id)
    if not run:
        raise HTTPException(404, "摘要任务不存在")
    return run


@app.get("/api/sessions/{sid}/conversation-summary-revisions")
def get_conversation_summary_revisions(sid: str, limit: int = 50) -> list[dict]:
    return conversation_summaries.list_revisions(sid, limit=limit)


@app.get("/api/sessions/{sid}/conversation-summary-events")
def get_conversation_summary_events(sid: str, limit: int = 100) -> list[dict]:
    return conversation_summaries.list_events(sid, limit=limit)


@app.get("/api/history-recall/events")
def get_history_recall_events(session_id: str | None = None,
                              limit: int = 50) -> list[dict]:
    return history_recall.list_events(session_id=session_id, limit=limit)


@app.post("/api/history-recall/rebuild")
def rebuild_history_recall_index() -> dict[str, int]:
    return history_recall.rebuild_index()


class ContextControlsIn(BaseModel):
    reference_chat_history: bool | None = None
    summary_injection_enabled: bool | None = None


@app.get("/api/context/controls")
def get_context_controls() -> dict:
    return context_controls.read()


@app.put("/api/context/controls")
def put_context_controls(body: ContextControlsIn) -> dict:
    return context_controls.update(
        reference_chat_history=body.reference_chat_history,
        summary_injection_enabled=body.summary_injection_enabled,
    )


@app.get("/api/context/diagnostics")
def get_context_diagnostics(session_id: str | None = None, limit: int = 50) -> dict:
    """Advanced, body-free diagnostics; never returns message or summary text."""
    return {
        "controls": context_controls.read(),
        "component_priority": list(context_assembler.OPTIONAL_COMPONENT_PRIORITY),
        "package_events": context_diagnostics.list_events(session_id=session_id, limit=limit),
        "history_events": history_recall.list_events(session_id=session_id, limit=limit),
        "summary_runs": conversation_summaries.list_runs(session_id=session_id, limit=limit),
        "summary_revisions": (
            conversation_summaries.list_revisions(session_id, limit=limit)
            if session_id else []
        ),
        "context_contributors": context_contributions.diagnostics(),
    }


@app.post("/api/sessions/{sid}/conversation-summary-rebuild")
def rebuild_conversation_summary(sid: str) -> dict:
    try:
        return conversation_summary_service.rebuild(sid)
    except conversation_summaries.ConversationSummaryError as exc:
        raise HTTPException(400, {"code": exc.code, "message": str(exc)}) from exc


@app.delete("/api/sessions/{sid}/conversation-summary-derived")
def delete_conversation_summary_derived(sid: str) -> dict:
    try:
        return conversation_summaries.delete_derived(sid)
    except conversation_summaries.ConversationSummaryError as exc:
        raise HTTPException(404, {"code": exc.code, "message": str(exc)}) from exc


class ConversationSummaryModelIn(BaseModel):
    mode: str
    provider_id: str | None = None
    model: str | None = None
    allow_remote_history: bool = False


@app.get("/api/conversation-summaries/model-config")
def get_conversation_summary_model_config() -> dict:
    return conversation_summary_service.get_model_config()


@app.put("/api/conversation-summaries/model-config")
def put_conversation_summary_model_config(body: ConversationSummaryModelIn) -> dict:
    try:
        return conversation_summary_service.set_model_config(
            mode=body.mode, provider_id=body.provider_id, model=body.model,
            allow_remote_history=body.allow_remote_history,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/messages/{mid}/favorite")
def toggle_favorite(mid: str) -> dict:
    conn = db.connect()
    try:
        row = conn.execute("SELECT favorite FROM messages WHERE id = ?", (mid,)).fetchone()
        if not row:
            raise HTTPException(404, "消息不存在")
        newv = 0 if row["favorite"] else 1
        conn.execute("UPDATE messages SET favorite = ? WHERE id = ?", (newv, mid))
        conn.commit()
        return {"ok": True, "favorite": bool(newv)}
    finally:
        conn.close()


@app.get("/api/knowledge/citations/{citation_id}")
def read_knowledge_citation(citation_id: str) -> dict:
    """只返回仍与保存哈希一致的真实本地切片；快照不能冒充已删除来源。"""
    conn = db.connect()
    try:
        citation = conn.execute(
            "SELECT * FROM knowledge_message_citations WHERE id=?", (citation_id,),
        ).fetchone()
        if not citation:
            raise HTTPException(404, "引用不存在")
        source = conn.execute(
            "SELECT c.content,c.content_sha256,d.status,d.governance_status,d.index_version,"
            "co.status collection_status "
            "FROM knowledge_chunks c JOIN knowledge_documents d ON d.id=c.document_id "
            "JOIN knowledge_collections co ON co.id=d.collection_id "
            "WHERE c.id=? AND c.document_id=?",
            (citation["chunk_id"], citation["document_id"]),
        ).fetchone()
        if (
            not source or source["content_sha256"] != citation["content_sha256"]
            or hashlib.sha256(source["content"].encode("utf-8")).hexdigest()
            != citation["content_sha256"]
            or source["status"] != "indexed" or source["governance_status"] != "active"
            or source["index_version"] not in knowledge_search.COMPATIBLE_INDEX_VERSIONS
            or source["collection_status"] != "active"
        ):
            raise HTTPException(410, "原始资料已变化、停用或删除")
        result = knowledge_context.citation_public(citation)
        result["content"] = source["content"]
        return result
    finally:
        conn.close()


@app.get("/api/kig/evidence-links/{evidence_link_id}")
def read_kig_evidence_link(evidence_link_id: str) -> dict:
    """Open the current owner-system source or explicitly report unavailability."""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM kig_evidence_links WHERE id=?", (evidence_link_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "证据来源不存在")
    finally:
        conn.close()
    return kig_evidence.open_evidence_link(row)


# ---------------------------------------------------------------- 聊天（流式）
class ChatIn(BaseModel):
    session_id: str
    content: str
    regenerate: bool = False
    request_nonce: Optional[str] = Field(default=None, min_length=16, max_length=64,
                                         pattern=r"^[A-Za-z0-9_-]+$")
    knowledge_grant_token: Optional[str] = Field(default=None, max_length=256)
    knowledge_skip_restricted: bool = False
    attachment_ids: list[str] = Field(default_factory=list)
    ingress_messages: list[turn_ingress.TurnIngressMessage] = Field(
        default_factory=list, max_length=turn_ingress.MAX_MESSAGES,
    )
    temporary_chat: bool = False
    chat_nonce: Optional[str] = Field(default=None, min_length=16, max_length=64,
                                     pattern=r"^[A-Za-z0-9_-]+$")
    cancel_token: Optional[str] = Field(default=None, min_length=16, max_length=64,
                                       pattern=r"^[A-Za-z0-9_-]+$")
    image_transmission_consent: bool = False
    image_provider_id: Optional[str] = Field(default=None, max_length=80)
    image_model: Optional[str] = Field(default=None, max_length=200)
    image_location_revision: Optional[int] = Field(default=None, ge=1)
    persona_mode: Optional[str] = Field(
        default=None, pattern=r"^(companionship|focused_work)$",
    )
    persona_style: dict[str, str] = Field(default_factory=dict)


class ChatCancelIn(BaseModel):
    cancel_token: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class ContextContributorToggleIn(BaseModel):
    enabled: bool


@app.get("/api/cie/settings")
def read_cie_settings() -> dict[str, object]:
    return cie_settings.snapshot() | {
        "window_ms": turn_ingress.DEFAULT_WINDOW_MS,
        "window_min_ms": turn_ingress.MIN_WINDOW_MS,
        "window_max_ms": turn_ingress.MAX_WINDOW_MS,
        "max_messages": turn_ingress.MAX_MESSAGES,
        "ingress_protocol_version": turn_ingress.PROTOCOL_VERSION,
    }


@app.get("/api/cie/context-contributors")
def read_context_contributors() -> dict[str, object]:
    """Body-free registration, switch and recent collection diagnostics."""
    return context_contributions.diagnostics()


@app.put("/api/cie/context-contributors/{contributor_id}")
def put_context_contributor(
    contributor_id: str, body: ContextContributorToggleIn,
) -> dict[str, object]:
    try:
        return context_contributions.set_enabled(contributor_id, body.enabled)
    except KeyError as error:
        raise HTTPException(404, "上下文贡献者不存在") from error


@app.get("/api/cie/vision-capability")
def read_vision_capability() -> dict:
    provider, model = _current_model()
    return vision_capabilities.status(provider, model)


@app.post("/api/cie/vision-capability/probe")
async def probe_vision_capability() -> dict:
    if not cie_settings.is_enabled():
        raise HTTPException(409, {"code": "cie_disabled", "message": "CIE 图片能力尚未启用"})
    provider, model = _current_model()
    return await vision_capabilities.probe(provider, model)


@app.post("/api/chat/cancel")
def cancel_chat(body: ChatCancelIn) -> dict:
    result = chat_request_control.cancel(body.cancel_token)
    if not result["found"] and not cie_settings.is_enabled():
        raise HTTPException(409, {"code": "cie_disabled", "message": "CIE 生成打断尚未启用"})
    return result


def _current_model() -> tuple[Optional[dict], str]:
    try:
        cfg = json.loads(db.get_setting("current_model", "{}") or "{}")
    except (ValueError, TypeError):
        cfg = {}  # 存储被写坏时退回 mock，避免聊天永久 500
    pid = cfg.get("provider_id", "mock")
    model = cfg.get("model", "xiadie-mock")
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM providers WHERE id = ?", (pid,)).fetchone()
        if not row:
            return None, "xiadie-mock"
        prov = dict(row)
        prov["models"] = json.loads(prov["models"] or "[]")
        return prov, model
    finally:
        conn.close()


def _context_capability(provider: dict | None, model: str):
    configured = db.get_setting("model_context_capabilities", "{}")
    return context_budget.resolve_model_context_capability(
        provider, model, configured_profiles=configured,
    )


@app.post("/api/chat")
async def chat(body: ChatIn) -> StreamingResponse:
    if bool(body.chat_nonce) != bool(body.cancel_token):
        raise HTTPException(422, "chat_nonce 与 cancel_token 必须成对提供")
    if body.chat_nonce and not cie_settings.is_enabled():
        raise HTTPException(409, {"code": "cie_disabled", "message": "CIE 生成打断尚未启用"})
    if body.chat_nonce:
        request_state, replay_payload = chat_request_control.lookup(
            body.chat_nonce, body.session_id, body.cancel_token,
        )
        if request_state == "conflict":
            raise HTTPException(409, {"code": "chat_nonce_conflict", "message": "请求 nonce 已属于其他会话"})
        if request_state == "active":
            raise HTTPException(409, {"code": "chat_request_active", "message": "相同请求仍在处理中"})
        if request_state == "completed" and replay_payload:
            assistant_id = replay_payload.get("message_id")
            if assistant_id:
                replay_payload["knowledge_citations"] = [
                    knowledge_context.citation_public(row)
                    for row in _message_knowledge_citations(assistant_id)
                ]
                replay_payload["evidence_links"] = [
                    kig_evidence.evidence_link_public(row)
                    for row in _message_evidence_links(assistant_id)
                ]
            async def replay_completed():
                yield _sse("phase", {"phase": "completed", "replayed": True})
                yield _sse("final", replay_payload)
                yield _sse("done", replay_payload | {"replayed": True})
            return StreamingResponse(replay_completed(), media_type="text/event-stream")
    # CDS.2: a real user turn preempts only not-started low-priority cognition work.
    cognition_runtime.DEFAULT_GOVERNOR.cancel_pending_for_user_message()
    ingress_envelope: turn_ingress.TurnEnvelope | None = None
    if body.ingress_messages:
        if body.regenerate:
            raise HTTPException(400, "regenerate 不接受 ingress_messages")
        if not cie_settings.is_enabled():
            raise HTTPException(409, {"code": "cie_disabled", "message": "CIE 消息积累尚未启用"})
        try:
            ingress_envelope = turn_ingress.build_envelope(body.session_id, body.ingress_messages)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if body.content != ingress_envelope.content:
            raise HTTPException(409, {
                "code": "turn_envelope_mismatch",
                "message": "消息积累窗口内容与服务端封包不一致",
            })
    effective_content = ingress_envelope.content if ingress_envelope else body.content
    # Frozen single-source state writers still accept one evidence message ID.
    # Anchor their text to the last original message instead of falsely tying
    # the whole ephemeral envelope to that ID. Retrieval may use the envelope.
    anchored_ingress_index = None
    if ingress_envelope:
        anchored_ingress_index = next(
            (
                index for index in range(len(ingress_envelope.entries) - 1, -1, -1)
                if ingress_envelope.entries[index].content.strip()
            ),
            len(ingress_envelope.entries) - 1,
        )
    anchored_content = (
        ingress_envelope.entries[anchored_ingress_index].content
        if ingress_envelope and anchored_ingress_index is not None
        else effective_content
    )
    effective_attachment_ids = (
        list(ingress_envelope.attachment_ids) if ingress_envelope else body.attachment_ids
    )
    # 空 content 且无附件：拒绝（regenerate 不受此约束，因为复用历史消息）
    if not body.regenerate and not effective_content.strip() and not effective_attachment_ids:
        raise HTTPException(400, "content 和 attachment_ids 至少有一个非空")
    uid: str | None = None
    ingress_message_ids: list[str] = []
    anchored_uid: str | None = None
    replace_assistant_id: str | None = None
    provider, model = _current_model()
    kig_chat_result = None
    governed_context_contributions: tuple[context_contributions.GovernedContribution, ...] = ()
    image_data_urls: list[str] = []
    consumed_image_files: list[tuple[str, str]] = []
    short_memo_snapshot = None
    short_memo_items: list[dict[str, object]] = []
    short_memo_digest = ""
    conn = db.connect()
    try:
        sess = conn.execute("SELECT * FROM sessions WHERE id = ?", (body.session_id,)).fetchone()
        if not sess:
            raise HTTPException(404, "会话不存在")
        temporary_chat = bool(sess["temporary"]) or body.temporary_chat
        if temporary_chat and not sess["temporary"]:
            conn.execute("UPDATE sessions SET temporary=1,updated_at=? WHERE id=?",
                         (db.now(), body.session_id))
            # Companion-state reads use a separate SQLite connection.  Commit the
            # one-way privacy transition before those reads to avoid retaining a
            # write lock for the rest of chat preparation.
            conn.commit()

        if not temporary_chat:
            # Capture once at the request boundary.  A rollout change during a
            # request must not produce a mixed read/write policy.
            short_memo_snapshot = short_memo.rollout_snapshot(conn)
            try:
                short_memo_items = short_memo.recall(
                    effective_content, snapshot=short_memo_snapshot,
                )
                short_memo_digest = short_memo.render_recall(short_memo_items)
            except Exception:  # short-term continuity must never block chat
                logger.warning(
                    "short_memo_recall_failed session_id=%s", body.session_id, exc_info=True,
                )
                short_memo_items = []
                short_memo_digest = ""

        if cie_settings.is_enabled():
            contribution_request_id = f"cie-context:{db.new_id()}"
            try:
                contribution_batch = await context_contributions.collect(
                    context_contributions.ContributionRequest(
                        request_id=contribution_request_id,
                        session_id=body.session_id,
                        query=effective_content,
                        provider_id=str((provider or {}).get("id") or "mock"),
                        provider_location=str(
                            (provider or {}).get("execution_location") or "local"
                        ),
                        temporary_chat=temporary_chat,
                        now=db.now(),
                    ),
                )
                contribution_governance = kig_pipeline.govern_context_contributions(
                    contribution_batch,
                    provider=provider,
                    temporary_chat=temporary_chat,
                )
                governed_context_contributions = contribution_governance.accepted
            except Exception:  # third-party context must never block base chat
                logger.warning(
                    "cie_context_contribution_failed session_id=%s",
                    body.session_id,
                    exc_info=True,
                )
                governed_context_contributions = ()

        # 先分配/定位消息 ID，但在远传授权校验完成前不写入新消息。
        if not body.regenerate:
            if ingress_envelope:
                ingress_message_ids = [db.new_id() for _item in ingress_envelope.entries]
                uid = ingress_message_ids[-1]
                anchored_uid = ingress_message_ids[anchored_ingress_index]
            else:
                uid = db.new_id()
                anchored_uid = uid
        else:
            # 重新生成时先保留旧回复。构造上下文时排除它，只有新回复成功写入的
            # 同一事务中才删除旧回复，网络或模型失败不会造成内容丢失。
            last = conn.execute(
                "SELECT id FROM messages WHERE session_id = ? AND role = 'assistant'"
                " ORDER BY created_at DESC LIMIT 1",
                (body.session_id,),
            ).fetchone()
            if last:
                replace_assistant_id = last["id"]
            last_user = conn.execute(
                "SELECT id FROM messages WHERE session_id=? AND role='user'"
                " ORDER BY created_at DESC,id DESC LIMIT 1", (body.session_id,),
            ).fetchone()
            if last_user:
                uid = last_user["id"]
                anchored_uid = uid

        attachment_rows: dict[str, object] = {}
        if effective_attachment_ids:
            rows = conn.execute(
                "SELECT id,filename,mime_type,content_text,message_id,attachment_kind,"
                "storage_path,byte_count,pixel_width,pixel_height,expires_at"
                " FROM message_attachments WHERE id IN (%s)"
                % ",".join("?" * len(effective_attachment_ids)),
                effective_attachment_ids,
            ).fetchall()
            attachment_rows = {row["id"]: row for row in rows}
            if set(attachment_rows) != set(effective_attachment_ids):
                raise HTTPException(409, {
                    "code": "turn_attachment_unavailable",
                    "message": "本轮附件已失效或不存在",
                })
            if any(attachment_rows[aid]["message_id"] is not None for aid in effective_attachment_ids):
                raise HTTPException(409, {
                    "code": "turn_attachment_unavailable",
                    "message": "本轮附件已经绑定到其他消息",
                })

        image_ids = [
            aid for aid in effective_attachment_ids
            if attachment_rows[aid]["attachment_kind"] == "image"
        ]
        if image_ids:
            if body.regenerate:
                raise HTTPException(409, {
                    "code": "image_regenerate_unsupported",
                    "message": "图片原始字节已按单轮策略销毁，不能重新生成；请重新选择图片",
                })
            if not cie_settings.is_enabled():
                raise HTTPException(409, {"code": "cie_disabled", "message": "CIE 图片能力尚未启用"})
            if len(image_ids) > image_attachments.MAX_IMAGES_PER_TURN:
                raise HTTPException(413, {
                    "code": "image_count_exceeded",
                    "message": "每轮最多发送 4 张图片",
                })
            total_image_bytes = sum(int(attachment_rows[aid]["byte_count"] or 0) for aid in image_ids)
            if total_image_bytes > image_attachments.MAX_TOTAL_IMAGE_BYTES:
                raise HTTPException(413, {
                    "code": "image_bytes_exceeded",
                    "message": "本轮图片总字节超过 10 MiB 限制",
                })
            total_pixels = sum(
                int(attachment_rows[aid]["pixel_width"] or 0)
                * int(attachment_rows[aid]["pixel_height"] or 0)
                for aid in image_ids
            )
            if total_pixels > image_attachments.MAX_TOTAL_PIXELS:
                raise HTTPException(413, {
                    "code": "image_pixels_exceeded",
                    "message": "本轮图片总像素超过 1600 万限制",
                })
            capability_status = vision_capabilities.status(provider, model)
            if capability_status["status"] != "supported":
                raise HTTPException(409, {
                    "code": "vision_capability_unavailable",
                    "message": "当前模型尚未通过真实图片能力探测，不能假装看到了图片",
                    "capability": capability_status,
                })
            expected_snapshot = (
                capability_status["provider_id"], capability_status["model"],
                capability_status["provider_location_revision"],
            )
            supplied_snapshot = (
                body.image_provider_id, body.image_model, body.image_location_revision,
            )
            if supplied_snapshot != expected_snapshot:
                raise HTTPException(409, {
                    "code": "image_authorization_snapshot_changed",
                    "message": "图片发送目标已变化，请重新确认本轮授权",
                    "capability": capability_status,
                })
            location = capability_status["provider_location"]
            if location != "local" and not body.image_transmission_consent:
                raise HTTPException(409, {
                    "code": "image_transmission_consent_required",
                    "message": "向远程或位置未知的模型发送图片需要本轮单次确认",
                    "capability": capability_status,
                })
            if ingress_envelope:
                image_id_set = set(image_ids)
                expected_image_scope = "local_image" if location == "local" else "remote_image_once"
                for item in ingress_envelope.entries:
                    expected_scope = (
                        expected_image_scope
                        if any(aid in image_id_set for aid in item.attachment_ids)
                        else "local_text_only"
                    )
                    if item.authorization_scope != expected_scope:
                        raise HTTPException(409, {
                            "code": "turn_authorization_scope_mismatch",
                            "message": "积累消息的图片授权范围与当前发送目标不一致",
                        })
            current_time = db.now()
            try:
                for aid in image_ids:
                    row = attachment_rows[aid]
                    if not row["storage_path"] or float(row["expires_at"] or 0) <= current_time:
                        raise HTTPException(410, {
                            "code": "image_attachment_expired",
                            "message": "图片临时数据已过期，请重新选择图片",
                        })
                    image_data_urls.append(
                        image_attachments.load_data_url(row["storage_path"], row["mime_type"]),
                    )
            except (OSError, image_attachments.ImageAttachmentError) as error:
                raise HTTPException(410, {
                    "code": "image_attachment_unavailable",
                    "message": "图片临时数据不可用，请重新选择图片",
                }) from error

        # 构造上下文：人设 + 记忆摘要 + 历史
        digest, recalled_memories = (
            ("", []) if temporary_chat else memory.build_digest(effective_content)
        )
        current_state = companion_state.get_state(persist_advance=False)
        next_state = (
            current_state
            if body.regenerate
            else companion_state.preview_interaction(anchored_content, current_state)
        )
        style = companion_state.get_style_guidance(next_state)
        projection_rollout = inner_state_projection.rollout_mode()
        projection_mapping = None
        if projection_rollout != "off":
            try:
                request_mode = body.persona_mode or "companionship"
                projection = inner_state_projection.build(
                    state=next_state,
                    goals=personal_goals.list_goals(limit=20),
                    sagas=saga_lifecycle.list_sagas(status="active", limit=2),
                    life_events=life_events.list_events(limit=3),
                    short_memos=short_memo_items,
                    request_mode=request_mode,
                    current_intent=inner_state_projection.classify_current_intent(
                        effective_content, request_mode=request_mode,
                    ),
                )
                projection_mapping = projection.as_mapping() if projection else None
            except Exception:  # request-local expression hints must never block chat
                logger.warning(
                    "inner_state_projection_failed session_id=%s", body.session_id, exc_info=True,
                )
                projection_mapping = None
        try:
            persona_compilation = persona_v2.compile_for_request(
                legacy_prompt=persona.PERSONA_PROMPT,
                mode=body.persona_mode,
                style=body.persona_style,
                provider=provider,
                model=model,
                projection=projection_mapping,
                projection_rollout_mode=projection_rollout,
            )
        except persona_v2.PersonaResourceError as exc:
            raise HTTPException(422, str(exc)) from exc
        legacy_lore_digest = lore.retrieve_lore(effective_content)
        worldbook_recall = worldbook_r1.retrieve_for_request(
            effective_content, legacy_content=legacy_lore_digest,
        )
        lore_digest = worldbook_recall.content
        recall_mode = knowledge_recall.settings()["mode"]
        # 提前计算 capability，供知识召回动态预算和上下文装配共用
        capability = _context_capability(provider, model)
        # 纯附件无文字消息：跳过知识召回（避免误触发远传授权询问）
        content_has_text = bool(effective_content.strip())
        if content_has_text:
            knowledge_retrieval, recall_decision = knowledge_context.prepare_for_mode(
                effective_content, mode=recall_mode, provider=provider,
                lore_text=lore_digest, memory_text=digest, session_id=body.session_id,
                capability=capability,
            )
        else:
            knowledge_retrieval, recall_decision = None, None
        try:
            if knowledge_retrieval is not None:
                knowledge_retrieval = knowledge_grants.authorize_chat_locked(
                    conn, prepared=knowledge_retrieval, session_id=body.session_id,
                    user_message_id=uid or "", request_nonce=body.request_nonce,
                    content=effective_content, provider=provider, model=model,
                    grant_token=body.knowledge_grant_token,
                    skip_restricted=body.knowledge_skip_restricted,
                    recall_mode=recall_mode,
                )
        except knowledge_grants.GrantError as error:
            if conn.in_transaction:
                conn.rollback()
            raise HTTPException(
                error.status_code, {"code": error.code, "message": str(error)},
            ) from error

        if content_has_text and uid:
            try:
                kig_chat_result = kig_pipeline.prepare_for_chat(
                    query=effective_content, source_message_id=uid, session_id=body.session_id,
                    provider=provider, recall_mode=recall_mode,
                    authorized_knowledge_chunk_ids=frozenset(
                        str(item["chunk_id"])
                        for item in (knowledge_retrieval or {}).get("results", ())
                        if item.get("chunk_id")
                    ),
                    temporary_chat=temporary_chat,
                )
                knowledge_retrieval = kig_pipeline.filter_knowledge_prepared(
                    knowledge_retrieval, kig_chat_result,
                )
            except Exception:  # KIG degradation must never block companionship chat
                logger.warning("kig_chat_prepare_failed session_id=%s", body.session_id, exc_info=True)
                kig_chat_result = None

        if not body.regenerate:
            if ingress_envelope:
                created_at = db.now()
                conn.executemany(
                    "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
                    [
                        (message_id, body.session_id, "user", item.content, created_at + index * 0.000001)
                        for index, (message_id, item) in enumerate(
                            zip(ingress_message_ids, ingress_envelope.entries), start=1,
                        )
                    ],
                )
            else:
                conn.execute(
                    "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
                    (uid, body.session_id, "user", effective_content, db.now()),
                )
            cnt = conn.execute(
                "SELECT COUNT(*) c FROM messages WHERE session_id=? AND role='user'",
                (body.session_id,),
            ).fetchone()["c"]
            if cnt == 1:
                conn.execute(
                    "UPDATE sessions SET title=? WHERE id=?",
                    (effective_content.strip()[:20] or "新对话", body.session_id),
                )

        if not body.regenerate and uid and recall_mode == "smart" and recall_decision:
            knowledge_recall.record_actual_locked(
                conn, session_id=body.session_id, user_message_id=uid,
                user_text=effective_content, provider=provider, result=recall_decision,
                injected_count=len((knowledge_retrieval or {}).get("results", [])),
                grant_id=(knowledge_retrieval or {}).get("_grant_id"),
            )

        if replace_assistant_id:
            history = conn.execute(
                "SELECT id,role,content,model FROM messages"
                " WHERE session_id=? AND id!=? ORDER BY created_at,id",
                (body.session_id, replace_assistant_id),
            ).fetchall()
        else:
            history = conn.execute(
                "SELECT id,role,content,model FROM messages"
                " WHERE session_id=? ORDER BY created_at,id",
                (body.session_id,),
            ).fetchall()
        # 读取本轮附件全文，回填 message_id，拼接 attachment_block
        attachment_block = ""
        if effective_attachment_ids and uid:
            found = attachment_rows
            if ingress_envelope and (
                set(found) != set(effective_attachment_ids)
                or any(found[aid]["message_id"] is not None for aid in effective_attachment_ids)
            ):
                raise HTTPException(409, {
                    "code": "turn_attachment_unavailable",
                    "message": "积累窗口中的附件已失效或已经绑定",
                })
            attachment_owner = {
                aid: ingress_message_ids[index]
                for index, item in enumerate(ingress_envelope.entries if ingress_envelope else ())
                for aid in item.attachment_ids
            }
            parts = []
            for aid in effective_attachment_ids:
                row = found.get(aid)
                if row:
                    if row["attachment_kind"] == "image":
                        # 图片不进入 attachment_block；原始字节只通过 apply_images
                        # 临时加入本轮 LLM messages，Memory/Knowledge/KIG 仍只看文本。
                        conn.execute(
                            "UPDATE message_attachments SET message_id=?"
                            " WHERE id=? AND message_id IS NULL",
                            (attachment_owner.get(aid, uid), aid),
                        )
                        consumed_image_files.append((aid, row["storage_path"]))
                    else:
                        conn.execute(
                            "UPDATE message_attachments SET message_id=? WHERE id=? AND message_id IS NULL",
                            (attachment_owner.get(aid, uid), aid),
                        )
                        parts.append("=== %s ===\n%s" % (row["filename"], row["content_text"]))
            if parts:
                attachment_block = "\n\n".join(parts)
        knowledge_block = knowledge_context.prompt_block(knowledge_retrieval)
        self_timeline.refresh(conn=conn)
        timeline_block = self_timeline.context_block(effective_content)
        effective_lore_digest = "\n\n".join(part for part in (lore_digest, timeline_block) if part)
        if knowledge_retrieval:
            conn.execute(
                "INSERT INTO knowledge_chat_retrievals("
                "id,session_id,user_message_id,trigger_reason,query_sha256,candidate_count,"
                "injected_count,knowledge_tokens,knowledge_token_budget,lore_tokens,memory_tokens,"
                "status,search_protocol_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    knowledge_retrieval["id"], body.session_id, uid, knowledge_retrieval["reason"],
                    knowledge_retrieval["query_sha256"], knowledge_retrieval["candidate_count"],
                    len(knowledge_retrieval["results"]), knowledge_retrieval["knowledge_tokens"],
                    knowledge_retrieval["knowledge_token_budget"], knowledge_retrieval["lore_tokens"],
                    knowledge_retrieval["memory_tokens"], knowledge_retrieval["status"],
                    knowledge_search.SEARCH_PROTOCOL_VERSION, db.now(),
                ),
            )
        active_summary = (
            conversation_summaries.active_revision_internal(body.session_id)
            if not temporary_chat and context_controls.summary_injection_enabled()
            else None
        )
        history_prepared = (
            {"turns": [], "retrieval_id": None}
            if temporary_chat else history_recall.prepare_locked(
                conn, effective_content, current_session_id=body.session_id,
            )
        )
        try:
            context_package = context_assembler.assemble(
                history=history,
                capability=capability,
                memory_digest=digest,
                short_memo_digest=short_memo_digest,
                affect_guidance=style,
                lore_digest=effective_lore_digest,
                knowledge_block=knowledge_block,
                active_summary=active_summary,
                cross_session_recall=history_prepared["turns"],
                current_session_id=body.session_id,
                attachment_block=attachment_block,
                retrieval_bundle=(kig_chat_result.bundle if kig_chat_result else None),
                context_contribution_candidates=governed_context_contributions,
                base_persona_prompt=persona_compilation.prompt,
                persona_meta=persona_compilation.public_meta(),
                worldbook_meta=worldbook_recall.public_meta(),
            )
        except context_budget.ContextBudgetError as error:
            if conn.in_transaction:
                conn.rollback()
            raise HTTPException(413, error.public_detail()) from error
        messages = list(context_package.messages)
        if image_data_urls:
            messages = vision_capabilities.apply_images(messages, image_data_urls)
        trimmed_count = context_package.trimmed_messages
        conn.commit()
    finally:
        conn.close()

    if not body.regenerate and not temporary_chat and short_memo_snapshot:
        source_items = (
            zip(ingress_message_ids, ingress_envelope.entries)
            if ingress_envelope else ((uid, None),)
        )
        for source_message_id, ingress_item in source_items:
            if not source_message_id:
                continue
            source_text = ingress_item.content if ingress_item is not None else effective_content
            try:
                await short_memo.validate_and_process_user_message(
                    session_id=body.session_id,
                    message_id=source_message_id,
                    text=source_text,
                    provider=provider,
                    model=model,
                    snapshot=short_memo_snapshot,
                )
            except Exception:  # silent extraction is an optional, non-blocking side effect
                logger.warning(
                    "short_memo_process_failed session_id=%s message_id=%s",
                    body.session_id, source_message_id, exc_info=True,
                )

    for _attachment_id, storage_name in consumed_image_files:
        image_attachments.remove(storage_name)
    if consumed_image_files:
        try:
            cleanup_conn = db.connect()
            try:
                cleanup_conn.executemany(
                    "UPDATE message_attachments SET storage_path=NULL"
                    " WHERE id=? AND storage_path=?",
                    consumed_image_files,
                )
                cleanup_conn.commit()
            finally:
                cleanup_conn.close()
        except Exception:
            logger.warning("cie_image_metadata_cleanup_failed", exc_info=True)

    if body.chat_nonce and body.cancel_token:
        request_state, _replay_payload = chat_request_control.begin(
            chat_nonce=body.chat_nonce,
            cancel_token=body.cancel_token,
            session_id=body.session_id,
        )
        if request_state != "started":
            raise HTTPException(409, {
                "code": "chat_request_race",
                "message": "请求状态已变化，请使用新的请求标识重试",
            })

    if kig_chat_result:
        try:
            kig_pipeline.persist_deterministic_relations(kig_chat_result)
        except Exception:  # derived governance persistence is non-blocking
            logger.warning("kig_relation_persist_failed session_id=%s", body.session_id, exc_info=True)

    if not body.regenerate and not temporary_chat and uid and recall_mode == "explicit" and content_has_text:
        # 只在后台记录影子判断；绝不修改本轮 messages 或 knowledge_block。
        # 纯附件无文字消息不触发知识召回，无需入队影子判断。
        knowledge_recall.enqueue(
            session_id=body.session_id, user_message_id=uid,
            user_text=effective_content, provider=provider,
        )

    # EAP v0.2 Conversation Presence v2：用户消息入库后更新 presence 状态。
    # 按 spec："新消息到达时自动使过期离开状态结束"；程序规则识别高精度表达。
    # presence 更新失败不应阻塞聊天（try/except 包裹）。
    if not body.regenerate and not temporary_chat and uid and content_has_text:
        try:
            proactive_orchestrator.handle_user_message(body.session_id)
        except Exception:  # noqa: BLE001 - proactive recovery must not block chat
            logger.warning(
                "proactive_user_return_failed session_id=%s message_id=%s",
                body.session_id, uid, exc_info=True,
            )
        try:
            proactive_feedback.capture_natural_feedback(
                body.session_id, anchored_uid, anchored_content,
            )
        except Exception:  # noqa: BLE001 - feedback inference must not block chat
            logger.warning(
                "proactive_feedback_capture_failed session_id=%s message_id=%s",
                body.session_id, uid, exc_info=True,
            )
        try:
            proactive_presence.update_presence(
                body.session_id,
                proactive_presence.detect_presence_signals(anchored_content),
                source_message_id=anchored_uid,
            )
        except Exception:  # noqa: BLE001 - presence failure must not block chat
            logger.warning(
                "presence_update_failed session_id=%s message_id=%s",
                body.session_id, uid, exc_info=True,
            )

    async def gen():
        nonlocal context_package, messages, trimmed_count
        used_memories = recalled_memories
        collected: list[str] = []
        try:
            if body.cancel_token:
                yield _sse("phase", {"phase": "retrieval"})
                if chat_request_control.is_cancelled(body.cancel_token):
                    _finish_knowledge_retrieval(knowledge_retrieval, status="failed")
                    yield _sse("cancelled", {"phase": "retrieval", "persisted": False})
                    chat_request_control.finish(body.cancel_token)
                    return
                chat_request_control.phase(body.cancel_token, "generation")
                yield _sse("phase", {"phase": "generation"})
            if used_memories and uid:
                try:
                    recorded_ids = set(archivist.record_injected_memories(
                        used_memories,
                        context_key=archivist.recall_context_key(body.session_id, uid),
                        source_session_id=body.session_id,
                    ))
                except Exception:  # noqa: BLE001 - 召回审计失败不能让聊天失败
                    recorded_ids = set()
                failed_reactivations = {
                    item["id"] for item in used_memories
                    if item.get("_reactivation_candidate") and item["id"] not in recorded_ids
                }
                if failed_reactivations:
                    used_memories = [
                        item for item in used_memories if item["id"] not in failed_reactivations
                    ]
                    used_digest, used_memories = memory.render_digest(used_memories)
                    context_package = context_assembler.assemble(
                        history=history,
                        capability=capability,
                        memory_digest=used_digest,
                        short_memo_digest=short_memo_digest,
                        affect_guidance=style,
                        lore_digest=effective_lore_digest,
                        knowledge_block=knowledge_block,
                        active_summary=active_summary,
                        cross_session_recall=history_prepared["turns"],
                        current_session_id=body.session_id,
                        retrieval_bundle=(kig_chat_result.bundle if kig_chat_result else None),
                        context_contribution_candidates=governed_context_contributions,
                        base_persona_prompt=persona_compilation.prompt,
                        persona_meta=persona_compilation.public_meta(),
                        worldbook_meta=worldbook_recall.public_meta(),
                    )
                    messages = list(context_package.messages)
                    trimmed_count = context_package.trimmed_messages
            try:
                context_diagnostics.record(
                    session_id=body.session_id,
                    user_message_id=uid,
                    meta=context_package.public_meta(),
                )
            except Exception:  # body-free diagnostics must never block companionship chat
                pass
            try:
                history_recall.record_injected(
                    history_prepared.get("event_id"),
                    len(context_package.cross_session_turns),
                )
            except Exception:  # noqa: BLE001 - 历史召回审计失败不能阻断陪伴聊天
                pass
            # 记账/恢复完成后再报告最终实际注入集合。
            yield _sse(
                "meta",
                {
                    "model": model,
                    "memory_used": bool(used_memories),
                    "memory_count": len(used_memories),
                    "memory_refs": [
                        {
                            "id": item["id"],
                            "layer": item["layer"],
                            "source_session_id": item.get("source_session_id"),
                            "source_message_id": item.get("source_message_id"),
                        }
                        for item in used_memories
                    ],
                    "knowledge_used": bool(knowledge_retrieval and knowledge_retrieval["results"]),
                    "knowledge_count": len((knowledge_retrieval or {}).get("results", [])),
                    "knowledge_source": (
                        "confirmed" if (knowledge_retrieval or {}).get("confirmed")
                        else (knowledge_retrieval or {}).get("source_mode", "none")
                        if (knowledge_retrieval or {}).get("results") else "none"
                    ),
                    "knowledge_recall_mode": recall_mode,
                    "history_recall_used": bool(context_package.cross_session_turns),
                    "history_recall_count": len(context_package.cross_session_turns),
                    "history_recall_refs": [
                        {
                            "source_type": "cross_session_history",
                            "session_id": item.session_id,
                            "session_title": item.session_title,
                            "user_message_id": item.user_message_id,
                            "assistant_message_id": item.assistant_message_id,
                            "user_created_at": item.user_created_at,
                            "assistant_created_at": item.assistant_created_at,
                            "locator": item.locator,
                        }
                        for item in context_package.cross_session_turns
                    ],
                    "context_trimmed": trimmed_count > 0,
                    "context_trimmed_messages": trimmed_count,
                    "context_trimmed_rounds": context_package.trimmed_rounds,
                    "context_budget": context_package.public_meta(),
                    "turn_ingress": (
                        ingress_envelope.public_meta(ingress_message_ids)
                        if ingress_envelope else None
                    ),
                },
            )
            async for chunk in llm.stream_chat(
                provider, model, messages,
                max_tokens=context_package.output_reserve_tokens,
                temperature=0.0 if persona_compilation.selected_v2 else None,
            ):
                if body.cancel_token and chat_request_control.is_cancelled(body.cancel_token):
                    _finish_knowledge_retrieval(knowledge_retrieval, status="failed")
                    yield _sse("cancelled", {"phase": "generation", "persisted": False})
                    chat_request_control.finish(body.cancel_token)
                    return
                collected.append(chunk)
                yield _sse("delta", {"text": chunk})
        except llm.LLMError as e:
            _finish_knowledge_retrieval(knowledge_retrieval, status="failed")
            if body.cancel_token:
                chat_request_control.finish(body.cancel_token)
            yield _sse("error", {"message": str(e), "hint": e.hint})
            return
        except Exception:  # noqa: BLE001 兜底：任何未预期异常也作为 error 事件下发，不静默截断流
            _finish_knowledge_retrieval(knowledge_retrieval, status="failed")
            if body.cancel_token:
                chat_request_control.finish(body.cancel_token)
            yield _sse("error", {"message": "生成中断", "hint": "回复生成过程中出现意外错误，请重试。"})
            return
        if body.cancel_token and chat_request_control.is_cancelled(body.cancel_token):
            _finish_knowledge_retrieval(knowledge_retrieval, status="failed")
            yield _sse("cancelled", {"phase": "generation", "persisted": False})
            chat_request_control.finish(body.cancel_token)
            return
        full, used_citations = knowledge_context.validate_citations(
            "".join(collected), knowledge_retrieval,
            strict_support=bool(kig_chat_result and (
                kig_chat_result.bundle.high_risk or kig_chat_result.bundle.complex_query
            )),
        )
        evidence_validation = kig_evidence.validate_answer(
            full, kig_chat_result.bundle if kig_chat_result else None,
        )
        full = evidence_validation.text
        # 持久化阶段一旦开始便不可取消，避免半写入或误删旧回复。
        if body.cancel_token:
            chat_request_control.phase(body.cancel_token, "persistence")
            yield _sse("phase", {"phase": "persistence"})
        c2 = db.connect()
        try:
            aid = db.new_id()
            c2.execute(
                "INSERT INTO messages(id, session_id, role, content, model, created_at) VALUES(?,?,?,?,?,?)",
                (aid, body.session_id, "assistant", full, model, db.now()),
            )
            if replace_assistant_id:
                conversation_summaries.invalidate_for_replaced_message_locked(
                    c2, body.session_id, replace_assistant_id,
                )
                c2.execute("DELETE FROM messages WHERE id = ?", (replace_assistant_id,))
            if knowledge_retrieval:
                for citation in used_citations:
                    knowledge_context.insert_citation_locked(
                        c2, assistant_id=aid, retrieval_id=knowledge_retrieval["id"], item=citation,
                    )
                c2.execute(
                    "UPDATE knowledge_chat_retrievals SET assistant_message_id=?,status='completed',"
                    "finished_at=? WHERE id=?",
                    (aid, db.now(), knowledge_retrieval["id"]),
                )
            if kig_chat_result and uid:
                kig_evidence.persist_validation_locked(
                    c2, bundle=kig_chat_result.bundle, validation=evidence_validation,
                    session_id=body.session_id, user_message_id=uid,
                    assistant_message_id=aid,
                )
            c2.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (db.now(), body.session_id))
            c2.commit()
        except Exception:
            if body.cancel_token:
                chat_request_control.finish(body.cancel_token)
            raise
        finally:
            c2.close()
        if body.cancel_token:
            chat_request_control.complete(body.cancel_token, {
                "message_id": aid,
                "content": full,
                "knowledge_citations": [],
                "evidence_links": [],
                "auto_memory": None,
                "memory_candidate": None,
                "companion_state": None,
                "affect_observation": None,
                "companion_cognition": None,
                "memory_observation": None,
            })
        saved_companion_state = None
        affect_observation = None
        memory_observation = None
        if not body.regenerate and not temporary_chat:
            saved_companion_state = companion_state.commit_interaction(
                anchored_content,
                source_session_id=body.session_id,
                source_message_id=anchored_uid,
            )
            try:
                memory_observation = memory_observer_service.enqueue_turn(
                    chat_provider=provider,
                    chat_model=model,
                    session_id=body.session_id,
                    user_message_id=anchored_uid,
                    assistant_message_id=aid,
                )
            except Exception:  # noqa: BLE001 - 观察器故障不能破坏已完成的回复和引用
                memory_observation = {
                    "status": "unlogged_failure",
                    "error_code": "observer_enqueue_failed",
                }
        if uid and not temporary_chat:
            # Regeneration creates a new source revision: the worker revokes the old
            # suggestion and evaluates the replacement without incrementing interaction_count.
            affect_observation = companion_cognition_service.enqueue_turn(
                chat_provider=provider,
                chat_model=model,
                session_id=body.session_id,
                user_message_id=anchored_uid,
                assistant_message_id=aid,
            )
            try:
                proactive_orchestrator.enqueue_after_chat(
                    session_id=body.session_id,
                    user_message_id=anchored_uid,
                    assistant_message_id=aid,
                )
            except Exception:  # noqa: BLE001 - orchestration must not break a completed chat
                logger.warning(
                    "proactive_source_enqueue_failed session_id=%s message_id=%s",
                    body.session_id, aid, exc_info=True,
                )
        if not temporary_chat:
            try:
                conversation_summary_service.enqueue_after_chat(
                    session_id=body.session_id, chat_provider=provider, chat_model=model,
                )
            except Exception:  # noqa: BLE001 - 摘要入队不能破坏已完成聊天
                pass
        # 旧关键词候选只在观察模型不可用时兜底；真实模型路径不再逐条等待确认。
        candidate = None
        if (
            not body.regenerate
            and not temporary_chat
            and db.get_setting("memory_enabled", db.DEFAULT_MEMORY_ENABLED) == "1"
            and (memory_observation or {}).get("error_code")
            in ("observer_model_unavailable", "observer_enqueue_failed")
        ):
            try:
                candidate = memory.maybe_create_candidate(
                    anchored_content, body.session_id, anchored_uid,
                )
            except Exception:  # noqa: BLE001 - 记忆兜底不能吞掉成功的聊天回复
                candidate = None
        final_payload = {
            "message_id": aid,
            "content": full,
            "knowledge_citations": [
                knowledge_context.citation_public(row) for row in _message_knowledge_citations(aid)
            ],
            "evidence_links": [
                kig_evidence.evidence_link_public(row) for row in _message_evidence_links(aid)
            ],
        }
        if body.chat_nonce:
            chat_request_control.update_completed(body.chat_nonce, final_payload | {
                "auto_memory": None,
                "memory_candidate": candidate,
                "companion_state": saved_companion_state,
                "affect_observation": affect_observation,
                "companion_cognition": affect_observation,
                "memory_observation": memory_observation,
            })
        yield _sse("final", final_payload)
        yield _sse(
            "done",
            {
                "message_id": aid,
                "auto_memory": None,
                "memory_candidate": candidate,
                "companion_state": saved_companion_state,
                "affect_observation": affect_observation,
                "companion_cognition": affect_observation,
                "memory_observation": memory_observation,
                **final_payload,
            },
        )

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------- 伴侣状态
@app.get("/api/companion-state")
def read_companion_state() -> dict:
    return companion_state.get_state()


@app.get("/api/companion-state/cognition-runs")
def read_companion_cognition_runs() -> list[dict]:
    return companion_cognition_service.list_runs()


@app.get("/api/companion-state/proactive-runtime")
def read_proactive_runtime() -> dict:
    return {
        "sources": proactive_orchestrator.list_runtime_sources(),
        "sagas": proactive_orchestrator.list_runtime_sagas(),
        "deliveries": proactive_delivery.list_deliveries(),
        "delivery_enabled": proactive_settings.load_settings()[
            "proactive_local_delivery_enabled"
        ] == "1",
    }


class ProactiveDeliveryClaimIn(BaseModel):
    consumer_id: str = Field(min_length=1, max_length=120)


class ProactiveDeliveryBeginIn(ProactiveDeliveryClaimIn):
    lease_token: str = Field(min_length=1, max_length=120)


class ProactiveDeliveryAckIn(ProactiveDeliveryBeginIn):
    success: bool
    error_code: str | None = Field(default=None, max_length=80)


class ProactiveFeedbackIn(BaseModel):
    feedback_kind: str = Field(min_length=1, max_length=40)
    request_nonce: str = Field(min_length=1, max_length=120)


class ProactiveFeedbackResolveIn(BaseModel):
    accept: bool


@app.post("/api/proactive-deliveries/claim")
def claim_proactive_delivery(body: ProactiveDeliveryClaimIn) -> dict:
    return {"delivery": proactive_delivery.claim_next(body.consumer_id)}


@app.post("/api/proactive-deliveries/{delivery_id}/begin")
def begin_proactive_delivery(delivery_id: str, body: ProactiveDeliveryBeginIn) -> dict:
    try:
        return proactive_delivery.begin_delivery(
            delivery_id, body.consumer_id, body.lease_token
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/proactive-deliveries/{delivery_id}/ack")
def acknowledge_proactive_delivery(delivery_id: str, body: ProactiveDeliveryAckIn) -> dict:
    try:
        return proactive_delivery.acknowledge_delivery(
            delivery_id, body.consumer_id, body.lease_token,
            success=body.success, error_code=body.error_code,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/proactive/history")
def proactive_history(limit: int = 50) -> list[dict]:
    return proactive_feedback.list_history(limit)


@app.get("/api/proactive/feedback/pending")
def proactive_pending_feedback(limit: int = 50) -> list[dict]:
    return proactive_feedback.list_pending(limit)


@app.post("/api/proactive/deliveries/{delivery_id}/feedback")
def submit_proactive_feedback(delivery_id: str, body: ProactiveFeedbackIn) -> dict:
    try:
        return proactive_feedback.create_feedback(
            delivery_id, body.feedback_kind, request_nonce=body.request_nonce,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/proactive/feedback/{feedback_id}/resolve")
def resolve_proactive_feedback(feedback_id: str, body: ProactiveFeedbackResolveIn) -> dict:
    try:
        return proactive_feedback.resolve_feedback(feedback_id, accept=body.accept)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/proactive/diagnostics")
def proactive_diagnostics(limit: int = 100) -> dict:
    return proactive_feedback.diagnostics(limit)


@app.get("/api/cognition/diagnostics")
def cognition_diagnostics(decision_kind: str | None = None, limit: int = 50) -> dict:
    """Read-only CDS diagnostics with a strict body-free field allowlist."""
    return cognitive_decision.diagnostics(decision_kind=decision_kind, limit=limit)


class KIGSourceRefIn(BaseModel):
    source_kind: str
    source_id: str
    revision: str
    content_hash: str
    status: str
    privacy_scope: str
    locator: str


class KIGSourceGovernanceIn(BaseModel):
    authority_level: str
    scope: dict = Field(default_factory=dict)
    applicable_from: float | None = None
    applicable_to: float | None = None
    version_label: str | None = Field(default=None, max_length=80)
    user_confirmed: bool = False


class KIGRelationResolveIn(BaseModel):
    accept: bool
    expected_revision: int = Field(ge=1)


@app.get("/api/kig/sources/{source_kind}/{source_id}")
def resolve_kig_source(source_kind: str, source_id: str) -> dict:
    """Resolve body-free canonical source metadata from its owner system."""
    try:
        return {"source_ref": kig_sources.registry.resolve(source_kind, source_id).to_dict()}
    except kig_sources.SourceRefError as exc:
        raise HTTPException(404 if exc.code == "source_missing" else 422,
                            detail={"code": exc.code, "message": str(exc)}) from exc


@app.post("/api/kig/sources/validate")
def validate_kig_source(body: KIGSourceRefIn) -> dict:
    """Reject stale or forged hashes, privacy metadata and locators."""
    try:
        ref = kig_sources.SourceRef(**body.model_dump())
        return {"valid": True, "source_ref": kig_sources.validate_ref(ref).to_dict()}
    except kig_sources.SourceRefError as exc:
        raise HTTPException(409, detail={"code": exc.code, "message": str(exc)}) from exc


@app.put("/api/kig/governance/sources/{source_kind}/{source_id}")
def update_kig_source_governance(
    source_kind: str, source_id: str, body: KIGSourceGovernanceIn,
) -> dict:
    try:
        ref = kig_sources.registry.resolve(source_kind, source_id)
        return {"governance": kig_governance.upsert_source_governance(
            ref, authority_level=body.authority_level, scope=body.scope,
            applicable_from=body.applicable_from, applicable_to=body.applicable_to,
            version_label=body.version_label, user_confirmed=body.user_confirmed,
        )}
    except kig_sources.SourceRefError as exc:
        raise HTTPException(404 if exc.code == "source_missing" else 422,
                            detail={"code": exc.code, "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(409, detail={"code": str(exc), "message": str(exc)}) from exc


@app.get("/api/kig/governance/version-relations")
def list_kig_version_relations(status: str = "proposed", limit: int = 50) -> dict:
    if status not in {"proposed", "confirmed", "rejected", "superseded"}:
        raise HTTPException(422, "版本关系状态无效")
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM kig_version_relations WHERE status=? "
            "ORDER BY requires_confirmation DESC,updated_at DESC,id DESC LIMIT ?",
            (status, max(1, min(int(limit), 200))),
        ).fetchall()
        return {"relations": [dict(row) for row in rows]}
    finally:
        conn.close()


@app.post("/api/kig/governance/version-relations/{relation_id}/resolve")
def resolve_kig_version_relation(relation_id: str, body: KIGRelationResolveIn) -> dict:
    try:
        return {"relation": kig_governance.resolve_relation(
            relation_id, accept=body.accept, expected_revision=body.expected_revision,
        )}
    except ValueError as exc:
        code = str(exc)
        raise HTTPException(404 if code == "relation_missing" else 409,
                            detail={"code": code, "message": code}) from exc


@app.get("/api/life/events")
def get_life_events(include_revoked: bool = False, limit: int = 100) -> dict:
    """Read-only LIFE.2 fact-layer projection; mutation remains domain-owned."""
    return {"items": life_events.list_events(include_revoked=include_revoked, limit=limit)}


@app.get("/api/life/events/diagnostics")
def get_life_event_diagnostics(event_id: str | None = None, limit: int = 200) -> dict:
    return {"items": life_events.diagnostics(event_id=event_id, limit=limit)}


@app.get("/api/life/state")
def get_life_state() -> dict:
    state = life_runtime.get_state()
    if state is None:
        return {"initialized": False, "algorithm_version": life_runtime.ALGORITHM_VERSION}
    return {
        "initialized": True, "algorithm_version": life_runtime.ALGORITHM_VERSION,
        "revision": state.revision, "logical_time": state.logical_time,
        "timezone_id": state.timezone_id, "current_activity": state.current_activity,
        "activity_since": state.activity_since, "energy": state.energy, "focus": state.focus,
        "rest_need": state.rest_need, "social_openness": state.social_openness,
        "conservative_mode": state.conservative_mode, "anomaly_code": state.anomaly_code,
    }


@app.get("/api/life/schedules/{local_date}")
def get_life_schedule(local_date: str, timezone_id: str = "Asia/Shanghai") -> dict:
    schedule = life_schedule.get_active_schedule(local_date=local_date, timezone_id=timezone_id)
    return {"item": schedule}


class LifeSettingsIn(BaseModel):
    mode: str | None = None
    short_memo_enabled: bool | None = None
    short_memo_remote_extraction_enabled: bool | None = None
    short_memo_default_ttl_seconds: int | None = None


class ShortMemoUpdateIn(BaseModel):
    expected_revision: int = Field(ge=1)
    expires_at: float


class ShortMemoClearIn(BaseModel):
    clear_events: bool = False
    privacy: bool = False


class LifeDiaryUpdateIn(BaseModel):
    expected_revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=8_000)


class LifeDateCreateIn(BaseModel):
    label: str = Field(min_length=1, max_length=160)
    recurrence: str = "yearly_solar"
    date_year: int | None = None
    date_month: int = Field(ge=1, le=12)
    date_day: int = Field(ge=1, le=31)
    timezone_id: str = Field(default="Asia/Shanghai", min_length=1, max_length=100)
    celebration_policy: str = "natural"


class LifeDateUpdateIn(BaseModel):
    expected_revision: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=160)
    celebration_policy: str


class LifeGoalCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    priority: int = Field(default=3, ge=1, le=5)


class LifeGoalUpdateIn(BaseModel):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=160)
    status: str | None = None


@app.get("/api/life/settings")
def get_life_settings() -> dict:
    mode = life_catchup.get_mode()
    return {
        "mode": mode,
        "offline_continuity_default": life_catchup.MODE_CONTINUOUS,
        "short_memo": short_memo.rollout_snapshot().public(),
    }


@app.patch("/api/life/settings")
def update_life_settings(body: LifeSettingsIn) -> dict:
    try:
        if body.mode is not None:
            life_catchup.set_mode(body.mode)
        short_memo.update_product_settings(
            enabled=body.short_memo_enabled,
            remote_extraction_enabled=body.short_memo_remote_extraction_enabled,
            default_ttl_seconds=body.short_memo_default_ttl_seconds,
        )
        return get_life_settings()
    except life_catchup.CatchUpError as exc:
        raise HTTPException(400, detail=exc.code) from exc
    except short_memo.ShortMemoError as exc:
        raise HTTPException(400, detail=exc.code) from exc


@app.get("/api/life/short-memos")
def list_short_memos() -> dict:
    return {"items": short_memo.list_active()}


@app.patch("/api/life/short-memos/{memo_id}")
def update_short_memo(memo_id: str, body: ShortMemoUpdateIn) -> dict:
    try:
        return short_memo.update_expiry(
            memo_id, expected_revision=body.expected_revision, expires_at=body.expires_at,
        )
    except short_memo.ShortMemoError as exc:
        status = 404 if exc.code == "short_memo_not_found" else 409
        raise HTTPException(status, detail=exc.code) from exc


@app.delete("/api/life/short-memos/{memo_id}")
def delete_short_memo(memo_id: str) -> dict:
    return {"deleted": short_memo.delete(memo_id)}


@app.delete("/api/life/short-memos")
def clear_short_memos(body: ShortMemoClearIn | None = None) -> dict:
    options = body or ShortMemoClearIn()
    return {"deleted_count": short_memo.clear(
        clear_events=options.clear_events, privacy=options.privacy,
    )}


@app.get("/api/life/diary")
def list_life_diary(include_revoked: bool = False, limit: int = 100) -> dict:
    return {"items": diary.list_entries(include_revoked=include_revoked, limit=limit)}


@app.patch("/api/life/diary/{item_id}")
def update_life_diary(item_id: str, body: LifeDiaryUpdateIn) -> dict:
    try:
        return diary.revise_entry(
            item_id, expected_revision=body.expected_revision, title=body.title,
            body=body.body, reason_code="user_edited",
        )
    except diary.DiaryError as exc:
        raise HTTPException(409, detail=exc.code) from exc


@app.delete("/api/life/diary/{item_id}")
def delete_life_diary(item_id: str, expected_revision: int) -> dict:
    try:
        return diary.revoke_entry(item_id, expected_revision=expected_revision)
    except diary.DiaryError as exc:
        raise HTTPException(409, detail=exc.code) from exc


@app.get("/api/life/dates")
def list_life_dates(include_revoked: bool = False, limit: int = 100) -> dict:
    return {"items": important_dates.list_dates(include_revoked=include_revoked, limit=limit)}


@app.post("/api/life/dates")
def create_life_date(body: LifeDateCreateIn) -> dict:
    source_id = db.new_id()
    source_hash = hashlib.sha256(
        json.dumps(body.model_dump(), ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    try:
        item = important_dates.create_candidate(
            label=body.label, recurrence=body.recurrence, date_year=body.date_year,
            date_month=body.date_month, date_day=body.date_day, timezone_id=body.timezone_id,
            confidence=1, source_kind="manual", source_id=source_id, source_revision="1",
            source_hash=source_hash, celebration_policy=body.celebration_policy,
        )
        return important_dates.confirm(
            item["id"], expected_revision=item["revision"], date_year=body.date_year,
            date_month=body.date_month, date_day=body.date_day,
        )
    except important_dates.ImportantDateError as exc:
        raise HTTPException(400, detail=exc.code) from exc


@app.patch("/api/life/dates/{item_id}")
def update_life_date(item_id: str, body: LifeDateUpdateIn) -> dict:
    try:
        return important_dates.revise(
            item_id, expected_revision=body.expected_revision, label=body.label,
            celebration_policy=body.celebration_policy,
        )
    except important_dates.ImportantDateError as exc:
        raise HTTPException(409, detail=exc.code) from exc


@app.delete("/api/life/dates/{item_id}")
def delete_life_date(item_id: str, expected_revision: int) -> dict:
    try:
        return important_dates.revoke(item_id, expected_revision=expected_revision)
    except important_dates.ImportantDateError as exc:
        raise HTTPException(409, detail=exc.code) from exc


@app.get("/api/life/goals")
def list_life_goals(include_revoked: bool = False, limit: int = 100) -> dict:
    return {"items": personal_goals.list_goals(include_revoked=include_revoked, limit=limit)}


@app.post("/api/life/goals")
def create_life_goal(body: LifeGoalCreateIn) -> dict:
    source_id = db.new_id()
    source_hash = hashlib.sha256(body.title.encode()).hexdigest()
    try:
        item = personal_goals.create_candidate(
            title=body.title, priority=body.priority, confidence=1,
            source_kind="user_explicit", source_id=source_id, source_revision="1",
            source_hash=source_hash, explicit_confirmation=True,
        )
        return personal_goals.transition(
            item["id"], expected_revision=item["revision"], to_status="active",
            reason_code="user_created",
        )
    except personal_goals.GoalError as exc:
        raise HTTPException(400, detail=exc.code) from exc


@app.patch("/api/life/goals/{item_id}")
def update_life_goal(item_id: str, body: LifeGoalUpdateIn) -> dict:
    revision = body.expected_revision
    try:
        item = personal_goals.get_goal(item_id)
        if not item or item["revision"] != revision:
            raise personal_goals.GoalError("revision_conflict", "goal changed")
        if body.title is not None and body.title != item["title"]:
            item = personal_goals.rename(item_id, expected_revision=revision, title=body.title)
            revision = item["revision"]
        if body.status is not None and body.status != item["status"]:
            item = personal_goals.transition(
                item_id, expected_revision=revision, to_status=body.status,
                reason_code="user_changed_status",
            )
        return item
    except personal_goals.GoalError as exc:
        raise HTTPException(409, detail=exc.code) from exc


@app.delete("/api/life/goals/{item_id}")
def delete_life_goal(item_id: str, expected_revision: int) -> dict:
    try:
        return personal_goals.transition(
            item_id, expected_revision=expected_revision, to_status="revoked",
            reason_code="user_deleted",
        )
    except personal_goals.GoalError as exc:
        raise HTTPException(409, detail=exc.code) from exc


@app.post("/api/life/rebuild")
def rebuild_life_views() -> dict:
    affected_diaries = diary.rebuild_invalid_sources()
    indexed = self_timeline.refresh()
    return {"affected_diaries": affected_diaries, "timeline_entries": indexed}


@app.get("/api/life/export")
def export_life_data() -> dict:
    return {
        "export_version": "life-export-v2", "exported_at": db.now(),
        "settings": get_life_settings(), "state": get_life_state(),
        "events": life_events.list_events(include_revoked=True, limit=500),
        "diary": diary.list_entries(include_revoked=True, limit=500),
        "important_dates": important_dates.list_dates(include_revoked=True, limit=500),
        "personal_goals": personal_goals.list_goals(include_revoked=True, limit=500),
        "short_memo": short_memo.export_data(),
    }


@app.get("/api/life/diagnostics")
def get_life_diagnostics() -> dict:
    state = life_runtime.get_state()
    conn = db.connect()
    try:
        schema_row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "life_events", "diary_entries", "important_dates", "personal_goals",
                "short_memos", "short_memo_events",
            )
        }
        sources = [dict(row) for row in conn.execute(
            "SELECT source_type,source_id,source_revision,source_status FROM self_timeline_entries "
            "ORDER BY indexed_at DESC,id DESC LIMIT 20"
        ).fetchall()]
    finally:
        conn.close()
    return {
        "schema_version": schema_row["value"] if schema_row else "unknown",
        "state_revision": state.revision if state else None,
        "state_algorithm": state.algorithm_version if state else life_runtime.ALGORITHM_VERSION,
        "anomaly_code": state.anomaly_code if state else None,
        "counts": counts, "sources": sources, "short_memo": short_memo.diagnostics(),
    }


class CognitionFeedbackIn(BaseModel):
    decision_kind: str = Field(min_length=1, max_length=80)
    feedback_kind: str = Field(min_length=1, max_length=40)
    source_run_id: str | None = Field(default=None, max_length=80)
    request_nonce: str = Field(min_length=1, max_length=128)


class CognitionRollbackIn(BaseModel):
    request_nonce: str = Field(min_length=1, max_length=128)


@app.get("/api/cognition/calibration")
def cognition_calibration_diagnostics(limit: int = 100) -> dict:
    return cognition_calibration.diagnostics(limit)


@app.post("/api/cognition/feedback")
def submit_cognition_feedback(body: CognitionFeedbackIn) -> dict:
    try:
        return cognition_calibration.submit_feedback(
            decision_kind=body.decision_kind, feedback_kind=body.feedback_kind,
            source_run_id=body.source_run_id, request_nonce=body.request_nonce,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class CognitionSettingsIn(BaseModel):
    enabled: bool | None = None
    diagnostics_visible: bool | None = None
    decision_modes: dict[str, str] | None = None
    model_bindings: dict[str, dict[str, str] | None] | None = None


@app.get("/api/cognition/settings")
def read_cognition_settings() -> dict:
    return cognition_settings.get_settings()


@app.put("/api/cognition/settings")
def write_cognition_settings(body: CognitionSettingsIn) -> dict:
    try:
        return cognition_settings.update_settings(
            enabled=body.enabled, diagnostics_visible=body.diagnostics_visible,
            decision_modes=body.decision_modes, model_bindings=body.model_bindings,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/cognition/settings/rollback")
def rollback_cognition_settings() -> dict:
    return cognition_settings.rollback_to_legacy()


@app.get("/api/cognition/diagnostics/v2")
def cognition_diagnostics_v2(decision_kind: str | None = None, limit: int = 100) -> dict:
    return cognition_diagnostic_views.read(decision_kind=decision_kind, limit=limit)


@app.post("/api/cognition/calibration/{decision_kind}/rollback")
def rollback_cognition_profile(decision_kind: str, body: CognitionRollbackIn) -> dict:
    try:
        return cognition_calibration.rollback_profile(
            decision_kind=decision_kind, request_nonce=body.request_nonce,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.delete("/api/proactive/data")
def clear_proactive_data() -> dict:
    return proactive_feedback.clear_pending_and_history()


@app.post("/api/proactive/settings/reset")
def reset_proactive_settings() -> dict:
    values, revision = proactive_settings.reset_public_settings()
    return {"settings": values, "revision": revision}


@app.post("/api/proactive/runtime/system-resume")
def notify_proactive_system_resume() -> dict:
    return {"guard_until": proactive_settings.mark_system_resume()}


@app.post("/api/companion-state/reset")
def reset_companion_state() -> dict:
    return companion_state.reset_state()


class CompanionTickIn(BaseModel):
    minutes: float = Field(gt=0, le=7 * 24 * 60)


@app.post("/api/companion-state/tick")
def tick_companion_state(body: CompanionTickIn) -> dict:
    return companion_state.tick(body.minutes)


@app.get("/api/companion-state/events")
def get_companion_state_events(limit: int = 50) -> list[dict]:
    return companion_state.list_events(limit)


@app.get("/api/companion-state/observer-runs")
def get_affect_observer_runs(limit: int = 50) -> list[dict]:
    return affect_observer_service.list_runs(limit)


class ObserverModelIn(BaseModel):
    mode: str
    provider_id: str | None = None
    model: str | None = None


@app.get("/api/companion-state/observer-model")
def get_affect_observer_model() -> dict:
    return affect_observer_service.get_model_config()


@app.put("/api/companion-state/observer-model")
def set_affect_observer_model(body: ObserverModelIn) -> dict:
    try:
        return affect_observer_service.set_model_config(body.mode, body.provider_id, body.model)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# ---------------------------------------------------------------- 记忆
@app.get("/api/memory-observer/runs")
def get_memory_observer_runs(limit: int = 50) -> list[dict]:
    return memory_observer_service.list_runs(limit)


@app.get("/api/memory-observer/runs/{run_id}/result")
def get_memory_observer_run_result(run_id: str) -> dict:
    result = memory_observer_service.get_run_result(run_id)
    if not result:
        raise HTTPException(404, "记忆观察记录不存在")
    return result


@app.get("/api/memory-observer/model")
def get_memory_observer_model() -> dict:
    return memory_observer_service.get_model_config()


@app.put("/api/memory-observer/model")
def set_memory_observer_model(body: ObserverModelIn) -> dict:
    try:
        return memory_observer_service.set_model_config(
            body.mode, body.provider_id, body.model
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/knowledge/documents")
def get_knowledge_documents(collection_id: Optional[str] = None, status: Optional[str] = None,
                            query: Optional[str] = None) -> list[dict]:
    allowed_statuses = {
        "staged", "queued", "parsing", "indexed", "failed", "cancelled",
        "delete_pending", "delete_failed",
    }
    if status and status not in allowed_statuses:
        raise HTTPException(400, "知识文档状态筛选无效")
    if query and len(query) > 120:
        raise HTTPException(400, "文档搜索最多 120 字符")
    return [
        knowledge.public_document(document)
        for document in knowledge.list_documents(
            collection_id=collection_id, status=status, query=(query or "").strip() or None,
        )
    ]


@app.get("/api/knowledge/collections")
def get_knowledge_collections() -> list[dict]:
    return knowledge_management.list_collections()


class KnowledgeCollectionPolicyIn(BaseModel):
    default_transmission_policy: str
    apply_existing: bool = False


@app.patch("/api/knowledge/collections/{collection_id}/transmission-policy")
def patch_knowledge_collection_policy(
    collection_id: str, body: KnowledgeCollectionPolicyIn,
) -> dict:
    try:
        result = knowledge_policy.update_collection_policy(
            collection_id, body.default_transmission_policy,
            apply_existing=body.apply_existing,
        )
    except knowledge_policy.KnowledgePolicyError as error:
        status = 409 if error.code in {
            "collection_contains_deleting_document", "sensitive_remote_forbidden",
        } else 400
        raise HTTPException(status, {"code": error.code, "message": str(error)}) from error
    if not result:
        raise HTTPException(404, "知识库集合不存在")
    return result


class KnowledgeTagsIn(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=10)


@app.patch("/api/knowledge/documents/{document_id}/tags")
def patch_knowledge_document_tags(document_id: str, body: KnowledgeTagsIn) -> dict:
    try:
        result = knowledge_management.update_tags(document_id, body.tags)
    except knowledge.KnowledgeImportError as error:
        raise HTTPException(409 if error.code == "document_deleting" else 400, str(error)) from error
    if not result:
        raise HTTPException(404, "知识文档不存在")
    return result


class KnowledgeTransmissionPolicyIn(BaseModel):
    transmission_policy: str


@app.patch("/api/knowledge/documents/{document_id}/transmission-policy")
def patch_knowledge_document_transmission_policy(
    document_id: str, body: KnowledgeTransmissionPolicyIn,
) -> dict:
    try:
        result = knowledge_policy.update_document_policy(document_id, body.transmission_policy)
    except knowledge_policy.KnowledgePolicyError as error:
        status = 409 if error.code == "document_deleting" else 400
        raise HTTPException(status, str(error)) from error
    if not result:
        raise HTTPException(404, "知识文档不存在")
    return result


@app.get("/api/knowledge/documents/{document_id}/policy-events")
def get_knowledge_document_policy_events(document_id: str, limit: int = 50) -> list[dict]:
    if limit < 1 or limit > 100:
        raise HTTPException(400, "策略事件数量须为 1 到 100")
    result = knowledge_policy.list_document_policy_events(document_id, limit)
    if result is None:
        raise HTTPException(404, "知识文档不存在")
    return result


@app.post("/api/knowledge/documents/{document_id}/reindex", status_code=202)
def reindex_knowledge_document(document_id: str) -> dict:
    try:
        result = knowledge_management.enqueue_reindex(document_id)
    except knowledge.KnowledgeImportError as error:
        raise HTTPException(409, str(error)) from error
    if not result:
        raise HTTPException(404, "知识文档不存在")
    return _public_knowledge_run(result)


class KnowledgeArchiveIn(BaseModel):
    archived: bool


@app.patch("/api/knowledge/documents/{document_id}/archive")
def archive_knowledge_document(document_id: str, body: KnowledgeArchiveIn) -> dict:
    try:
        result = knowledge_management.set_archived(document_id, archived=body.archived)
    except knowledge.KnowledgeImportError as error:
        raise HTTPException(409, detail={"code": error.code, "message": str(error)}) from error
    if not result:
        raise HTTPException(404, "knowledge document does not exist")
    return result


@app.get("/api/knowledge/documents/{document_id}/impact-preview")
def preview_knowledge_document_impact(document_id: str, action: str) -> dict:
    try:
        result = knowledge_management.impact_preview(document_id, action=action)
    except knowledge.KnowledgeImportError as error:
        raise HTTPException(400, detail={"code": error.code, "message": str(error)}) from error
    if not result:
        raise HTTPException(404, "knowledge document does not exist")
    return result


@app.delete("/api/knowledge/documents/{document_id}", status_code=202)
def delete_knowledge_document(document_id: str) -> dict:
    try:
        result = knowledge_management.enqueue_delete(document_id)
    except knowledge.KnowledgeImportError as error:
        raise HTTPException(409, str(error)) from error
    if not result:
        raise HTTPException(404, "知识文档不存在")
    return _public_deletion_run(result)


@app.get("/api/knowledge/deletion-runs/{run_id}")
def get_knowledge_deletion_run(run_id: str) -> dict:
    result = knowledge_management.get_deletion_run(run_id)
    if not result:
        raise HTTPException(404, "知识删除任务不存在")
    return _public_deletion_run(result)


@app.post("/api/knowledge/deletion-runs/{run_id}/retry", status_code=202)
def retry_knowledge_deletion(run_id: str) -> dict:
    try:
        result = knowledge_management.retry_delete(run_id)
    except knowledge.KnowledgeImportError as error:
        raise HTTPException(409, str(error)) from error
    if not result:
        raise HTTPException(404, "知识删除任务不存在")
    return _public_deletion_run(result)


@app.get("/api/knowledge/retrievals")
def get_knowledge_retrievals(session_id: Optional[str] = None,
                             limit: int = 30) -> list[dict]:
    if limit < 1 or limit > 100:
        raise HTTPException(400, "审计记录数量须为 1 到 100")
    rows = knowledge_management.list_retrieval_audits(session_id=session_id, limit=limit)
    for row in rows:
        row["session_available"] = bool(row["session_available"])
        row["query_fingerprint"] = row.pop("query_sha256")[:12]
    return rows


@app.get("/api/knowledge/audit-lifecycle")
def get_knowledge_audit_lifecycle() -> dict:
    return knowledge_cleanup.stats()


@app.get("/api/knowledge/export-manifest")
def get_knowledge_export_manifest() -> dict:
    return knowledge_management.export_manifest()


class KnowledgeClearAllIn(BaseModel):
    confirmation: str


@app.post("/api/knowledge/clear-all", status_code=202)
def clear_all_knowledge(body: KnowledgeClearAllIn) -> dict:
    if body.confirmation != "CLEAR_ALL_KNOWLEDGE":
        raise HTTPException(400, "完整清除确认文本无效")
    return knowledge_management.clear_all()


class KnowledgeRecallSettingsIn(BaseModel):
    mode: Optional[str] = Field(default=None, pattern=r"^(off|explicit|smart)$")
    shadow_enabled: Optional[bool] = None


@app.get("/api/knowledge/recall/settings")
def get_knowledge_recall_settings() -> dict:
    return knowledge_recall.settings()


@app.patch("/api/knowledge/recall/settings")
def patch_knowledge_recall_settings(body: KnowledgeRecallSettingsIn) -> dict:
    try:
        return knowledge_recall.update_settings(
            mode=body.mode, shadow_enabled=body.shadow_enabled,
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/api/knowledge/recall-decisions")
def get_knowledge_recall_decisions(
    session_id: Optional[str] = None, limit: int = 30,
) -> list[dict]:
    if limit < 1 or limit > 100:
        raise HTTPException(400, "召回判断数量须为 1 到 100")
    return knowledge_recall.list_decisions(session_id=session_id, limit=limit)


@app.get("/api/knowledge/recall-decisions/stats")
def get_knowledge_recall_decision_stats(session_id: Optional[str] = None) -> dict:
    return knowledge_recall.decision_stats(session_id=session_id)


class KnowledgeRecallPreflightIn(BaseModel):
    session_id: str
    request_nonce: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    content: str = Field(default="", max_length=8192)
    attachment_ids: list[str] = Field(default_factory=list)


@app.post("/api/knowledge/recall/preflight")
def preflight_knowledge_recall(body: KnowledgeRecallPreflightIn) -> dict:
    provider, model = _current_model()
    recall_mode = knowledge_recall.settings()["mode"]
    # 纯附件无文字消息：跳过知识召回，不会触发远传授权询问
    if not body.content.strip() and body.attachment_ids:
        return {
            "id": None,
            "status": "not_needed",
            "reason": "attachment_only",
            "recall_mode": recall_mode,
            "provider": {
                "id": (provider or {}).get("id"),
                "model": model,
                "location": (provider or {}).get("execution_location") or "unknown",
                "location_revision": max(1, int((provider or {}).get("location_revision") or 1)),
            },
            "documents": [],
            "document_count": 0,
            "chunk_count": 0,
            "token_range": {"min": 0, "max": 0},
            "single_use": False,
            "can_allow_once": False,
            "can_always_allow": False,
            "expires_at": None,
        }
    if not body.content.strip() and not body.attachment_ids:
        raise HTTPException(400, "content 和 attachment_ids 至少有一个非空")
    try:
        knowledge_grants.expire_due(limit=50)
        return knowledge_grants.preflight(
            session_id=body.session_id, request_nonce=body.request_nonce,
            content=body.content, provider=provider, model=model, recall_mode=recall_mode,
        )
    except knowledge_grants.GrantError as error:
        raise HTTPException(
            error.status_code, {"code": error.code, "message": str(error)},
        ) from error


class KnowledgeGrantResolveIn(BaseModel):
    grant_id: str
    action: str = Field(pattern=r"^(allow_once|always_allow|local_only)$")
    session_id: str
    request_nonce: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    content: str = Field(min_length=1)


@app.post("/api/knowledge/transmission-grants")
def resolve_knowledge_transmission_grant(body: KnowledgeGrantResolveIn) -> dict:
    provider, model = _current_model()
    recall_mode = knowledge_recall.settings()["mode"]
    try:
        return knowledge_grants.resolve(
            grant_id=body.grant_id, action=body.action, session_id=body.session_id,
            request_nonce=body.request_nonce, content=body.content,
            provider=provider, model=model, recall_mode=recall_mode,
        )
    except knowledge_grants.GrantError as error:
        raise HTTPException(
            error.status_code, {"code": error.code, "message": str(error)},
        ) from error


@app.post("/api/knowledge/transmission-grants/{grant_id}/deny")
def deny_knowledge_transmission_grant(grant_id: str) -> dict:
    try:
        return knowledge_grants.deny(grant_id)
    except knowledge_grants.GrantError as error:
        raise HTTPException(
            error.status_code, {"code": error.code, "message": str(error)},
        ) from error


@app.get("/api/knowledge/transmission-grants/{grant_id}")
def get_knowledge_transmission_grant(grant_id: str) -> dict:
    result = knowledge_grants.get_grant(grant_id)
    if not result:
        raise HTTPException(404, "授权记录不存在")
    return result


@app.get("/api/knowledge/recall-decisions/{decision_id}")
def get_knowledge_recall_decision(decision_id: str) -> dict:
    result = knowledge_recall.get_decision(decision_id)
    if not result:
        raise HTTPException(404, "召回判断不存在")
    return result


class KnowledgeSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=256)
    collection_id: Optional[str] = None
    document_ids: list[str] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=6, ge=1, le=12)
    context_window: int = Field(default=0, ge=0, le=1)
    max_chars: int = Field(default=4000, ge=256, le=8000)
    mode: str = Field(default="auto", pattern="^(auto|fts|vector)$")


@app.post("/api/knowledge/search")
def search_knowledge(body: KnowledgeSearchIn) -> dict:
    try:
        return knowledge_search.hybrid_search(
            body.query, collection_id=body.collection_id, document_ids=body.document_ids,
            tags=body.tags,
            limit=body.limit, context_window=body.context_window, max_chars=body.max_chars,
            mode=body.mode,
        )
    except knowledge_search.SearchError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/api/knowledge/embedding/status")
def get_knowledge_embedding_status() -> dict:
    return knowledge_embeddings.availability()


@app.post("/api/knowledge/documents/{document_id}/embedding", status_code=202)
def build_knowledge_document_embedding(document_id: str) -> dict:
    status = knowledge_embeddings.availability()
    if not status["available"]:
        raise HTTPException(409, "本地 BGE-M3 模型或运行依赖不可用，FTS 仍可正常检索")
    run = knowledge_embeddings.enqueue(document_id)
    if not run:
        conn = db.connect()
        try:
            document = conn.execute("SELECT status FROM knowledge_documents WHERE id=?", (document_id,)).fetchone()
        finally:
            conn.close()
        if not document:
            raise HTTPException(404, "知识文档不存在")
        raise HTTPException(409, "文档尚未完成本地词法索引或向量任务已存在")
    knowledge_worker.wake_worker()
    return {key: run.get(key) for key in (
        "id", "document_id", "provider_id", "model", "embedding_version", "status",
        "attempt_count", "max_attempts", "vector_count", "error_code", "created_at", "updated_at",
    )}


@app.post("/api/knowledge/documents/import")
async def import_knowledge_document(request: Request) -> dict:
    declared_length = request.headers.get("content-length")
    if declared_length:
        try:
            if int(declared_length) > knowledge.MAX_FILE_BYTES:
                raise HTTPException(413, "文件超过 10 MiB 限制")
        except ValueError as error:
            raise HTTPException(400, "Content-Length 无效") from error
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > knowledge.MAX_FILE_BYTES:
            raise HTTPException(413, "文件超过 10 MiB 限制")
    filename = unquote(request.headers.get("X-Xiadie-Filename", ""))
    collection_id = request.headers.get("X-Xiadie-Collection", "default")
    sensitivity = request.headers.get("X-Xiadie-Sensitivity", "normal")
    try:
        return knowledge.public_import_result(
            knowledge.import_file(
                filename, request.headers.get("content-type", "application/octet-stream"),
                bytes(body), collection_id=collection_id, sensitivity=sensitivity,
            )
        )
    except knowledge.KnowledgeImportError as error:
        # 统一返回 {code, message} 结构化格式，前端 ApiError 可拿到 code 做分类 toast
        status = 413 if error.code in {"file_too_large", "decoded_text_too_large"} else (
            415 if error.code in {
                "file_type_unsupported", "mime_type_mismatch", "encoding_unsupported",
                "binary_content_rejected",
            } else 409 if error.code in {
                "document_quota_exceeded", "storage_quota_exceeded",
            } else 400
        )
        raise HTTPException(status, {"code": error.code, "message": str(error)}) from error
    except OSError as error:
        raise HTTPException(507, "无法把文件安全保存到本地知识库") from error


@app.post("/api/chat/attachments")
async def upload_chat_attachment(request: Request) -> dict:
    """聊天框附件上传：文本本地解析；图片验证后仅存放到本轮临时区。

    文本附件仅用于本轮对话阅读（通过 attachment_block 直接注入 system prompt），
    不存入知识库。存入知识库会导致 transmission_policy=ask_each_time，
    知识库检索命中附件时触发远传授权 409，与"本轮直接阅读"的意图冲突。
    用户如需持久化文本，可从知识库页面单独上传。图片原始字节不进入知识库或消息历史。
    """
    import os as _os
    import secrets as _secrets
    filename = unquote(request.headers.get("X-Xiadie-Filename", ""))
    if not filename:
        raise HTTPException(400, "缺少文件名")
    ext = _os.path.splitext(filename)[1].lower()
    declared_mime = request.headers.get("content-type", "application/octet-stream")
    normalized_mime = declared_mime.split(";", 1)[0].strip().lower()
    is_image = ext in {".png", ".jpg", ".jpeg"} or normalized_mime.startswith("image/")
    max_bytes = image_attachments.MAX_IMAGE_BYTES if is_image else knowledge.MAX_FILE_BYTES
    if is_image:
        if not cie_settings.is_enabled():
            raise HTTPException(409, {"code": "cie_disabled", "message": "CIE 图片能力尚未启用"})
        provider, model = _current_model()
        capability_status = vision_capabilities.status(provider, model)
        if capability_status["status"] != "supported":
            raise HTTPException(409, {
                "code": "vision_capability_unavailable",
                "message": "当前模型尚未通过真实图片能力探测，不能上传到本轮对话",
                "capability": capability_status,
            })
    declared_length = request.headers.get("content-length")
    if declared_length:
        try:
            if int(declared_length) > max_bytes:
                raise HTTPException(413, "图片超过 5 MiB 限制" if is_image else "文件超过 10 MiB 限制")
        except ValueError as error:
            raise HTTPException(400, "Content-Length 无效") from error
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise HTTPException(413, "图片超过 5 MiB 限制" if is_image else "文件超过 10 MiB 限制")
    attachment_id = _secrets.token_hex(8)
    if is_image:
        try:
            metadata = image_attachments.inspect_image(bytes(body), declared_mime)
        except image_attachments.ImageAttachmentError as error:
            status = 415 if error.code in {"image_mime_mismatch", "image_format_unsupported"} else 413
            raise HTTPException(status, {"code": error.code, "message": str(error)}) from error
        # 仅在本次图片已通过接纳检查后触发轻量 GC；拒绝路径保持无副作用。
        image_attachments.cleanup_expired()
        storage_name = image_attachments.save(attachment_id, bytes(body))
        expires_at = db.now() + image_attachments.TTL_SECONDS
        conn = db.connect()
        try:
            conn.execute(
                "INSERT INTO message_attachments(id,message_id,filename,mime_type,content_text,"
                "content_sha256,char_count,created_at,attachment_kind,storage_path,byte_count,"
                "pixel_width,pixel_height,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    attachment_id, None, filename, metadata["mime_type"], "",
                    metadata["content_sha256"], 0, db.now(), "image", storage_name,
                    metadata["byte_count"], metadata["pixel_width"],
                    metadata["pixel_height"], expires_at,
                ),
            )
            conn.commit()

        except Exception:
            image_attachments.remove(storage_name)
            raise
        finally:
            conn.close()
        return {
            "id": attachment_id,
            "filename": filename,
            "mime_type": metadata["mime_type"],
            "attachment_kind": "image",
            "char_count": 0,
            "byte_count": metadata["byte_count"],
            "pixel_width": metadata["pixel_width"],
            "pixel_height": metadata["pixel_height"],
            "expires_at": expires_at,
            "content_preview": f"图片 {metadata['pixel_width']}×{metadata['pixel_height']}",
            "vision_capability": capability_status,
        }
    # 同步解析文件提取纯文本
    try:
        result = knowledge_parser.parse(bytes(body), extension=ext)
        content_text = result["normalized_text"]
        char_count = result["char_count"]
    except knowledge_parser.ParserError as error:
        # 统一返回 {code, message} 结构化格式，与 import_knowledge_document 对齐
        status = 415 if error.code in {
            "parser_unsupported", "encoding_unsupported",
        } else 400
        raise HTTPException(status, {"code": error.code, "message": str(error)}) from error
    content_sha256 = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
    mime_type = declared_mime
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO message_attachments(id, message_id, filename, mime_type,"
            " content_text, content_sha256, char_count, created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (attachment_id, None, filename, mime_type,
             content_text, content_sha256, char_count, db.now()),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "id": attachment_id,
        "filename": filename,
        "mime_type": mime_type,
        "attachment_kind": "text",
        "char_count": char_count,
        "byte_count": len(body),
        "content_preview": content_text[:200],
    }


@app.get("/api/knowledge/import-runs/{run_id}")
def get_knowledge_import_run(run_id: str) -> dict:
    run = knowledge_worker.get_run(run_id)
    if not run:
        raise HTTPException(404, "知识导入任务不存在")
    return _public_knowledge_run(run)


@app.post("/api/knowledge/import-runs/{run_id}/cancel")
def cancel_knowledge_import_run(run_id: str) -> dict:
    run = knowledge_worker.cancel(run_id)
    if not run:
        raise HTTPException(404, "知识导入任务不存在")
    return _public_knowledge_run(run)


def _public_knowledge_run(run: dict) -> dict:
    allowed = {
        "id", "document_id", "trigger", "status", "current_stage", "progress",
        "attempt_count", "max_attempts", "error_code", "next_attempt_at", "started_at",
        "finished_at", "created_at", "updated_at", "events",
    }
    return {key: value for key, value in run.items() if key in allowed}


def _public_deletion_run(run: dict) -> dict:
    allowed = {
        "id", "document_id", "status", "attempt_count", "error_code", "started_at",
        "finished_at", "created_at", "updated_at", "events",
    }
    return {key: value for key, value in run.items() if key in allowed}


class MemoryIn(BaseModel):
    layer: str = "L2"
    content: str
    tags: str = ""


@app.get("/api/memories")
def get_memories(layer: Optional[str] = None) -> list[dict]:
    return memory.list_memories(layer)


@app.post("/api/memories")
def add_memory(body: MemoryIn) -> dict:
    if not body.content.strip():
        raise HTTPException(400, "记忆内容不能为空")
    return memory.create_memory(body.layer, body.content, body.tags, source="manual")


@app.patch("/api/memories/{mid}")
def patch_memory(mid: str, body: dict) -> dict:
    if body.get("layer") is not None and body["layer"] not in ("L0", "L1", "L2"):
        raise HTTPException(400, "非法的记忆层级")
    if body.get("status") is not None:
        raise HTTPException(400, "记忆状态不能普通编辑；恢复请使用生命周期接口")
    m = memory.update_memory(mid, **body)
    if not m:
        raise HTTPException(404, "记忆不存在")
    return m


class FragmentLifecycleIn(BaseModel):
    target_status: str
    reason: str = Field(default="", max_length=240)
    expected_revision: int | None = Field(default=None, ge=0)


@app.post("/api/memories/{mid}/lifecycle")
def transition_memory_lifecycle(mid: str, body: FragmentLifecycleIn) -> dict:
    if body.target_status != "active":
        raise HTTPException(400, "用户接口只允许恢复记忆；冷却和冻结由 Archivist 评估")
    try:
        return archivist.reactivate_fragment(
            mid, trigger="user", reason=body.reason,
            expected_revision=body.expected_revision,
        )
    except archivist.ArchivistLifecycleError as exc:
        status = 404 if exc.code == "fragment_missing" else (
            409 if exc.code == "revision_conflict" else 400
        )
        raise HTTPException(status, str(exc)) from exc


@app.get("/api/memories/{mid}/lifecycle")
def get_memory_lifecycle(mid: str) -> dict:
    fragment = memory.get_memory(mid)
    if not fragment or fragment["status"] == "tombstone":
        raise HTTPException(404, "记忆不存在")
    evaluations = archivist.evaluate_fragments([mid])
    return {
        "fragment": fragment,
        "evaluation": evaluations[0] if evaluations else None,
        "events": archivist.list_lifecycle_events(mid),
        "relations": memory_conflicts.relations_for_fragment(mid),
    }


class MemoryRelationStatusIn(BaseModel):
    status: str
    reason: str = Field(min_length=1, max_length=240)


@app.get("/api/memory-relations")
def get_memory_relations(status: Optional[str] = "active", limit: int = 100) -> list[dict]:
    try:
        return memory_conflicts.list_relations(status=status or None, limit=limit)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/api/memory-relations/scan")
def scan_memory_relations(limit: int = 50) -> dict:
    return memory_conflicts.scan_conflicts(limit=limit)


@app.post("/api/memory-relations/{relation_id}/status")
def set_memory_relation_status(relation_id: str, body: MemoryRelationStatusIn) -> dict:
    try:
        result = memory_conflicts.set_status(relation_id, body.status, reason=body.reason)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not result:
        raise HTTPException(404, "冲突关系不存在")
    return result


class ArchivistRunIn(BaseModel):
    trigger: str = "manual"
    request_key: Optional[str] = Field(default=None, max_length=120)
    scan_budget: int = Field(default=50, ge=1, le=200)
    transition_budget: int = Field(default=10, ge=0, le=100)
    runtime_budget_ms: int = Field(default=2000, ge=100, le=30000)
    model_call_budget: int = Field(default=0, ge=0, le=20)


@app.post("/api/archivist/runs")
def enqueue_archivist_run(body: ArchivistRunIn) -> dict:
    try:
        return archivist_worker.enqueue(
            trigger=body.trigger, request_key=body.request_key,
            scan_budget=body.scan_budget, transition_budget=body.transition_budget,
            runtime_budget_ms=body.runtime_budget_ms,
            model_call_budget=body.model_call_budget,
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/api/archivist/runs")
def get_archivist_runs(limit: int = 50) -> list[dict]:
    return archivist_worker.list_runs(limit=limit)


@app.get("/api/archivist/runs/{run_id}")
def get_archivist_run(run_id: str) -> dict:
    run = archivist_worker.get_run(run_id)
    if not run:
        raise HTTPException(404, "Archivist 任务不存在")
    return run


@app.post("/api/archivist/runs/{run_id}/cancel")
def cancel_archivist_run(run_id: str) -> dict:
    try:
        run = archivist_worker.cancel(run_id)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    if not run:
        raise HTTPException(404, "Archivist 任务不存在")
    return run


class MemoryCorrectionIn(BaseModel):
    content: str = Field(min_length=1, max_length=400)
    note: str = Field(default="", max_length=240)


@app.post("/api/memories/{mid}/correct")
def correct_memory(mid: str, body: MemoryCorrectionIn) -> dict:
    if not body.content.strip():
        raise HTTPException(400, "纠正后的记忆内容不能为空")
    result = memory.correct_memory(mid, body.content, body.note)
    if not result:
        raise HTTPException(404, "记忆不存在")
    return result


@app.delete("/api/memories/{mid}")
def remove_memory(mid: str, privacy: bool = False) -> dict:
    if not memory.delete_memory(mid, privacy=privacy):
        raise HTTPException(404, "记忆不存在")
    return {"ok": True, "privacy_cleared": privacy}


class CandidateDecisionIn(BaseModel):
    content: Optional[str] = None
    layer: Optional[str] = None
    tags: Optional[str] = None
    note: str = ""


@app.get("/api/memory-candidates")
def get_memory_candidates(status: Optional[str] = "pending") -> list[dict]:
    if status is not None and status not in ("pending", "accepted", "rejected"):
        raise HTTPException(400, "非法的候选状态")
    return memory.list_candidates(status)


@app.get("/api/memory-candidates/{cid}")
def get_memory_candidate(cid: str) -> dict:
    candidate = memory.get_candidate(cid)
    if not candidate:
        raise HTTPException(404, "记忆候选不存在")
    return candidate


@app.post("/api/memory-candidates/{cid}/accept")
def accept_memory_candidate(cid: str, body: CandidateDecisionIn) -> dict:
    if body.layer is not None and body.layer not in ("L0", "L1", "L2"):
        raise HTTPException(400, "非法的记忆层级")
    if body.content is not None and not body.content.strip():
        raise HTTPException(400, "记忆内容不能为空")
    result = memory.accept_candidate(cid, body.content, body.layer, body.tags)
    if not result:
        raise HTTPException(409, "候选不存在或已处理")
    return result


@app.post("/api/memory-candidates/{cid}/reject")
def reject_memory_candidate(cid: str, body: CandidateDecisionIn) -> dict:
    result = memory.reject_candidate(cid, body.note)
    if not result:
        raise HTTPException(409, "候选不存在或已处理")
    return result


@app.get("/api/memory-events/{object_type}/{object_id}")
def get_memory_events(object_type: str, object_id: str) -> list[dict]:
    if object_type not in ("candidate", "fragment", "entity", "episode_candidate", "episode"):
        raise HTTPException(400, "非法的记忆对象类型")
    return memory.list_events(object_type, object_id)


@app.get("/api/memory/stats")
def get_memory_stats() -> dict:
    """返回记忆层级分布真实统计（有效记忆：enabled=1 AND status='active'）。

    供设置页"记忆层级分布"卡片展示，替代之前的硬编码占位值。
    """
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT layer, COUNT(*) AS n FROM memory_fragments "
            "WHERE enabled=1 AND status='active' "
            "GROUP BY layer ORDER BY layer"
        ).fetchall()
    finally:
        conn.close()
    counts = {row["layer"]: row["n"] for row in rows}
    return {
        "L0": counts.get("L0", 0),
        "L1": counts.get("L1", 0),
        "L2": counts.get("L2", 0),
    }


# ---------------------------------------------------------------- Episode
class EpisodeConsolidatorRunIn(BaseModel):
    trigger: str = "manual"
    request_key: Optional[str] = None


@app.post("/api/episode-consolidator/runs")
def enqueue_episode_consolidator_run(body: EpisodeConsolidatorRunIn) -> dict:
    try:
        return episode_consolidator.enqueue(trigger=body.trigger, request_key=body.request_key)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/api/episode-consolidator/runs")
def get_episode_consolidator_runs(limit: int = 50) -> list[dict]:
    return episode_consolidator.list_runs(limit=limit)


@app.get("/api/episode-summary/model")
def get_episode_summary_model() -> dict:
    return episode_summary_service.get_model_config()


@app.put("/api/episode-summary/model")
def set_episode_summary_model(body: ObserverModelIn) -> dict:
    try:
        return episode_summary_service.set_model_config(
            body.mode, body.provider_id, body.model
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/api/episode-consolidator/runs/{run_id}")
def get_episode_consolidator_run(run_id: str) -> dict:
    run = episode_consolidator.get_run(run_id)
    if not run:
        raise HTTPException(404, "Episode 整理任务不存在")
    return run


@app.post("/api/episode-consolidator/runs/{run_id}/cancel")
def cancel_episode_consolidator_run(run_id: str) -> dict:
    try:
        run = episode_consolidator.cancel(run_id)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    if not run:
        raise HTTPException(404, "Episode 整理任务不存在")
    return run


class EpisodeDecisionIn(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    significance: Optional[int] = None
    fragment_ids: Optional[list[str]] = None
    note: str = ""


class EpisodeCorrectionIn(BaseModel):
    title: Optional[str] = Field(default=None, max_length=80)
    summary: Optional[str] = Field(default=None, max_length=600)
    significance: Optional[int] = None
    note: str = Field(default="", max_length=240)
    expected_revision: int | None = Field(default=None, ge=0)


@app.get("/api/episode-candidates")
def get_episode_candidates(status: str = "pending") -> list[dict]:
    if status not in ("pending", "accepted", "rejected"):
        raise HTTPException(400, "非法的 Episode 候选状态")
    return episodes.list_candidates(status)


@app.get("/api/episode-group-candidates")
def get_episode_group_candidates(status: str = "observing") -> list[dict]:
    try:
        return episodes.list_group_candidates(status)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/api/episode-candidates/generate")
def generate_episode_candidates() -> dict:
    run = episode_consolidator.enqueue(trigger="manual")
    return {"queued": True, "run": run}


@app.post("/api/episode-candidates/{candidate_id}/accept")
def accept_episode_candidate(candidate_id: str, body: EpisodeDecisionIn) -> dict:
    if body.title is not None and not body.title.strip():
        raise HTTPException(400, "Episode 标题不能为空")
    if body.summary is not None and not body.summary.strip():
        raise HTTPException(400, "Episode 摘要不能为空")
    if body.significance is not None and not 1 <= body.significance <= 10:
        raise HTTPException(400, "重要度必须在 1 到 10 之间")
    try:
        episode = episodes.accept_candidate(
            candidate_id, body.title, body.summary, body.significance, body.fragment_ids
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not episode:
        raise HTTPException(409, "候选不存在或已处理")
    return episode


@app.post("/api/episode-candidates/{candidate_id}/reject")
def reject_episode_candidate(candidate_id: str, body: EpisodeDecisionIn) -> dict:
    candidate = episodes.reject_candidate(candidate_id, body.note)
    if not candidate:
        raise HTTPException(409, "候选不存在或已处理")
    return candidate


@app.get("/api/episodes")
def get_episodes(status: Optional[str] = None) -> list[dict]:
    try:
        return episodes.list_episodes(status=status)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/api/episodes/{episode_id}/correct")
def correct_episode(episode_id: str, body: EpisodeCorrectionIn) -> dict:
    try:
        episode = episodes.correct_episode(
            episode_id, title=body.title, summary=body.summary,
            significance=body.significance, note=body.note,
            expected_revision=body.expected_revision,
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not episode:
        raise HTTPException(404, "Episode 不存在")
    return episode


@app.get("/api/episodes/{episode_id}")
def get_episode(episode_id: str) -> dict:
    episode = episodes.get_episode(episode_id)
    if not episode or episode["status"] == "tombstone":
        raise HTTPException(404, "Episode 不存在")
    return episode


class EpisodeLifecycleIn(BaseModel):
    target_status: str
    reason: str = Field(default="", max_length=240)
    expected_revision: int = Field(ge=0)


@app.post("/api/episodes/{episode_id}/lifecycle")
def transition_episode_lifecycle(episode_id: str, body: EpisodeLifecycleIn) -> dict:
    try:
        return slow_lifecycle.transition_episode(
            episode_id, body.target_status, trigger="user", reason=body.reason,
            expected_revision=body.expected_revision,
        )
    except slow_lifecycle.SlowLifecycleError as error:
        status = 404 if error.code == "missing" else (
            409 if error.code == "revision_conflict" else 400
        )
        raise HTTPException(status, str(error)) from error


# ---------------------------------------------------------------- Saga
class SagaConsolidatorRunIn(BaseModel):
    trigger: str = "manual"
    request_key: Optional[str] = None


class SagaLifecycleIn(BaseModel):
    target_status: str
    reason: str = Field(min_length=1, max_length=240)
    evidence_episode_ids: list[str] = Field(default_factory=list, max_length=12)
    expected_revision: int = Field(ge=0)


class SagaCorrectionIn(BaseModel):
    title: Optional[str] = Field(default=None, max_length=80)
    summary: Optional[str] = Field(default=None, max_length=1200)
    theme: Optional[str] = Field(default=None, max_length=80)
    current_stage: Optional[str] = Field(default=None, max_length=300)
    significance: Optional[int] = Field(default=None, ge=1, le=10)
    note: str = Field(default="", max_length=240)
    expected_revision: int = Field(ge=0)


class SagaSourceCorrectionIn(BaseModel):
    episode_ids: list[str] = Field(min_length=2, max_length=12)
    note: str = Field(min_length=1, max_length=240)
    expected_revision: int = Field(ge=0)


@app.post("/api/saga-consolidator/runs")
def enqueue_saga_consolidator_run(body: SagaConsolidatorRunIn) -> dict:
    try:
        return saga_consolidator.enqueue(trigger=body.trigger, request_key=body.request_key)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/api/saga-consolidator/runs")
def get_saga_consolidator_runs(limit: int = 50) -> list[dict]:
    return saga_consolidator.list_runs(limit=limit)


@app.get("/api/saga-consolidator/runs/{run_id}")
def get_saga_consolidator_run(run_id: str) -> dict:
    run = saga_consolidator.get_run(run_id)
    if not run:
        raise HTTPException(404, "Saga 整理任务不存在")
    return run


@app.post("/api/saga-consolidator/runs/{run_id}/cancel")
def cancel_saga_consolidator_run(run_id: str) -> dict:
    try:
        run = saga_consolidator.cancel(run_id)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    if not run:
        raise HTTPException(404, "Saga 整理任务不存在")
    return run


@app.get("/api/saga-summary/model")
def get_saga_summary_model() -> dict:
    return saga_summary_service.get_model_config()


@app.put("/api/saga-summary/model")
def set_saga_summary_model(body: ObserverModelIn) -> dict:
    try:
        return saga_summary_service.set_model_config(body.mode, body.provider_id, body.model)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/api/sagas")
def get_sagas(status: Optional[str] = None, limit: int = 50, offset: int = 0) -> list[dict]:
    try:
        return saga_lifecycle.list_sagas(status, limit=limit, offset=offset)
    except saga_lifecycle.SagaLifecycleError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/api/sagas/{saga_id}")
def get_saga(saga_id: str) -> dict:
    saga = saga_lifecycle.get_saga(saga_id)
    if not saga:
        raise HTTPException(404, "Saga 不存在")
    return saga


@app.get("/api/sagas/{saga_id}/timeline")
def get_saga_timeline(saga_id: str) -> list[dict]:
    saga = saga_lifecycle.get_saga(saga_id)
    if not saga:
        raise HTTPException(404, "Saga 不存在")
    return saga["timeline"]


@app.get("/api/sagas/{saga_id}/sources")
def get_saga_sources(saga_id: str) -> list[dict]:
    saga = saga_lifecycle.get_saga(saga_id)
    if not saga:
        raise HTTPException(404, "Saga 不存在")
    return [item for item in saga["timeline"] if item["removed_at"] is None]


@app.get("/api/sagas/{saga_id}/events")
def get_saga_events(saga_id: str) -> list[dict]:
    if not saga_lifecycle.get_saga(saga_id):
        raise HTTPException(404, "Saga 不存在")
    return saga_lifecycle.list_events(saga_id)


@app.get("/api/sagas/{saga_id}/relationship-suggestions")
def get_saga_relationship_suggestions(saga_id: str) -> list[dict]:
    if not saga_lifecycle.get_saga(saga_id):
        raise HTTPException(404, "Saga 不存在")
    return saga_lifecycle.list_relationship_suggestions(saga_id)


@app.post("/api/sagas/{saga_id}/lifecycle")
def transition_saga(saga_id: str, body: SagaLifecycleIn) -> dict:
    try:
        saga = saga_lifecycle.transition(
            saga_id, body.target_status, reason=body.reason, source="user",
            evidence_episode_ids=body.evidence_episode_ids,
            expected_revision=body.expected_revision,
        )
    except saga_lifecycle.SagaLifecycleError as error:
        status = 409 if error.code in {
            "revision_conflict", "lifecycle_noop", "tombstone_terminal",
            "illegal_lifecycle_transition",
        } else 400
        raise HTTPException(status, str(error)) from error
    if not saga:
        raise HTTPException(404, "Saga 不存在")
    return saga


@app.post("/api/sagas/{saga_id}/correct")
def correct_saga(saga_id: str, body: SagaCorrectionIn) -> dict:
    try:
        saga = saga_lifecycle.correct_content(
            saga_id, title=body.title, summary=body.summary, theme=body.theme,
            current_stage=body.current_stage, significance=body.significance,
            note=body.note, expected_revision=body.expected_revision,
        )
    except saga_lifecycle.SagaLifecycleError as error:
        raise HTTPException(
            409 if error.code in {"revision_conflict", "tombstone_terminal"} else 400,
            str(error),
        ) from error
    if not saga:
        raise HTTPException(404, "Saga 不存在")
    return saga


@app.post("/api/sagas/{saga_id}/correct-sources")
def correct_saga_sources(saga_id: str, body: SagaSourceCorrectionIn) -> dict:
    try:
        saga = saga_lifecycle.correct_sources(
            saga_id, body.episode_ids, note=body.note,
            expected_revision=body.expected_revision,
        )
    except (saga_lifecycle.SagaLifecycleError, saga_summary.SagaSummaryValidationError) as error:
        code = getattr(error, "code", "source_correction_invalid")
        raise HTTPException(
            409 if code in {
                "revision_conflict", "source_cross_saga_conflict",
                "source_grouping_conflict", "source_correction_noop", "tombstone_terminal",
            } else 400,
            str(error),
        ) from error
    if not saga:
        raise HTTPException(404, "Saga 不存在")
    return saga


# ---------------------------------------------------------------- 记忆实体
class EntityIn(BaseModel):
    name: str
    entity_type: str = "concept"
    aliases: list[str] = Field(default_factory=list)
    summary: str = ""
    tags: list[str] = Field(default_factory=list)


class EntityLinkIn(BaseModel):
    fragment_id: str
    relation: str = "mentions"


class EntityMergeIn(BaseModel):
    source_entity_id: str


@app.get("/api/entities")
def get_entities() -> list[dict]:
    return entities.list_entities()


@app.post("/api/entities")
def add_entity(body: EntityIn) -> dict:
    if body.entity_type not in entities.ENTITY_TYPES:
        raise HTTPException(400, "非法的实体类型")
    try:
        entity = entities.create_entity(
            body.name, body.entity_type, body.aliases, body.summary, body.tags
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return entities.get_entity(entity["id"])


@app.get("/api/entities/{eid}")
def get_entity(eid: str) -> dict:
    entity = entities.get_entity(eid)
    if not entity or entity["status"] != "active":
        raise HTTPException(404, "实体不存在")
    return entity


@app.patch("/api/entities/{eid}")
def patch_entity(eid: str, body: dict) -> dict:
    if body.get("entity_type") is not None and body["entity_type"] not in entities.ENTITY_TYPES:
        raise HTTPException(400, "非法的实体类型")
    try:
        entity = entities.update_entity(eid, **body)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not entity:
        raise HTTPException(404, "实体不存在")
    return entity


@app.delete("/api/entities/{eid}")
def remove_entity(eid: str) -> dict:
    if not entities.archive_entity(eid):
        raise HTTPException(404, "实体不存在")
    return {"ok": True}


@app.post("/api/entities/{eid}/links")
def add_entity_link(eid: str, body: EntityLinkIn) -> dict:
    if not entities.link_fragment(eid, body.fragment_id, body.relation):
        raise HTTPException(404, "实体或记忆不存在")
    return get_entity(eid)


@app.delete("/api/entities/{eid}/links/{fragment_id}")
def remove_entity_link(eid: str, fragment_id: str) -> dict:
    if not entities.unlink_fragment(eid, fragment_id):
        raise HTTPException(404, "关联不存在")
    return get_entity(eid)


@app.post("/api/entities/{eid}/merge")
def merge_entity(eid: str, body: EntityMergeIn) -> dict:
    entity = entities.merge_entities(eid, body.source_entity_id)
    if not entity:
        raise HTTPException(409, "实体不存在、已处理或不能与自身合并")
    return entity


# ---------------------------------------------------------------- 任务
class TaskIn(BaseModel):
    title: str
    due_date: Optional[str] = None
    source_session_id: Optional[str] = None


@app.get("/api/tasks")
def list_tasks(today: bool = False) -> list[dict]:
    conn = db.connect()
    try:
        sql = "SELECT * FROM tasks WHERE status != 'archived'"
        if today:
            sql += " AND status IN ('todo','doing')"
        sql += " ORDER BY CASE status WHEN 'doing' THEN 0 WHEN 'todo' THEN 1 ELSE 2 END, updated_at DESC"
        rows = conn.execute(sql).fetchall()
        if today:
            rows = rows[:5]  # 今日任务只展示最重要的几条（需求 TASK-003）
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.post("/api/tasks")
def create_task(body: TaskIn) -> dict:
    if not body.title.strip():
        raise HTTPException(400, "任务标题不能为空")
    conn = db.connect()
    try:
        tid = db.new_id()
        t = db.now()
        src = "chat" if body.source_session_id else "manual"
        conn.execute(
            "INSERT INTO tasks(id, title, due_date, source, source_session_id, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (tid, body.title.strip(), body.due_date, src, body.source_session_id, t, t),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone())
    finally:
        conn.close()


@app.patch("/api/tasks/{tid}")
def update_task(tid: str, body: dict) -> dict:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        if body.get("status") is not None and body["status"] not in (
            "todo", "doing", "done", "archived"
        ):
            raise HTTPException(400, "非法的任务状态")
        for field in ("title", "status", "due_date"):
            if field in body and body[field] is not None:
                conn.execute(f"UPDATE tasks SET {field} = ?, updated_at = ? WHERE id = ?",
                             (body[field], db.now(), tid))
        conn.commit()
        return dict(conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone())
    finally:
        conn.close()


@app.delete("/api/tasks/{tid}")
def delete_task(tid: str) -> dict:
    conn = db.connect()
    try:
        conn.execute("DELETE FROM tasks WHERE id = ?", (tid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ---------------------------------------------------------------- 供应商 / 模型
@app.get("/api/providers")
def get_providers() -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute("SELECT * FROM providers ORDER BY sort").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["models"] = json.loads(d["models"] or "[]")
            d["enabled"] = bool(d["enabled"])
            # 密钥不明文回传（需求：设置不应明文显示完整密钥）
            d["has_key"] = bool(d.pop("api_key", ""))
            out.append(d)
        return out
    finally:
        conn.close()


@app.patch("/api/providers/{pid}")
def update_provider(pid: str, body: dict) -> dict:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM providers WHERE id = ?", (pid,)).fetchone()
        if not row:
            raise HTTPException(404, "供应商不存在")
        provider = dict(row)
        base_url = str(body["base_url"]) if body.get("base_url") is not None else provider["base_url"]
        try:
            location = knowledge_policy.provider_location_update(
                provider,
                base_url=base_url,
                requested_location=body.get("execution_location"),
                location_was_requested="execution_location" in body and body["execution_location"] is not None,
            )
        except knowledge_policy.KnowledgePolicyError as error:
            raise HTTPException(400, str(error)) from error
        conn.execute(
            "UPDATE providers SET base_url=?,execution_location=?,location_revision=?,"
            "location_confirmed_at=? WHERE id=?",
            (base_url, location["execution_location"], location["location_revision"],
             location["location_confirmed_at"], pid),
        )
        if body.get("api_key"):  # 只在传了非空 key 时更新，避免误清空
            conn.execute("UPDATE providers SET api_key = ? WHERE id = ?", (body["api_key"], pid))
            secret_store.get_store().store(f"provider:{pid}", body["api_key"])
            # 旧的 api_key 明文仍然在 providers 表中，迁移会用 _migrate_key_to_secret_store 清除
        if "models" in body and body["models"] is not None:
            conn.execute("UPDATE providers SET models = ? WHERE id = ?",
                         (json.dumps(body["models"], ensure_ascii=False), pid))
        if "enabled" in body and body["enabled"] is not None:
            conn.execute("UPDATE providers SET enabled = ? WHERE id = ?",
                         (1 if body["enabled"] else 0, pid))
        conn.commit()
        return get_providers_one(pid)
    finally:
        conn.close()


def get_providers_one(pid: str) -> dict:
    conn = db.connect()
    try:
        r = conn.execute("SELECT * FROM providers WHERE id = ?", (pid,)).fetchone()
        d = dict(r)
        d["models"] = json.loads(d["models"] or "[]")
        d["enabled"] = bool(d["enabled"])
        d["has_key"] = bool(d.pop("api_key", ""))
        return d
    finally:
        conn.close()


class TestIn(BaseModel):
    provider_id: str
    model: str


@app.post("/api/providers/test")
async def test_provider(body: TestIn) -> dict:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM providers WHERE id = ?", (body.provider_id,)).fetchone()
        if not row:
            raise HTTPException(404, "供应商不存在")
        prov = dict(row)
    finally:
        conn.close()
    if prov["id"] == "mock":
        return {"ok": True, "message": "演示模型始终可用"}
    return await llm.test_connection(prov["base_url"], prov["api_key"], body.model)


class DiscoverModelsIn(BaseModel):
    provider_id: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None


@app.post("/api/providers/discover-models")
async def discover_provider_models(body: DiscoverModelsIn) -> dict:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id, base_url, api_key FROM providers WHERE id = ?", (body.provider_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "供应商不存在")
        provider = dict(row)
    finally:
        conn.close()

    if provider["id"] == "mock":
        return {"ok": True, "models": ["xiadie-mock"], "message": "内置演示模型"}
    base_url = body.base_url.strip() if body.base_url is not None else provider["base_url"]
    # 输入框留空时沿用已保存密钥；临时输入的密钥不会出现在响应中。
    api_key = body.api_key.strip() if body.api_key else provider["api_key"]
    return await llm.discover_models(base_url, api_key)


@app.get("/api/current-model")
def current_model() -> dict:
    prov, model = _current_model()
    context_capability = _context_capability(prov, model)
    return {
        "provider_id": prov["id"] if prov else "mock",
        "provider_name": prov["name"] if prov else "内置演示",
        "model": model,
        "capabilities": _capabilities(prov, model) if prov else ["local"],
        "vision_capability": vision_capabilities.status(prov, model),
        "context_capability": context_capability.public_meta(),
    }


class SelectModelIn(BaseModel):
    provider_id: str
    model: str


@app.post("/api/current-model")
def set_current_model(body: SelectModelIn) -> dict:
    db.set_setting("current_model", json.dumps(
        {"provider_id": body.provider_id, "model": body.model}))
    return current_model()


def _capabilities(prov: dict, model: str) -> list[str]:
    """能力标签；图片能力只接受当前 Provider 位置版本的探测证据。"""
    caps = ["stream"]
    m = model.lower()
    if prov["id"] == "ollama":
        caps.append("local")
    if any(k in m for k in ("reason", "r1", "o1", "o3", "thinking")):
        caps.append("reasoning")
    if vision_capabilities.status(prov, model)["status"] == "supported":
        caps.append("vision")
    caps.append("tools")
    return caps


# ---------------------------------------------------------------- 工具日志 / 设置
@app.get("/api/tool-logs")
def tool_logs() -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute("SELECT * FROM tool_logs ORDER BY created_at DESC LIMIT 50").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/settings/{key}")
def read_setting(key: str) -> dict:
    if key == "memory_enabled":
        default = db.DEFAULT_MEMORY_ENABLED
    elif key == "knowledge_default_policy":
        default = "remote_allowed"
    elif key.startswith("proactive_"):
        spec = proactive_settings.SETTING_REGISTRY.get(key)
        if spec is None:
            raise HTTPException(404, "未知的主动陪伴设置项")
        default = spec.default
    else:
        default = ""
    return {"key": key, "value": db.get_setting(key, default)}


@app.put("/api/settings/{key}")
def write_setting(key: str, body: dict) -> dict:
    # 保留键（如 current_model 存 JSON）须走专用接口，避免通用端点写入非法值把功能写坏
    if key in (
        "current_model", "conversation_history_recall_mode",
        "conversation_summary_injection_enabled",
    ):
        raise HTTPException(400, "该设置项须通过专用接口修改")
    value = str(body.get("value", ""))
    if key == "memory_enabled" and value not in {"0", "1"}:
        raise HTTPException(400, "长期记忆开关只接受 0 或 1")
    if key == "knowledge_default_policy" and value not in {
        "remote_allowed", "ask_each_time", "local_only",
    }:
        raise HTTPException(400, "知识库默认策略只接受 remote_allowed/ask_each_time/local_only")
    if key.startswith("proactive_"):
        try:
            value, _revision = proactive_settings.write_public_setting(key, value)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    else:
        db.set_setting(key, value)
    return {"key": key, "value": db.get_setting(key)}


# ---------------------------------------------------------------- helpers
def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _finish_knowledge_retrieval(prepared: dict | None, *, status: str) -> None:
    if not prepared:
        return
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE knowledge_chat_retrievals SET status=?,finished_at=? WHERE id=?",
            (status, db.now(), prepared["id"]),
        )
        conn.commit()
    finally:
        conn.close()


def _message_knowledge_citations(assistant_id: str) -> list:
    conn = db.connect()
    try:
        return conn.execute(
            "SELECT * FROM knowledge_message_citations WHERE assistant_message_id=? ORDER BY citation_key",
            (assistant_id,),
        ).fetchall()
    finally:
        conn.close()


def _message_evidence_links(assistant_id: str) -> list:
    conn = db.connect()
    try:
        return conn.execute(
            "SELECT * FROM kig_evidence_links WHERE assistant_message_id=? "
            "AND validation_status='active' "
            "ORDER BY citation_key,id",
            (assistant_id,),
        ).fetchall()
    finally:
        conn.close()


def _msg(r) -> dict:
    d = dict(r)
    d["favorite"] = bool(d["favorite"])
    return d
