"""遐蝶后端：FastAPI + SQLite。

分层职责（需求第 10 节）：模型、会话、任务、记忆、工具，均保存在本地 SQLite。
不做多窗口调度、不推倒重写。此文件只负责 HTTP 接口与编排。
"""
import json
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import (
    archivist, companion_state, db, entities, episode_consolidator, episode_summary_service,
    episodes, llm, lore, memory, saga_consolidator, saga_lifecycle, saga_summary,
    saga_summary_service,
)
from . import memory_observer_service
from .affect import observer_service as affect_observer_service
from .persona import build_system_prompt
from .security import ALLOWED_ORIGINS, TOKEN_HEADER, local_api_guard


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    await affect_observer_service.start_worker()
    await memory_observer_service.start_worker()
    await episode_consolidator.start_worker()
    await saga_consolidator.start_worker()
    try:
        yield
    finally:
        await saga_consolidator.stop_worker()
        await episode_consolidator.stop_worker()
        await memory_observer_service.stop_worker()
        await affect_observer_service.stop_worker()


app = FastAPI(title="遐蝶 Agent Backend", version="0.1.0", lifespan=lifespan)

# init 也在模块导入时执行一次，保证裸 TestClient（不走 lifespan）也有表可用。
db.init_db()

# 只允许明确的本地开发来源和 Electron file:// 来源；实际数据接口还需临时令牌。
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", TOKEN_HEADER],
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
        return [_msg(r) for r in rows]
    finally:
        conn.close()


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


# ---------------------------------------------------------------- 聊天（流式）
class ChatIn(BaseModel):
    session_id: str
    content: str
    regenerate: bool = False


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


@app.post("/api/chat")
async def chat(body: ChatIn) -> StreamingResponse:
    uid: str | None = None
    replace_assistant_id: str | None = None
    conn = db.connect()
    try:
        sess = conn.execute("SELECT * FROM sessions WHERE id = ?", (body.session_id,)).fetchone()
        if not sess:
            raise HTTPException(404, "会话不存在")

        # 记录用户消息（regenerate 时不重复插入）
        if not body.regenerate:
            uid = db.new_id()
            conn.execute(
                "INSERT INTO messages(id, session_id, role, content, created_at) VALUES(?,?,?,?,?)",
                (uid, body.session_id, "user", body.content, db.now()),
            )
            # 首条用户消息用作会话标题
            cnt = conn.execute(
                "SELECT COUNT(*) c FROM messages WHERE session_id = ? AND role='user'",
                (body.session_id,),
            ).fetchone()["c"]
            if cnt == 1:
                conn.execute("UPDATE sessions SET title = ? WHERE id = ?",
                             (body.content.strip()[:20] or "新对话", body.session_id))
            conn.commit()
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
        if replace_assistant_id:
            history = conn.execute(
                "SELECT role, content FROM messages"
                " WHERE session_id = ? AND id != ? ORDER BY created_at",
                (body.session_id, replace_assistant_id),
            ).fetchall()
        else:
            history = conn.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at",
                (body.session_id,),
            ).fetchall()
        current_state = companion_state.get_state(persist_advance=False)
        next_state = (
            current_state
            if body.regenerate
            else companion_state.preview_interaction(body.content, current_state)
        )
        style = companion_state.get_style_guidance(next_state)
        lore_digest = lore.retrieve_lore(body.content)
        messages = [{
            "role": "system",
            "content": build_system_prompt(digest, style, lore_digest),
        }]
        messages += [{"role": r["role"], "content": r["content"]} for r in history]
    finally:
        conn.close()

    provider, model = _current_model()

    async def gen():
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
                    messages[0]["content"] = build_system_prompt(used_digest, style, lore_digest)
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
                },
            )
            async for chunk in llm.stream_chat(provider, model, messages):
                collected.append(chunk)
                yield _sse("delta", {"text": chunk})
        except llm.LLMError as e:
            yield _sse("error", {"message": str(e), "hint": e.hint})
            return
        except Exception:  # noqa: BLE001 兜底：任何未预期异常也作为 error 事件下发，不静默截断流
            yield _sse("error", {"message": "生成中断", "hint": "回复生成过程中出现意外错误，请重试。"})
            return
        full = "".join(collected)
        # 持久化助手回复
        c2 = db.connect()
        try:
            aid = db.new_id()
            c2.execute(
                "INSERT INTO messages(id, session_id, role, content, model, created_at) VALUES(?,?,?,?,?,?)",
                (aid, body.session_id, "assistant", full, model, db.now()),
            )
            if replace_assistant_id:
                c2.execute("DELETE FROM messages WHERE id = ?", (replace_assistant_id,))
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
            affect_observation = affect_observer_service.enqueue_turn(
                chat_provider=provider,
                chat_model=model,
                session_id=body.session_id,
                user_message_id=uid,
                assistant_message_id=aid,
            )
            memory_observation = memory_observer_service.enqueue_turn(
                chat_provider=provider,
                chat_model=model,
                session_id=body.session_id,
                user_message_id=uid,
                assistant_message_id=aid,
            )
        # 旧关键词候选只在观察模型不可用时兜底；真实模型路径不再逐条等待确认。
        candidate = None
        if (
            not body.regenerate
            and db.get_setting("memory_enabled", "1") == "1"
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
                "memory_observation": memory_observation,
            },
        )

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------- 伴侣状态
@app.get("/api/companion-state")
def read_companion_state() -> dict:
    return companion_state.get_state()


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
def get_episodes() -> list[dict]:
    return episodes.list_episodes()


@app.post("/api/episodes/{episode_id}/correct")
def correct_episode(episode_id: str, body: EpisodeCorrectionIn) -> dict:
    try:
        episode = episodes.correct_episode(
            episode_id, title=body.title, summary=body.summary,
            significance=body.significance, note=body.note,
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not episode:
        raise HTTPException(404, "Episode 不存在")
    return episode


@app.get("/api/episodes/{episode_id}")
def get_episode(episode_id: str) -> dict:
    episode = episodes.get_episode(episode_id)
    if not episode or episode["status"] != "active":
        raise HTTPException(404, "Episode 不存在")
    return episode


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
        if "base_url" in body and body["base_url"] is not None:
            conn.execute("UPDATE providers SET base_url = ? WHERE id = ?", (body["base_url"], pid))
        if body.get("api_key"):  # 只在传了非空 key 时更新，避免误清空
            conn.execute("UPDATE providers SET api_key = ? WHERE id = ?", (body["api_key"], pid))
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
    return {
        "provider_id": prov["id"] if prov else "mock",
        "provider_name": prov["name"] if prov else "内置演示",
        "model": model,
        "capabilities": _capabilities(prov, model) if prov else ["local"],
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
    return {"key": key, "value": db.get_setting(key)}


@app.put("/api/settings/{key}")
def write_setting(key: str, body: dict) -> dict:
    # 保留键（如 current_model 存 JSON）须走专用接口，避免通用端点写入非法值把功能写坏
    if key in ("current_model",):
        raise HTTPException(400, "该设置项须通过专用接口修改")
    db.set_setting(key, str(body.get("value", "")))
    return {"key": key, "value": db.get_setting(key)}


# ---------------------------------------------------------------- helpers
def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _msg(r) -> dict:
    d = dict(r)
    d["favorite"] = bool(d["favorite"])
    return d
