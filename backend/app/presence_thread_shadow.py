"""CDS.3 read-only PresenceAndThreadObserver protocol and offline shadow reference.

EAP remains the sole writer of Conversation Presence v2. This module can only
produce bounded Shadow proposals; it never updates presence, creates proactive
candidates or sends messages.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import cognitive_decision as cds
from .proactive import presence

DECISION_KIND = "presence_thread_observer"
POLICY_VERSION = "presence-thread-shadow-policy-v1"
INPUT_VERSION = "presence-thread-input-v1"
OUTPUT_VERSION = "presence-thread-result-v1"
VALIDATOR_VERSION = "presence-thread-validator-v1"
FALLBACK_VERSION = "presence-thread-eap-v2-fallback-v1"

EXPECT_RETURN_VALUES = frozenset({"yes", "no", "unknown"})
CLOSURE_VALUES = frozenset({"open", "paused", "closed", "unknown"})
RESPONSE_NEED_VALUES = frozenset({"none", "normal", "defer", "unknown"})
THREAD_VALUES = frozenset({"test_result", "meal_return", "shower_return"})
REASON_CODES = frozenset({
    "explicit_sleep", "explicit_departure", "explicit_end", "explicit_boundary",
    "explicit_busy", "explicit_extended", "quoted_or_meta", "ordinary_exchange",
    "unknown_silence", "thread_continuation", "legacy_fallback",
})

_META = re.compile(r"翻译|分析|按钮|文档|台词|例句|标题|正则|字符串|测试用例|关键词")
_SLEEP = re.compile(r"晚安|我.*睡了|先睡了|去睡|睡觉去了|我要睡|该睡了|困了.*睡")
_END = re.compile(r"先这样|就这样吧|再见|拜拜|下次聊|今天先到这|先聊到这|回头聊|到这里|结束聊天")
_DND = re.compile(r"勿扰|别打扰|不要打扰|不被打扰|别烦我|先别找我|不要找我|别来消息|暂停联系")
_TEST_DEPARTURE = re.compile(r"(?:我|先)?去.*(?:测|跑)|跑.*测试|测(?:试)?完.*回来|测试一下.*回来")
_MEAL = re.compile(r"去吃饭|吃饭去|去吃个饭|去午饭|去晚饭|去早饭|去吃晚饭|去吃早饭|去觅食|吃完饭回来|吃点东西")
_SHOWER = re.compile(r"去洗澡|去洗个澡|洗澡去|去沐浴")
_BUSY = re.compile(r"在开会|开会中|要去开会|开个会|会议中|忙着开会|开完会再说|全屏.*游戏|打游戏|开黑")
_EXTENDED = re.compile(r"出差|出门.*几天|离开.*几天|回老家|去旅游")


@dataclass(frozen=True)
class PresenceThreadInput:
    candidate_ids: tuple[str, ...]
    source_message_id: str | None
    valid_message_ids: tuple[str, ...]
    text: str
    silence_observed: bool
    legacy_presence_state: str
    legacy_open_thread: bool
    legacy_open_thread_topic: str | None
    current_open_threads: tuple[str, ...] = ()


@dataclass(frozen=True)
class PresenceThreadResult:
    action: str
    selected_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    confidence_band: str
    evidence_message_ids: tuple[str, ...]
    presence_state: str
    expect_return: str
    conversation_closure: str
    open_threads: tuple[str, ...]
    last_declared_activity: str
    followup_allowed: bool
    earliest_followup_hint_seconds: float | None
    response_need: str


def candidate_ids() -> tuple[str, ...]:
    return tuple(
        [f"presence:{value}" for value in sorted(presence.USER_STATUS_VALUES)]
        + [f"thread:{value}" for value in sorted(THREAD_VALUES)]
    )


def _result(*, payload: PresenceThreadInput, state: str, reason: str,
            expect_return: str, closure: str, threads: tuple[str, ...] = (),
            activity: str = "none", followup_allowed: bool = False,
            hint: float | None = None, response_need: str = "none",
            confidence: str = "high") -> PresenceThreadResult:
    selected = (f"presence:{state}",) + tuple(f"thread:{item}" for item in threads)
    evidence = (payload.source_message_id,) if payload.source_message_id else ()
    return PresenceThreadResult(
        action=cds.DecisionAction.SELECT.value, selected_ids=selected,
        reason_codes=(reason,), confidence_band=confidence,
        evidence_message_ids=evidence, presence_state=state, expect_return=expect_return,
        conversation_closure=closure, open_threads=threads,
        last_declared_activity=activity, followup_allowed=followup_allowed,
        earliest_followup_hint_seconds=hint, response_need=response_need,
    )


def observe_shadow(payload: PresenceThreadInput) -> PresenceThreadResult:
    """Conservative offline reference used to calibrate the bounded output contract."""
    text = payload.text.strip()
    if payload.silence_observed or not text:
        return _result(
            payload=payload, state=presence.UserStatus.UNKNOWN, reason="unknown_silence",
            expect_return="unknown", closure="unknown", response_need="unknown",
            confidence="low",
        )
    if _META.search(text):
        return _result(
            payload=payload, state=presence.UserStatus.ONLINE, reason="quoted_or_meta",
            expect_return="unknown", closure="open", followup_allowed=True,
            response_need="normal",
        )
    if payload.current_open_threads:
        return _result(
            payload=payload, state=presence.UserStatus.ONLINE, reason="thread_continuation",
            expect_return="unknown", closure="open", threads=payload.current_open_threads,
            activity="thread_return", followup_allowed=True, response_need="normal",
        )
    if _DND.search(text):
        return _result(
            payload=payload, state=presence.UserStatus.DO_NOT_DISTURB,
            reason="explicit_boundary", expect_return="no", closure="paused",
            activity="do_not_disturb",
        )
    if _SLEEP.search(text):
        return _result(
            payload=payload, state=presence.UserStatus.AWAY_SLEEP, reason="explicit_sleep",
            expect_return="unknown", closure="paused", activity="sleep",
        )
    if _END.search(text):
        return _result(
            payload=payload, state=presence.UserStatus.ENDED_CONVERSATION,
            reason="explicit_end", expect_return="unknown", closure="closed",
            activity="conversation_end",
        )
    if _TEST_DEPARTURE.search(text):
        return _result(
            payload=payload, state=presence.UserStatus.AWAY_BRIEF,
            reason="explicit_departure", expect_return="yes", closure="paused",
            threads=("test_result",), activity="testing", followup_allowed=True, hint=1800,
        )
    if _MEAL.search(text):
        return _result(
            payload=payload, state=presence.UserStatus.AWAY_BRIEF,
            reason="explicit_departure", expect_return="yes", closure="paused",
            threads=("meal_return",), activity="meal", followup_allowed=True, hint=1800,
        )
    if _SHOWER.search(text):
        return _result(
            payload=payload, state=presence.UserStatus.AWAY_BRIEF,
            reason="explicit_departure", expect_return="yes", closure="paused",
            threads=("shower_return",), activity="shower", followup_allowed=True, hint=1800,
        )
    if _BUSY.search(text):
        return _result(
            payload=payload, state=presence.UserStatus.AWAY_BUSY, reason="explicit_busy",
            expect_return="yes", closure="paused", activity="busy", hint=7200,
        )
    if _EXTENDED.search(text):
        return _result(
            payload=payload, state=presence.UserStatus.AWAY_EXTENDED,
            reason="explicit_extended", expect_return="yes", closure="paused",
            activity="extended_absence", hint=86400,
        )
    return _result(
        payload=payload, state=presence.UserStatus.ONLINE, reason="ordinary_exchange",
        expect_return="unknown", closure="open", followup_allowed=True,
        response_need="normal", confidence="medium",
    )


def legacy_fallback(payload: PresenceThreadInput) -> PresenceThreadResult:
    state = payload.legacy_presence_state
    topic_map = {"测试结果": "test_result", "吃饭": "meal_return", "洗澡": "shower_return"}
    threads = (
        (topic_map[payload.legacy_open_thread_topic],)
        if payload.legacy_open_thread and payload.legacy_open_thread_topic in topic_map else ()
    )
    return _result(
        payload=payload, state=state, reason="legacy_fallback",
        expect_return="yes" if state in presence.DEFAULT_EXPECTED_RETURN else "unknown",
        closure=("closed" if state == presence.UserStatus.ENDED_CONVERSATION
                 else "paused" if state != presence.UserStatus.ONLINE else "open"),
        threads=threads, followup_allowed=bool(threads), confidence="low",
    )


def validate(payload: PresenceThreadInput, result: PresenceThreadResult) -> None:
    if tuple(payload.candidate_ids) != candidate_ids():
        raise cds.DecisionProtocolError("candidate_snapshot_mismatch", "presence candidates changed")
    if payload.source_message_id and payload.source_message_id not in payload.valid_message_ids:
        raise cds.DecisionProtocolError("source_message_invalid", "source message is not valid")
    if not set(payload.current_open_threads) <= THREAD_VALUES:
        raise cds.DecisionProtocolError("thread_semantics_invalid", "invalid current thread")
    if result.presence_state not in presence.USER_STATUS_VALUES:
        raise cds.DecisionProtocolError("presence_state_invalid", "unknown presence state")
    if result.expect_return not in EXPECT_RETURN_VALUES or result.conversation_closure not in CLOSURE_VALUES:
        raise cds.DecisionProtocolError("presence_semantics_invalid", "invalid presence semantics")
    if result.response_need not in RESPONSE_NEED_VALUES or not set(result.open_threads) <= THREAD_VALUES:
        raise cds.DecisionProtocolError("thread_semantics_invalid", "invalid thread semantics")
    if not set(result.reason_codes) <= REASON_CODES:
        raise cds.DecisionProtocolError("reason_code_not_allowed", "unknown reason code")
    if result.confidence_band not in {item.value for item in cds.ConfidenceBand}:
        raise cds.DecisionProtocolError("confidence_invalid", "invalid confidence")
    expected_evidence = (payload.source_message_id,) if payload.source_message_id else ()
    if result.evidence_message_ids != expected_evidence:
        raise cds.DecisionProtocolError("evidence_message_invalid", "result must cite the source message")
    expected_selected = {f"presence:{result.presence_state}"} | {
        f"thread:{item}" for item in result.open_threads
    }
    if set(result.selected_ids) != expected_selected or not expected_selected <= set(payload.candidate_ids):
        raise cds.DecisionProtocolError("candidate_not_allowed", "result selected unbound semantics")
    if result.earliest_followup_hint_seconds is not None and result.earliest_followup_hint_seconds < 0:
        raise cds.DecisionProtocolError("followup_hint_invalid", "followup hint must be non-negative")
    if not result.followup_allowed and result.open_threads:
        raise cds.DecisionProtocolError("thread_followup_mismatch", "open thread requires followup permission")


cds.REGISTRY.register(cds.DecisionKindDefinition(
    decision_kind=DECISION_KIND,
    input_type=PresenceThreadInput,
    result_type=PresenceThreadResult,
    input_schema_version=INPUT_VERSION,
    output_schema_version=OUTPUT_VERSION,
    validator=validate,
    validator_version=VALIDATOR_VERSION,
    fallback=legacy_fallback,
    fallback_version=FALLBACK_VERSION,
    fallback_owner="eap",
    application_owner="eap",
    privacy_class="user_private",
    max_candidates=len(candidate_ids()),
    timeout_seconds=5.0,
    result_ttl_seconds=cds.DIAGNOSTIC_TTL_SECONDS,
    model_binding_revision=cds.MODEL_BINDING_POLICY_VERSION,
    mode=cds.DecisionMode.SHADOW,
    prompt_template_hash=cds._canonical_hash("presence-thread-observer-shadow-v1"),  # noqa: SLF001
))
