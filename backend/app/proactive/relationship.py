"""EAP v0.2 关系意义判断：LLM 输出 9 种关系意义标签，程序映射为受限 delta。

按 spec 第 11 节"关系积温修订"：
- 普通聊天不再默认增加 bond（ordinary_exchange 的 bond_delta = 0）
- 明确感谢、可靠帮助、共同成功、边界修复 → 根据语义产生受限关系建议
- 程序执行：单轮限幅、同一事件幂等、来源证据校验、trust 变化条件限制、用户沉默不产生负变化

本模块独立于 affect/engine.py（已冻结 affect-v1.2），通过 episode_relationship_delta_suggestions
表提供新的关系 delta 机制。engine.py 的 fallback bond 增量仍然存在（affect-v1.2 冻结），
但新机制对 ordinary_exchange 不产生额外 bond delta。
"""

import json
import sqlite3
from dataclasses import dataclass
from typing import Optional

from .. import db
from ..affect import repository
from .protocols import RELATIONSHIP_MEANING_V1
from .run_ledger import make_idempotency_key

# 9 种关系意义标签（spec 第 11 节）
class RelationshipLabel:
    ORDINARY_EXCHANGE = "ordinary_exchange"            # 普通问答
    SHARED_APPRECIATION = "shared_appreciation"        # 明确感谢
    RELIABLE_HELP = "reliable_help"                    # 可靠帮助
    SHARED_SUCCESS = "shared_success"                  # 共同成功
    VULNERABLE_DISCLOSURE = "vulnerable_disclosure"    # 脆弱披露
    BOUNDARY_RESPECTED = "boundary_respected"          # 边界被尊重
    BOUNDARY_REPAIR = "boundary_repair"                # 边界修复
    REUNION = "reunion"                                # 重逢
    CONFLICT = "conflict"                              # 冲突

ALL_LABELS = (
    RelationshipLabel.ORDINARY_EXCHANGE,
    RelationshipLabel.SHARED_APPRECIATION,
    RelationshipLabel.RELIABLE_HELP,
    RelationshipLabel.SHARED_SUCCESS,
    RelationshipLabel.VULNERABLE_DISCLOSURE,
    RelationshipLabel.BOUNDARY_RESPECTED,
    RelationshipLabel.BOUNDARY_REPAIR,
    RelationshipLabel.REUNION,
    RelationshipLabel.CONFLICT,
)

# 标签到 delta 的映射（spec 第 11 节）
# 注意：ordinary_exchange 的 bond_delta = 0（普通问答不产生显著 bond 增量）
# conflict 是唯一可能产生负 trust 的标签（用户沉默不降低 bond/trust，但明确冲突可以）
LABEL_DELTAS = {
    RelationshipLabel.ORDINARY_EXCHANGE: {
        "bond_delta": 0.0, "familiarity_delta": 0.0005, "trust_delta": 0.0,
        "attachment_delta": 0.0, "rapport_delta": 0.0002,
    },
    RelationshipLabel.SHARED_APPRECIATION: {
        "bond_delta": 0.001, "familiarity_delta": 0.001, "trust_delta": 0.0,
        "attachment_delta": 0.0, "rapport_delta": 0.001,
    },
    RelationshipLabel.RELIABLE_HELP: {
        "bond_delta": 0.001, "familiarity_delta": 0.001, "trust_delta": 0.002,
        "attachment_delta": 0.0, "rapport_delta": 0.001,
    },
    RelationshipLabel.SHARED_SUCCESS: {
        "bond_delta": 0.002, "familiarity_delta": 0.001, "trust_delta": 0.001,
        "attachment_delta": 0.001, "rapport_delta": 0.002,
    },
    RelationshipLabel.VULNERABLE_DISCLOSURE: {
        "bond_delta": 0.001, "familiarity_delta": 0.002, "trust_delta": 0.0,
        "attachment_delta": 0.002, "rapport_delta": 0.001,
    },
    RelationshipLabel.BOUNDARY_RESPECTED: {
        "bond_delta": 0.0, "familiarity_delta": 0.0, "trust_delta": 0.002,
        "attachment_delta": 0.0, "rapport_delta": 0.0,
    },
    RelationshipLabel.BOUNDARY_REPAIR: {
        "bond_delta": 0.0, "familiarity_delta": 0.0, "trust_delta": 0.003,
        "attachment_delta": 0.0, "rapport_delta": 0.001,
    },
    RelationshipLabel.REUNION: {
        "bond_delta": 0.002, "familiarity_delta": 0.001, "trust_delta": 0.0,
        "attachment_delta": 0.002, "rapport_delta": 0.002,
    },
    RelationshipLabel.CONFLICT: {
        "bond_delta": 0.0, "familiarity_delta": 0.0, "trust_delta": -0.005,
        "attachment_delta": 0.0, "rapport_delta": -0.002,
    },
}

