"""EAP v0.2 LIFE 接入适配层：proactive seed 接收队列。

按 spec 第 8 节"EAP 与 LIFE 边界"，LIFE 专项负责生活事件领域（LifeEvent、
PersonalGoal、ImportantDate、Diary、SelfTimeline）。LIFE 生活事件只能产生
proactive seed 投递到 `life_proactive_seeds` 表；EAP 消费 seed 并建立
ContactEpisode，再走 proactive-decision-v2 决策流程。

边界约束（spec 第 8 节联动规则）：
- LIFE 不得直接发送主动消息：seed_kind 永远为 'life_share'，receive_life_seed
  只落库 seed，不创建 ContactEpisode 或 ProactiveCandidate。
- EAP 不得伪造或修改 LifeEvent：consume_seed 只关联已有 episode_id，不写 LIFE 侧表。
- LIFE 生活事件只能产生 proactive seed：source_event_type 限定 5 种。
- EAP 不得伪造 LifeEvent：所有函数只读写 life_proactive_seeds 表。

模块隔离：本模块只导入 db/protocols/run_ledger，不接入 main.py（接入留给 EAP.J）。
本阶段（EAP.I）只实施接口预留和边界约束测试，LIFE 专项启动后实际接入。
"""

from dataclasses import dataclass
from typing import Optional

from .. import db
from .protocols import PROACTIVE_DECISION_V2
from .run_ledger import make_idempotency_key


# LIFE 5 种源事件类型（spec 第 8 节 LIFE 拥有领域）
class LifeSeedSourceType:
    LIFE_EVENT = "life_event"            # LifeEvent
    PERSONAL_GOAL = "personal_goal"      # PersonalGoal
    IMPORTANT_DATE = "important_date"    # ImportantDate
    DIARY_ENTRY = "diary_entry"          # Diary
    SELF_TIMELINE = "self_timeline"      # SelfTimeline


ALL_SOURCE_TYPES = (
    LifeSeedSourceType.LIFE_EVENT,
    LifeSeedSourceType.PERSONAL_GOAL,
    LifeSeedSourceType.IMPORTANT_DATE,
    LifeSeedSourceType.DIARY_ENTRY,
    LifeSeedSourceType.SELF_TIMELINE,
)

# 默认 origin_type 映射：source_type → origin_type
# LIFE 里程碑类事件默认转 milestone；日记/自传类事件默认转 life_share
DEFAULT_ORIGIN_TYPE_MAP = {
    "life_event": "milestone",
    "personal_goal": "milestone",
    "important_date": "milestone",
    "diary_entry": "life_share",
    "self_timeline": "life_share",
}

# 有效 origin_type 集合（与 contact_episodes.origin_type CHECK 约束对齐）
_VALID_ORIGIN_TYPES = frozenset({
    "expected_return", "emotional_care", "milestone", "life_share",
})

# 永远为 'life_share'：LIFE 不得直接发送主动消息，只能产生 seed
SEED_KIND = "life_share"

# 协议版本（与 ContactEpisode/Candidate 一致）
PROTOCOL_VERSION = PROACTIVE_DECISION_V2


@dataclass
class LifeProactiveSeed:
    """life_proactive_seeds 表的记录。"""
    id: str
    source_event_type: str
    source_event_id: str
    source_event_summary: str
    topic: str
    origin_type: str
    seed_kind: str
    source_revision: str
    source_hash: str
    consumed_at: Optional[float]
    consumed_episode_id: Optional[str]
    consumed_candidate_id: Optional[str]
    rejected_at: Optional[float]
    rejection_reason: Optional[str]
    idempotency_key: str
    protocol_version: str
    created_at: float
    updated_at: float


