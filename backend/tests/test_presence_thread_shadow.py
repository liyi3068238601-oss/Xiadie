"""CDS.3 PresenceAndThreadObserver stays bounded, synthetic and Shadow-only."""
from __future__ import annotations

import hashlib
import json
import runpy
from pathlib import Path

import pytest

from app import cognitive_decision as cds
from app import db, presence_thread_shadow as observer
from app.proactive import presence

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "cds3_presence_shadow_v1.json"
REPORT_PATH = PROJECT_DIR / "docs" / "reports" / "cds-3-presence-shadow.json"
GENERATOR_PATH = BACKEND_DIR / "scripts" / "generate_cds3_presence_fixture.py"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _payload(text: str, *, message_id: str | None = "m-synthetic",
             silence: bool = False) -> observer.PresenceThreadInput:
    legacy = presence.detect_presence_signals(text)
    return observer.PresenceThreadInput(
        candidate_ids=observer.candidate_ids(), source_message_id=message_id,
        valid_message_ids=(message_id,) if message_id else (), text=text,
        silence_observed=silence, legacy_presence_state=legacy.user_status,
        legacy_open_thread=legacy.open_thread,
        legacy_open_thread_topic=legacy.open_thread_topic,
    )


def test_fixture_is_deterministic_synthetic_and_has_900_balanced_turns():
    fixture = _fixture()
    generated = runpy.run_path(str(GENERATOR_PATH))["build_fixture"]()
    assert fixture == generated
    assert fixture["synthetic_only"] is True and fixture["contains_user_data"] is False
    assert fixture["scenario_count"] == len(fixture["cases"]) == 900
    groups = {case["group"] for case in fixture["cases"]}
    assert len(groups) == 15
    assert all(sum(case["group"] == group for case in fixture["cases"]) == 60 for group in groups)


def test_report_is_body_free_bound_to_fixture_and_passes_completion_gates():
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["fixture_sha256"] == hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    assert report["sample_count"] == 900 and len(report["outcomes"]) == 900
    assert report["shadow_exact_rate"] == 1.0 and report["source_binding_rate"] == 1.0
    assert report["completion_gates"] == {
        "goodnight_expected_return_error_rate": 0.0,
        "test_departure_open_thread_rate": 1.0,
        "unknown_silence_rejection_rate": 0.0,
    }
    encoded = json.dumps(report, ensure_ascii=False)
    assert "input" not in encoded and "raw_model_output" not in encoded


def test_registry_contract_is_kind_specific_and_cannot_exceed_shadow():
    definition = cds.REGISTRY.get(observer.DECISION_KIND)
    assert definition.input_type is observer.PresenceThreadInput
    assert definition.result_type is observer.PresenceThreadResult
    assert definition.mode is cds.DecisionMode.SHADOW
    assert definition.fallback_owner == definition.application_owner == "eap"
    assert definition.privacy_class == "user_private"
    assert definition.model_binding_revision == cds.MODEL_BINDING_POLICY_VERSION


@pytest.mark.parametrize(
    ("text", "state", "expect_return", "threads"),
    [
        ("晚安", "away_sleep", "unknown", ()),
        ("我去测试一下", "away_brief", "yes", ("test_result",)),
        ("翻译晚安这个词", "online", "unknown", ()),
        ("测试一下这个函数", "online", "unknown", ()),
    ],
)
def test_shadow_semantics_are_conservative(text, state, expect_return, threads):
    result = observer.observe_shadow(_payload(text))
    observer.validate(_payload(text), result)
    assert (result.presence_state, result.expect_return, result.open_threads) == (
        state, expect_return, threads,
    )


def test_unknown_silence_is_never_rejection_or_relationship_fact():
    result = observer.observe_shadow(_payload("", message_id=None, silence=True))
    assert result.presence_state == "unknown"
    assert result.conversation_closure == "unknown"
    assert result.evidence_message_ids == () and result.reason_codes == ("unknown_silence",)


def test_existing_bounded_thread_survives_the_return_turn_in_shadow():
    payload = _payload("测试完成了")
    payload = observer.PresenceThreadInput(
        **{**payload.__dict__, "current_open_threads": ("test_result",)}
    )
    result = observer.observe_shadow(payload)
    assert result.presence_state == "online"
    assert result.open_threads == ("test_result",)
    assert result.reason_codes == ("thread_continuation",)


def test_strong_departure_signal_wins_without_erasing_the_existing_thread():
    payload = _payload("晚安")
    payload = observer.PresenceThreadInput(
        **{**payload.__dict__, "current_open_threads": ("test_result",)}
    )
    result = observer.observe_shadow(payload)
    assert result.presence_state == "away_sleep"
    assert result.open_threads == ("test_result",)
    assert result.followup_allowed is False


def test_validator_rejects_unbound_evidence_and_thread_ids():
    payload = _payload("我去测试一下")
    valid = observer.observe_shadow(payload)
    with pytest.raises(cds.DecisionProtocolError, match="source message"):
        observer.validate(payload, observer.PresenceThreadResult(
            **{**valid.__dict__, "evidence_message_ids": ("invented",)}
        ))
    with pytest.raises(cds.DecisionProtocolError, match="thread semantics"):
        observer.validate(payload, observer.PresenceThreadResult(
            **{**valid.__dict__, "open_threads": ("invented",)}
        ))


def test_shadow_run_cannot_apply_or_write_eap_presence_and_candidates():
    payload = _payload("我去测试一下")
    source = (cds.SourceSnapshot("message", "m-synthetic", "1", "a" * 64),)
    header = cds.build_header(
        decision_kind=observer.DECISION_KIND, policy_version=observer.POLICY_VERSION,
        request_id="presence-shadow-run", mode=cds.DecisionMode.SHADOW,
        source_snapshot=source,
    )
    candidates = tuple(
        cds.CandidateRef(item, "presence_semantic", hashlib.sha256(item.encode()).hexdigest())
        for item in observer.candidate_ids()
    )
    result = observer.observe_shadow(payload)
    raw = json.dumps({
        **result.__dict__, "selected_ids": list(result.selected_ids),
        "reason_codes": list(result.reason_codes),
        "evidence_message_ids": list(result.evidence_message_ids),
        "open_threads": list(result.open_threads),
    })
    conn = db.connect()
    try:
        before = (
            conn.execute("SELECT COUNT(*) FROM conversation_presence").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM proactive_candidates").fetchone()[0],
        )
    finally:
        conn.close()
    run, _ = cds.create_run(header, payload, candidates)
    outcome = cds.evaluate_output(run.id, header, payload, raw, current_snapshot=source)
    conn = db.connect()
    try:
        after = (
            conn.execute("SELECT COUNT(*) FROM conversation_presence").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM proactive_candidates").fetchone()[0],
        )
    finally:
        conn.close()
    assert outcome["application_allowed"] is False and outcome["fallback_used"] is False
    assert after == before


def test_active_header_is_rejected_before_a_run_can_be_created():
    payload = _payload("晚安")
    source = (cds.SourceSnapshot("message", "m-synthetic", "1", "b" * 64),)
    header = cds.build_header(
        decision_kind=observer.DECISION_KIND, policy_version=observer.POLICY_VERSION,
        request_id="presence-active-run", mode=cds.DecisionMode.ACTIVE,
        source_snapshot=source,
    )
    candidates = tuple(
        cds.CandidateRef(item, "presence_semantic", hashlib.sha256(item.encode()).hexdigest())
        for item in observer.candidate_ids()
    )
    with pytest.raises(cds.DecisionProtocolError) as exc:
        cds.create_run(header, payload, candidates)
    assert exc.value.code == "mode_not_authorized"