# 单轮限幅（参考 affect/observer.py RELATIONSHIP_CAPS，但扩展为 5 维）
SINGLE_TURN_CAPS = {
    "bond": (0.0, 0.003),        # 单轮 bond 增量上限 0.003
    "familiarity": (0.0, 0.003),
    "trust": (-0.01, 0.005),     # trust 允许小幅负值（conflict）
    "attachment": (0.0, 0.003),
    "rapport": (-0.005, 0.003),
}


@dataclass
class RelationshipDeltaSuggestion:
    """episode_relationship_delta_suggestions 表的记录。"""
    id: str
    session_id: str
    source_message_id: Optional[str]
    source_assistant_message_id: Optional[str]
    episode_id: Optional[str]
    relationship_label: str
    bond_delta: float
    familiarity_delta: float
    trust_delta: float
    attachment_delta: float
    rapport_delta: float
    cap_bond_applied: float
    cap_trust_applied: float
    source_revision: str
    source_hash: str
    evidence: list[dict]
    reason: str
    confidence: float
    idempotency_key: str
    status: str
    applied_at: Optional[float]
    revoked_at: Optional[float]
    revocation_reason: Optional[str]
    created_at: float


def _clamp(value: float, low: float, high: float) -> float:
    """限制 value 在 [low, high] 范围内。"""
    return max(low, min(high, value))


