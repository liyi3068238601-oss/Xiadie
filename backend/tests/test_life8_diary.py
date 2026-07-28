from __future__ import annotations

import hashlib

import pytest

from app import db, diary, life_events


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture(autouse=True)
def clean_diary():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM diary_entry_sources")
        conn.execute("DELETE FROM diary_entry_revisions")
        conn.execute("DELETE FROM diary_entries")
        conn.execute("DELETE FROM continuity_threads")
        conn.execute("DELETE FROM life_event_audit_events")
        conn.execute("DELETE FROM life_event_sources")
        conn.execute("DELETE FROM life_event_revisions")
        conn.execute("DELETE FROM life_events")
        conn.commit()
    finally:
        conn.close()


def _event(*, layer="observed", semantic="event"):
    source = life_events.SourceRef("system_observation", semantic, "1", _hash(semantic))
    return life_events.create_event(
        event_kind="observation", world_layer=layer, summary="synthetic source",
        source_refs=(source,),
        idempotency_key=life_events.make_idempotency_key(
            event_kind="observation", source_refs=(source,), semantic_key=semantic,
        ),
    )[0]


def _entry(*, body="今天整理了窗边的光影。", policy="private", thread_id=None, semantic="event"):
    event = _event(semantic=semantic)
    return diary.create_entry(
        entry_date="2026-07-26", title="一段记录", body=body,
        source_kind="life_event", source_id=event["id"], source_revision=str(event["revision"]),
        source_hash=_hash(f"source:{event['id']}"), share_policy=policy, thread_id=thread_id,
    )


def test_schema_70_adds_diary_threads_sources_and_versions():
    conn = db.connect()
    try:
        version = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert version == "76"
    assert {"diary_entries", "diary_entry_revisions", "diary_entry_sources", "continuity_threads"} <= tables


def test_planned_life_event_is_not_valid_diary_evidence():
    source = life_events.SourceRef("system_observation", "planned", "1", _hash("planned"))
    planned = life_events.create_event(
        event_kind="activity", world_layer="planned", summary="plan", source_refs=(source,),
        idempotency_key=life_events.make_idempotency_key(
            event_kind="activity", source_refs=(source,), semantic_key="planned",
        ),
    )[0]
    with pytest.raises(diary.DiaryError) as exc:
        diary.create_entry(
            entry_date="2026-07-26", title="错误事实", body="把计划当成做过",
            source_kind="life_event", source_id=planned["id"], source_revision="1",
            source_hash=_hash("planned-source"),
        )
    assert exc.value.code == "source_unavailable"


def test_sensitive_filter_and_provider_specific_share_authorization():
    entry = _entry(body="今天想到一段创伤经历。", policy="ask")
    assert entry["sensitivity"] == "sensitive"
    assert not diary.can_share(
        entry, provider_location="remote", certification_level="decision_verified",
        explicit_authorization=False,
    )
    assert diary.can_share(
        entry, provider_location="remote", certification_level="decision_verified",
        explicit_authorization=True,
    )
    assert diary.can_share(
        entry, provider_location="local", certification_level="local_sensitive_verified",
        explicit_authorization=False,
    )
    assert not diary.can_share(
        entry, provider_location="local", certification_level="decision_verified",
        explicit_authorization=False,
    )


@pytest.mark.parametrize(
    "value",
    (
        "卡号 6222 0200 0000 0000",
        "证件 11010519491231002X",
        "电话 13800138000",
        "联系 xiadie@example.com",
    ),
)
def test_sensitive_filter_recognizes_common_identifier_formats(value):
    assert diary.classify_sensitivity("修订", value) == "sensitive"


def test_sensitive_filter_does_not_treat_ordinary_numbers_as_identifiers():
    assert diary.classify_sensitivity("散步", "今天走了 12345 步") == "normal"


def test_private_and_never_are_hard_boundaries_even_with_authorization():
    for policy in ("private", "never"):
        entry = _entry(policy=policy, semantic=policy)
        assert not diary.can_share(
            entry, provider_location="local", certification_level="local_sensitive_verified",
            explicit_authorization=True,
        )


def test_motif_fatigue_rejects_fourth_repetition():
    thread = diary.create_thread(title="窗边观察", motif_code="window_light")
    for index in range(3):
        _entry(thread_id=thread["id"], semantic=f"motif-{index}")
    with pytest.raises(diary.DiaryError) as exc:
        _entry(thread_id=thread["id"], semantic="motif-4")
    assert exc.value.code == "motif_fatigue"


def test_revision_is_append_only_and_reclassifies_sensitivity():
    entry = _entry(policy="natural")
    revised = diary.revise_entry(
        entry["id"], expected_revision=1, title="修订", body="包含密码信息", reason_code="self_edit",
    )
    assert revised["revision"] == 2 and revised["sensitivity"] == "sensitive"
    conn = db.connect()
    try:
        revisions = conn.execute(
            "SELECT revision FROM diary_entry_revisions WHERE diary_entry_id=? ORDER BY revision",
            (entry["id"],),
        ).fetchall()
    finally:
        conn.close()
    assert [row["revision"] for row in revisions] == [1, 2]


def test_invalidated_last_source_revokes_entry_on_rebuild():
    event = _event(semantic="revoke-source")
    entry = diary.create_entry(
        entry_date="2026-07-26", title="记录", body="有来源",
        source_kind="life_event", source_id=event["id"], source_revision="1",
        source_hash=_hash("revoke-diary"),
    )
    life_events.revoke_event(event["id"], expected_revision=1, reason_code="source_revoked")
    assert diary.rebuild_invalid_sources() == 1
    assert diary.get_entry(entry["id"])["status"] == "revoked"


def test_diary_creation_does_not_write_user_memory_or_delivery():
    conn = db.connect()
    try:
        before_memory = conn.execute("SELECT COUNT(*) FROM memory_fragments").fetchone()[0]
        before_delivery = conn.execute("SELECT COUNT(*) FROM proactive_deliveries").fetchone()[0]
    finally:
        conn.close()
    _entry(policy="natural")
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM memory_fragments").fetchone()[0] == before_memory
        assert conn.execute("SELECT COUNT(*) FROM proactive_deliveries").fetchone()[0] == before_delivery
    finally:
        conn.close()


def test_thirty_day_fallback_corpus_has_no_exact_template_replay():
    bodies = []
    for day in range(1, 31):
        entry_date = f"2026-06-{day:02d}"
        title, body = diary.deterministic_fallback_text(
            entry_date=entry_date, source_summary=f"第 {day} 天的合成生活片段",
        )
        bodies.append((title, body))
    assert len(set(bodies)) == 30
    assert all(len(title) <= 160 and len(body) <= 8_000 for title, body in bodies)
