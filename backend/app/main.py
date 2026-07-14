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

from . import companion_state, db, entities, episodes, llm, lore, memory
from .persona import build_system_prompt
from .security import ALLOWED_ORIGINS, TOKEN_HEADER, local_api_guard


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


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
            # 重新生成：先删掉该会话最近一条 assistant 回复，
            # 否则历史会以旧回复结尾（语义变成续写）且新回复会重复堆积。
            last = conn.execute(
                "SELECT id FROM messages WHERE session_id = ? AND role = 'assistant'"
                " ORDER BY created_at DESC LIMIT 1",
                (body.session_id,),
            ).fetchone()
            if last:
                conn.execute("DELETE FROM messages WHERE id = ?", (last["id"],))
                conn.commit()

        # 构造上下文：人设 + 记忆摘要 + 历史
        digest, recalled_memories = memory.build_digest(body.content)
        history = conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at",
            (body.session_id,),
        ).fetchall()
        current_state = companion_state.get_state()
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
        # 先发一个元事件：模型信息 + 是否命中记忆（前端展示"已参考记忆"）
        yield _sse(
            "meta",
            {
                "model": model,
                "memory_used": bool(recalled_memories),
                "memory_count": len(recalled_memories),
                "memory_refs": [
                    {
                        "id": item["id"],
                        "layer": item["layer"],
                        "source_session_id": item.get("source_session_id"),
                        "source_message_id": item.get("source_message_id"),
                    }
                    for item in recalled_memories
                ],
            },
        )
        collected: list[str] = []
        try:
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
            c2.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (db.now(), body.session_id))
            c2.commit()
        finally:
            c2.close()
        if not body.regenerate:
            companion_state.save_state(next_state)
        # 只生成待确认候选，不再把模型判断静默写成正式记忆。
        candidate = (
            memory.maybe_create_candidate(body.content, body.session_id, uid)
            if not body.regenerate
            else None
        )
        yield _sse(
            "done",
            {"message_id": aid, "auto_memory": None, "memory_candidate": candidate},
        )

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------- 伴侣状态
@app.get("/api/companion-state")
def read_companion_state() -> dict:
    state = companion_state.get_state()
    state["style_guidance"] = companion_state.get_style_guidance(state)
    return state


@app.post("/api/companion-state/reset")
def reset_companion_state() -> dict:
    state = companion_state.reset_state()
    state["style_guidance"] = companion_state.get_style_guidance(state)
    return state


# ---------------------------------------------------------------- 记忆
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
    if body.get("status") is not None and body["status"] not in (
        "active", "cooling", "frozen", "tombstone"
    ):
        raise HTTPException(400, "非法的记忆状态")
    m = memory.update_memory(mid, **body)
    if not m:
        raise HTTPException(404, "记忆不存在")
    return m


@app.delete("/api/memories/{mid}")
def remove_memory(mid: str) -> dict:
    if not memory.delete_memory(mid):
        raise HTTPException(404, "记忆不存在")
    return {"ok": True}


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
class EpisodeDecisionIn(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    significance: Optional[int] = None
    fragment_ids: Optional[list[str]] = None
    note: str = ""


@app.get("/api/episode-candidates")
def get_episode_candidates(status: str = "pending") -> list[dict]:
    if status not in ("pending", "accepted", "rejected"):
        raise HTTPException(400, "非法的 Episode 候选状态")
    return episodes.list_candidates(status)


@app.post("/api/episode-candidates/generate")
def generate_episode_candidates() -> dict:
    created = episodes.generate_candidates()
    return {"created": len(created), "candidates": created}


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


@app.get("/api/episodes/{episode_id}")
def get_episode(episode_id: str) -> dict:
    episode = episodes.get_episode(episode_id)
    if not episode or episode["status"] != "active":
        raise HTTPException(404, "Episode 不存在")
    return episode


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