def process_relationship_delta(
    session_id: str,
    source_message_id: str,
    label: str,
    *,
    episode_id: Optional[str] = None,
    source_assistant_message_id: Optional[str] = None,
    evidence: Optional[list[dict]] = None,
    reason: str = "",
    confidence: float = 0.0,
) -> Optional[RelationshipDeltaSuggestion]:
    """处理关系意义标签，产生受限 delta 建议并落库。

    幂等：同一 source_message_id 只产生一条建议（UNIQUE 约束）
    单轮限幅：delta 受 SINGLE_TURN_CAPS 限制
    来源证据校验：source_message_id 必须存在（外键约束）

    返回建议记录；如果已存在相同 source_message_id 的建议，返回 None。
    """
    if label not in LABEL_DELTAS:
        return None

    source_revision, source_hash = source_identity(
        session_id, source_message_id, source_assistant_message_id,
    )
    deltas = LABEL_DELTAS[label]
    idempotency_key = make_idempotency_key(
        RELATIONSHIP_MEANING_V1, session_id, source_message_id, source_revision,
    )

    # 单轮限幅
    bond_clamped = _clamp(deltas["bond_delta"], *SINGLE_TURN_CAPS["bond"])
    familiarity_clamped = _clamp(deltas["familiarity_delta"], *SINGLE_TURN_CAPS["familiarity"])
    trust_clamped = _clamp(deltas["trust_delta"], *SINGLE_TURN_CAPS["trust"])
    attachment_clamped = _clamp(deltas["attachment_delta"], *SINGLE_TURN_CAPS["attachment"])
    rapport_clamped = _clamp(deltas["rapport_delta"], *SINGLE_TURN_CAPS["rapport"])

    now = db.now()
    record_id = db.new_id()

    conn = db.connect()
    try:
        # Same source revision is idempotent; a corrected source creates a new revision.
        existing = conn.execute(
            "SELECT id FROM episode_relationship_delta_suggestions "
            "WHERE source_message_id=? AND source_revision=?",
            (source_message_id, source_revision),
        ).fetchone()
        if existing:
            return None

        conn.execute(
            "INSERT INTO episode_relationship_delta_suggestions"
            " (id, session_id, source_message_id, source_assistant_message_id, episode_id, relationship_label,"
            "  bond_delta, familiarity_delta, trust_delta, attachment_delta, rapport_delta,"
            "  cap_bond_applied, cap_trust_applied, source_revision, source_hash,"
            "  evidence_json, reason, confidence, idempotency_key, status,"
            "  protocol_version, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " 'proposed', ?, ?, ?)",
            (
                record_id, session_id, source_message_id, source_assistant_message_id,
                episode_id, label,
                bond_clamped, familiarity_clamped, trust_clamped, attachment_clamped, rapport_clamped,
                bond_clamped, trust_clamped, source_revision, source_hash,
                json.dumps(evidence or [], ensure_ascii=False), reason[:240],
                _clamp(float(confidence), 0.0, 1.0), idempotency_key,
                RELATIONSHIP_MEANING_V1, now, now,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        existing = conn.execute(
            "SELECT * FROM episode_relationship_delta_suggestions "
            "WHERE idempotency_key=?", (idempotency_key,),
        ).fetchone()
        if existing:
            return None
        raise
    finally:
        conn.close()

    return RelationshipDeltaSuggestion(
        id=record_id, session_id=session_id, source_message_id=source_message_id,
        source_assistant_message_id=source_assistant_message_id,
        episode_id=episode_id, relationship_label=label,
        bond_delta=bond_clamped, familiarity_delta=familiarity_clamped,
        trust_delta=trust_clamped, attachment_delta=attachment_clamped,
        rapport_delta=rapport_clamped,
        cap_bond_applied=bond_clamped, cap_trust_applied=trust_clamped,
        source_revision=source_revision, source_hash=source_hash,
        evidence=evidence or [], reason=reason[:240], confidence=_clamp(float(confidence), 0, 1),
        idempotency_key=idempotency_key, status="proposed",
        applied_at=None, revoked_at=None, revocation_reason=None, created_at=now,
    )


def source_identity(
    session_id: str, source_message_id: str,
    source_assistant_message_id: Optional[str] = None,
) -> tuple[str, str]:
    _, _, messages = _source_messages(
        session_id, source_message_id, source_assistant_message_id,
    )
    from .run_ledger import compute_source_hash
    source_hash = compute_source_hash(messages)
    return source_hash, source_hash


def _source_messages(
    session_id: str, source_message_id: str,
    source_assistant_message_id: Optional[str] = None,
) -> tuple[str, str, list[dict]]:
    conn = db.connect()
    try:
        user = conn.execute(
            "SELECT id,role,content FROM messages WHERE id=? AND session_id=?",
            (source_message_id, session_id),
        ).fetchone()
        if not user or user["role"] != "user":
            raise ValueError("relationship source user message is unavailable")
        messages = [{"id": user["id"], "role": user["role"], "content": user["content"]}]
        if source_assistant_message_id:
            assistant = conn.execute(
                "SELECT id,role,content FROM messages WHERE id=? AND session_id=?",
                (source_assistant_message_id, session_id),
            ).fetchone()
            if not assistant or assistant["role"] != "assistant":
                raise ValueError("relationship source assistant message is unavailable")
            messages.append({
                "id": assistant["id"], "role": assistant["role"],
                "content": assistant["content"],
            })
    finally:
        conn.close()
    return user["content"], assistant["content"] if source_assistant_message_id else "", messages


def apply_suggestion(suggestion_id: str) -> RelationshipDeltaSuggestion:
    """Atomically verify the source, apply bond/trust, audit, and mark applied."""
    repository.get_snapshot(advance_time=False)
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM episode_relationship_delta_suggestions WHERE id=?", (suggestion_id,)
        ).fetchone()
        if not row:
            raise ValueError("relationship suggestion not found")
        if row["status"] == "applied":
            conn.rollback()
            return _row_to_suggestion(row)
        if row["status"] != "proposed":
            raise ValueError("only proposed relationship suggestions can be applied")
        try:
            revision, current_hash = source_identity(
                row["session_id"], row["source_message_id"],
                row["source_assistant_message_id"],
            )
        except ValueError:
            conn.rollback()
            revoke_suggestion(suggestion_id, "source_unavailable")
            return get_suggestion(suggestion_id)
        if revision != row["source_revision"] or current_hash != row["source_hash"]:
            conn.rollback()
            revoke_suggestion(suggestion_id, "source_changed")
            return get_suggestion(suggestion_id)
        user_text, assistant_text, _ = _source_messages(
            row["session_id"], row["source_message_id"], row["source_assistant_message_id"],
        )
        from .schemas import validate_relationship_meaning
        validate_relationship_meaning({
            "protocol_version": RELATIONSHIP_MEANING_V1,
            "label": row["relationship_label"],
            "evidence": json.loads(row["evidence_json"]),
            "confidence": row["confidence"],
            "reason": row["reason"] or "grounded relationship meaning",
        }, user_text=user_text, assistant_text=assistant_text)
        before_relation = conn.execute(
            "SELECT bond,trust FROM relationship_state WHERE id=1"
        ).fetchone()
        after, event_id = repository.apply_relationship_delta_in_transaction(
            conn, bond_delta=row["bond_delta"], trust_delta=row["trust_delta"],
            source="relationship_meaning", reason=(
                f"{RELATIONSHIP_MEANING_V1}:{row['relationship_label']}:{row['reason']}"
            ), source_session_id=row["session_id"],
            source_message_id=row["source_message_id"],
        )
        now = db.now()
        applied_bond = after["relationship"]["bond"] - before_relation["bond"]
        applied_trust = after["relationship"]["trust"] - before_relation["trust"]
        cursor = conn.execute(
            "UPDATE episode_relationship_delta_suggestions SET status='applied',"
            "cap_bond_applied=?,cap_trust_applied=?,applied_event_id=?,applied_at=?,updated_at=? "
            "WHERE id=? AND status='proposed'",
            (applied_bond, applied_trust, event_id, now, now, suggestion_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("relationship suggestion changed concurrently")
        conn.commit()
        return get_suggestion(suggestion_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def revoke_suggestion(suggestion_id: str, reason: str) -> RelationshipDeltaSuggestion:
    """Revoke with a compensating event; preserve later manual relationship corrections."""
    repository.get_snapshot(advance_time=False)
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM episode_relationship_delta_suggestions WHERE id=?", (suggestion_id,)
        ).fetchone()
        if not row:
            raise ValueError("relationship suggestion not found")
        if row["status"] == "revoked":
            conn.rollback()
            return _row_to_suggestion(row)
        preserve_manual = False
        if row["status"] == "applied":
            manual_rows = conn.execute(
                "SELECT delta_json FROM affect_events WHERE created_at>? "
                "AND source IN ('user','developer')", (row["applied_at"],),
            ).fetchall()
            preserve_manual = any(
                abs(float(json.loads(item["delta_json"])["relationship"].get("bond", 0))) > 1e-12
                or abs(float(json.loads(item["delta_json"])["relationship"].get("trust", 0))) > 1e-12
                for item in manual_rows
            )
        audit_reason = f"{RELATIONSHIP_MEANING_V1}:revoke:{reason}"
        if row["status"] == "applied" and not preserve_manual:
            _, event_id = repository.apply_relationship_delta_in_transaction(
                conn, bond_delta=-row["cap_bond_applied"],
                trust_delta=-row["cap_trust_applied"],
                source="relationship_meaning_revoke", reason=audit_reason,
                source_session_id=row["session_id"], source_message_id=row["source_message_id"],
            )
        else:
            if preserve_manual:
                audit_reason += ":manual_change_preserved"
            event_id = repository.record_relationship_audit_in_transaction(
                conn, source="relationship_meaning_revoke", reason=audit_reason,
                source_session_id=row["session_id"], source_message_id=row["source_message_id"],
            )
        now = db.now()
        cursor = conn.execute(
            "UPDATE episode_relationship_delta_suggestions SET status='revoked',"
            "revocation_event_id=?,revoked_at=?,revocation_reason=?,updated_at=? "
            "WHERE id=? AND status=?",
            (event_id, now, audit_reason, now, suggestion_id, row["status"]),
        )
        if cursor.rowcount != 1:
            raise ValueError("relationship suggestion changed concurrently")
        conn.commit()
        return get_suggestion(suggestion_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_suggestion(suggestion_id: str) -> RelationshipDeltaSuggestion:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM episode_relationship_delta_suggestions WHERE id=?", (suggestion_id,)
        ).fetchone()
        if not row:
            raise ValueError("relationship suggestion not found")
        return _row_to_suggestion(row)
    finally:
        conn.close()


def revoke_invalidated_suggestions() -> int:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM episode_relationship_delta_suggestions "
            "WHERE status IN ('proposed','applied') AND source_revision!=''"
        ).fetchall()
    finally:
        conn.close()
    count = 0
    for row in rows:
        try:
            revision, current_hash = source_identity(
                row["session_id"], row["source_message_id"], row["source_assistant_message_id"],
            )
            valid = revision == row["source_revision"] and current_hash == row["source_hash"]
        except (TypeError, ValueError):
            valid = False
        if not valid:
            revoke_suggestion(row["id"], "source_invalidated")
            count += 1
    return count


def get_suggestion_by_source_message(
    source_message_id: str, source_revision: Optional[str] = None,
) -> Optional[RelationshipDeltaSuggestion]:
    """按 source_message_id 查询建议（幂等检查用）。"""
    conn = db.connect()
    try:
        if source_revision is None:
            row = conn.execute(
                "SELECT * FROM episode_relationship_delta_suggestions "
                "WHERE source_message_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                (source_message_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM episode_relationship_delta_suggestions "
                "WHERE source_message_id=? AND source_revision=? LIMIT 1",
                (source_message_id, source_revision),
            ).fetchone()
        if not row:
            return None
        return _row_to_suggestion(row)
    finally:
        conn.close()


def _row_to_suggestion(row) -> RelationshipDeltaSuggestion:
    return RelationshipDeltaSuggestion(
        id=row["id"], session_id=row["session_id"],
        source_message_id=row["source_message_id"],
        source_assistant_message_id=row["source_assistant_message_id"],
        episode_id=row["episode_id"],
        relationship_label=row["relationship_label"],
        bond_delta=row["bond_delta"],
        familiarity_delta=row["familiarity_delta"],
        trust_delta=row["trust_delta"],
        attachment_delta=row["attachment_delta"],
        rapport_delta=row["rapport_delta"],
        cap_bond_applied=row["cap_bond_applied"],
        cap_trust_applied=row["cap_trust_applied"],
        source_revision=row["source_revision"], source_hash=row["source_hash"],
        evidence=json.loads(row["evidence_json"]), reason=row["reason"],
        confidence=row["confidence"],
        idempotency_key=row["idempotency_key"],
        status=row["status"],
        applied_at=row["applied_at"],
        revoked_at=row["revoked_at"], revocation_reason=row["revocation_reason"],
        created_at=row["created_at"],
    )
