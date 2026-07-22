"""EAP v0.2 LIFE 接入适配层测试（Task 2.9 / EAP.I）。

覆盖 spec 第 8 节"EAP 与 LIFE 边界"联动规则：
- LIFE 生活事件只能产生 proactive seed
- EAP 判断是否适合接近用户、采用何种强度
- LIFE 不得直接发送主动消息
- EAP 不得伪造或修改 LifeEvent

测试分组：
1. 接收 seed 测试（6 个）
2. 查询测试（4 个）
3. 消费测试（5 个）
4. 拒绝测试（3 个）
5. 边界约束测试（6 个，关键）
6. schema 测试（3 个）

本阶段（EAP.I）只验证接口预留和边界约束，LIFE 专项启动后实际接入。
"""
import pytest

from app import db
from app.proactive import episodes as episodes_mod
from app.proactive import life_adapter as life_mod
from app.proactive.episodes import OriginType
from app.proactive.life_adapter import (
    ALL_SOURCE_TYPES,
    DEFAULT_ORIGIN_TYPE_MAP,
    SEED_KIND,
    LifeProactiveSeed,
    LifeSeedSourceType,
    consume_seed,
    get_seed,
    get_seed_by_source,
    list_pending_seeds,
    receive_life_seed,
    reject_seed,
)


# ---------- 公共 fixture ----------

