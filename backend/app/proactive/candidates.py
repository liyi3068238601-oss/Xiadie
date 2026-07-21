"""EAP v0.2 Proactive Candidate：本地候选生成。

按 spec 第 6.3 节决策流程第 1 步：候选有效性检查（来源是否有效、是否已投递、第一层硬边界预检）。
本模块只承载候选 CRUD 和有效性预检，不做决策（决策见 decision.py）。

模块隔离：只导入 db/protocols/run_ledger，不接入 main.py（接入留给 EAP.G/H/J）。
"""

import json
from dataclasses import dataclass
from typing import Optional

from .. import db
from .protocols import PROACTIVE_DECISION_V2
from .run_ledger import compute_source_hash


# 6 种候选类型（spec 第 6.3 节决策流程第 1 步）
class CandidateKind:
    CHAT_CONTINUATION = "chat_continuation"      # 对话延续
    RETURN_FOLLOWUP = "return_followup"          # 回来跟进（open_thread 衔接）
    EMOTIONAL_CARE = "emotional_care"            # 情感关怀
    MILESTONE_FOLLOWUP = "milestone_followup"    # 里程碑跟进
    CASUAL_GREETING = "casual_greeting"          # 轻量问候
    LIFE_SHARE = "life_share"                    # 生活事件分享


ALL_CANDIDATE_KINDS = (
    CandidateKind.CHAT_CONTINUATION,
    CandidateKind.RETURN_FOLLOWUP,
    CandidateKind.EMOTIONAL_CARE,
    CandidateKind.MILESTONE_FOLLOWUP,
    CandidateKind.CASUAL_GREETING,
    CandidateKind.LIFE_SHARE,
)


# 候选状态机（spec 第 6.3 节决策流程第 1 步）
class CandidateStatus:
    PENDING = "pending"        # 新建，待评估
    EVALUATING = "evaluating"  # 正在评估
    APPROVED = "approved"      # 决策为 send，等待投递
    DEFERRED = "deferred"      # 决策为 defer，等待重评估
    SUPPRESSED = "suppressed"  # 决策为 suppress，被硬边界或评估否决
    ABANDONED = "abandoned"    # 决策为 abandon，话题消失或失效
    DELIVERED = "delivered"    # 已投递


ALL_CANDIDATE_STATUSES = (
    CandidateStatus.PENDING,
    CandidateStatus.EVALUATING,
    CandidateStatus.APPROVED,
    CandidateStatus.DEFERRED,
    CandidateStatus.SUPPRESSED,
    CandidateStatus.ABANDONED,
    CandidateStatus.DELIVERED,
)


# 终态：不能再转换
TERMINAL_CANDIDATE_STATUSES = frozenset({
    CandidateStatus.SUPPRESSED,
    CandidateStatus.ABANDONED,
    CandidateStatus.DELIVERED,
})


# 默认过期时间：24 小时
DEFAULT_CANDIDATE_EXPIRY = 24 * 3600


@dataclass
class ProactiveCandidate:
    """proactive_candidates 表的记录。"""
    id: str
    session_id: str
    episode_id: Optional[str]
    candidate_kind: str
    topic: str
    source_refs: dict
    open_thread: Optional[str]
    source_hash: str
    status: str
    expires_at: Optional[float]
    protocol_version: str
    created_at: float
    updated_at: float


def _serialize_source_refs(source_refs: Optional[dict]) -> str:
    """将 source_refs dict 序列化为 JSON 字符串（None → '{}'）。"""
    if not source_refs:
        return "{}"
    return json.dumps(source_refs, ensure_ascii=False, sort_keys=True)


def _parse_source_refs(raw: Optional[str]) -> dict:
    """从 JSON 字符串解析 source_refs（None 或空 → {}）。"""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def create_candidate(
    session_id: str,
    *,
    candidate_kind: str,
    topic: str,
    episode_id: Optional[str] = None,
    source_refs: Optional[dict] = None,
    open_thread: Optional[str] = None,
    source_messages: Optional[list] = None,
    expires_at: Optional[float] = None,
    now: Optional[float] = None,
) -> ProactiveCandidate:
    """创建新的 ProactiveCandidate，初始状态 pending。

    - candidate_kind 必须在 ALL_CANDIDATE_KINDS 中
    - topic 非空
    - source_messages 列表用于计算 source_hash（spec 第 6.5 节复用公共 DecisionRun）
    - 如未提供 expires_at，按 DEFAULT_CANDIDATE_EXPIRY 计算
    """
    if candidate_kind not in ALL_CANDIDATE_KINDS:
        raise ValueError(f"invalid candidate_kind: {candidate_kind!r}")

    if not topic or not topic.strip():
        raise ValueError("topic must be non-empty")

    now = now if now is not None else db.now()
    if expires_at is None:
        expires_at = now + DEFAULT_CANDIDATE_EXPIRY

    # 计算 source_hash（参考 conversation_summaries._source_hash 实现）
    source_hash = compute_source_hash(source_messages) if source_messages else ""

    record_id = db.new_id()
    source_refs_json = _serialize_source_refs(source_refs)

    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO proactive_candidates"
            " (id, session_id, episode_id, candidate_kind, topic, source_refs,"
            "  open_thread, source_hash, status, expires_at,"
            "  protocol_version, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record_id, session_id, episode_id, candidate_kind, topic,
                source_refs_json, open_thread, source_hash,
                CandidateStatus.PENDING, expires_at,
                PROACTIVE_DECISION_V2, now, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return ProactiveCandidate(
        id=record_id, session_id=session_id, episode_id=episode_id,
        candidate_kind=candidate_kind, topic=topic,
        source_refs=source_refs or {}, open_thread=open_thread,
        source_hash=source_hash, status=CandidateStatus.PENDING,
        expires_at=expires_at, protocol_version=PROACTIVE_DECISION_V2,
        created_at=now, updated_at=now,
    )


