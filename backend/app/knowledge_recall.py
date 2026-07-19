"""K.2 本地召回预检：只做影子判断，不改变聊天上下文。"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable

from . import db, knowledge_context, knowledge_recall_thresholds, knowledge_search

PROTOCOL_VERSION = "knowledge-recall-decision-v1"
TIMEOUT_MS = 1_500
NATURAL_TOKEN_BUDGET = 700
MAX_NATURAL_RESULTS = 4
EMPTY_POLICY_SNAPSHOT = hashlib.sha256(b"[]").hexdigest()
REASON_CODES = frozenset({
    "queued", "explicit_request", "explicit_forbidden", "companion_smalltalk",
    "emotional_support", "simple_task", "ambiguous_reference", "no_candidates",
    "fts_no_terms", "preflight_search_failed", "preflight_failed", "preflight_timeout",
    "entity_hit", "exact_term_hit", "semantic_candidate", "lexical_candidate",
    "transmission_consent_required", "local_only_remote_provider", "duplicate_candidates",
    "source_conflict", "source_unavailable", "provider_revision_changed",
})
RECALL_MODES = frozenset({"off", "explicit", "smart"})

_FORBID = re.compile(
    r"(?:不要|别|无需|不用).{0,8}(?:(?:查|检索|搜索|引用).{0,6}(?:知识库|资料|文档|文件)|"
    r"(?:知识库|资料|文档|文件).{0,6}(?:查|检索|搜索|引用|找))"
)
_DOUBLE_NEGATIVE = re.compile(r"(?:不要|别|无需|不用).{0,2}不(?:查|检索|搜索|引用|找)")
_SOURCE_CONFLICT = re.compile(r"(?:我记得|记忆|印象).{0,40}(?:资料|文档).{0,12}(?:怎么写|不一样|冲突|却说)")
_GREETING = re.compile(r"^(?:嗨|你好|您好|早上好|中午好|下午好|晚上好|晚安|在吗)[呀啊哦嘛吗！!，,。\s]*(?:今天)?(?:陪我聊(?:一会儿|会儿)?(?:吧|嘛)?)?[。！!？?\s]*$")
_EMOTION = re.compile(r"(?:有点|很|太|好)?(?:累|难过|伤心|焦虑|烦|孤独|委屈|害怕|想哭|睡不着|没精神)")
_SIMPLE_TASK = re.compile(r"^(?:帮我)?(?:翻译|改写|润色|计算|算一下|列个清单|起个标题|写一句).{0,48}$")
_AMBIGUOUS = re.compile(r"^(?:嗯+|哦+|好吧|然后呢|继续|你觉得呢|她呢|他呢|它呢|这个呢|那个呢|后来呢)[？?。！!\s]*$")
_CONTEXT_REFERENCE = re.compile(r"(?:它|这个|那个|上述|前面(?:那个)?|刚才(?:那个)?|这份|该项目|这个项目)")

# 查询清理：去掉无检索价值的寒暄、情感和语气前缀/后缀
_QUERY_CLEAN_PREFIXES = re.compile(
    r"^(?:嗨|你好|您好|早上好|中午好|下午好|晚上好|在吗"
    r"(?:[，。！？、；：,.!?;:\s]+(?:今天|那个|对了|我想问(?:一下)?|"
    r"帮我|麻烦|请问|想问(?:一下)?))?"
    r")[，。！？、；：,.!?;:\s]+"
)
_QUERY_CLEAN_SUFFIXES = re.compile(
    r"[，。！？、；：,.!?;:\s]*(?:谢谢|感谢|拜托|好吗|可以吗|行吗|对吧|确定吗|"
    r"(?:觉得|认为)(?:呢|怎么样)|怎么办|怎么处理|请问|麻烦了|辛苦了|拜托了)[。！？,.!?\s]*$"
)
_QUERY_CLEAN_FILLERS = re.compile(r"[啊呀哦嗯嘛吧呢啦哈呵嘿噢哎呦]+")
_CJK_STOP_LIST = frozenset({
    "的", "了", "和", "与", "或", "是", "在", "有", "我", "你", "他", "她", "它",
    "这", "那", "个", "们", "都", "也", "还", "要", "就", "能", "会", "可以",
    "因为", "所以", "但是", "如果", "虽然", "这个", "那个", "什么", "怎么",
    "一个", "一些", "一下", "可能", "应该", "然后", "而且", "之后", "之前",
    "非常", "比较", "特别", "一般", "大概", "左右",
})


def settings() -> dict:
    mode = db.get_setting("knowledge_recall_mode", "explicit")
    if mode not in RECALL_MODES:
        mode = "explicit"
    return {
        "mode": mode,
        "shadow_enabled": db.get_setting("knowledge_shadow_recall_enabled", "1") == "1",
        "protocol_version": PROTOCOL_VERSION,
        "threshold_version": knowledge_recall_thresholds.THRESHOLD_VERSION,
        "natural_token_budget": NATURAL_TOKEN_BUDGET,
        "automatic_injection_enabled": knowledge_recall_thresholds.AUTOMATIC_INJECTION_ENABLED,
        "answer_behavior": "disabled" if mode == "off" else (
            "smart_high_confidence" if mode == "smart" else "explicit_unchanged"
        ),
        "stores_query_or_content": False,
    }


def set_shadow_enabled(enabled: bool) -> dict:
    db.set_setting("knowledge_shadow_recall_enabled", "1" if enabled else "0")
    return settings()


def update_settings(*, mode: str | None = None, shadow_enabled: bool | None = None) -> dict:
    if mode is not None and mode not in RECALL_MODES:
        raise ValueError("知识召回模式无效")
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if mode is not None:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='knowledge_recall_mode'"
            ).fetchone()
            before = row["value"] if row and row["value"] in RECALL_MODES else "explicit"
            conn.execute(
                "INSERT INTO settings(key,value) VALUES('knowledge_recall_mode',?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (mode,),
            )
            if mode != before:
                conn.execute(
                    "INSERT INTO knowledge_recall_mode_events("
                    "id,before_mode,after_mode,actor,reason_code,created_at)"
                    " VALUES(?,?,?,'user','user_changed_recall_mode',?)",
                    (db.new_id(), before, mode, db.now()),
                )
        if shadow_enabled is not None:
            conn.execute(
                "INSERT INTO settings(key,value) VALUES('knowledge_shadow_recall_enabled',?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("1" if shadow_enabled else "0",),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return settings()


def enqueue(*, session_id: str, user_message_id: str | None, user_text: str,
            provider: dict | None) -> str | None:
    """快速落一个无正文 queued 记录；实际检索由 worker 的线程完成。"""
    if not settings()["shadow_enabled"] or not user_message_id:
        return None
    decision_id = db.new_id()
    provider = provider or {}
    provider_snapshot = {
        "id": provider.get("id"),
        "execution_location": provider.get("execution_location") or "unknown",
        "location_revision": max(1, int(provider.get("location_revision") or 1)),
    }
    location = str(provider_snapshot["execution_location"])
    if location not in {"local", "remote", "unknown"}:
        location = "unknown"
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO knowledge_recall_decisions("
            "id,session_id,user_message_id,protocol_version,threshold_version,recall_mode,shadow,action,reason_code,"
            "confidence_band,query_sha256,policy_snapshot_sha256,provider_id,provider_location,"
            "provider_location_revision,status,created_at) VALUES(?,?,?,?,?,?,1,'skip','queued',"
            "'low',?,?,?,?,?,'queued',?)",
            (decision_id, session_id, user_message_id, PROTOCOL_VERSION,
             knowledge_recall_thresholds.THRESHOLD_VERSION, "smart",
             _fingerprint(user_text), EMPTY_POLICY_SNAPSHOT, provider_snapshot["id"], location,
             provider_snapshot["location_revision"], db.now()),
        )
        conn.commit()
    finally:
        conn.close()
    from . import knowledge_recall_service
    knowledge_recall_service.enqueue(decision_id, user_text, provider_snapshot, session_id)
    return decision_id


def evaluate(
    user_text: str, provider: dict | None = None, *,
    session_id: str | None = None,
    search_fn: Callable[..., dict] | None = None,
    policy_fn: Callable[[set[str]], dict[str, dict]] | None = None,
) -> dict:
    started = time.perf_counter()
    text = str(user_text or "").strip()
    provider = provider or {}
    base = {
        "recall_mode": "smart",
        "action": "skip", "reason_code": "no_candidates", "confidence_band": "low",
        "candidate_count": 0, "eligible_count": 0, "injected_count": 0,
        "retrieval_mode": "none", "vector_available": False, "vector_error_code": None,
        "policy_snapshot_sha256": EMPTY_POLICY_SNAPSHOT,
        "natural_selected_count": 0, "natural_tokens": 0,
        "features": {"term_strength": 0, "search_ms": 0, "policy_ms": 0},
        "_selected_results": [],
    }
    if _FORBID.search(text) and not _DOUBLE_NEGATIVE.search(text):
        base["recall_mode"] = "explicit"
        return _finish(base, started, "skip", "explicit_forbidden", "high")
    query_source = _DOUBLE_NEGATIVE.sub("查", text)
    explicit_query, _ = knowledge_context.retrieval_query(query_source)
    if explicit_query:
        base["recall_mode"] = "explicit"
    if _GREETING.fullmatch(text):
        return _finish(base, started, "skip", "companion_smalltalk", "high")
    if _EMOTION.search(text) and not explicit_query:
        return _finish(base, started, "skip", "emotional_support", "high")
    if _SIMPLE_TASK.fullmatch(text) and not explicit_query:
        return _finish(base, started, "skip", "simple_task", "high")
    if _AMBIGUOUS.fullmatch(text) or len(text) < 2:
        return _finish(base, started, "skip", "ambiguous_reference", "high")

    context_entities = recent_context_entities(session_id) if not explicit_query else []
    query = explicit_query or clean_query(text, context_entities=context_entities)[
        :knowledge_search.MAX_QUERY_CHARS
    ]
    search_started = time.perf_counter()
    try:
        search_kwargs = {
            "limit": 6, "context_window": 0, "max_chars": 4000,
            "max_per_collection": knowledge_search.NATURAL_MAX_PER_COLLECTION,
        }
        found = (search_fn or knowledge_search.hybrid_search)(query, **search_kwargs)
    except knowledge_search.SearchError as error:
        reason = "fts_no_terms" if error.code == "knowledge_query_has_no_terms" else "preflight_search_failed"
        return _finish(base, started, "skip", reason, "low", status="failed")
    search_ms = max(0, round((time.perf_counter() - search_started) * 1000, 3))
    raw_results = found.get("results", [])
    results = raw_results if explicit_query else [
        item for item in raw_results if _natural_candidate_admitted(item)
    ]
    policy_started = time.perf_counter()
    document_ids = {
        document_id for item in results for document_id in _candidate_document_ids(item)
    }
    documents = (policy_fn or _document_policies)(document_ids)
    policy_ms = max(0, round((time.perf_counter() - policy_started) * 1000, 3))
    natural_results, natural_tokens = select_natural_candidates(results)
    base.update({
        "candidate_count": len(results),
        "retrieval_mode": found.get("retrieval_mode", "fts"),
        "vector_available": bool(found.get("vector_available")),
        "vector_error_code": found.get("vector_error_code"),
        "policy_snapshot_sha256": _policy_snapshot(documents),
        "natural_selected_count": len(natural_results),
        "natural_tokens": natural_tokens,
        "_selected_results": natural_results,
        "_search_query": query,
        "features": {
            **found.get("diagnostics", {}),
            "query_changed": query != text,
            "query_char_count": len(query),
            "raw_candidate_count": len(raw_results),
            "admitted_candidate_count": len(results),
            "term_strength": 0,
            "search_ms": search_ms,
            "policy_ms": policy_ms,
        },
    })
    if not results:
        return _finish(base, started, "skip", "no_candidates", "low")

    location = str(provider.get("execution_location") or "unknown")
    consent_count = local_only_count = 0
    if location != "local":
        eligible_count = 0
        for item in results:
            policies = {
                documents[document_id]["transmission_policy"]
                for document_id in _candidate_document_ids(item) if document_id in documents
            }
            if "remote_allowed" in policies:
                eligible_count += 1
            elif "ask_each_time" in policies:
                consent_count += 1
            elif "local_only" in policies:
                local_only_count += 1
        base["eligible_count"] = eligible_count
    else:
        base["eligible_count"] = len(results)
    if explicit_query:
        action, reason, confidence = "retrieve", "explicit_request", "high"
    elif _SOURCE_CONFLICT.search(text):
        action, reason, confidence = "retrieve", "source_conflict", "high"
    else:
        # 标题实体只看融合排序最前的两个已准入候选，避免低位无关标题把普通词误判为实体命中。
        term_strength = _term_strength(text, results[:2])
        base["features"]["term_strength"] = term_strength
        if term_strength >= knowledge_recall_thresholds.EXACT_TERM_HIGH_MIN_CHARS:
            action, reason, confidence = "retrieve", "exact_term_hit", "high"
        elif term_strength >= knowledge_recall_thresholds.ENTITY_MEDIUM_MIN_CHARS:
            action, reason, confidence = "retrieve", "entity_hit", "medium"
        elif any(item.get("match_type") in {"vector", "hybrid"} for item in results):
            confidence = "high" if knowledge_recall_thresholds.SEMANTIC_AUTO_HIGH_ENABLED else "medium"
            action, reason = "retrieve", "semantic_candidate"
        else:
            action, reason, confidence = "retrieve", "lexical_candidate", "medium"
    # 远传策略只能限制已经达到 high 的真实候选，绝不能把弱语义候选反向“升格”为 high。
    if location != "local" and confidence == "high":
        if consent_count:
            action, reason = "ask", "transmission_consent_required"
        elif local_only_count:
            action, reason = "ask", "local_only_remote_provider"
    return _finish(base, started, action, reason, confidence)


def complete(decision_id: str, result: dict) -> None:
    if result.get("reason_code") not in REASON_CODES:
        fail(decision_id)
        return
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE knowledge_recall_decisions SET recall_mode=?,action=?,reason_code=?,confidence_band=?,"
            "candidate_count=?,eligible_count=?,injected_count=0,retrieval_mode=?,vector_available=?,"
            "vector_error_code=?,policy_snapshot_sha256=?,latency_ms=?,status=?,finished_at=? WHERE id=?",
            (result.get("recall_mode", "smart"), result["action"], result["reason_code"], result["confidence_band"],
             result["candidate_count"], result["eligible_count"], result["retrieval_mode"],
             int(result["vector_available"]), result.get("vector_error_code"),
             result["policy_snapshot_sha256"], result["latency_ms"], result["status"],
             db.now(), decision_id),
        )
        conn.commit()
    finally:
        conn.close()


def record_actual_locked(
    conn, *, session_id: str, user_message_id: str, user_text: str,
    provider: dict | None, result: dict, injected_count: int,
    grant_id: str | None = None,
) -> str:
    """在聊天事务中保存真实 smart 判断；仅保存哈希、计数和枚举。"""
    reason = str(result.get("reason_code") or "no_candidates")
    if reason not in REASON_CODES:
        reason = "preflight_failed"
    provider = provider or {}
    location = str(provider.get("execution_location") or "unknown")
    if location not in {"local", "remote", "unknown"}:
        location = "unknown"
    decision_id = db.new_id()
    now = db.now()
    conn.execute(
        "INSERT INTO knowledge_recall_decisions("
        "id,session_id,user_message_id,protocol_version,threshold_version,recall_mode,shadow,action,reason_code,"
        "confidence_band,query_sha256,policy_snapshot_sha256,candidate_count,eligible_count,"
        "injected_count,retrieval_mode,vector_available,vector_error_code,provider_id,"
        "provider_location,provider_location_revision,latency_ms,status,created_at,finished_at)"
        " VALUES(?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            decision_id, session_id, user_message_id, PROTOCOL_VERSION,
            knowledge_recall_thresholds.THRESHOLD_VERSION,
            result.get("recall_mode", "smart"), result.get("action", "skip"), reason,
            result.get("confidence_band", "low"), _fingerprint(user_text),
            result.get("policy_snapshot_sha256", EMPTY_POLICY_SNAPSHOT),
            max(0, int(result.get("candidate_count") or 0)),
            max(0, int(result.get("eligible_count") or 0)), max(0, int(injected_count)),
            result.get("retrieval_mode", "none"), int(bool(result.get("vector_available"))),
            result.get("vector_error_code"), provider.get("id"), location,
            max(1, int(provider.get("location_revision") or 1)),
            max(0, int(result.get("latency_ms") or 0)), result.get("status", "completed"),
            now, now,
        ),
    )
    if grant_id:
        conn.execute(
            "UPDATE knowledge_transmission_grants SET recall_decision_id=? WHERE id=?",
            (decision_id, grant_id),
        )
    return decision_id


def fail(decision_id: str, *, timed_out: bool = False) -> None:
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE knowledge_recall_decisions SET action='skip',reason_code=?,confidence_band='low',"
            "latency_ms=?,status=?,finished_at=? WHERE id=?",
            ("preflight_timeout" if timed_out else "preflight_failed", TIMEOUT_MS if timed_out else 0,
             "timed_out" if timed_out else "failed", db.now(), decision_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_decisions(*, session_id: str | None = None, limit: int = 30) -> list[dict]:
    conn = db.connect()
    try:
        where, params = "", []
        if session_id:
            where, params = " WHERE session_id=?", [session_id]
        rows = conn.execute(
            "SELECT * FROM knowledge_recall_decisions" + where +
            " ORDER BY created_at DESC,id DESC LIMIT ?", params + [max(1, min(limit, 100))],
        ).fetchall()
        return [_public(dict(row)) for row in rows]
    finally:
        conn.close()


def get_decision(decision_id: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM knowledge_recall_decisions WHERE id=?", (decision_id,)).fetchone()
        return _public(dict(row)) if row else None
    finally:
        conn.close()


def decision_stats(*, session_id: str | None = None) -> dict:
    conn = db.connect()
    try:
        where, params = " WHERE status!='queued'", []
        if session_id:
            where += " AND session_id=?"
            params.append(session_id)
        rows = [dict(row) for row in conn.execute(
            "SELECT action,reason_code,latency_ms,vector_available,status FROM "
            "knowledge_recall_decisions" + where + " ORDER BY created_at DESC LIMIT 1000",
            params,
        ).fetchall()]
    finally:
        conn.close()
    total = len(rows)
    action_counts = {key: 0 for key in ("skip", "retrieve", "ask")}
    reason_counts: dict[str, int] = {}
    for row in rows:
        action_counts[row["action"]] += 1
        reason_counts[row["reason_code"]] = reason_counts.get(row["reason_code"], 0) + 1
    latencies = sorted(int(row["latency_ms"]) for row in rows)
    return {
        "sample_count": total,
        "scope": "session" if session_id else "global",
        "action_counts": action_counts,
        "action_rates": {
            key: round(value / total, 4) if total else 0.0 for key, value in action_counts.items()
        },
        "reason_counts": dict(sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))),
        "latency_ms": {
            "average": round(sum(latencies) / total, 3) if total else 0.0,
            "p50": _percentile(latencies, 0.50),
            "p90": _percentile(latencies, 0.90),
            "p99": _percentile(latencies, 0.99),
        },
        "vector_available_rate": round(
            sum(bool(row["vector_available"]) for row in rows) / total, 4,
        ) if total else 0.0,
        "timeout_rate": round(
            sum(row["status"] == "timed_out" for row in rows) / total, 4,
        ) if total else 0.0,
    }


def select_natural_candidates(results: list[dict]) -> tuple[list[dict], int]:
    """K.3 独立自然召回预算；只做选择统计，影子阶段不会注入这些结果。"""
    selected: list[dict] = []
    used = 0
    for item in results:
        metadata = " ".join([
            str(item.get("original_name") or ""),
            " ".join(str(value) for value in item.get("heading_path", [])),
        ])
        cost = knowledge_context.estimate_tokens(metadata) + knowledge_context.estimate_tokens(
            str(item.get("content") or "")
        ) + 12
        if used + cost > NATURAL_TOKEN_BUDGET:
            continue
        selected.append(item)
        used += cost
        if len(selected) >= MAX_NATURAL_RESULTS:
            break
    return selected, used


def _document_policies(document_ids: set[str]) -> dict[str, dict]:
    if not document_ids:
        return {}
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id,transmission_policy,policy_revision FROM knowledge_documents WHERE id IN (" +
            ",".join("?" for _ in document_ids) + ")", sorted(document_ids),
        ).fetchall()
        return {row["id"]: dict(row) for row in rows}
    finally:
        conn.close()


def _policy_snapshot(documents: dict[str, dict]) -> str:
    payload = [[key, row["transmission_policy"], row["policy_revision"]]
               for key, row in sorted(documents.items())]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def _term_strength(text: str, results: list[dict]) -> int:
    folded = text.casefold()
    strength = 0
    for item in results:
        stem = re.sub(r"\.[^.]+$", "", str(item.get("original_name") or "")).casefold()
        for token in re.findall(r"[\u3400-\u9fff]{2,}|[a-z0-9_-]{3,}", stem):
            if token in folded:
                strength = max(strength, len(token))
            if re.fullmatch(r"[\u3400-\u9fff]+", token):
                for size in range(min(4, len(token)), 1, -1):
                    if any(token[index:index + size] in folded for index in range(len(token) - size + 1)):
                        strength = max(strength, size)
                        break
        for tag in item.get("tags", []):
            value = str(tag).casefold()
            if len(value) >= 2 and value in folded:
                strength = max(strength, len(value))
    return strength


def _natural_candidate_admitted(item: dict) -> bool:
    """FTS 命中保留；纯 dense 需过 K.3 校准下限。旧/测试适配器缺少特征时保守保留。"""
    if item.get("fts_position") is not None:
        return True
    score = item.get("vector_score")
    if score is None:
        return "fts_position" not in item and "dense_position" not in item
    return float(score) >= knowledge_recall_thresholds.SEMANTIC_CANDIDATE_MIN_SCORE


def _candidate_document_ids(item: dict) -> set[str]:
    values = item.get("duplicate_document_ids") or [item["document_id"]]
    return {str(value) for value in values}


def _finish(base: dict, started: float, action: str, reason: str, confidence: str,
            *, status: str = "completed") -> dict:
    return {**base, "action": action, "reason_code": reason, "confidence_band": confidence,
            "latency_ms": max(0, round((time.perf_counter() - started) * 1000)), "status": status}


def _fingerprint(text: str) -> str:
    normalized = " ".join(str(text or "").split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def clean_query(text: str, *, context_entities: list[str] | None = None) -> str:
    """本地确定性查询清理：去掉寒暄、感叹、无检索价值的前缀后缀。

    保留：人名、项目名、术语、数字、时间、英文单词。
    去掉：问候语、情感表达、句末礼貌语、语气词、常见停用词。
    """
    if not text or not text.strip():
        return ""
    cleaned = str(text).strip()
    # 去掉前缀寒暄/求助
    cleaned = _QUERY_CLEAN_PREFIXES.sub("", cleaned, count=1)
    # 去掉后缀感谢/请求
    cleaned = _QUERY_CLEAN_SUFFIXES.sub("", cleaned, count=1)
    # 去掉纯语气词
    cleaned = _QUERY_CLEAN_FILLERS.sub("", cleaned)
    # 去掉常见停用词（仅在清理后仍较长时）
    if len(cleaned) > 40:
        tokens = cleaned.split()
        tokens = [t for t in tokens if t not in _CJK_STOP_LIST]
        cleaned = " ".join(tokens)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if _CONTEXT_REFERENCE.search(cleaned):
        additions = [
            entity for entity in (context_entities or [])
            if entity.casefold() not in cleaned.casefold()
        ][:2]
        if additions:
            cleaned = f"{cleaned} {' '.join(additions)}"
    # 清理后无有效内容则返回原文本
    if not cleaned or len(cleaned) < 2:
        return str(text).strip()
    return cleaned


def recent_context_entities(session_id: str | None) -> list[str]:
    """只延续最近两轮中出现过、且可由本地知识元数据验证的名称或标签。"""
    if not session_id:
        return []
    conn = db.connect()
    try:
        messages = conn.execute(
            "SELECT content FROM messages WHERE session_id=? ORDER BY created_at DESC,id DESC LIMIT 4",
            (session_id,),
        ).fetchall()
        documents = conn.execute(
            "SELECT original_name,tags_json FROM knowledge_documents"
            " WHERE status='indexed' AND indexed_at IS NOT NULL ORDER BY updated_at DESC LIMIT 100"
        ).fetchall()
    finally:
        conn.close()
    recent = "\n".join(str(row["content"] or "") for row in messages).casefold()
    candidates: list[str] = []
    for row in documents:
        stem = re.sub(r"\.[^.]{1,8}$", "", str(row["original_name"] or "")).strip()
        values = [stem]
        try:
            values.extend(str(value).strip() for value in json.loads(row["tags_json"] or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        for value in values:
            if 2 <= len(value) <= 80 and value.casefold() in recent and value not in candidates:
                candidates.append(value)
    return candidates[:4]


def _public(item: dict) -> dict:
    item["shadow"] = bool(item["shadow"])
    item["vector_available"] = bool(item["vector_available"])
    item["query_fingerprint"] = item.pop("query_sha256")[:12]
    item["policy_fingerprint"] = item.pop("policy_snapshot_sha256")[:12]
    return item


def _percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    index = min(len(values) - 1, max(0, int((len(values) - 1) * ratio + 0.5)))
    return values[index]
