"""Archivist E.2：真实召回计数、纯保留评分与只读保护识别。"""
from __future__ import annotations

import math
from typing import Iterable

from . import db

RETENTION_POLICY_VERSION = "fragment-retention-v1"
RECALL_POLICY_VERSION = "memory-recall-accounting-v1"
RECENCY_HORIZON_DAYS = 180.0
RECALL_SATURATION_COUNT = 20
MAX_DUPLICATE_PENALTY = 0.25


def recall_context_key(session_id: str, user_message_id: str) -> str:
    """同一用户消息的首次生成、流式重试和 regenerate 共用一个轮次键。"""
    session = str(session_id or "").strip()
    message = str(user_message_id or "").strip()
    if not session or not message:
        raise ValueError("召回轮次必须包含会话和用户消息 ID")
    return f"chat:{session}:{message}"


def estimate_tokens(text: str) -> int:
    """无 tokenizer 时的保守本地估算；只用于审计，不参与模型预算。"""
    value = str(text or "")
    return max(1, math.ceil(len(value) / 2)) if value else 0


def record_injected_memories(
    memories: Iterable[dict], *, context_key: str, source_session_id: str | None,
    injected_at: float | None = None,
) -> list[str]:
    """仅为已经装入模型上下文的 Fragment 原子记账；重复轮次不重复计数。"""
    key = str(context_key or "").strip()
    if not key or len(key) > 240:
        raise ValueError("召回轮次键无效")
    unique = {str(item.get("id") or ""): item for item in memories if item.get("id")}
    if not unique:
        return []
    at = float(injected_at if injected_at is not None else db.now())
    inserted: list[str] = []
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for fragment_id, item in unique.items():
            cursor = conn.execute(
                "INSERT OR IGNORE INTO memory_recall_events("
                "id,fragment_id,context_key,source_session_id,token_estimate,policy_version,"
                "injected_at) SELECT ?,f.id,?,?,?,?,? FROM memory_fragments f"
                " WHERE f.id=? AND f.status='active' AND f.enabled=1"
                " AND f.sensitivity='normal'",
                (
                    db.new_id(), key, source_session_id,
                    estimate_tokens(str(item.get("content") or "")),
                    RECALL_POLICY_VERSION, at, fragment_id,
                ),
            )
            if cursor.rowcount <= 0:
                continue
            conn.execute(
                "UPDATE memory_fragments SET recall_count=recall_count+1,"
                "last_recalled_at=CASE WHEN last_recalled_at IS NULL OR last_recalled_at<?"
                " THEN ? ELSE last_recalled_at END WHERE id=?",
                (at, at, fragment_id),
            )
            inserted.append(fragment_id)
        conn.commit()
        return inserted
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def recall_strength(recall_count: int) -> float:
    count = max(0, int(recall_count or 0))
    return _unit(math.log1p(min(count, RECALL_SATURATION_COUNT))
                 / math.log1p(RECALL_SATURATION_COUNT))


def recency_strength(
    *, last_recalled_at: float | None, created_at: float, now: float,
) -> float:
    basis = float(last_recalled_at if last_recalled_at is not None else created_at)
    age_days = max(0.0, float(now) - basis) / 86_400.0
    return _unit(1.0 - age_days / RECENCY_HORIZON_DAYS)


def relationship_significance(fragment: dict, *, in_episode: bool = False) -> float:
    """只读取持久 scope/kind 与正式 Episode，不读取即时情绪轴。"""
    scope = {
        "relationship": 1.0, "self": 0.7, "user": 0.6, "world": 0.2,
    }.get(str(fragment.get("scope") or "world"), 0.2)
    kind = {
        "relationship": 1.0, "experience": 0.8, "correction": 0.75,
        "preference": 0.65, "plan": 0.55, "fact": 0.4, "observation": 0.25,
    }.get(str(fragment.get("kind") or "fact"), 0.25)
    return _unit(max(scope, kind, 0.7 if in_episode else 0.0))


