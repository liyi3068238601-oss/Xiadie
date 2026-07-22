"""EAP 公共 run 账本工具测试：source_hash、RunStatus、idempotency_key。"""
import hashlib
import json
import pytest

from app import db
from app.proactive import run_ledger
from app.proactive.protocols import PROACTIVE_DECISION_V2


def test_compute_source_hash_is_deterministic():
    """同一输入应产生同一 hash。"""
    msgs = [
        {"id": "m1", "role": "user", "content": "hello"},
        {"id": "m2", "role": "assistant", "content": "hi"},
    ]
    h1 = run_ledger.compute_source_hash(msgs)
    h2 = run_ledger.compute_source_hash(msgs)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_compute_source_hash_changes_on_content_change():
    """内容变化应产生不同 hash。"""
    msgs_a = [{"id": "m1", "role": "user", "content": "hello"}]
    msgs_b = [{"id": "m1", "role": "user", "content": "hi"}]
    assert run_ledger.compute_source_hash(msgs_a) != run_ledger.compute_source_hash(msgs_b)


def test_compute_source_hash_independent_of_order():
    """消息顺序变化应产生不同 hash（按输入顺序计算，不重排）。"""
    msgs_a = [
        {"id": "m1", "role": "user", "content": "a"},
        {"id": "m2", "role": "assistant", "content": "b"},
    ]
    msgs_b = [
        {"id": "m2", "role": "assistant", "content": "b"},
        {"id": "m1", "role": "user", "content": "a"},
    ]
    assert run_ledger.compute_source_hash(msgs_a) != run_ledger.compute_source_hash(msgs_b)


def test_compute_source_hash_matches_reference_implementation():
    """与参考实现（手动 sha256）一致。"""
    msgs = [{"id": "m1", "role": "user", "content": "hello"}]
    normalized = [{"id": "m1", "role": "user", "content": "hello"}]
    payload = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert run_ledger.compute_source_hash(msgs) == expected


def test_compute_source_hash_ignores_extra_keys():
    """只取 id/role/content，忽略其他键。"""
    msgs_full = [{"id": "m1", "role": "user", "content": "hello", "extra": "ignored"}]
    msgs_min = [{"id": "m1", "role": "user", "content": "hello"}]
    assert run_ledger.compute_source_hash(msgs_full) == run_ledger.compute_source_hash(msgs_min)


def test_make_idempotency_key_format():
    """幂等键格式为 protocol:part1:part2。"""
    key = run_ledger.make_idempotency_key(PROACTIVE_DECISION_V2, "ep1", "turn1")
    assert key == "proactive-decision-v2:ep1:turn1"


def test_make_idempotency_key_with_single_part():
    """单参数幂等键。"""
    key = run_ledger.make_idempotency_key(PROACTIVE_DECISION_V2, "ep1")
    assert key == "proactive-decision-v2:ep1"


def test_run_status_constants():
    """RunStatus 常量与 affect_observer_runs.status 对齐。"""
    assert run_ledger.RunStatus.QUEUED == "queued"
    assert run_ledger.RunStatus.RUNNING == "running"
    assert run_ledger.RunStatus.APPLIED == "applied"
    assert run_ledger.RunStatus.RECOVERY_PENDING == "recovery_pending"
    assert run_ledger.RunStatus.EXHAUSTED == "exhausted"
    assert run_ledger.RunStatus.SKIPPED == "skipped"


def test_legacy_adapter_does_not_require_table_migration():
    adapted = run_ledger.adapt_legacy_run(
        {"id": "legacy-1", "status": "done", "revision": "7", "hash": "abc"},
        legacy_table="conversation_summary_runs", protocol_version="conversation-summary-v1",
        revision_field="revision", hash_field="hash",
    )
    assert adapted.legacy_id == "legacy-1"
    assert adapted.source_revision == "7"
    assert adapted.source_hash == "abc"
    assert adapted.status == run_ledger.RunStatus.SKIPPED


def test_decision_run_repository_is_idempotent_and_auditable():
    db.init_db()
    key = run_ledger.make_idempotency_key(PROACTIVE_DECISION_V2, db.new_id())
    created, was_created = run_ledger.create_or_get_run(
        task_kind="proactive_decision", protocol_version=PROACTIVE_DECISION_V2,
        source_type="candidate", source_id="candidate-1", source_revision="r1",
        source_hash="a" * 64, idempotency_key=key, max_attempts=2, now=100.0,
    )
    duplicate, duplicate_created = run_ledger.create_or_get_run(
        task_kind="proactive_decision", protocol_version=PROACTIVE_DECISION_V2,
        source_type="candidate", source_id="candidate-1", source_revision="r1",
        source_hash="a" * 64, idempotency_key=key, max_attempts=2, now=101.0,
    )
    assert was_created is True and duplicate_created is False
    assert duplicate.id == created.id
    running = run_ledger.transition_run(
        created.id, run_ledger.RunStatus.RUNNING, provider_id="mock", model_id="xiadie-mock",
        now=102.0,
    )
    applied = run_ledger.transition_run(
        created.id, run_ledger.RunStatus.APPLIED, latency_ms=12,
        input_tokens=3, output_tokens=4, warnings=["bounded"], now=103.0,
    )
    assert running.attempt_count == 1
    assert applied.completed_at == 103.0
    assert applied.provider_id == "mock"
    assert applied.model_id == "xiadie-mock"
    assert applied.warnings == ["bounded"]


def test_decision_run_rejects_invalid_transition():
    db.init_db()
    key = run_ledger.make_idempotency_key(PROACTIVE_DECISION_V2, db.new_id())
    run, _ = run_ledger.create_or_get_run(
        task_kind="test", protocol_version=PROACTIVE_DECISION_V2,
        source_type="candidate", source_id="candidate-2", source_revision="",
        source_hash="b" * 64, idempotency_key=key,
    )
    with pytest.raises(ValueError, match="invalid DecisionRun transition"):
        run_ledger.transition_run(run.id, run_ledger.RunStatus.APPLIED)