def get_candidate(candidate_id: str) -> Optional[ProactiveCandidate]:
    """按 ID 查询。"""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM proactive_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        if not row:
            return None
        return _row_to_candidate(row)
    finally:
        conn.close()


def list_candidates_by_session(
    session_id: str, *, status: Optional[str] = None,
) -> list:
    """按 session 查询候选，可按 status 过滤。"""
    conn = db.connect()
    try:
        if status is not None:
            rows = conn.execute(
                "SELECT * FROM proactive_candidates "
                "WHERE session_id=? AND status=? "
                "ORDER BY created_at DESC",
                (session_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM proactive_candidates "
                "WHERE session_id=? ORDER BY created_at DESC",
                (session_id,),
            ).fetchall()
        return [_row_to_candidate(row) for row in rows]
    finally:
        conn.close()


def list_pending_candidates(*, now: Optional[float] = None) -> list:
    """列出所有 pending 候选。"""
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM proactive_candidates WHERE status=? "
            "ORDER BY created_at ASC",
            (CandidateStatus.PENDING,),
        ).fetchall()
        return [_row_to_candidate(row) for row in rows]
    finally:
        conn.close()


def transition_candidate_status(
    candidate_id: str,
    new_status: str,
    *,
    now: Optional[float] = None,
) -> ProactiveCandidate:
    """状态转换。

    - 终态状态不能再转换
    - 校验 new_status 在 ALL_CANDIDATE_STATUSES 中
    - 更新 updated_at
    """
    if new_status not in ALL_CANDIDATE_STATUSES:
        raise ValueError(f"invalid status: {new_status!r}")

    now = now if now is not None else db.now()

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM proactive_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"candidate not found: {candidate_id}")

        current_status = row["status"]
        if current_status in TERMINAL_CANDIDATE_STATUSES:
            raise ValueError(
                f"cannot transition from terminal status: {current_status!r}"
            )

        conn.execute(
            "UPDATE proactive_candidates SET status=?, updated_at=? WHERE id=?",
            (new_status, now, candidate_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM proactive_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
    finally:
        conn.close()

    return _row_to_candidate(row)


def is_candidate_valid(
    candidate: ProactiveCandidate,
    *,
    now: Optional[float] = None,
) -> tuple:
    """来源有效性 + 第一层硬边界预检。

    返回 (is_valid, reasons)：
    - is_valid: True 表示候选可进入决策流程
    - reasons: 失败原因字符串列表（is_valid=True 时为空列表）

    本函数只做基础有效性检查（候选未过期、未在终态、source_hash 非空），
    完整的第一层硬边界检查（topic/kind/channel/已投递等）见 decision.check_layer1_hard_boundary。
    """
    now = now if now is not None else db.now()
    reasons = []

    # 已在终态的候选不应再被评估
    if candidate.status in TERMINAL_CANDIDATE_STATUSES:
        reasons.append(f"candidate_in_terminal_status:{candidate.status}")

    # 过期候选不应再被评估
    if candidate.expires_at is not None and candidate.expires_at < now:
        reasons.append("candidate_expired")

    # source_hash 为空意味着候选没有来源证据，不应进入决策
    if not candidate.source_hash:
        reasons.append("source_hash_empty")

    return (len(reasons) == 0, reasons)


def _row_to_candidate(row) -> ProactiveCandidate:
    """内部：从 sqlite3.Row 构造 ProactiveCandidate。"""
    return ProactiveCandidate(
        id=row["id"], session_id=row["session_id"], episode_id=row["episode_id"],
        candidate_kind=row["candidate_kind"], topic=row["topic"],
        source_refs=_parse_source_refs(row["source_refs"]),
        open_thread=row["open_thread"],
        source_hash=row["source_hash"], status=row["status"],
        expires_at=row["expires_at"],
        protocol_version=row["protocol_version"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )
