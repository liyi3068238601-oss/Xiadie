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
    archivist, archivist_worker, companion_state, context_assembler, context_budget,
    context_controls, context_diagnostics, conversation_summaries,
    conversation_summary_service, db,
    entities, episode_consolidator, history_recall,
    episode_summary_service, episodes, knowledge, knowledge_cleanup, knowledge_context,
    knowledge_embeddings, knowledge_grants,
    knowledge_management, knowledge_parser, knowledge_policy, knowledge_recall, knowledge_recall_service, knowledge_search,
    knowledge_worker, llm, lore, memory, memory_conflicts, saga_consolidator, saga_lifecycle, saga_summary,
    saga_summary_service, secret_store, slow_lifecycle,
)
from . import memory_observer_service
from .affect import observer_service as affect_observer_service
from .proactive import presence as proactive_presence
from .proactive import settings as proactive_settings
from .proactive import cognition_service as companion_cognition_service
from .proactive import orchestrator as proactive_orchestrator
from .proactive import delivery as proactive_delivery
from .security import ALLOWED_ORIGINS, TOKEN_HEADER, local_api_guard

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
        cursor = conn.execute(
            "DELETE FROM message_attachments WHERE message_id IS NULL AND created_at < ?",
            (cutoff,),
        )
        conn.commit()
        return cursor.rowcount or 0
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # 启动时清理上一次运行遗留的孤儿附件（message_id IS NULL 且超过 1 小时）
    cleanup_orphan_attachments()
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
    knowledge_recall_service.start_worker()
    try:
        yield
    finally:
        knowledge_recall_service.stop_worker()
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
            "INSERT INTO sessions(id, title, created_at, updated_at) VALUES(?,?,?,?)",
            (sid, (body.title or "新对话").strip() or "新对话", t, t),
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
        attachments_by_message: dict[str, list[dict]] = {}
        attach_rows = conn.execute(
            "SELECT id, message_id, filename, mime_type, char_count, content_sha256, created_at"
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
            })
        for message in messages:
            message["knowledge_citations"] = by_message.get(message["id"], [])
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
            "SELECT id, message_id, filename, mime_type, content_text, char_count"
            " FROM message_attachments WHERE id=? AND message_id=?",
            (aid, mid),
        ).fetchone()
        if not row:
            raise HTTPException(404, "附件不存在")
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
        cursor = conn.execute(
            "DELETE FROM message_attachments WHERE id=? AND message_id IS NULL",
            (attachment_id,),
        )
        conn.commit()
        if cursor.rowcount == 0:
            row = conn.execute(
                "SELECT message_id FROM message_attachments WHERE id=?",
                (attachment_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(404, "附件不存在")
            raise HTTPException(409, "附件已绑定到消息，不能单独删除")
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
            "SELECT c.content,c.content_sha256,d.status,d.index_version,co.status collection_status "
            "FROM knowledge_chunks c JOIN knowledge_documents d ON d.id=c.document_id "
            "JOIN knowledge_collections co ON co.id=d.collection_id "
            "WHERE c.id=? AND c.document_id=?",
            (citation["chunk_id"], citation["document_id"]),
        ).fetchone()
        if (
            not source or source["content_sha256"] != citation["content_sha256"]
            or hashlib.sha256(source["content"].encode("utf-8")).hexdigest()
            != citation["content_sha256"]
            or source["status"] != "indexed" or source["index_version"] != knowledge_search.INDEX_VERSION
            or source["collection_status"] != "active"
        ):
            raise HTTPException(410, "原始资料已变化、停用或删除")
        result = knowledge_context.citation_public(citation)
        result["content"] = source["content"]
        return result
    finally:
        conn.close()


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
    # 空 content 且无附件：拒绝（regenerate 不受此约束，因为复用历史消息）
    if not body.regenerate and not body.content.strip() and not body.attachment_ids:
        raise HTTPException(400, "content 和 attachment_ids 至少有一个非空")
    uid: str | None = None
    replace_assistant_id: str | None = None
    provider, model = _current_model()
    conn = db.connect()
    try:
        sess = conn.execute("SELECT * FROM sessions WHERE id = ?", (body.session_id,)).fetchone()
        if not sess:
            raise HTTPException(404, "会话不存在")

        # 先分配/定位消息 ID，但在远传授权校验完成前不写入新消息。
        if not body.regenerate:
            uid = db.new_id()
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

        # 构造上下文：人设 + 记忆摘要 + 历史
        digest, recalled_memories = memory.build_digest(body.content)
        current_state = companion_state.get_state(persist_advance=False)
        next_state = (
            current_state
            if body.regenerate
            else companion_state.preview_interaction(body.content, current_state)
        )
        style = companion_state.get_style_guidance(next_state)
        lore_digest = lore.retrieve_lore(body.content)
        recall_mode = knowledge_recall.settings()["mode"]
        # 提前计算 capability，供知识召回动态预算和上下文装配共用
        capability = _context_capability(provider, model)
        # 纯附件无文字消息：跳过知识召回（避免误触发远传授权询问）
        content_has_text = bool(body.content.strip())
        if content_has_text:
            knowledge_retrieval, recall_decision = knowledge_context.prepare_for_mode(
                body.content, mode=recall_mode, provider=provider,
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
                    content=body.content, provider=provider, model=model,
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

        if not body.regenerate:
            conn.execute(
                "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
                (uid, body.session_id, "user", body.content, db.now()),
            )
            cnt = conn.execute(
                "SELECT COUNT(*) c FROM messages WHERE session_id=? AND role='user'",
                (body.session_id,),
            ).fetchone()["c"]
            if cnt == 1:
                conn.execute(
                    "UPDATE sessions SET title=? WHERE id=?",
                    (body.content.strip()[:20] or "新对话", body.session_id),
                )

        if not body.regenerate and uid and recall_mode == "smart" and recall_decision:
            knowledge_recall.record_actual_locked(
                conn, session_id=body.session_id, user_message_id=uid,
                user_text=body.content, provider=provider, result=recall_decision,
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
        if body.attachment_ids and uid:
            rows = conn.execute(
                "SELECT id, filename, content_text FROM message_attachments WHERE id IN (%s)"
                % ",".join("?" * len(body.attachment_ids)),
                body.attachment_ids,
            ).fetchall()
            found = {row["id"]: row for row in rows}
            parts = []
            for aid in body.attachment_ids:
                row = found.get(aid)
                if row:
                    conn.execute(
                        "UPDATE message_attachments SET message_id=? WHERE id=? AND message_id IS NULL",
                        (uid, aid),
                    )
                    parts.append("=== %s ===\n%s" % (row["filename"], row["content_text"]))
            if parts:
                attachment_block = "\n\n".join(parts)
        knowledge_block = knowledge_context.prompt_block(knowledge_retrieval)
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
            if context_controls.summary_injection_enabled() else None
        )
        history_prepared = history_recall.prepare_locked(
            conn, body.content, current_session_id=body.session_id,
        )
        try:
            context_package = context_assembler.assemble(
                history=history,
                capability=capability,
                memory_digest=digest,
                affect_guidance=style,
                lore_digest=lore_digest,
                knowledge_block=knowledge_block,
                active_summary=active_summary,
                cross_session_recall=history_prepared["turns"],
                current_session_id=body.session_id,
                attachment_block=attachment_block,
            )
        except context_budget.ContextBudgetError as error:
            if conn.in_transaction:
                conn.rollback()
            raise HTTPException(413, error.public_detail()) from error
        messages = list(context_package.messages)
        trimmed_count = context_package.trimmed_messages
        conn.commit()
    finally:
        conn.close()

    if not body.regenerate and uid and recall_mode == "explicit" and content_has_text:
        # 只在后台记录影子判断；绝不修改本轮 messages 或 knowledge_block。
        # 纯附件无文字消息不触发知识召回，无需入队影子判断。
        knowledge_recall.enqueue(
            session_id=body.session_id, user_message_id=uid,
            user_text=body.content, provider=provider,
        )

    # EAP v0.2 Conversation Presence v2：用户消息入库后更新 presence 状态。
    # 按 spec："新消息到达时自动使过期离开状态结束"；程序规则识别高精度表达。
    # presence 更新失败不应阻塞聊天（try/except 包裹）。
    if not body.regenerate and uid and content_has_text:
        try:
            proactive_orchestrator.handle_user_message(body.session_id)
        except Exception:  # noqa: BLE001 - proactive recovery must not block chat
            logger.warning(
                "proactive_user_return_failed session_id=%s message_id=%s",
                body.session_id, uid, exc_info=True,
            )
        try:
            proactive_presence.update_presence(
                body.session_id,
                proactive_presence.detect_presence_signals(body.content),
                source_message_id=uid,
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
                        affect_guidance=style,
                        lore_digest=lore_digest,
                        knowledge_block=knowledge_block,
                        active_summary=active_summary,
                        cross_session_recall=history_prepared["turns"],
                        current_session_id=body.session_id,
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
                },
            )
            async for chunk in llm.stream_chat(
                provider, model, messages,
                max_tokens=context_package.output_reserve_tokens,
            ):
                collected.append(chunk)
                yield _sse("delta", {"text": chunk})
        except llm.LLMError as e:
            _finish_knowledge_retrieval(knowledge_retrieval, status="failed")
            yield _sse("error", {"message": str(e), "hint": e.hint})
            return
        except Exception:  # noqa: BLE001 兜底：任何未预期异常也作为 error 事件下发，不静默截断流
            _finish_knowledge_retrieval(knowledge_retrieval, status="failed")
            yield _sse("error", {"message": "生成中断", "hint": "回复生成过程中出现意外错误，请重试。"})
            return
        full, used_citations = knowledge_context.validate_citations(
            "".join(collected), knowledge_retrieval,
        )
        # 持久化助手回复
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
            c2.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (db.now(), body.session_id))
            c2.commit()
        finally:
            c2.close()
        saved_companion_state = None
        affect_observation = None
        memory_observation = None
        if not body.regenerate:
            saved_companion_state = companion_state.commit_interaction(
                body.content,
                source_session_id=body.session_id,
                source_message_id=uid,
            )
            try:
                memory_observation = memory_observer_service.enqueue_turn(
                    chat_provider=provider,
                    chat_model=model,
                    session_id=body.session_id,
                    user_message_id=uid,
                    assistant_message_id=aid,
                )
            except Exception:  # noqa: BLE001 - 观察器故障不能破坏已完成的回复和引用
                memory_observation = {
                    "status": "unlogged_failure",
                    "error_code": "observer_enqueue_failed",
                }
        if uid:
            # Regeneration creates a new source revision: the worker revokes the old
            # suggestion and evaluates the replacement without incrementing interaction_count.
            affect_observation = companion_cognition_service.enqueue_turn(
                chat_provider=provider,
                chat_model=model,
                session_id=body.session_id,
                user_message_id=uid,
                assistant_message_id=aid,
            )
            try:
                proactive_orchestrator.enqueue_after_chat(
                    session_id=body.session_id,
                    user_message_id=uid,
                    assistant_message_id=aid,
                )
            except Exception:  # noqa: BLE001 - orchestration must not break a completed chat
                logger.warning(
                    "proactive_source_enqueue_failed session_id=%s message_id=%s",
                    body.session_id, aid, exc_info=True,
                )
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
            and db.get_setting("memory_enabled", db.DEFAULT_MEMORY_ENABLED) == "1"
            and (memory_observation or {}).get("error_code")
            in ("observer_model_unavailable", "observer_enqueue_failed")
        ):
            try:
                candidate = memory.maybe_create_candidate(body.content, body.session_id, uid)
            except Exception:  # noqa: BLE001 - 记忆兜底不能吞掉成功的聊天回复
                candidate = None
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
                "content": full,
                "knowledge_citations": [
                    knowledge_context.citation_public(row) for row in _message_knowledge_citations(aid)
                ],
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
    """聊天框附件上传：同步解析文件提取纯文本供本轮注入。

    附件仅用于本轮对话阅读（通过 attachment_block 直接注入 system prompt），
    不存入知识库。存入知识库会导致 transmission_policy=ask_each_time，
    知识库检索命中附件时触发远传授权 409，与"本轮直接阅读"的意图冲突。
    用户如需持久化，可从知识库页面单独上传。
    """
    import os as _os
    import secrets as _secrets
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
    if not filename:
        raise HTTPException(400, "缺少文件名")
    ext = _os.path.splitext(filename)[1].lower()
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
    attachment_id = _secrets.token_hex(8)
    content_sha256 = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
    mime_type = request.headers.get("content-type", "application/octet-stream")
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
        "char_count": char_count,
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
    """能力标签（需求 MODEL-003），按供应商/模型名简单推断。"""
    caps = ["stream"]
    m = model.lower()
    if prov["id"] == "ollama":
        caps.append("local")
    if any(k in m for k in ("reason", "r1", "o1", "o3", "thinking")):
        caps.append("reasoning")
    if any(k in m for k in ("4o", "vl", "vision", "gpt-4")):
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


def _msg(r) -> dict:
    d = dict(r)
    d["favorite"] = bool(d["favorite"])
    return d