def retention_score(
    fragment: dict, *, now: float, relationship: float = 0.0,
    in_active_saga: bool = False, duplicate_penalty: float = 0.0,
) -> dict:
    """无数据库副作用的 fragment-retention-v1 评分。"""
    components = {
        "importance": _unit(fragment.get("importance", 0.5)),
        "recall_strength": recall_strength(int(fragment.get("recall_count") or 0)),
        "recency": recency_strength(
            last_recalled_at=fragment.get("last_recalled_at"),
            created_at=float(fragment.get("created_at") or now), now=now,
        ),
        "relationship_significance": _unit(relationship),
        "active_saga_bonus": 1.0 if in_active_saga else 0.0,
        "confidence": _unit(fragment.get("confidence", 0.0)),
        "duplicate_penalty": min(MAX_DUPLICATE_PENALTY, _unit(duplicate_penalty)),
    }
    contributions = {
        "importance": components["importance"] * 0.35,
        "recall_strength": components["recall_strength"] * 0.20,
        "recency": components["recency"] * 0.15,
        "relationship_significance": components["relationship_significance"] * 0.15,
        "active_saga_bonus": components["active_saga_bonus"] * 0.10,
        "confidence": components["confidence"] * 0.05,
        "duplicate_penalty": -components["duplicate_penalty"],
    }
    score = _unit(sum(contributions.values()))
    return {
        "policy_version": RETENTION_POLICY_VERSION,
        "score": round(score, 6),
        "components": {key: round(value, 6) for key, value in components.items()},
        "contributions": {key: round(value, 6) for key, value in contributions.items()},
    }


def protection_reasons(fragment: dict) -> list[str]:
    """返回保护原因；保护只阻止自动降温，不阻止用户删除或纠错。"""
    reasons: list[str] = []
    kind = str(fragment.get("kind") or "fact")
    importance = _unit(fragment.get("importance", 0.0))
    if fragment.get("layer") == "L0":
        reasons.append("core_memory")
    if kind in {"preference", "relationship"} and importance >= 0.85:
        reasons.append("stable_boundary")
    if kind == "correction":
        reasons.append("current_correction")
    if bool(fragment.get("is_active_saga_anchor")):
        reasons.append("active_saga_anchor")
    if kind == "plan" and fragment.get("status") == "active":
        reasons.append("unfinished_plan")
    return reasons


def evaluate_fragments(
    fragment_ids: Iterable[str], *, now: float | None = None,
    duplicate_penalties: dict[str, float] | None = None,
) -> list[dict]:
    """用一个 SQL 快照读取 Fragment→Episode→Saga 保护依赖，再调用纯评分函数。"""
    ordered = list(dict.fromkeys(str(value) for value in fragment_ids if value))
    if not ordered:
        return []
    marks = ",".join("?" for _ in ordered)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT f.*,"
            " EXISTS(SELECT 1 FROM memory_episode_fragments ef"
            " JOIN memory_episodes e ON e.id=ef.episode_id AND e.status!='tombstone'"
            " WHERE ef.fragment_id=f.id) AS in_episode,"
            " EXISTS(SELECT 1 FROM memory_episode_fragments ef"
            " JOIN memory_saga_episodes se ON se.episode_id=ef.episode_id"
            "  AND se.removed_at IS NULL"
            " JOIN memory_sagas s ON s.id=se.saga_id AND s.status='active'"
            " WHERE ef.fragment_id=f.id) AS in_active_saga,"
            " EXISTS(SELECT 1 FROM memory_episode_fragments ef"
            " JOIN memory_saga_episodes se ON se.episode_id=ef.episode_id"
            "  AND se.removed_at IS NULL AND se.role='anchor'"
            " JOIN memory_sagas s ON s.id=se.saga_id AND s.status='active'"
            " WHERE ef.fragment_id=f.id) AS is_active_saga_anchor"
            f" FROM memory_fragments f WHERE f.id IN ({marks})",
            ordered,
        ).fetchall()
    finally:
        conn.close()
    by_id = {row["id"]: dict(row) for row in rows}
    at = float(now if now is not None else db.now())
    penalties = duplicate_penalties or {}
    results = []
    for fragment_id in ordered:
        fragment = by_id.get(fragment_id)
        if not fragment:
            continue
        relationship = relationship_significance(
            fragment, in_episode=bool(fragment["in_episode"])
        )
        result = retention_score(
            fragment, now=at, relationship=relationship,
            in_active_saga=bool(fragment["in_active_saga"]),
            duplicate_penalty=penalties.get(fragment_id, 0.0),
        )
        results.append({
            "fragment_id": fragment_id,
            **result,
            "protection_reasons": protection_reasons(fragment),
            "dependency_flags": {
                "in_episode": bool(fragment["in_episode"]),
                "in_active_saga": bool(fragment["in_active_saga"]),
                "is_active_saga_anchor": bool(fragment["is_active_saga_anchor"]),
            },
        })
    return results


def _unit(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
