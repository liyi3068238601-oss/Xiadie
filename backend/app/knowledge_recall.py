"""K.2 本地召回预检：只做影子判断，不改变聊天上下文。"""
from __future__ import annotations

import hashlib
import json
import re
import time

from . import db, knowledge_context, knowledge_search

PROTOCOL_VERSION = "knowledge-recall-decision-v1"
TIMEOUT_MS = 1_500
EMPTY_POLICY_SNAPSHOT = hashlib.sha256(b"[]").hexdigest()
REASON_CODES = frozenset({
    "queued", "explicit_request", "explicit_forbidden", "companion_smalltalk",
    "emotional_support", "simple_task", "ambiguous_reference", "no_candidates",
    "fts_no_terms", "preflight_search_failed", "preflight_failed", "preflight_timeout",
    "entity_hit", "exact_term_hit", "semantic_candidate", "lexical_candidate",
    "transmission_consent_required", "local_only_remote_provider", "duplicate_candidates",
    "source_conflict", "source_unavailable", "provider_revision_changed",
})

_FORBID = re.compile(
    r"(?:不要|别|无需|不用).{0,8}(?:(?:查|检索|搜索|引用).{0,6}(?:知识库|资料|文档|文件)|"
    r"(?:知识库|资料|文档|文件).{0,6}(?:查|检索|搜索|引用|找))"
)
_GREETING = re.compile(r"^(?:嗨|你好|您好|早上好|中午好|下午好|晚上好|晚安|在吗)[呀啊哦嘛吗！!，,。\s]*(?:今天)?(?:陪我聊(?:一会儿|会儿)?(?:吧|嘛)?)?[。！!？?\s]*$")
_EMOTION = re.compile(r"(?:有点|很|太|好)?(?:累|难过|伤心|焦虑|烦|孤独|委屈|害怕|想哭|睡不着|没精神)")
_SIMPLE_TASK = re.compile(r"^(?:帮我)?(?:翻译|改写|润色|计算|算一下|列个清单|起个标题|写一句).{0,48}$")
_AMBIGUOUS = re.compile(r"^(?:嗯+|哦+|好吧|然后呢|继续|你觉得呢|她呢|他呢|它呢|这个呢|那个呢|后来呢)[？?。！!\s]*$")


def settings() -> dict:
    return {
        "shadow_enabled": db.get_setting("knowledge_shadow_recall_enabled", "1") == "1",
        "protocol_version": PROTOCOL_VERSION,
        "answer_behavior": "explicit_unchanged",
        "stores_query_or_content": False,
    }


def set_shadow_enabled(enabled: bool) -> dict:
    db.set_setting("knowledge_shadow_recall_enabled", "1" if enabled else "0")
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
            "id,session_id,user_message_id,protocol_version,recall_mode,shadow,action,reason_code,"
            "confidence_band,query_sha256,policy_snapshot_sha256,provider_id,provider_location,"
            "provider_location_revision,status,created_at) VALUES(?,?,?,?,?,1,'skip','queued',"
            "'low',?,?,?,?,?,'queued',?)",
            (decision_id, session_id, user_message_id, PROTOCOL_VERSION, "smart",
             _fingerprint(user_text), EMPTY_POLICY_SNAPSHOT, provider_snapshot["id"], location,
             provider_snapshot["location_revision"], db.now()),
        )
        conn.commit()
    finally:
        conn.close()
    from . import knowledge_recall_service
    knowledge_recall_service.enqueue(decision_id, user_text, provider_snapshot)
    return decision_id


def evaluate(user_text: str, provider: dict | None = None) -> dict:
    started = time.perf_counter()
    text = str(user_text or "").strip()
    provider = provider or {}
    base = {
        "recall_mode": "smart",
        "action": "skip", "reason_code": "no_candidates", "confidence_band": "low",
        "candidate_count": 0, "eligible_count": 0, "injected_count": 0,
        "retrieval_mode": "none", "vector_available": False, "vector_error_code": None,
        "policy_snapshot_sha256": EMPTY_POLICY_SNAPSHOT,
    }
    if _FORBID.search(text):
        base["recall_mode"] = "explicit"
        return _finish(base, started, "skip", "explicit_forbidden", "high")
    explicit_query, _ = knowledge_context.retrieval_query(text)
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

    query = explicit_query or text[:knowledge_search.MAX_QUERY_CHARS]
    try:
        found = knowledge_search.hybrid_search(query, limit=6, context_window=0, max_chars=4000)
    except knowledge_search.SearchError as error:
        reason = "fts_no_terms" if error.code == "knowledge_query_has_no_terms" else "preflight_search_failed"
        return _finish(base, started, "skip", reason, "low", status="failed")
    results = found.get("results", [])
    documents = _document_policies({item["document_id"] for item in results})
    base.update({
        "candidate_count": len(results),
        "retrieval_mode": found.get("retrieval_mode", "fts"),
        "vector_available": bool(found.get("vector_available")),
        "vector_error_code": found.get("vector_error_code"),
        "policy_snapshot_sha256": _policy_snapshot(documents),
    })
    if not results:
        return _finish(base, started, "skip", "no_candidates", "low")

    location = str(provider.get("execution_location") or "unknown")
    policies = {row["transmission_policy"] for row in documents.values()}
    if location != "local":
        eligible = {
            doc_id for doc_id, row in documents.items()
            if row["transmission_policy"] == "remote_allowed"
        }
        base["eligible_count"] = sum(item["document_id"] in eligible for item in results)
        if "ask_each_time" in policies:
            return _finish(base, started, "ask", "transmission_consent_required", "high")
        if not eligible and "local_only" in policies:
            return _finish(base, started, "ask", "local_only_remote_provider", "high")
    else:
        base["eligible_count"] = len(results)
    if explicit_query:
        return _finish(base, started, "retrieve", "explicit_request", "high")
    term_strength = _term_strength(text, results)
    if term_strength >= 3:
        return _finish(base, started, "retrieve", "exact_term_hit", "high")
    if term_strength == 2:
        return _finish(base, started, "retrieve", "entity_hit", "medium")
    if any(item.get("match_type") in {"vector", "hybrid"} for item in results):
        return _finish(base, started, "retrieve", "semantic_candidate", "medium")
    return _finish(base, started, "retrieve", "lexical_candidate", "medium")


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


def _finish(base: dict, started: float, action: str, reason: str, confidence: str,
            *, status: str = "completed") -> dict:
    return {**base, "action": action, "reason_code": reason, "confidence_band": confidence,
            "latency_ms": max(0, round((time.perf_counter() - started) * 1000)), "status": status}


def _fingerprint(text: str) -> str:
    normalized = " ".join(str(text or "").split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _public(item: dict) -> dict:
    item["shadow"] = bool(item["shadow"])
    item["vector_available"] = bool(item["vector_available"])
    item["query_fingerprint"] = item.pop("query_sha256")[:12]
    item["policy_fingerprint"] = item.pop("policy_snapshot_sha256")[:12]
    return item
