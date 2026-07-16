"""Archivist E.2：真实召回计数、纯保留评分与只读保护识别。"""
from __future__ import annotations

import json
import math
import re
from typing import Iterable

from . import db

RETENTION_POLICY_VERSION = "fragment-retention-v1"
RECALL_POLICY_VERSION = "memory-recall-accounting-v1"
RECENCY_HORIZON_DAYS = 180.0
RECALL_SATURATION_COUNT = 20
MAX_DUPLICATE_PENALTY = 0.25
ACTIVE_TO_COOLING_DAYS = 14
COOLING_TO_FROZEN_DAYS = 30
COOLING_SCORE_THRESHOLD = 0.45
FROZEN_SCORE_THRESHOLD = 0.30
REACTIVATION_SCORE_THRESHOLD = 0.50


class ArchivistLifecycleError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


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
            snapshot = _load_snapshots(conn, [fragment_id]).get(fragment_id)
            if snapshot and snapshot["status"] in {"cooling", "frozen"} and item.get(
                "_reactivation_candidate"
            ):
                hypothetical = {
                    **snapshot,
                    "recall_count": int(snapshot.get("recall_count") or 0) + 1,
                    "last_recalled_at": at,
                }
                scored = _score_snapshot(hypothetical, now=at)
                if scored["score"] >= REACTIVATION_SCORE_THRESHOLD:
                    _transition_locked(
                        conn, snapshot, "active", scored=scored,
                        reason_code="reactivated_by_recall", source="recall", now=at,
                    )
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
    conn = db.connect()
    try:
        by_id = _load_snapshots(conn, ordered)
    finally:
        conn.close()
    at = float(now if now is not None else db.now())
    penalties = duplicate_penalties or {}
    results = []
    for fragment_id in ordered:
        fragment = by_id.get(fragment_id)
        if not fragment:
            continue
        result = _score_snapshot(
            fragment, now=at, duplicate_penalty=penalties.get(fragment_id, 0.0)
        )
        results.append({
            "fragment_id": fragment_id,
            **result,
            "protection_reasons": protection_reasons(fragment),
            "dependency_flags": {
                "in_episode": bool(fragment["in_episode"]),
                "in_active_episode": bool(fragment["in_active_episode"]),
                "in_active_saga": bool(fragment["in_active_saga"]),
                "is_active_saga_anchor": bool(fragment["is_active_saga_anchor"]),
            },
        })
    return results


