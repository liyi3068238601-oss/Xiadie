"""EAP v0.2 关系意义判断测试。

覆盖：
1. 9 种标签 delta 映射（ordinary_exchange 的 bond_delta = 0 等）
2. 幂等：同一 source_message_id 重复调用只产生一条建议
3. 单轮限幅：delta 受 SINGLE_TURN_CAPS 限制
4. 来源证据校验：source_message_id 和 session_id 必须存在（外键约束）
5. 用户沉默不产生负变化：除 conflict 外其他标签的 trust_delta >= 0
6. schema：migration 50 后 schema_version = "53"，表存在，9 种标签 CHECK 约束
"""
import pytest
from concurrent.futures import ThreadPoolExecutor

from app import db
from app.affect import repository
from app.proactive import relationship
from app.proactive.relationship import RelationshipLabel


def _setup_session(session_id: str) -> None:
    """插入测试 session，满足外键约束。"""
    now = db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (session_id, "relationship 测试", now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_message(session_id: str, content: str = "测试消息") -> str:
    """插入测试 user 消息，返回 message_id（满足 source_message_id 外键）。"""
    msg_id = db.new_id()
    now = db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (msg_id, session_id, "user", content, now),
        )
        conn.commit()
    finally:
        conn.close()
    return msg_id


def _insert_assistant_message(session_id: str, content: str = "测试回复") -> str:
    msg_id = db.new_id()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (msg_id, session_id, "assistant", content, db.now()),
        )
        conn.commit()
    finally:
        conn.close()
    return msg_id


def _cleanup(session_id: str) -> None:
    conn = db.connect()
    try:
        conn.execute(
            "DELETE FROM episode_relationship_delta_suggestions WHERE session_id=?",
            (session_id,),
        )
        conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        conn.commit()
    finally:
        conn.close()


# ---------- 1. 9 种标签 delta 映射测试 ----------

def test_ordinary_exchange_has_zero_bond_delta():
    """ordinary_exchange 的 bond_delta = 0（普通问答不产生显著 bond 增量）。"""
    deltas = relationship.LABEL_DELTAS[RelationshipLabel.ORDINARY_EXCHANGE]
    assert deltas["bond_delta"] == 0.0


def test_shared_appreciation_has_positive_bond_delta():
    """shared_appreciation 的 bond_delta > 0（明确感谢产生 bond 增量）。"""
    deltas = relationship.LABEL_DELTAS[RelationshipLabel.SHARED_APPRECIATION]
    assert deltas["bond_delta"] > 0.0


def test_reliable_help_has_positive_trust_delta():
    """reliable_help 的 trust_delta > 0（可靠帮助提升 trust）。"""
    deltas = relationship.LABEL_DELTAS[RelationshipLabel.RELIABLE_HELP]
    assert deltas["trust_delta"] > 0.0


def test_boundary_respected_has_positive_trust_delta():
    """boundary_respected 的 trust_delta > 0（边界被尊重提升 trust）。"""
    deltas = relationship.LABEL_DELTAS[RelationshipLabel.BOUNDARY_RESPECTED]
    assert deltas["trust_delta"] > 0.0


def test_boundary_repair_has_positive_trust_delta():
    """boundary_repair 的 trust_delta > 0（边界修复提升 trust）。"""
    deltas = relationship.LABEL_DELTAS[RelationshipLabel.BOUNDARY_REPAIR]
    assert deltas["trust_delta"] > 0.0


def test_conflict_has_negative_trust_delta():
    """conflict 的 trust_delta < 0（明确冲突可降低 trust）。"""
    deltas = relationship.LABEL_DELTAS[RelationshipLabel.CONFLICT]
    assert deltas["trust_delta"] < 0.0


def test_all_labels_have_valid_deltas():
    """9 种标签的 delta 都在 SINGLE_TURN_CAPS 范围内。"""
    assert len(relationship.ALL_LABELS) == 9
    for label in relationship.ALL_LABELS:
        deltas = relationship.LABEL_DELTAS[label]
        bond_lo, bond_hi = relationship.SINGLE_TURN_CAPS["bond"]
        fam_lo, fam_hi = relationship.SINGLE_TURN_CAPS["familiarity"]
        trust_lo, trust_hi = relationship.SINGLE_TURN_CAPS["trust"]
        att_lo, att_hi = relationship.SINGLE_TURN_CAPS["attachment"]
        rap_lo, rap_hi = relationship.SINGLE_TURN_CAPS["rapport"]
        assert bond_lo <= deltas["bond_delta"] <= bond_hi, label
        assert fam_lo <= deltas["familiarity_delta"] <= fam_hi, label
        assert trust_lo <= deltas["trust_delta"] <= trust_hi, label
        assert att_lo <= deltas["attachment_delta"] <= att_hi, label
        assert rap_lo <= deltas["rapport_delta"] <= rap_hi, label


