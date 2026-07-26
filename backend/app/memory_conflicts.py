"""小集合、确定性的 Fragment 冲突预筛；只建关系，不改正文或状态。"""
from __future__ import annotations

import json
import re

from . import db

DETECTOR_VERSION = "fragment-conflict-v1"
MUTABLE_KINDS = frozenset({"fact", "preference", "plan", "relationship", "observation"})
NEGATIONS = ("不再", "并不是", "不是", "没有", "并非", "不", "没")
POSSIBLE_THRESHOLD = 0.42


def scan_conflicts(*, limit: int = 50) -> dict:
    candidates = _candidate_pairs(max(1, min(int(limit), 100)))
    created = superseded = possible = 0
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for pair in candidates:
            projection = classify_projection(pair["source_content"], pair["target_content"])
            relation_type = projection["relation_type"]
            confidence = projection["confidence"]
            rule = projection["reason_code"]
            if not relation_type:
                continue
            now = db.now()
            relation_id = db.new_id()
            cursor = conn.execute(
                "INSERT OR IGNORE INTO memory_fragment_relations("
                "id,source_fragment_id,target_fragment_id,entity_id,relation_type,status,"
                "confidence,rule_code,detector_version,created_at,updated_at)"
                " VALUES(?,?,?,?,?,'active',?,?,?,?,?)",
                (relation_id, pair["source_fragment_id"], pair["target_fragment_id"],
                 pair["entity_id"], relation_type, confidence, rule, DETECTOR_VERSION, now, now),
            )
            if cursor.rowcount <= 0:
                continue
            _event(
                conn, relation_id, "detected", None, "active", rule, "archivist",
                {"relation_type": relation_type, "confidence": confidence}, now,
            )
            created += 1
            superseded += int(relation_type == "superseded")
            possible += int(relation_type == "possible_conflict")
        conn.commit()
        return {
            "candidate_count": len(candidates), "created_count": created,
            "superseded_count": superseded, "possible_conflict_count": possible,
            "model_calls_used": 0,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_relations(*, status: str | None = "active", limit: int = 100) -> list[dict]:
    if status is not None and status not in {"active", "resolved", "dismissed"}:
        raise ValueError("冲突关系状态无效")
    conn = db.connect()
    try:
        where, params = (" WHERE r.status=?", [status]) if status else ("", [])
        rows = conn.execute(
            "SELECT r.*,sf.content AS source_content,tf.content AS target_content,"
            " e.name AS entity_name FROM memory_fragment_relations r"
            " JOIN memory_fragments sf ON sf.id=r.source_fragment_id"
            " JOIN memory_fragments tf ON tf.id=r.target_fragment_id"
            " JOIN memory_entities e ON e.id=r.entity_id" + where +
            " ORDER BY r.updated_at DESC,r.id LIMIT ?",
            (*params, max(1, min(int(limit), 200))),
        ).fetchall()
        return [_relation_row(conn, row) for row in rows]
    finally:
        conn.close()


def relations_for_fragment(fragment_id: str) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT r.*,sf.content AS source_content,tf.content AS target_content,"
            " e.name AS entity_name FROM memory_fragment_relations r"
            " JOIN memory_fragments sf ON sf.id=r.source_fragment_id"
            " JOIN memory_fragments tf ON tf.id=r.target_fragment_id"
            " JOIN memory_entities e ON e.id=r.entity_id"
            " WHERE r.source_fragment_id=? OR r.target_fragment_id=?"
            " ORDER BY r.updated_at DESC,r.id", (fragment_id, fragment_id),
        ).fetchall()
        return [_relation_row(conn, row) for row in rows]
    finally:
        conn.close()


def set_status(relation_id: str, status: str, *, reason: str) -> dict | None:
    if status not in {"resolved", "dismissed"}:
        raise ValueError("只能解决或忽略冲突关系")
    if not reason.strip():
        raise ValueError("处理冲突关系必须说明原因")
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM memory_fragment_relations WHERE id=?", (relation_id,)
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        if row["status"] != status:
            now = db.now()
            conn.execute(
                "UPDATE memory_fragment_relations SET status=?,updated_at=? WHERE id=?",
                (status, now, relation_id),
            )
            _event(conn, relation_id, "status_changed", row["status"], status,
                   reason.strip(), "user", {}, now)
        conn.commit()
        updated = conn.execute(
            "SELECT r.*,sf.content AS source_content,tf.content AS target_content,"
            " e.name AS entity_name FROM memory_fragment_relations r"
            " JOIN memory_fragments sf ON sf.id=r.source_fragment_id"
            " JOIN memory_fragments tf ON tf.id=r.target_fragment_id"
            " JOIN memory_entities e ON e.id=r.entity_id WHERE r.id=?", (relation_id,),
        ).fetchone()
        return _relation_row(conn, updated)
    finally:
        conn.close()


def _candidate_pairs(limit: int) -> list[dict]:
    kinds = ",".join("?" for _ in MUTABLE_KINDS)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT DISTINCT fe1.entity_id,f1.id AS left_id,f1.content AS left_content,"
            " f1.created_at AS left_created,f2.id AS right_id,f2.content AS right_content,"
            " f2.created_at AS right_created FROM memory_fragment_entities fe1"
            " JOIN memory_fragment_entities fe2 ON fe2.entity_id=fe1.entity_id"
            "  AND fe1.fragment_id<fe2.fragment_id"
            " JOIN memory_entities e ON e.id=fe1.entity_id AND e.status='active'"
            " JOIN memory_fragments f1 ON f1.id=fe1.fragment_id"
            " JOIN memory_fragments f2 ON f2.id=fe2.fragment_id"
            " WHERE f1.status='active' AND f2.status='active'"
            " AND f1.enabled=1 AND f2.enabled=1"
            " AND f1.sensitivity='normal' AND f2.sensitivity='normal'"
            " AND f1.scope=f2.scope AND f1.kind=f2.kind"
            f" AND f1.kind IN ({kinds})"
            " AND NOT EXISTS (SELECT 1 FROM memory_fragment_relations r"
            " WHERE r.source_fragment_id=CASE WHEN f1.created_at<f2.created_at"
            " OR (f1.created_at=f2.created_at AND f1.id<f2.id) THEN f1.id ELSE f2.id END"
            " AND r.target_fragment_id=CASE WHEN f1.created_at<f2.created_at"
            " OR (f1.created_at=f2.created_at AND f1.id<f2.id) THEN f2.id ELSE f1.id END"
            " AND r.entity_id=fe1.entity_id)"
            " ORDER BY CASE WHEN f1.created_at<f2.created_at THEN f1.created_at"
            " ELSE f2.created_at END,f1.id,f2.id LIMIT ?",
            (*sorted(MUTABLE_KINDS), limit),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            left_first = (item["left_created"], item["left_id"]) <= (
                item["right_created"], item["right_id"]
            )
            result.append({
                "entity_id": item["entity_id"],
                "source_fragment_id": item["left_id"] if left_first else item["right_id"],
                "source_content": item["left_content"] if left_first else item["right_content"],
                "target_fragment_id": item["right_id"] if left_first else item["left_id"],
                "target_content": item["right_content"] if left_first else item["left_content"],
            })
        return result
    finally:
        conn.close()


def _classify(left: str, right: str) -> tuple[str | None, float, str]:
    projection = classify_projection(left, right)
    return projection["relation_type"], projection["confidence"], projection["reason_code"]


def classify_projection(left: str, right: str) -> dict:
    a, b = _normalize(left), _normalize(right)
    if not a or not b or a == b:
        return {"relation_type": None, "confidence": 0.0, "reason_code": ""}
    stripped_a, neg_a = _without_negation(a)
    stripped_b, neg_b = _without_negation(b)
    if stripped_a == stripped_b and neg_a != neg_b:
        return {
            "relation_type": "superseded",
            "confidence": 0.98,
            "reason_code": "explicit_negation_newer_wins",
        }
    similarity = _similarity(a, b)
    if similarity >= POSSIBLE_THRESHOLD:
        return {
            "relation_type": "possible_conflict",
            "confidence": round(similarity, 6),
            "reason_code": "shared_entity_scope_kind_similarity",
        }
    return {"relation_type": None, "confidence": 0.0, "reason_code": ""}


def _normalize(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", str(value).casefold())


def _without_negation(value: str) -> tuple[str, bool]:
    result, found = value, False
    for token in NEGATIONS:
        if token in result:
            result, found = result.replace(token, ""), True
    return result, found


def _similarity(left: str, right: str) -> float:
    a, b = _grams(left), _grams(right)
    return len(a & b) / len(a | b) if a and b else 0.0


def _grams(value: str) -> set[str]:
    if len(value) < 3:
        return {value} if value else set()
    return {value[index:index + 3] for index in range(len(value) - 2)}


def _event(conn, relation_id: str, action: str, before: str | None, after: str,
           reason: str, source: str, metadata: dict, now: float) -> None:
    conn.execute(
        "INSERT INTO memory_fragment_relation_events("
        "id,relation_id,action,before_status,after_status,reason_code,source,detector_version,"
        "metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (db.new_id(), relation_id, action, before, after, reason[:240], source,
         DETECTOR_VERSION, json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), now),
    )


def _relation_row(conn, row) -> dict:
    result = dict(row)
    events = conn.execute(
        "SELECT * FROM memory_fragment_relation_events WHERE relation_id=?"
        " ORDER BY created_at,id", (result["id"],),
    ).fetchall()
    result["events"] = []
    for event in events:
        item = dict(event)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        result["events"].append(item)
    return result