def assess_and_transition(fragment_id: str, *, now: float | None = None) -> dict:
    """对一个 Fragment 最多执行一步自动降温；绝不产生 tombstone。"""
    at = float(now if now is not None else db.now())
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        snapshot = _load_snapshots(conn, [fragment_id]).get(fragment_id)
        if not snapshot:
            raise ArchivistLifecycleError("fragment_missing", "记忆不存在")
        scored = _score_snapshot(snapshot, now=at)
        blocked = protection_reasons(snapshot)
        target = None
        reason = "no_transition"
        reference = float(snapshot.get("last_recalled_at") or snapshot["created_at"])
        if snapshot["status"] == "active":
            if not snapshot["enabled"]:
                reason = "disabled_fragment"
            elif at - reference < ACTIVE_TO_COOLING_DAYS * 86_400:
                reason = "cooling_minimum_age"
            elif scored["score"] >= COOLING_SCORE_THRESHOLD:
                reason = "retention_above_cooling"
            elif blocked:
                reason = "protected_fragment"
            elif snapshot["in_active_episode"]:
                reason = "active_episode_source"
            else:
                target, reason = "cooling", "retention_below_cooling"
        elif snapshot["status"] == "cooling":
            cooling_since = float(snapshot.get("cooling_since") or snapshot["updated_at"])
            if at - cooling_since < COOLING_TO_FROZEN_DAYS * 86_400:
                reason = "frozen_minimum_age"
            elif scored["score"] >= FROZEN_SCORE_THRESHOLD:
                reason = "retention_above_frozen"
            elif blocked:
                reason = "protected_fragment"
            elif snapshot["in_active_episode"]:
                reason = "active_episode_source"
            elif float(snapshot["updated_at"]) > cooling_since:
                reason = "modified_during_cooling"
            else:
                target, reason = "frozen", "retention_below_frozen"
        if target:
            changed = _transition_locked(
                conn, snapshot, target, scored=scored, reason_code=reason,
                source="archivist", now=at,
            )
            conn.commit()
            return {"changed": True, "fragment": changed, "evaluation": scored,
                    "protection_reasons": blocked, "reason_code": reason}
        conn.commit()
        return {"changed": False, "fragment": snapshot, "evaluation": scored,
                "protection_reasons": blocked, "reason_code": reason}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reactivate_fragment(
    fragment_id: str, *, trigger: str, reason: str = "",
    expected_revision: int | None = None, now: float | None = None,
) -> dict:
    reason_codes = {
        "recall": "reactivated_by_recall",
        "new_evidence": "reactivated_by_new_evidence",
        "user": "reactivated_by_user",
    }
    if trigger not in reason_codes:
        raise ArchivistLifecycleError("reactivation_trigger_invalid", "非法的恢复来源")
    at = float(now if now is not None else db.now())
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        snapshot = _load_snapshots(conn, [fragment_id]).get(fragment_id)
        if not snapshot:
            raise ArchivistLifecycleError("fragment_missing", "记忆不存在")
        if expected_revision is not None and snapshot["lifecycle_revision"] != expected_revision:
            raise ArchivistLifecycleError("revision_conflict", "记忆状态已变化，请刷新后重试")
        if snapshot["status"] == "tombstone":
            raise ArchivistLifecycleError("tombstone_terminal", "已删除记忆不可恢复")
        if snapshot["status"] == "active":
            conn.commit()
            return snapshot
        scored = _score_snapshot(snapshot, now=at)
        if trigger == "recall" and scored["score"] < REACTIVATION_SCORE_THRESHOLD:
            raise ArchivistLifecycleError("reactivation_score_low", "当前相关性不足以自动恢复")
        changed = _transition_locked(
            conn, snapshot, "active", scored=scored,
            reason_code=reason_codes[trigger], source=trigger,
            now=at, metadata={"reason": reason.strip()} if reason.strip() else None,
        )
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def find_reactivation_candidates(query: str, *, limit: int = 3, now: float | None = None) -> list[dict]:
    """从非 active 正文中做小规模强匹配；只返回候选，不在这里改变状态。"""
    terms = _strong_terms(query)
    if not terms:
        return []
    clauses = " OR ".join("(content LIKE ? OR tags LIKE ?)" for _ in terms)
    params = [value for term in terms for value in (f"%{term}%", f"%{term}%")]
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM memory_fragments WHERE status IN ('cooling','frozen')"
            " AND enabled=1 AND sensitivity='normal' AND (" + clauses + ")"
            " ORDER BY importance DESC,updated_at DESC LIMIT ?",
            (*params, max(1, min(limit * 8, 40))),
        ).fetchall()
        snapshots = _load_snapshots(conn, [row["id"] for row in rows])
    finally:
        conn.close()
    at = float(now if now is not None else db.now())
    result = []
    for row in rows:
        item = snapshots[row["id"]]
        if not _is_strong_match(query, str(item.get("content") or ""), terms):
            continue
        hypothetical = {
            **item,
            "recall_count": int(item.get("recall_count") or 0) + 1,
            "last_recalled_at": at,
        }
        scored = _score_snapshot(hypothetical, now=at)
        if scored["score"] < REACTIVATION_SCORE_THRESHOLD:
            continue
        result.append({**item, "_reactivation_candidate": True,
                       "_reactivation_score": scored["score"]})
        if len(result) >= limit:
            break
    return result