# ---------- 2. 幂等测试 ----------

def test_process_delta_is_idempotent():
    """同一 source_message_id 重复调用只产生一条建议。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    msg_id = _insert_message(session_id)
    try:
        first = relationship.process_relationship_delta(session_id, msg_id, RelationshipLabel.SHARED_APPRECIATION)
        second = relationship.process_relationship_delta(session_id, msg_id, RelationshipLabel.SHARED_APPRECIATION)
        assert first is not None
        assert second is None  # 幂等：第二次返回 None
        # DB 中只有一条
        conn = db.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM episode_relationship_delta_suggestions WHERE source_message_id=?",
                (msg_id,),
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 1
    finally:
        _cleanup(session_id)


def test_process_delta_returns_none_if_exists():
    """已存在时返回 None（通过 get_suggestion_by_source_message 可查到）。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    msg_id = _insert_message(session_id)
    try:
        first = relationship.process_relationship_delta(session_id, msg_id, RelationshipLabel.RELIABLE_HELP)
        assert first is not None
        # 查询应能查到
        found = relationship.get_suggestion_by_source_message(msg_id)
        assert found is not None
        assert found.id == first.id
        # 再次处理应返回 None
        again = relationship.process_relationship_delta(session_id, msg_id, RelationshipLabel.RELIABLE_HELP)
        assert again is None
    finally:
        _cleanup(session_id)


# ---------- 3. 单轮限幅测试 ----------

def test_delta_clamped_to_caps():
    """delta 被 SINGLE_TURN_CAPS 限制（通过 process 落库后值在 cap 范围内）。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    msg_id = _insert_message(session_id)
    try:
        # conflict 的 trust_delta = -0.005，在 cap (-0.01, 0.005) 范围内
        record = relationship.process_relationship_delta(
            session_id, msg_id, RelationshipLabel.CONFLICT,
        )
        assert record is not None
        bond_lo, bond_hi = relationship.SINGLE_TURN_CAPS["bond"]
        trust_lo, trust_hi = relationship.SINGLE_TURN_CAPS["trust"]
        rap_lo, rap_hi = relationship.SINGLE_TURN_CAPS["rapport"]
        assert bond_lo <= record.bond_delta <= bond_hi
        assert trust_lo <= record.trust_delta <= trust_hi
        assert rap_lo <= record.rapport_delta <= rap_hi
        # cap_bond_applied / cap_trust_applied 记录限幅后值
        assert record.cap_bond_applied == record.bond_delta
        assert record.cap_trust_applied == record.trust_delta
    finally:
        _cleanup(session_id)


def test_clamp_helper():
    """_clamp 工具函数正确限幅。"""
    assert relationship._clamp(0.5, 0.0, 0.3) == 0.3
    assert relationship._clamp(-0.5, 0.0, 0.3) == 0.0
    assert relationship._clamp(0.1, 0.0, 0.3) == 0.1


# ---------- 4. 来源证据校验测试 ----------

def test_process_delta_requires_valid_source_message():
    """source_message_id 必须存在（外键约束）。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    try:
        # 不存在的 message_id
        fake_msg_id = db.new_id()
        with pytest.raises(Exception):
            relationship.process_relationship_delta(
                session_id, fake_msg_id, RelationshipLabel.ORDINARY_EXCHANGE,
            )
    finally:
        _cleanup(session_id)


def test_process_delta_requires_valid_session():
    """session_id 必须存在（外键约束）。"""
    db.init_db()
    # 不存在的 session_id，但因为 messages 表也要求 session_id 存在，
    # 我们先创建一个 message（不可能，因为 session 不存在），所以这里直接测试
    # 在一个真实 session 中插入 message，然后用伪造的 session_id 调用 process
    real_session = db.new_id()
    _setup_session(real_session)
    msg_id = _insert_message(real_session)
    fake_session = db.new_id()
    try:
        with pytest.raises(Exception):
            relationship.process_relationship_delta(
                fake_session, msg_id, RelationshipLabel.ORDINARY_EXCHANGE,
            )
    finally:
        _cleanup(real_session)