def receive_life_seed(
    *,
    source_event_type: str,
    source_event_id: str,
    source_event_summary: str,
    topic: Optional[str] = None,
    origin_type: Optional[str] = None,
    source_revision: str = "",
    source_hash: str = "",
    now: Optional[float] = None,
) -> Optional[LifeProactiveSeed]:
    """接收 LIFE 投递的 proactive seed 并落库。

    幂等：相同 (source_event_type, source_event_id, source_revision) 只接收一次。

    边界约束：
    - source_event_type 必须在 ALL_SOURCE_TYPES 中（LIFE 只能投递这 5 种事件）。
    - seed_kind 永远为 'life_share'（LIFE 不得直接发送主动消息，只能产生 seed）。
    - 不在此函数中创建 ContactEpisode 或 Candidate（EAP 不得伪造 LifeEvent）。

    返回创建的 seed 记录；如已存在相同 source 的 seed，返回 None。
    """
    # 边界约束 1：source_event_type 必须在 5 种中
    if source_event_type not in ALL_SOURCE_TYPES:
        raise ValueError(
            f"invalid source_event_type: {source_event_type!r}; "
            f"must be one of {ALL_SOURCE_TYPES}"
        )

    if not source_event_id or not source_event_id.strip():
        raise ValueError("source_event_id must be non-empty")

    if not source_event_summary or not source_event_summary.strip():
        raise ValueError("source_event_summary must be non-empty")

    # 默认 topic：未提供时使用 source_event_summary
    effective_topic = topic if topic is not None else source_event_summary
    if not effective_topic or not effective_topic.strip():
        raise ValueError("topic must be non-empty")

    # 默认 origin_type：未提供时按 DEFAULT_ORIGIN_TYPE_MAP 取
    if origin_type is None:
        origin_type = DEFAULT_ORIGIN_TYPE_MAP[source_event_type]
    if origin_type not in _VALID_ORIGIN_TYPES:
        raise ValueError(f"invalid origin_type: {origin_type!r}")

    now = now if now is not None else db.now()

    # 幂等检查：相同 (source_event_type, source_event_id, source_revision) 已存在则返回 None
    existing = get_seed_by_source(
        source_event_type, source_event_id, source_revision,
    )
    if existing is not None:
        return None

    record_id = db.new_id()
    idempotency_key = make_idempotency_key(
        PROTOCOL_VERSION, "life_seed",
        source_event_type, source_event_id, source_revision,
    )

    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO life_proactive_seeds"
            " (id, source_event_type, source_event_id, source_event_summary,"
            "  topic, origin_type, seed_kind, source_revision, source_hash,"
            "  consumed_at, consumed_episode_id, consumed_candidate_id,"
            "  rejected_at, rejection_reason,"
            "  idempotency_key, protocol_version, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, ?, ?, ?, ?)",
            (
                record_id, source_event_type, source_event_id,
                source_event_summary, effective_topic, origin_type,
                SEED_KIND, source_revision, source_hash,
                idempotency_key, PROTOCOL_VERSION, now, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return LifeProactiveSeed(
        id=record_id, source_event_type=source_event_type,
        source_event_id=source_event_id,
        source_event_summary=source_event_summary, topic=effective_topic,
        origin_type=origin_type, seed_kind=SEED_KIND,
        source_revision=source_revision, source_hash=source_hash,
        consumed_at=None, consumed_episode_id=None,
        consumed_candidate_id=None, rejected_at=None,
        rejection_reason=None, idempotency_key=idempotency_key,
        protocol_version=PROTOCOL_VERSION, created_at=now, updated_at=now,
    )


def list_pending_seeds(*, limit: int = 50) -> list:
    """列出所有未消费的 seed（consumed_at IS NULL AND rejected_at IS NULL）。

    按 created_at 升序返回，便于 EAP 按投递顺序消费。
    """
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM life_proactive_seeds "
            "WHERE consumed_at IS NULL AND rejected_at IS NULL "
            "ORDER BY created_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_seed(row) for row in rows]
    finally:
        conn.close()


def get_seed(seed_id: str) -> Optional[LifeProactiveSeed]:
    """按 ID 查询 seed。"""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM life_proactive_seeds WHERE id=?",
            (seed_id,),
        ).fetchone()
        if not row:
            return None
        return _row_to_seed(row)
    finally:
        conn.close()


def get_seed_by_source(
    source_event_type: str,
    source_event_id: str,
    source_revision: str = "",
) -> Optional[LifeProactiveSeed]:
    """按 (source_event_type, source_event_id, source_revision) 查询 seed。

    用于幂等检查：相同来源的 seed 只接收一次。
    """
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM life_proactive_seeds "
            "WHERE source_event_type=? AND source_event_id=? "
            "AND source_revision=? LIMIT 1",
            (source_event_type, source_event_id, source_revision),
        ).fetchone()
        if not row:
            return None
        return _row_to_seed(row)
    finally:
        conn.close()


