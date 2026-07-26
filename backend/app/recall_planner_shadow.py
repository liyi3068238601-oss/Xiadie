"""CDS.4 bounded RecallPlanner Shadow contract.

The planner proposes source needs and bounded query terms only. CTX, Knowledge,
MEM and Lore remain responsible for permissions, candidates, retrieval and budgets.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from . import cognitive_decision as cds

DECISION_KIND = "recall_planner"
POLICY_VERSION = "recall-planner-shadow-policy-v1"


class SourceKind(str, Enum):
    MEMORY = "memory"
    HISTORY = "history"
    KNOWLEDGE = "knowledge"
    LORE = "lore"
    EPISODE_SAGA = "episode_saga"


class TaskType(str, Enum):
    ORDINARY_CHAT = "ordinary_chat"
    EMOTIONAL_SUPPORT = "emotional_support"
    CURRENT_TASK = "current_task"
    PAST_DECISION_RECOVERY = "past_decision_recovery"
    EXACT_QUOTE_LOOKUP = "exact_quote_lookup"
    DOCUMENT_FACT_LOOKUP = "document_fact_lookup"
    DOCUMENT_ANALYSIS = "document_analysis"
    MULTI_DOCUMENT_COMPARISON = "multi_document_comparison"
    RELATIONSHIP_CONTINUITY = "relationship_continuity"
    WORLD_LORE_QUESTION = "world_lore_question"


NEED_VALUES = frozenset({"none", "low", "medium", "high", "critical"})
QUERY_INTENTS = frozenset({
    "none", "ordinary_continuity", "support_context", "resume_current_task",
    "recover_past_decision", "find_exact_quote", "retrieve_document_fact",
    "analyze_document", "compare_documents", "relationship_continuity", "retrieve_world_lore",
    "legacy_fallback",
})
REASON_CODES = frozenset({
    "ordinary_chat", "emotional_support", "current_task", "past_decision",
    "exact_quote", "document_fact", "document_analysis", "multi_document",
    "relationship_continuity", "world_lore", "explicit_retrieval_forbidden",
    "legacy_fallback",
})

_NEGATED_FORBID = re.compile(r"(?:不要|别|无需|不用).{0,2}不(?:查|检索|搜索|召回|回忆|找|引用)")
_FORBID = re.compile(r"(?:不要|别|无需|不用)(?:再|去|帮我|进行)?(?:查找|查|检索|搜索|召回|回忆|找|引用|使用)")
_EXACT = re.compile(r"原话|逐字|准确措辞|一字不差|当时怎么说")
_MULTI_DOC = re.compile(r"多份|多个文档|两份文档|对比.*文档|比较.*资料|跨文档")
_DOC_ANALYSIS = re.compile(r"分析.*(?:文档|资料|文件)|总结.*(?:文档|资料|文件)|从(?:文档|资料|文件).*推断")
_DOC_FACT = re.compile(r"知识库|资料里|文档里|文件中|查资料|引用文档|规范里")
_LORE = re.compile(r"遐蝶.*(?:设定|背景|身世|故事|死亡之触)|翁法罗斯|奥赫玛|死亡泰坦|黄金裔|玻吕茜亚|阿格莱雅")
_PAST = re.compile(r"以前|之前|过去|曾经|上次|当时|那次|还记得|决定过|最终怎么定")
_RELATIONSHIP = re.compile(r"我们.*(?:关系|共同|一起|经历|约定)|共同完成|这段陪伴|相处|陪伴多久|那段经历")
_EMOTION = re.compile(r"难过|伤心|焦虑|害怕|孤独|委屈|想哭|压力|崩溃|睡不着|需要安慰")
_CURRENT = re.compile(r"继续|接着|下一步|当前任务|这个阶段|刚才的代码|正在做|修复这个")
_TERM_RE = re.compile(r"[A-Za-z0-9_+.#-]{2,40}|[\u3400-\u9fff]{2,12}")
_NOISE = frozenset({
    "不要", "不用", "无需", "帮我", "请问", "一下", "这个", "那个", "我们",
    "怎么", "什么", "是否", "可以", "告诉我", "继续", "资料里", "文档里",
})


@dataclass(frozen=True)
class RecallPlannerInput:
    candidate_ids: tuple[str, ...]
    source_message_id: str
    valid_message_ids: tuple[str, ...]
    text: str
    forbidden_sources: tuple[str, ...]
    legacy_selected_sources: tuple[str, ...]


@dataclass(frozen=True)
class RecallPlannerResult:
    action: str
    selected_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    confidence_band: str
    evidence_message_ids: tuple[str, ...]
    task_type: str
    query_intent: str
    memory_need: str
    history_need: str
    knowledge_need: str
    lore_need: str
    episode_saga_need: str
    query_terms: tuple[str, ...]
    hard_refusal: bool
    advisory_expand_only: bool


def candidate_ids() -> tuple[str, ...]:
    return tuple(f"source:{item.value}" for item in SourceKind)


def detect_forbidden_sources(text: str) -> tuple[str, ...]:
    if _NEGATED_FORBID.search(text):
        return ()
    return tuple(item.value for item in SourceKind) if _FORBID.search(text) else ()


def _query_terms(text: str) -> tuple[str, ...]:
    cleaned = re.sub(r"[，。！？、；：,.!?;:\s]+", " ", text)
    terms = []
    for value in _TERM_RE.findall(cleaned):
        value = value.strip()
        if value in _NOISE or value in terms:
            continue
        terms.append(value)
        if len(terms) == 8:
            break
    return tuple(terms)


def _result(payload: RecallPlannerInput, *, task: TaskType, intent: str, reason: str,
            memory: str = "none", history: str = "none", knowledge: str = "none",
            lore: str = "none", episode_saga: str = "none", hard_refusal: bool = False,
            confidence: str = "high") -> RecallPlannerResult:
    needs = {
        SourceKind.MEMORY.value: memory, SourceKind.HISTORY.value: history,
        SourceKind.KNOWLEDGE.value: knowledge, SourceKind.LORE.value: lore,
        SourceKind.EPISODE_SAGA.value: episode_saga,
    }
    selected = tuple(f"source:{kind}" for kind, need in needs.items() if need != "none")
    return RecallPlannerResult(
        action=cds.DecisionAction.SKIP.value if hard_refusal or not selected else cds.DecisionAction.SELECT.value,
        selected_ids=selected, reason_codes=(reason,), confidence_band=confidence,
        evidence_message_ids=(payload.source_message_id,), task_type=task.value,
        query_intent=intent, memory_need=memory, history_need=history,
        knowledge_need=knowledge, lore_need=lore, episode_saga_need=episode_saga,
        query_terms=() if hard_refusal else _query_terms(payload.text),
        hard_refusal=hard_refusal, advisory_expand_only=True,
    )


def plan_shadow(payload: RecallPlannerInput) -> RecallPlannerResult:
    """Offline reference for bounded routing; never performs retrieval."""
    if payload.forbidden_sources:
        return _result(
            payload, task=TaskType.ORDINARY_CHAT, intent="none",
            reason="explicit_retrieval_forbidden", hard_refusal=True,
        )
    text = payload.text.strip()
    if _EXACT.search(text):
        return _result(payload, task=TaskType.EXACT_QUOTE_LOOKUP, intent="find_exact_quote",
                       reason="exact_quote", history="critical")
    if _MULTI_DOC.search(text):
        return _result(payload, task=TaskType.MULTI_DOCUMENT_COMPARISON, intent="compare_documents",
                       reason="multi_document", knowledge="critical")
    if _DOC_ANALYSIS.search(text):
        return _result(payload, task=TaskType.DOCUMENT_ANALYSIS, intent="analyze_document",
                       reason="document_analysis", knowledge="critical")
    if _DOC_FACT.search(text):
        return _result(payload, task=TaskType.DOCUMENT_FACT_LOOKUP,
                       intent="retrieve_document_fact", reason="document_fact", knowledge="high")
    if _LORE.search(text):
        return _result(payload, task=TaskType.WORLD_LORE_QUESTION, intent="retrieve_world_lore",
                       reason="world_lore", lore="critical")
    if _PAST.search(text):
        return _result(payload, task=TaskType.PAST_DECISION_RECOVERY,
                       intent="recover_past_decision", reason="past_decision",
                       memory="medium", history="high", episode_saga="low")
    if _RELATIONSHIP.search(text):
        return _result(payload, task=TaskType.RELATIONSHIP_CONTINUITY,
                       intent="relationship_continuity", reason="relationship_continuity",
                       memory="high", history="medium", episode_saga="high")
    if _EMOTION.search(text):
        return _result(payload, task=TaskType.EMOTIONAL_SUPPORT, intent="support_context",
                       reason="emotional_support", memory="medium", episode_saga="low")
    if _CURRENT.search(text):
        return _result(payload, task=TaskType.CURRENT_TASK, intent="resume_current_task",
                       reason="current_task", memory="medium", history="low")
    return _result(payload, task=TaskType.ORDINARY_CHAT, intent="ordinary_continuity",
                   reason="ordinary_chat", memory="low", confidence="medium")


def legacy_fallback(payload: RecallPlannerInput) -> RecallPlannerResult:
    if payload.forbidden_sources:
        return _result(payload, task=TaskType.ORDINARY_CHAT, intent="none",
                       reason="explicit_retrieval_forbidden", hard_refusal=True)
    selected = set(payload.legacy_selected_sources)
    return _result(
        payload, task=TaskType.ORDINARY_CHAT, intent="legacy_fallback", reason="legacy_fallback",
        memory="low" if "memory" in selected else "none",
        history="high" if "history" in selected else "none",
        knowledge="high" if "knowledge" in selected else "none",
        lore="medium" if "lore" in selected else "none",
        episode_saga="low" if "episode_saga" in selected else "none", confidence="low",
    )


def validate(payload: RecallPlannerInput, result: RecallPlannerResult) -> None:
    if payload.candidate_ids != candidate_ids():
        raise cds.DecisionProtocolError("candidate_snapshot_mismatch", "recall source candidates changed")
    if payload.source_message_id not in payload.valid_message_ids:
        raise cds.DecisionProtocolError("source_message_invalid", "source message is not valid")
    if not set(payload.forbidden_sources) <= {item.value for item in SourceKind}:
        raise cds.DecisionProtocolError("forbidden_source_invalid", "unknown forbidden source")
    if result.task_type not in {item.value for item in TaskType} or result.query_intent not in QUERY_INTENTS:
        raise cds.DecisionProtocolError("recall_semantics_invalid", "unknown task or query intent")
    needs = {
        "memory": result.memory_need, "history": result.history_need,
        "knowledge": result.knowledge_need, "lore": result.lore_need,
        "episode_saga": result.episode_saga_need,
    }
    if any(value not in NEED_VALUES for value in needs.values()):
        raise cds.DecisionProtocolError("recall_need_invalid", "invalid source need")
    if any(needs[item] != "none" for item in payload.forbidden_sources):
        raise cds.DecisionProtocolError("retrieval_forbidden", "forbidden source was requested")
    expected_selected = {f"source:{kind}" for kind, need in needs.items() if need != "none"}
    if set(result.selected_ids) != expected_selected or not expected_selected <= set(payload.candidate_ids):
        raise cds.DecisionProtocolError("candidate_not_allowed", "unbound recall source selected")
    if result.evidence_message_ids != (payload.source_message_id,):
        raise cds.DecisionProtocolError("evidence_message_invalid", "planner must cite source message")
    if not set(result.reason_codes) <= REASON_CODES:
        raise cds.DecisionProtocolError("reason_code_not_allowed", "unknown planner reason")
    if result.confidence_band not in {item.value for item in cds.ConfidenceBand}:
        raise cds.DecisionProtocolError("confidence_invalid", "invalid confidence")
    if len(result.query_terms) > 8 or any(
        not isinstance(term, str) or not term or len(term) > 40 or "\n" in term
        for term in result.query_terms
    ):
        raise cds.DecisionProtocolError("query_terms_invalid", "query suggestion is unbounded")
    if result.hard_refusal and (expected_selected or result.query_terms or result.action != "skip"):
        raise cds.DecisionProtocolError("hard_refusal_invalid", "hard refusal must select nothing")
    if result.advisory_expand_only is not True:
        raise cds.DecisionProtocolError("application_boundary_invalid", "planner cannot inject directly")


cds.REGISTRY.register(cds.DecisionKindDefinition(
    decision_kind=DECISION_KIND, input_type=RecallPlannerInput, result_type=RecallPlannerResult,
    input_schema_version="recall-planner-input-v1",
    output_schema_version="recall-planner-result-v1", validator=validate,
    validator_version="recall-planner-validator-v1", fallback=legacy_fallback,
    fallback_version="recall-planner-legacy-fallback-v1", fallback_owner="ctx",
    application_owner="ctx", privacy_class="user_private", max_candidates=len(candidate_ids()),
    timeout_seconds=8.0, result_ttl_seconds=cds.DIAGNOSTIC_TTL_SECONDS,
    model_binding_revision=cds.MODEL_BINDING_POLICY_VERSION, mode=cds.DecisionMode.SHADOW,
    prompt_template_hash=cds._canonical_hash("recall-planner-shadow-v1"),  # noqa: SLF001
))