def list_lifecycle_events(fragment_id: str) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM memory_lifecycle_events WHERE fragment_id=?"
            " ORDER BY revision,id", (fragment_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["score_components"] = json.loads(item.pop("score_components_json"))
            result.append(item)
        return result
    finally:
        conn.close()


def _load_snapshots(conn, fragment_ids: list[str]) -> dict[str, dict]:
    if not fragment_ids:
        return {}
    marks = ",".join("?" for _ in fragment_ids)
    rows = conn.execute(
        "SELECT f.*,"
        " EXISTS(SELECT 1 FROM memory_episode_fragments ef"
        " JOIN memory_episodes e ON e.id=ef.episode_id AND e.status!='tombstone'"
        " WHERE ef.fragment_id=f.id) AS in_episode,"
        " EXISTS(SELECT 1 FROM memory_episode_fragments ef"
        " JOIN memory_episodes e ON e.id=ef.episode_id AND e.status='active'"
        " WHERE ef.fragment_id=f.id) AS in_active_episode,"
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
        f" FROM memory_fragments f WHERE f.id IN ({marks})", fragment_ids,
    ).fetchall()
    return {row["id"]: dict(row) for row in rows}


def _score_snapshot(snapshot: dict, *, now: float, duplicate_penalty: float = 0.0) -> dict:
    return retention_score(
        snapshot, now=now,
        relationship=relationship_significance(
            snapshot, in_episode=bool(snapshot.get("in_episode"))
        ),
        in_active_saga=bool(snapshot.get("in_active_saga")),
        duplicate_penalty=duplicate_penalty,
    )


def _transition_locked(
    conn, snapshot: dict, target: str, *, scored: dict, reason_code: str,
    source: str, now: float, metadata: dict | None = None,
) -> dict:
    current = snapshot["status"]
    if target == "tombstone" or current == "tombstone":
        raise ArchivistLifecycleError("tombstone_forbidden", "Archivist 生命周期不能产生或恢复墓碑")
    allowed = {("active", "cooling"), ("cooling", "frozen"),
               ("cooling", "active"), ("frozen", "active")}
    if (current, target) not in allowed:
        raise ArchivistLifecycleError("transition_invalid", "非法的 Fragment 生命周期转换")
    revision = int(snapshot["lifecycle_revision"]) + 1
    if current == "cooling" and target == "frozen":
        _remove_fts_locked(conn, snapshot)
    elif current == "frozen" and target == "active":
        _restore_fts_locked(conn, snapshot)
    cooling_since = now if target == "cooling" else (
        None if target == "active" else snapshot.get("cooling_since")
    )
    frozen_at = now if target == "frozen" else (
        None if target == "active" else snapshot.get("frozen_at")
    )
    fts_indexed = 0 if target == "frozen" else (
        1 if target == "active" else int(snapshot.get("fts_indexed", 1))
    )
    conn.execute(
        "UPDATE memory_fragments SET status=?,cooling_since=?,frozen_at=?,"
        "lifecycle_policy_version=?,lifecycle_revision=?,fts_indexed=?,updated_at=?"
        " WHERE id=? AND lifecycle_revision=?",
        (target, cooling_since, frozen_at, RETENTION_POLICY_VERSION, revision, fts_indexed, now,
         snapshot["id"], snapshot["lifecycle_revision"]),
    )
    if conn.execute("SELECT changes() changed").fetchone()["changed"] != 1:
        raise ArchivistLifecycleError("revision_conflict", "记忆状态已变化，请刷新后重试")
    components = {**scored["components"], "contributions": scored["contributions"]}
    if metadata:
        components["metadata"] = metadata
    conn.execute(
        "INSERT INTO memory_lifecycle_events("
        "id,fragment_id,revision,from_status,to_status,retention_score,"
        "score_components_json,reason_code,source,policy_version,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            db.new_id(), snapshot["id"], revision, current, target, scored["score"],
            json.dumps(components, ensure_ascii=False, separators=(",", ":")),
            reason_code, source, RETENTION_POLICY_VERSION, now,
        ),
    )
    return dict(conn.execute(
        "SELECT * FROM memory_fragments WHERE id=?", (snapshot["id"],)
    ).fetchone())


def _remove_fts_locked(conn, snapshot: dict) -> None:
    conn.execute(
        "INSERT INTO memory_fragments_fts(memory_fragments_fts,rowid,content,tags)"
        " SELECT 'delete',rowid,content,tags FROM memory_fragments WHERE id=?",
        (snapshot["id"],),
    )


def _restore_fts_locked(conn, snapshot: dict) -> None:
    latest = conn.execute(
        "SELECT to_status FROM memory_lifecycle_events WHERE fragment_id=?"
        " ORDER BY revision DESC LIMIT 1", (snapshot["id"],),
    ).fetchone()
    # 旧库 frozen 没有移除事件，索引仍存在；只有由本服务冻结的行需要重建。
    if latest and latest["to_status"] == "frozen":
        conn.execute(
            "INSERT INTO memory_fragments_fts(rowid,content,tags)"
            " SELECT rowid,content,tags FROM memory_fragments WHERE id=?",
            (snapshot["id"],),
        )


def _strong_terms(query: str) -> list[str]:
    return list(dict.fromkeys(
        term.lower() for term in re.findall(r"[\u4e00-\u9fff]{3,}|[A-Za-z0-9_-]{3,}", query)
    ))[:8]


def _is_strong_match(query: str, content: str, terms: list[str]) -> bool:
    normalized_query = re.sub(r"\s+", "", query).lower()
    normalized_content = re.sub(r"\s+", "", content).lower()
    if len(normalized_query) >= 3 and normalized_query in normalized_content:
        return True
    matched = sum(1 for term in terms if term in normalized_content)
    return matched >= min(2, len(terms))


def _unit(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