def consume_seed(
    seed_id: str,
    *,
    episode_id: str,
    candidate_id: Optional[str] = None,
    now: Optional[float] = None,
) -> LifeProactiveSeed:
    """标记 seed 已被 EAP 消费，关联到 ContactEpisode 和 Candidate。

    边界约束：
    - 必须提供 episode_id（EAP 通过 ContactEpisode 处理，不直接发送）。
    - 不创建或修改 LIFE 侧数据（EAP 不得伪造或修改 LifeEvent）。
    - 标记 consumed_at 时间戳。

    - 如果 seed 已被消费，抛出 ValueError。
    - 如果 seed 已被拒绝，抛出 ValueError。
    - 验证 episode_id 在 contact_episodes 表中存在（外键约束 + 主动校验）。
    """
    if not episode_id or not episode_id.strip():
        raise ValueError("episode_id must be non-empty")

    now = now if now is not None else db.now()

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM life_proactive_seeds WHERE id=?",
            (seed_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"seed not found: {seed_id}")

        if row["consumed_at"] is not None:
            raise ValueError(
                f"seed already consumed at {row['consumed_at']}"
            )
        if row["rejected_at"] is not None:
            raise ValueError(
                f"seed already rejected at {row['rejected_at']}"
            )

        # 验证 episode_id 存在（外键约束本身会保护，但提前校验给出清晰错误）
        ep_row = conn.execute(
            "SELECT id FROM contact_episodes WHERE id=?",
            (episode_id,),
        ).fetchone()
        if ep_row is None:
            raise ValueError(
                f"episode not found: {episode_id} (EAP 不得伪造 LifeEvent，"
                f"必须使用已存在的 ContactEpisode)"
            )

        # 如提供 candidate_id，验证存在
        if candidate_id is not None:
            cand_row = conn.execute(
                "SELECT id FROM proactive_candidates WHERE id=?",
                (candidate_id,),
            ).fetchone()
            if cand_row is None:
                raise ValueError(
                    f"candidate not found: {candidate_id}"
                )

        conn.execute(
            "UPDATE life_proactive_seeds SET "
            " consumed_at=?, consumed_episode_id=?, consumed_candidate_id=?, "
            " updated_at=? WHERE id=?",
            (now, episode_id, candidate_id, now, seed_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM life_proactive_seeds WHERE id=?",
            (seed_id,),
        ).fetchone()
    finally:
        conn.close()

    return _row_to_seed(row)


def reject_seed(
    seed_id: str,
    *,
    reason: str,
    now: Optional[float] = None,
) -> LifeProactiveSeed:
    """标记 seed 被拒绝（EAP 判断不适合接近）。

    不影响 LIFE 侧数据。seed 保留在表中用于审计。

    - 如果 seed 已被拒绝，抛出 ValueError。
    - 如果 seed 已被消费，抛出 ValueError。
    """
    if not reason or not reason.strip():
        raise ValueError("reason must be non-empty")

    now = now if now is not None else db.now()

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM life_proactive_seeds WHERE id=?",
            (seed_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"seed not found: {seed_id}")

        if row["rejected_at"] is not None:
            raise ValueError(
                f"seed already rejected at {row['rejected_at']}"
            )
        if row["consumed_at"] is not None:
            raise ValueError(
                f"seed already consumed at {row['consumed_at']}"
            )

        conn.execute(
            "UPDATE life_proactive_seeds SET "
            " rejected_at=?, rejection_reason=?, updated_at=? WHERE id=?",
            (now, reason, now, seed_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM life_proactive_seeds WHERE id=?",
            (seed_id,),
        ).fetchone()
    finally:
        conn.close()

    return _row_to_seed(row)


def _row_to_seed(row) -> LifeProactiveSeed:
    """内部：从 sqlite3.Row 构造 LifeProactiveSeed。"""
    return LifeProactiveSeed(
        id=row["id"],
        source_event_type=row["source_event_type"],
        source_event_id=row["source_event_id"],
        source_event_summary=row["source_event_summary"],
        topic=row["topic"],
        origin_type=row["origin_type"],
        seed_kind=row["seed_kind"],
        source_revision=row["source_revision"],
        source_hash=row["source_hash"],
        consumed_at=row["consumed_at"],
        consumed_episode_id=row["consumed_episode_id"],
        consumed_candidate_id=row["consumed_candidate_id"],
        rejected_at=row["rejected_at"],
        rejection_reason=row["rejection_reason"],
        idempotency_key=row["idempotency_key"],
        protocol_version=row["protocol_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