def _setup_session(session_id: str) -> None:
    """插入测试 session，满足 contact_episodes 外键约束。"""
    now = db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (session_id, "life_adapter 测试", now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _cleanup(session_id: str) -> None:
    """清理测试数据。"""
    conn = db.connect()
    try:
        # 先清理 life_proactive_seeds（避免外键悬空）
        conn.execute("DELETE FROM life_proactive_seeds", ())
        conn.execute("DELETE FROM proactive_decisions WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM proactive_candidates WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM contact_episodes WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        conn.commit()
    finally:
        conn.close()


def _make_episode(session_id: str, *, topic: str = "测试话题"):
    """创建测试用 ContactEpisode。"""
    return episodes_mod.create_episode(
        session_id, topic=topic, origin_type=OriginType.LIFE_SHARE,
    )


# ---------- 1. 接收 seed 测试 ----------

def test_receive_life_seed_basic():
    """基本接收：落库后字段正确。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = receive_life_seed(
            source_event_type=LifeSeedSourceType.DIARY_ENTRY,
            source_event_id="diary-001",
            source_event_summary="今天写完了一段日记，想分享给遐蝶",
            topic="日记分享",
            origin_type="life_share",
            source_revision="rev-1",
            source_hash="hash-001",
            now=1000.0,
        )
        assert record is not None
        assert record.id  # 自动生成
        assert record.source_event_type == "diary_entry"
        assert record.source_event_id == "diary-001"
        assert record.source_event_summary == "今天写完了一段日记，想分享给遐蝶"
        assert record.topic == "日记分享"
        assert record.origin_type == "life_share"
        assert record.seed_kind == SEED_KIND  # 永远 'life_share'
        assert record.source_revision == "rev-1"
        assert record.source_hash == "hash-001"
        assert record.consumed_at is None
        assert record.consumed_episode_id is None
        assert record.consumed_candidate_id is None
        assert record.rejected_at is None
        assert record.rejection_reason is None
        assert record.idempotency_key  # 非空
        assert record.protocol_version == "proactive-decision-v2"
        assert record.created_at == 1000.0
        assert record.updated_at == 1000.0
    finally:
        _cleanup(session_id)


def test_receive_life_seed_invalid_source_type():
    """非法 source_event_type 抛出 ValueError。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        with pytest.raises(ValueError, match="invalid source_event_type"):
            receive_life_seed(
                source_event_type="invalid_type",
                source_event_id="x",
                source_event_summary="summary",
            )
        # 边界约束：LIFE 只能投递这 5 种事件
        with pytest.raises(ValueError):
            receive_life_seed(
                source_event_type="message",  # 试图直接发消息
                source_event_id="x",
                source_event_summary="summary",
            )
    finally:
        _cleanup(session_id)


def test_receive_life_seed_default_topic():
    """未提供 topic 时使用 source_event_summary。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = receive_life_seed(
            source_event_type=LifeSeedSourceType.LIFE_EVENT,
            source_event_id="le-001",
            source_event_summary="今天完成了人生第一个半马",
        )
        assert record.topic == "今天完成了人生第一个半马"
    finally:
        _cleanup(session_id)


def test_receive_life_seed_default_origin_type():
    """未提供 origin_type 时按 DEFAULT_ORIGIN_TYPE_MAP 取。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        # life_event → milestone
        r1 = receive_life_seed(
            source_event_type=LifeSeedSourceType.LIFE_EVENT,
            source_event_id="le-001",
            source_event_summary="完成了半马",
        )
        assert r1.origin_type == "milestone"

        # personal_goal → milestone
        r2 = receive_life_seed(
            source_event_type=LifeSeedSourceType.PERSONAL_GOAL,
            source_event_id="pg-001",
            source_event_summary="读 100 本书的目标完成",
        )
        assert r2.origin_type == "milestone"

        # important_date → milestone
        r3 = receive_life_seed(
            source_event_type=LifeSeedSourceType.IMPORTANT_DATE,
            source_event_id="id-001",
            source_event_summary="今天是生日",
        )
        assert r3.origin_type == "milestone"

        # diary_entry → life_share
        r4 = receive_life_seed(
            source_event_type=LifeSeedSourceType.DIARY_ENTRY,
            source_event_id="de-001",
            source_event_summary="写了一段日记",
        )
        assert r4.origin_type == "life_share"

        # self_timeline → life_share
        r5 = receive_life_seed(
            source_event_type=LifeSeedSourceType.SELF_TIMELINE,
            source_event_id="st-001",
            source_event_summary="时间线更新",
        )
        assert r5.origin_type == "life_share"
    finally:
        _cleanup(session_id)


def test_receive_life_seed_idempotency():
    """相同 (source_type, source_id, revision) 重复调用返回 None。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        r1 = receive_life_seed(
            source_event_type=LifeSeedSourceType.LIFE_EVENT,
            source_event_id="le-001",
            source_event_summary="完成了半马",
            source_revision="rev-1",
        )
        assert r1 is not None

        # 相同来源重复投递 → None
        r2 = receive_life_seed(
            source_event_type=LifeSeedSourceType.LIFE_EVENT,
            source_event_id="le-001",
            source_event_summary="完成了半马",
            source_revision="rev-1",
        )
        assert r2 is None

        # 不同 revision 视为新事件
        r3 = receive_life_seed(
            source_event_type=LifeSeedSourceType.LIFE_EVENT,
            source_event_id="le-001",
            source_event_summary="完成了半马（修订）",
            source_revision="rev-2",
        )
        assert r3 is not None
        assert r3.id != r1.id

        # 不同 source_event_id 视为新事件
        r4 = receive_life_seed(
            source_event_type=LifeSeedSourceType.LIFE_EVENT,
            source_event_id="le-002",
            source_event_summary="另一个生活事件",
            source_revision="rev-1",
        )
        assert r4 is not None
    finally:
        _cleanup(session_id)


def test_receive_life_seed_seed_kind_always_life_share():
    """seed_kind 永远为 'life_share'：LIFE 不得直接发送主动消息。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        for source_type in ALL_SOURCE_TYPES:
            record = receive_life_seed(
                source_event_type=source_type,
                source_event_id=f"id-{source_type}",
                source_event_summary=f"summary-{source_type}",
            )
            assert record.seed_kind == "life_share", (
                f"source_type={source_type} 的 seed_kind 必须为 'life_share'"
            )
    finally:
        _cleanup(session_id)


# ---------- 2. 查询测试 ----------

def test_get_seed():
    """按 ID 查询 seed。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        record = receive_life_seed(
            source_event_type=LifeSeedSourceType.DIARY_ENTRY,
            source_event_id="de-001",
            source_event_summary="一段日记",
        )
        loaded = get_seed(record.id)
        assert loaded is not None
        assert loaded.id == record.id
        assert loaded.source_event_id == "de-001"
    finally:
        _cleanup(session_id)


def test_get_seed_not_found():
    """查询不存在的 ID 返回 None。"""
    db.init_db()
    loaded = get_seed("nonexistent-id")
    assert loaded is None


def test_get_seed_by_source():
    """按 (source_event_type, source_event_id, source_revision) 查询 seed。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        receive_life_seed(
            source_event_type=LifeSeedSourceType.PERSONAL_GOAL,
            source_event_id="pg-001",
            source_event_summary="读 100 本书",
            source_revision="rev-1",
        )
        loaded = get_seed_by_source("personal_goal", "pg-001", "rev-1")
        assert loaded is not None
        assert loaded.source_event_id == "pg-001"

        # 不同 revision 返回 None
        loaded2 = get_seed_by_source("personal_goal", "pg-001", "rev-2")
        assert loaded2 is None

        # 默认 revision = ""
        receive_life_seed(
            source_event_type=LifeSeedSourceType.PERSONAL_GOAL,
            source_event_id="pg-002",
            source_event_summary="另一目标",
        )
        loaded3 = get_seed_by_source("personal_goal", "pg-002")
        assert loaded3 is not None
    finally:
        _cleanup(session_id)


def test_list_pending_seeds():
    """列出未消费的 seed，按 created_at 升序。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        r1 = receive_life_seed(
            source_event_type=LifeSeedSourceType.LIFE_EVENT,
            source_event_id="le-001",
            source_event_summary="事件 1",
            now=1000.0,
        )
        r2 = receive_life_seed(
            source_event_type=LifeSeedSourceType.DIARY_ENTRY,
            source_event_id="de-001",
            source_event_summary="事件 2",
            now=2000.0,
        )
        pending = list_pending_seeds()
        assert len(pending) >= 2
        # 升序：r1 在前
        ids = [p.id for p in pending]
        assert ids.index(r1.id) < ids.index(r2.id)

        # limit 参数生效
        limited = list_pending_seeds(limit=1)
        assert len(limited) == 1
    finally:
        _cleanup(session_id)


# ---------- 3. 消费测试 ----------

def test_consume_seed_basic():
    """基本消费：标记 consumed_at + consumed_episode_id。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        seed = receive_life_seed(
            source_event_type=LifeSeedSourceType.LIFE_EVENT,
            source_event_id="le-001",
            source_event_summary="完成了半马",
            now=1000.0,
        )
        episode = _make_episode(session_id, topic="马拉松完赛跟进")

        consumed = consume_seed(seed.id, episode_id=episode.id, now=2000.0)
        assert consumed.consumed_at == 2000.0
        assert consumed.consumed_episode_id == episode.id
        assert consumed.consumed_candidate_id is None
        assert consumed.updated_at == 2000.0

        # 重新查询确认
        reloaded = get_seed(seed.id)
        assert reloaded.consumed_at == 2000.0
        assert reloaded.consumed_episode_id == episode.id
    finally:
        _cleanup(session_id)


def test_consume_seed_with_candidate():
    """消费时关联 candidate_id。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        seed = receive_life_seed(
            source_event_type=LifeSeedSourceType.DIARY_ENTRY,
            source_event_id="de-001",
            source_event_summary="一段日记",
        )
        episode = _make_episode(session_id)
        # 创建一个真实的 ProactiveCandidate 满足外键
        from app.proactive.candidates import CandidateKind
        from app.proactive import candidates as candidates_mod
        candidate = candidates_mod.create_candidate(
            session_id, candidate_kind=CandidateKind.LIFE_SHARE,
            topic="日记分享", episode_id=episode.id,
        )

        consumed = consume_seed(
            seed.id, episode_id=episode.id, candidate_id=candidate.id,
        )
        assert consumed.consumed_candidate_id == candidate.id
        assert consumed.consumed_episode_id == episode.id
    finally:
        _cleanup(session_id)


def test_consume_seed_invalid_episode_id():
    """episode_id 不存在抛出 ValueError（EAP 不得伪造 LifeEvent）。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        seed = receive_life_seed(
            source_event_type=LifeSeedSourceType.LIFE_EVENT,
            source_event_id="le-001",
            source_event_summary="事件",
        )
        with pytest.raises(ValueError, match="episode not found"):
            consume_seed(seed.id, episode_id="nonexistent-episode-id")
    finally:
        _cleanup(session_id)


def test_consume_seed_already_consumed():
    """再次消费抛出 ValueError。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        seed = receive_life_seed(
            source_event_type=LifeSeedSourceType.LIFE_EVENT,
            source_event_id="le-001",
            source_event_summary="事件",
        )
        episode = _make_episode(session_id)

        consume_seed(seed.id, episode_id=episode.id)
        with pytest.raises(ValueError, match="already consumed"):
            consume_seed(seed.id, episode_id=episode.id)
    finally:
        _cleanup(session_id)


def test_consume_seed_updates_consumed_at():
    """consume_seed 更新 consumed_at 时间戳。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        seed = receive_life_seed(
            source_event_type=LifeSeedSourceType.LIFE_EVENT,
            source_event_id="le-001",
            source_event_summary="事件",
            now=1000.0,
        )
        episode = _make_episode(session_id)

        consumed = consume_seed(seed.id, episode_id=episode.id, now=5000.0)
        assert consumed.consumed_at == 5000.0
        assert consumed.updated_at == 5000.0
        assert consumed.created_at == 1000.0  # 创建时间不变
    finally:
        _cleanup(session_id)


# ---------- 4. 拒绝测试 ----------

def test_reject_seed_basic():
    """基本拒绝：标记 rejected_at + rejection_reason。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        seed = receive_life_seed(
            source_event_type=LifeSeedSourceType.LIFE_EVENT,
            source_event_id="le-001",
            source_event_summary="事件",
            now=1000.0,
        )
        rejected = reject_seed(seed.id, reason="用户在睡觉，不适合接近", now=2000.0)
        assert rejected.rejected_at == 2000.0
        assert rejected.rejection_reason == "用户在睡觉，不适合接近"
        assert rejected.consumed_at is None  # 未消费
        assert rejected.updated_at == 2000.0
    finally:
        _cleanup(session_id)


def test_reject_seed_already_rejected():
    """已拒绝的 seed 再次拒绝抛出 ValueError。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        seed = receive_life_seed(
            source_event_type=LifeSeedSourceType.LIFE_EVENT,
            source_event_id="le-001",
            source_event_summary="事件",
        )
        reject_seed(seed.id, reason="原因 1")
        with pytest.raises(ValueError, match="already rejected"):
            reject_seed(seed.id, reason="原因 2")
    finally:
        _cleanup(session_id)


def test_rejected_seed_excluded_from_pending():
    """被拒绝的 seed 不在 list_pending_seeds 中。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        s1 = receive_life_seed(
            source_event_type=LifeSeedSourceType.LIFE_EVENT,
            source_event_id="le-001",
            source_event_summary="事件 1",
        )
        s2 = receive_life_seed(
            source_event_type=LifeSeedSourceType.LIFE_EVENT,
            source_event_id="le-002",
            source_event_summary="事件 2",
        )
        reject_seed(s1.id, reason="不合适")

        pending = list_pending_seeds()
        ids = [p.id for p in pending]
        assert s2.id in ids
        assert s1.id not in ids  # 被排除
    finally:
        _cleanup(session_id)


# ---------- 5. 边界约束测试（关键） ----------

def test_boundary_life_cannot_send_directly():
    """边界约束：LIFE 不得直接发送主动消息。

    receive_life_seed 不创建 ContactEpisode（contact_episodes 表无新记录）。
    """
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        # 接收前 contact_episodes 数量
        conn = db.connect()
        try:
            before = conn.execute(
                "SELECT COUNT(*) FROM contact_episodes WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
        finally:
            conn.close()

        receive_life_seed(
            source_event_type=LifeSeedSourceType.LIFE_EVENT,
            source_event_id="le-001",
            source_event_summary="事件",
        )

        # 接收后 contact_episodes 数量不变（LIFE 不得直接建立 ContactEpisode）
        conn = db.connect()
        try:
            after = conn.execute(
                "SELECT COUNT(*) FROM contact_episodes WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
        finally:
            conn.close()
        assert after == before, "LIFE 不得直接创建 ContactEpisode"
    finally:
        _cleanup(session_id)


def test_boundary_life_cannot_send_directly_no_candidate():
    """边界约束：receive_life_seed 不创建 ProactiveCandidate。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        conn = db.connect()
        try:
            before = conn.execute(
                "SELECT COUNT(*) FROM proactive_candidates WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
        finally:
            conn.close()

        receive_life_seed(
            source_event_type=LifeSeedSourceType.LIFE_EVENT,
            source_event_id="le-001",
            source_event_summary="事件",
        )

        conn = db.connect()
        try:
            after = conn.execute(
                "SELECT COUNT(*) FROM proactive_candidates WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
        finally:
            conn.close()
        assert after == before, "LIFE 不得直接创建 ProactiveCandidate"
    finally:
        _cleanup(session_id)


def test_boundary_eap_cannot_forge_life_event():
    """边界约束：EAP 不得伪造或修改 LifeEvent。

    consume_seed 只读写 life_proactive_seeds 表，不写其他 LIFE 侧表。
    本阶段 LIFE 表未建，验证 consume_seed 不创建/修改 contact_episodes 行
    （只关联已存在的 episode_id，不修改 episode 本身）。
    """
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        seed = receive_life_seed(
            source_event_type=LifeSeedSourceType.LIFE_EVENT,
            source_event_id="le-001",
            source_event_summary="事件",
        )
        episode = _make_episode(session_id, topic="原始话题")

        # 记录 episode 的原始 updated_at
        original_ep_updated = episode.updated_at

        consume_seed(seed.id, episode_id=episode.id, now=9999.0)

        # 验证 episode 行的 updated_at 未被 consume_seed 修改
        reloaded_ep = episodes_mod.get_episode(episode.id)
        assert reloaded_ep.updated_at == original_ep_updated, (
            "consume_seed 不得修改 contact_episodes 行（EAP 不得修改 LIFE 侧数据）"
        )

        # 验证 consume_seed 不创建额外的 contact_episodes
        conn = db.connect()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM contact_episodes WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 1, "consume_seed 不应创建额外的 ContactEpisode"
    finally:
        _cleanup(session_id)


def test_boundary_seed_kind_constrained():
    """边界约束：seed_kind 必须为 'life_share'，CHECK 约束验证。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        # 通过模块 API 创建的所有 seed 的 seed_kind 都必须是 'life_share'
        for source_type in ALL_SOURCE_TYPES:
            record = receive_life_seed(
                source_event_type=source_type,
                source_event_id=f"id-{source_type}",
                source_event_summary=f"summary-{source_type}",
            )
            assert record.seed_kind == "life_share"

        # 直接 SQL 尝试插入非法 seed_kind 应被 CHECK 约束拒绝
        conn = db.connect()
        try:
            with pytest.raises(Exception):
                conn.execute(
                    "INSERT INTO life_proactive_seeds"
                    " (id, source_event_type, source_event_id, source_event_summary,"
                    "  topic, origin_type, seed_kind, source_revision, source_hash,"
                    "  consumed_at, consumed_episode_id, consumed_candidate_id,"
                    "  rejected_at, rejection_reason,"
                    "  idempotency_key, protocol_version, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, 'direct_send', '', '', "
                    "  NULL, NULL, NULL, NULL, NULL, ?, ?, ?, ?)",
                    (
                        db.new_id(), "life_event", "x", "y", "t", "life_share",
                        "key", "pv", 1.0, 1.0,
                    ),
                )
                conn.commit()
        finally:
            conn.close()
    finally:
        _cleanup(session_id)


def test_boundary_source_type_constrained():
    """边界约束：source_event_type 必须在 5 种中，CHECK 约束验证。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        # 模块 API 验证（5 种合法）
        for source_type in ALL_SOURCE_TYPES:
            record = receive_life_seed(
                source_event_type=source_type,
                source_event_id=f"id-{source_type}",
                source_event_summary=f"summary-{source_type}",
            )
            assert record is not None

        # 非法 source_event_type 抛出 ValueError
        with pytest.raises(ValueError):
            receive_life_seed(
                source_event_type="direct_message",  # 试图绕过限制直接发消息
                source_event_id="x",
                source_event_summary="y",
            )

        # 直接 SQL 尝试插入非法 source_event_type 应被 CHECK 约束拒绝
        conn = db.connect()
        try:
            with pytest.raises(Exception):
                conn.execute(
                    "INSERT INTO life_proactive_seeds"
                    " (id, source_event_type, source_event_id, source_event_summary,"
                    "  topic, origin_type, seed_kind, source_revision, source_hash,"
                    "  consumed_at, consumed_episode_id, consumed_candidate_id,"
                    "  rejected_at, rejection_reason,"
                    "  idempotency_key, protocol_version, created_at, updated_at)"
                    " VALUES (?, 'direct_message', ?, ?, ?, ?, 'life_share', '', '', "
                    "  NULL, NULL, NULL, NULL, NULL, ?, ?, ?, ?)",
                    (
                        db.new_id(), "x", "y", "t", "life_share",
                        "key", "pv", 1.0, 1.0,
                    ),
                )
                conn.commit()
        finally:
            conn.close()
    finally:
        _cleanup(session_id)


def test_boundary_no_direct_send_path():
    """边界约束：LIFE 投递 seed 后没有自动产生 SEND 决策。

    receive_life_seed 后 proactive_decisions 表无新记录（无 SEND 决策）。
    """
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        conn = db.connect()
        try:
            before = conn.execute(
                "SELECT COUNT(*) FROM proactive_decisions WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
        finally:
            conn.close()

        receive_life_seed(
            source_event_type=LifeSeedSourceType.LIFE_EVENT,
            source_event_id="le-001",
            source_event_summary="事件",
        )

        # 投递 seed 后 proactive_decisions 无新记录（必须由 EAP 决策流程产生）
        conn = db.connect()
        try:
            after = conn.execute(
                "SELECT COUNT(*) FROM proactive_decisions WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
        finally:
            conn.close()
        assert after == before, "LIFE 投递 seed 不应自动产生 SEND 决策"
    finally:
        _cleanup(session_id)


# ---------- 6. schema 测试 ----------

def test_schema_version_includes_cds2_migration_62():
    """EAP remains frozen at 60; CDS migrations now advance the live schema to 62."""
    db.init_db()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        assert row[0] == "62"
    finally:
        conn.close()


def test_life_proactive_seeds_table_exists():
    """life_proactive_seeds 表存在。"""
    db.init_db()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='life_proactive_seeds'"
        ).fetchone()
        assert row is not None
        assert row["name"] == "life_proactive_seeds"
    finally:
        conn.close()


def test_life_proactive_seeds_check_constraints():
    """CHECK 约束：seed_kind 必须为 'life_share'，source_event_type 必须为 5 种之一。"""
    db.init_db()
    conn = db.connect()
    try:
        now = db.now()

        # 合法插入应该成功
        conn.execute(
            "INSERT INTO life_proactive_seeds"
            " (id, source_event_type, source_event_id, source_event_summary,"
            "  topic, origin_type, seed_kind, source_revision, source_hash,"
            "  consumed_at, consumed_episode_id, consumed_candidate_id,"
            "  rejected_at, rejection_reason,"
            "  idempotency_key, protocol_version, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, ?, ?, ?, ?)",
            (
                db.new_id(), "life_event", "x", "y", "t", "milestone",
                "life_share", "", "", "key1", "pv", now, now,
            ),
        )
        conn.commit()

        # 非法 seed_kind 应被 CHECK 拒绝
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO life_proactive_seeds"
                " (id, source_event_type, source_event_id, source_event_summary,"
                "  topic, origin_type, seed_kind, source_revision, source_hash,"
                "  consumed_at, consumed_episode_id, consumed_candidate_id,"
                "  rejected_at, rejection_reason,"
                "  idempotency_key, protocol_version, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'direct_send', '', '', "
                "  NULL, NULL, NULL, NULL, NULL, ?, ?, ?, ?)",
                (
                    db.new_id(), "life_event", "x2", "y", "t", "milestone",
                    "key2", "pv", now, now,
                ),
            )
            conn.commit()

        # 非法 source_event_type 应被 CHECK 拒绝
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO life_proactive_seeds"
                " (id, source_event_type, source_event_id, source_event_summary,"
                "  topic, origin_type, seed_kind, source_revision, source_hash,"
                "  consumed_at, consumed_episode_id, consumed_candidate_id,"
                "  rejected_at, rejection_reason,"
                "  idempotency_key, protocol_version, created_at, updated_at)"
                " VALUES (?, 'invalid_type', ?, ?, ?, ?, 'life_share', '', '', "
                "  NULL, NULL, NULL, NULL, NULL, ?, ?, ?, ?)",
                (
                    db.new_id(), "x3", "y", "t", "milestone",
                    "key3", "pv", now, now,
                ),
            )
            conn.commit()

        # 非法 origin_type 应被 CHECK 拒绝
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO life_proactive_seeds"
                " (id, source_event_type, source_event_id, source_event_summary,"
                "  topic, origin_type, seed_kind, source_revision, source_hash,"
                "  consumed_at, consumed_episode_id, consumed_candidate_id,"
                "  rejected_at, rejection_reason,"
                "  idempotency_key, protocol_version, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, 'invalid_origin', 'life_share', '', '', "
                "  NULL, NULL, NULL, NULL, NULL, ?, ?, ?, ?)",
                (
                    db.new_id(), "life_event", "x4", "y", "t",
                    "key4", "pv", now, now,
                ),
            )
            conn.commit()

        # CHECK 约束：consumed_episode_id 非空时 consumed_at 必须非空
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO life_proactive_seeds"
                " (id, source_event_type, source_event_id, source_event_summary,"
                "  topic, origin_type, seed_kind, source_revision, source_hash,"
                "  consumed_at, consumed_episode_id, consumed_candidate_id,"
                "  rejected_at, rejection_reason,"
                "  idempotency_key, protocol_version, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'life_share', '', '', "
                "  NULL, 'fake-episode-id', NULL, NULL, NULL, ?, ?, ?, ?)",
                (
                    db.new_id(), "life_event", "x5", "y", "t", "milestone",
                    "key5", "pv", now, now,
                ),
            )
            conn.commit()
    finally:
        conn.close()
