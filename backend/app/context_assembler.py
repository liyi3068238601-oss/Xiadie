"""CTX.4 统一上下文组装器。

本模块只接收调用方已经取得的结构化候选，不读取数据库、不调用检索器或摘要服务。
它重新验证会话摘要的来源边界，并在同一个硬预算中组合摘要、最近原文、长期记忆、
角色设定与用户知识。任何摘要失效都只会退回 CTX.1 的安全完整轮次裁剪。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from . import context_budget, conversation_summary_protocol
from .persona import build_system_prompt

PACKAGE_PROTOCOL_VERSION = "context-package-v1"
SUMMARY_PROTOCOL_VERSION = "conversation-summary-v1"
OPTIONAL_SYSTEM_SHARE = 0.35
OPTIONAL_COMPONENT_SHARES = {
    "rolling_summary": 0.35,
    "existing_memory_digest": 0.25,
    "knowledge": 0.25,
    "lore": 0.15,
}
OPTIONAL_COMPONENT_PRIORITY = (
    "rolling_summary", "existing_memory_digest", "knowledge", "lore",
)
_UNTRUSTED_SUMMARY_DIRECTIVE = re.compile(
    r"(?:忽略(?:以上|此前|之前).{0,24}(?:指令|要求)|"
    r"ignore\s+(?:all\s+)?(?:previous|prior).{0,24}instructions?|"
    r"<\s*/?\s*(?:system|assistant|tool)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SummaryUse:
    revision_id: str
    revision: int
    source_start_message_id: str
    source_end_message_id: str
    source_message_count: int
    summary_text: str


@dataclass(frozen=True)
class ContextPackage:
    budget_plan: context_budget.BudgetPlan
    summary: SummaryUse | None
    raw_messages_after_summary: int
    raw_rounds_after_summary: int
    component_tokens: dict[str, int]

    @property
    def messages(self) -> tuple[dict[str, str], ...]:
        return self.budget_plan.messages

    @property
    def output_reserve_tokens(self) -> int:
        return self.budget_plan.output_reserve_tokens

    @property
    def trimmed_messages(self) -> int:
        return self.budget_plan.trimmed_messages

    @property
    def trimmed_rounds(self) -> int:
        return self.budget_plan.trimmed_rounds

    def public_meta(self) -> dict[str, object]:
        meta = self.budget_plan.public_meta()
        meta.update({
            "package_protocol_version": PACKAGE_PROTOCOL_VERSION,
            "summary_used": self.summary is not None,
            "summary_revision": self.summary.revision if self.summary else None,
            "summary_covered_messages": (
                self.summary.source_message_count if self.summary else 0
            ),
            "recent_raw_messages": self.raw_messages_after_summary,
            "recent_raw_rounds": self.raw_rounds_after_summary,
        })
        components = dict(meta["component_tokens"])
        components.update(self.component_tokens)
        meta["component_tokens"] = components
        return meta


def assemble(
    *,
    history: Sequence[Mapping[str, object]],
    capability: context_budget.ModelContextCapability,
    memory_digest: str = "",
    affect_guidance: str = "",
    lore_digest: str = "",
    knowledge_block: str = "",
    active_summary: Mapping[str, object] | None = None,
    output_reserve_tokens: int | None = None,
) -> ContextPackage:
    """构造单次模型请求；成功结果必定满足 CTX.1 硬预算不变量。"""
    rows = [_message(message) for message in history]
    summary = _validated_summary(rows, active_summary)
    raw_history = rows
    if summary is not None:
        end = next(
            index for index, message in enumerate(rows)
            if message["id"] == summary.source_end_message_id
        )
        raw_history = rows[end + 1:]

    optional_budget = max(
        0, int(capability.effective_context_window * OPTIONAL_SYSTEM_SHARE),
    )
    affect = _truncate_to_tokens(affect_guidance, 512)
    while True:
        components = _bounded_components(
            optional_budget,
            rolling_summary=summary.summary_text if summary else "",
            existing_memory_digest=memory_digest,
            knowledge=knowledge_block,
            lore=lore_digest,
        )
        system_prompt = build_system_prompt(
            components["existing_memory_digest"], affect,
            components["lore"], components["knowledge"],
            components["rolling_summary"],
        )
        try:
            plan = context_budget.build_budget_plan(
                system_prompt=system_prompt,
                history=raw_history,
                capability=capability,
                system_components={
                    "existing_memory_digest": components["existing_memory_digest"],
                    "affect_guidance": affect,
                    "lore": components["lore"],
                    "knowledge": components["knowledge"],
                    "rolling_summary": components["rolling_summary"],
                },
                output_reserve_tokens=output_reserve_tokens,
            )
            break
        except context_budget.ContextBudgetError as error:
            if error.code != "context_protected_region_exceeds_window" or optional_budget == 0:
                raise
            optional_budget = optional_budget // 2
    if summary is not None and not components["rolling_summary"]:
        # 不能在摘要正文完全放不下时仍丢弃它覆盖的原始消息。
        return assemble(
            history=history,
            capability=capability,
            memory_digest=memory_digest,
            affect_guidance=affect_guidance,
            lore_digest=lore_digest,
            knowledge_block=knowledge_block,
            active_summary=None,
            output_reserve_tokens=output_reserve_tokens,
        )
    raw_before_current = max(0, len(plan.messages) - 2)
    component_tokens = {
        name: context_budget.estimate_tokens(value)
        for name, value in components.items()
    }
    return ContextPackage(
        budget_plan=plan,
        summary=summary,
        raw_messages_after_summary=raw_before_current,
        raw_rounds_after_summary=raw_before_current // 2,
        component_tokens=component_tokens,
    )


def _message(message: Mapping[str, object]) -> dict[str, str]:
    item = dict(message)
    return {
        "id": str(item.get("id") or ""),
        "role": str(item.get("role") or ""),
        "content": str(item.get("content") or ""),
        "model": str(item.get("model") or ""),
    }


def _validated_summary(
    history: Sequence[Mapping[str, str]],
    candidate: Mapping[str, object] | None,
) -> SummaryUse | None:
    if not candidate or candidate.get("status") != "active":
        return None
    if candidate.get("protocol_version") != SUMMARY_PROTOCOL_VERSION:
        return None
    text = str(candidate.get("summary_text") or "").strip()
    if (len(text) > conversation_summary_protocol.MAX_SUMMARY_CHARS
            or _UNTRUSTED_SUMMARY_DIRECTIVE.search(text)):
        return None
    start_id = str(candidate.get("source_start_message_id") or "")
    end_id = str(candidate.get("source_end_message_id") or "")
    positions = {message["id"]: index for index, message in enumerate(history)}
    start, end = positions.get(start_id), positions.get(end_id)
    if not text or start is None or end is None or start > end:
        return None
    # 滚动摘要必须从会话最早的完整轮次连续覆盖；否则不能据此丢弃更早原文。
    if start != 0:
        return None
    source = [dict(message) for message in history[start:end + 1]]
    expected_count = _safe_int(candidate.get("source_message_count"))
    if len(source) != expected_count or len(source) < 2 or len(source) % 2:
        return None
    for index in range(0, len(source), 2):
        if source[index]["role"] != "user" or source[index + 1]["role"] != "assistant":
            return None
    if _source_hash(source) != str(candidate.get("source_hash") or ""):
        return None
    # 当前用户消息不能被较早摘要覆盖。
    if end >= len(history) - 1:
        return None
    return SummaryUse(
        revision_id=str(candidate.get("id") or ""),
        revision=_safe_int(candidate.get("revision")),
        source_start_message_id=start_id,
        source_end_message_id=end_id,
        source_message_count=expected_count,
        summary_text=text,
    )


def _source_hash(messages: Sequence[Mapping[str, str]]) -> str:
    canonical = [
        {
            "id": message["id"],
            "role": message["role"],
            "content": message["content"],
            "model": message["model"],
        }
        for message in messages
    ]
    encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _bounded_components(
    total_budget: int,
    **values: str,
) -> dict[str, str]:
    # 1M 是能力上限而不是填充目标；可选上下文至多占窗口的一小部分，余量留给
    # 当前消息、最近原文、人格安全规则和输出。
    total = max(0, int(total_budget))
    normalized = {name: str(values.get(name) or "") for name in OPTIONAL_COMPONENT_SHARES}
    requested = {
        name: context_budget.estimate_tokens(value) for name, value in normalized.items()
    }
    allocations = {
        name: min(requested[name], int(total * share))
        for name, share in OPTIONAL_COMPONENT_SHARES.items()
    }
    unused = max(0, total - sum(allocations.values()))
    for name in OPTIONAL_COMPONENT_PRIORITY:
        extra = min(unused, max(0, requested[name] - allocations[name]))
        allocations[name] += extra
        unused -= extra
    return {
        name: _truncate_to_tokens(value, allocations[name])
        for name, value in normalized.items()
    }


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    value = str(text or "")
    limit = max(0, int(max_tokens))
    if context_budget.estimate_tokens(value) <= limit:
        return value
    if limit == 0:
        return ""
    low, high = 0, len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if context_budget.estimate_tokens(value[:middle]) <= limit:
            low = middle
        else:
            high = middle - 1
    return value[:low].rstrip()


def _safe_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