def test_invalid_label_returns_none():
    """无效标签返回 None（不写入 DB）。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    msg_id = _insert_message(session_id)
    try:
        result = relationship.process_relationship_delta(
            session_id, msg_id, "invalid_label_xyz",
        )
        assert result is None
        # DB 中无记录
        assert relationship.get_suggestion_by_source_message(msg_id) is None
    finally:
        _cleanup(session_id)


# ---------- 5. 用户沉默不产生负变化测试 ----------
# spec："用户沉默不降低 bond/trust"

def test_silence_does_not_produce_negative_delta():
    """所有标签中，只有 conflict 产生负 trust；其他标签的 trust_delta >= 0。
    用户沉默（不主动发起冲突）不会降低 trust。
    """
    for label in relationship.ALL_LABELS:
        deltas = relationship.LABEL_DELTAS[label]
        if label == RelationshipLabel.CONFLICT:
            # conflict 是唯一允许负 trust 的标签
            assert deltas["trust_delta"] < 0
        else:
            assert deltas["trust_delta"] >= 0, f"{label} trust_delta should be >= 0"
            assert deltas["bond_delta"] >= 0, f"{label} bond_delta should be >= 0"


def test_ordinary_exchange_produces_no_negative_delta():
    """ordinary_exchange 的所有 delta >= 0（普通问答不降低任何维度）。"""
    deltas = relationship.LABEL_DELTAS[RelationshipLabel.ORDINARY_EXCHANGE]
    for key, value in deltas.items():
        assert value >= 0, f"{key} should be >= 0 for ordinary_exchange"


# ---------- 6. schema 测试 ----------

def test_schema_version_is_52():
    """migration 54 后 schema_version = '54'。"""
    db.init_db()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        assert row[0] == "61"
    finally:
        conn.close()


def test_episode_relationship_delta_suggestions_table_exists():
    """episode_relationship_delta_suggestions 表存在。"""
    db.init_db()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='episode_relationship_delta_suggestions'"
        ).fetchone()
        assert row is not None
        assert row["name"] == "episode_relationship_delta_suggestions"
    finally:
        conn.close()


def test_table_has_9_label_check_constraint():
    """CHECK 约束允许 9 种标签，拒绝其他值。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    # 预插入 9 条 message（每条对应一种标签）+ 1 条用于无效标签测试
    msg_ids = []
    now = db.now()
    conn = db.connect()
    try:
        for label in relationship.ALL_LABELS:
            new_msg = db.new_id()
            conn.execute(
                "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
                (new_msg, session_id, "user", f"msg-{label}", now),
            )
            msg_ids.append(new_msg)
        # 为无效标签测试额外插入一条
        invalid_msg_id = db.new_id()
        conn.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (invalid_msg_id, session_id, "user", "msg-invalid", now),
        )
        conn.commit()

        # 9 种标签都应能写入
        for label, msg_id in zip(relationship.ALL_LABELS, msg_ids):
            record_id = db.new_id()
            conn.execute(
                "INSERT INTO episode_relationship_delta_suggestions"
                " (id, session_id, source_message_id, episode_id, relationship_label,"
                "  bond_delta, familiarity_delta, trust_delta, attachment_delta, rapport_delta,"
                "  cap_bond_applied, cap_trust_applied, idempotency_key, status,"
                "  protocol_version, created_at, updated_at)"
                " VALUES (?, ?, ?, NULL, ?, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, ?, 'proposed', ?, ?, ?)",
                (record_id, session_id, msg_id, label,
                 f"key-{label}", "relationship-meaning-v1", now, now),
            )
        conn.commit()
        # 无效标签应被拒绝
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO episode_relationship_delta_suggestions"
                " (id, session_id, source_message_id, episode_id, relationship_label,"
                "  bond_delta, familiarity_delta, trust_delta, attachment_delta, rapport_delta,"
                "  cap_bond_applied, cap_trust_applied, idempotency_key, status,"
                "  protocol_version, created_at, updated_at)"
                " VALUES (?, ?, ?, NULL, 'invalid_label', 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, ?, 'proposed', ?, ?, ?)",
                (db.new_id(), session_id, invalid_msg_id,
                 "key-invalid", "relationship-meaning-v1", now, now),
            )
    finally:
        conn.close()
        _cleanup(session_id)


