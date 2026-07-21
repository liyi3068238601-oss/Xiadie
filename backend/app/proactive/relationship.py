"""EAP v0.2 关系意义判断：LLM 输出 9 种关系意义标签，程序映射为受限 delta。

按 spec 第 11 节"关系积温修订"：
- 普通聊天不再默认增加 bond（ordinary_exchange 的 bond_delta = 0）
- 明确感谢、可靠帮助、共同成功、边界修复 → 根据语义产生受限关系建议
- 程序执行：单轮限幅、同一事件幂等、来源证据校验、trust 变化条件限制、用户沉默不产生负变化

本模块独立于 affect/engine.py（已冻结 affect-v1.2），通过 episode_relationship_delta_suggestions
表提供新的关系 delta 机制。engine.py 的 fallback bond 增量仍然存在（affect-v1.2 冻结），
但新机制对 ordinary_exchange 不产生额外 bond delta。
"""

from dataclasses import dataclass
from typing import Optional

from .. import db
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
    source_message_id: str
    episode_id: Optional[str]
    relationship_label: str
    bond_delta: float
    familiarity_delta: float
    trust_delta: float
    attachment_delta: float
    rapport_delta: float
    cap_bond_applied: float
    cap_trust_applied: float
    idempotency_key: str
    status: str
    applied_at: Optional[float]
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
) -> Optional[RelationshipDeltaSuggestion]:
    """处理关系意义标签，产生受限 delta 建议并落库。

    幂等：同一 source_message_id 只产生一条建议（UNIQUE 约束）
    单轮限幅：delta 受 SINGLE_TURN_CAPS 限制
    来源证据校验：source_message_id 必须存在（外键约束）

    返回建议记录；如果已存在相同 source_message_id 的建议，返回 None。
    """
    if label not in LABEL_DELTAS:
        return None

    deltas = LABEL_DELTAS[label]
    idempotency_key = make_idempotency_key(
        RELATIONSHIP_MEANING_V1, session_id, source_message_id,
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
        # 幂等检查：同一 source_message_id 已有建议则跳过
        existing = conn.execute(
            "SELECT id FROM episode_relationship_delta_suggestions "
            "WHERE source_message_id=?",
            (source_message_id,),
        ).fetchone()
        if existing:
            return None

        conn.execute(
            "INSERT INTO episode_relationship_delta_suggestions"
            " (id, session_id, source_message_id, episode_id, relationship_label,"
            "  bond_delta, familiarity_delta, trust_delta, attachment_delta, rapport_delta,"
            "  cap_bond_applied, cap_trust_applied, idempotency_key, status,"
            "  protocol_version, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?)",
            (
                record_id, session_id, source_message_id, episode_id, label,
                bond_clamped, familiarity_clamped, trust_clamped, attachment_clamped, rapport_clamped,
                bond_clamped, trust_clamped, idempotency_key,
                RELATIONSHIP_MEANING_V1, now, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return RelationshipDeltaSuggestion(
        id=record_id, session_id=session_id, source_message_id=source_message_id,
        episode_id=episode_id, relationship_label=label,
        bond_delta=bond_clamped, familiarity_delta=familiarity_clamped,
        trust_delta=trust_clamped, attachment_delta=attachment_clamped,
        rapport_delta=rapport_clamped,
        cap_bond_applied=bond_clamped, cap_trust_applied=trust_clamped,
        idempotency_key=idempotency_key, status="proposed",
        applied_at=None, created_at=now,
    )


def get_suggestion_by_source_message(source_message_id: str) -> Optional[RelationshipDeltaSuggestion]:
    """按 source_message_id 查询建议（幂等检查用）。"""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM episode_relationship_delta_suggestions "
            "WHERE source_message_id=? ORDER BY created_at DESC LIMIT 1",
            (source_message_id,),
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
        episode_id=row["episode_id"],
        relationship_label=row["relationship_label"],
        bond_delta=row["bond_delta"],
        familiarity_delta=row["familiarity_delta"],
        trust_delta=row["trust_delta"],
        attachment_delta=row["attachment_delta"],
        rapport_delta=row["rapport_delta"],
        cap_bond_applied=row["cap_bond_applied"],
        cap_trust_applied=row["cap_trust_applied"],
        idempotency_key=row["idempotency_key"],
        status=row["status"],
        applied_at=row["applied_at"],
        created_at=row["created_at"],
    )