def test_idempotency_key_is_unique():
    """idempotency_key 是 UNIQUE 约束（重复插入应失败）。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    msg1 = _insert_message(session_id, content="msg1")
    msg2 = _insert_message(session_id, content="msg2")
    conn = db.connect()
    try:
        now = db.now()
        # 插入第一条
        conn.execute(
            "INSERT INTO episode_relationship_delta_suggestions"
            " (id, session_id, source_message_id, episode_id, relationship_label,"
            "  bond_delta, familiarity_delta, trust_delta, attachment_delta, rapport_delta,"
            "  cap_bond_applied, cap_trust_applied, idempotency_key, status,"
            "  protocol_version, created_at, updated_at)"
            " VALUES (?, ?, ?, NULL, ?, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, ?, 'proposed', ?, ?, ?)",
            (db.new_id(), session_id, msg1, RelationshipLabel.ORDINARY_EXCHANGE,
             "dup-key", "relationship-meaning-v1", now, now),
        )
        conn.commit()
        # 用相同 idempotency_key 再插入应失败
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO episode_relationship_delta_suggestions"
                " (id, session_id, source_message_id, episode_id, relationship_label,"
                "  bond_delta, familiarity_delta, trust_delta, attachment_delta, rapport_delta,"
                "  cap_bond_applied, cap_trust_applied, idempotency_key, status,"
                "  protocol_version, created_at, updated_at)"
                " VALUES (?, ?, ?, NULL, ?, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, ?, 'proposed', ?, ?, ?)",
                (db.new_id(), session_id, msg2, RelationshipLabel.SHARED_APPRECIATION,
                 "dup-key", "relationship-meaning-v1", now, now),
            )
    finally:
        conn.close()
        _cleanup(session_id)


def test_protocol_version_is_relationship_meaning_v1():
    """落库的 protocol_version 应为 RELATIONSHIP_MEANING_V1。"""
    db.init_db()
    session_id = db.new_id()
    _setup_session(session_id)
    msg_id = _insert_message(session_id)
    try:
        record = relationship.process_relationship_delta(
            session_id, msg_id, RelationshipLabel.SHARED_SUCCESS,
        )
        assert record is not None
        # 从 DB 查询确认
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT protocol_version FROM episode_relationship_delta_suggestions "
                "WHERE id=?",
                (record.id,),
            ).fetchone()
        finally:
            conn.close()
        from app.proactive.protocols import RELATIONSHIP_MEANING_V1
        assert row["protocol_version"] == RELATIONSHIP_MEANING_V1
    finally:
        _cleanup(session_id)


def test_apply_and_revoke_are_atomic_idempotent_and_traceable():
    db.init_db()
    repository.reset()
    session_id = db.new_id()
    _setup_session(session_id)
    user_id = _insert_message(session_id, "谢谢你的帮助")
    assistant_id = _insert_assistant_message(session_id, "不用客气")
    try:
        before = repository.get_snapshot(advance_time=False)
        proposed = relationship.process_relationship_delta(
            session_id, user_id, RelationshipLabel.SHARED_APPRECIATION,
            source_assistant_message_id=assistant_id,
            evidence=[{"speaker": "user", "quote": "谢谢你的帮助"}],
            reason="explicit appreciation", confidence=0.95,
        )
        applied = relationship.apply_suggestion(proposed.id)
        repeated = relationship.apply_suggestion(proposed.id)
        after = repository.get_snapshot(advance_time=False)
        assert applied.status == repeated.status == "applied"
        assert after["relationship"]["bond"] == pytest.approx(
            before["relationship"]["bond"] + proposed.bond_delta
        )
        revoked = relationship.revoke_suggestion(proposed.id, "source_corrected")
        restored = repository.get_snapshot(advance_time=False)
        assert revoked.status == "revoked"
        assert restored["relationship"]["bond"] == pytest.approx(
            before["relationship"]["bond"]
        )
        assert revoked.revocation_reason.startswith("relationship-meaning-v1:revoke")
    finally:
        _cleanup(session_id)


def test_source_change_revokes_then_allows_new_revision():
    db.init_db()
    repository.reset()
    session_id = db.new_id()
    _setup_session(session_id)
    user_id = _insert_message(session_id, "谢谢你")
    assistant_id = _insert_assistant_message(session_id, "不用客气")
    try:
        first = relationship.process_relationship_delta(
            session_id, user_id, RelationshipLabel.SHARED_APPRECIATION,
            source_assistant_message_id=assistant_id,
            evidence=[{"speaker": "user", "quote": "谢谢你"}],
            reason="explicit appreciation", confidence=0.9,
        )
        relationship.apply_suggestion(first.id)
        conn = db.connect()
        try:
            conn.execute("UPDATE messages SET content='普通问题' WHERE id=?", (user_id,))
            conn.commit()
        finally:
            conn.close()
        assert relationship.revoke_invalidated_suggestions() == 1
        assert relationship.get_suggestion(first.id).status == "revoked"
        second = relationship.process_relationship_delta(
            session_id, user_id, RelationshipLabel.ORDINARY_EXCHANGE,
            source_assistant_message_id=assistant_id,
            reason="corrected ordinary exchange", confidence=0.9,
        )
        assert second is not None and second.source_revision != first.source_revision
        assert relationship.apply_suggestion(second.id).status == "applied"
    finally:
        _cleanup(session_id)


def test_revoke_preserves_later_manual_relationship_reset():
    db.init_db()
    repository.reset()
    session_id = db.new_id()
    _setup_session(session_id)
    user_id = _insert_message(session_id, "谢谢你")
    try:
        proposed = relationship.process_relationship_delta(
            session_id, user_id, RelationshipLabel.SHARED_APPRECIATION,
            evidence=[{"speaker": "user", "quote": "谢谢你"}],
            reason="explicit appreciation", confidence=0.9,
        )
        relationship.apply_suggestion(proposed.id)
        repository.reset()  # source=user audit after apply
        manual = repository.get_snapshot(advance_time=False)["relationship"]["bond"]
        revoked = relationship.revoke_suggestion(proposed.id, "source_deleted")
        assert repository.get_snapshot(advance_time=False)["relationship"]["bond"] == manual
        assert "manual_change_preserved" in revoked.revocation_reason
    finally:
        _cleanup(session_id)


def test_concurrent_apply_changes_relationship_only_once():
    db.init_db()
    repository.reset()
    session_id = db.new_id()
    _setup_session(session_id)
    user_id = _insert_message(session_id, "谢谢你")
    try:
        proposed = relationship.process_relationship_delta(
            session_id, user_id, RelationshipLabel.SHARED_APPRECIATION,
            evidence=[{"speaker": "user", "quote": "谢谢你"}],
            reason="explicit appreciation", confidence=1.0,
        )
        before = repository.get_snapshot(advance_time=False)["relationship"]["bond"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: relationship.apply_suggestion(proposed.id), range(2)))
        after = repository.get_snapshot(advance_time=False)["relationship"]["bond"]
        assert all(item.status == "applied" for item in results)
        assert after == pytest.approx(before + proposed.bond_delta)
    finally:
        _cleanup(session_id)


def test_deleted_source_is_revoked_with_compensation():
    db.init_db()
    repository.reset()
    session_id = db.new_id()
    _setup_session(session_id)
    user_id = _insert_message(session_id, "谢谢你")
    try:
        before = repository.get_snapshot(advance_time=False)["relationship"]["bond"]
        proposed = relationship.process_relationship_delta(
            session_id, user_id, RelationshipLabel.SHARED_APPRECIATION,
            evidence=[{"speaker": "user", "quote": "谢谢你"}],
            reason="explicit appreciation", confidence=1.0,
        )
        relationship.apply_suggestion(proposed.id)
        conn = db.connect()
        try:
            conn.execute("DELETE FROM messages WHERE id=?", (user_id,))
            conn.commit()
        finally:
            conn.close()
        assert relationship.revoke_invalidated_suggestions() == 1
        revoked = relationship.get_suggestion(proposed.id)
        assert revoked.status == "revoked" and revoked.source_message_id is None
        assert repository.get_snapshot(advance_time=False)["relationship"]["bond"] == pytest.approx(before)
    finally:
        _cleanup(session_id)


def test_revoke_uses_actual_clamped_delta_at_relationship_boundary():
    db.init_db()
    state = repository.reset()
    state["relationship"]["bond"] = 0.9995
    repository.save_snapshot(state, event_type="manual", source="user", reason="manual boundary")
    session_id = db.new_id()
    _setup_session(session_id)
    user_id = _insert_message(session_id, "我们成功了")
    try:
        proposed = relationship.process_relationship_delta(
            session_id, user_id, RelationshipLabel.SHARED_SUCCESS,
            evidence=[{"speaker": "user", "quote": "我们成功了"}],
            reason="shared success", confidence=1.0,
        )
        applied = relationship.apply_suggestion(proposed.id)
        assert applied.cap_bond_applied == pytest.approx(0.0005)
        relationship.revoke_suggestion(proposed.id, "source_changed")
        assert repository.get_snapshot(advance_time=False)["relationship"]["bond"] == pytest.approx(0.9995)
    finally:
        _cleanup(session_id)
