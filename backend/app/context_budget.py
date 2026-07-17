"""上下文预算管理器。负责 token 计数、预算裁剪和长会话摘要。

不依赖外部 tokenizer，使用本地字符级估算。
"""
from __future__ import annotations

import re

from . import db

# 按 Provider ID 定义的上下文窗口（tokens）——来自官方文档的保守值
CONTEXT_WINDOWS: dict[str, int] = {
    "mock": 8192,
    "deepseek": 65536,
    "openai": 128000,
    "glm": 128000,
    "qwen": 32768,
    "kimi": 8192,
    "openrouter": 32768,
    "siliconflow": 32768,
    "ollama": 8192,
    "custom": 4096,
}
DEFAULT_CONTEXT_WINDOW = 4096
# 必须保留的基础 token 开销上限
SYSTEM_RESERVE = 2000
# 长会话触发摘要的轮次阈值
SUMMARY_THRESHOLD_ROUNDS = 20
# 每次生成摘要的轮次数
SUMMARY_EVERY_ROUNDS = 10
# 摘要用于替代历史的 token 预算
SUMMARY_SLOTS = 800


def estimate_tokens(text: str) -> int:
    """本地保守估算：CJK 字符逐字，拉丁词逐个，标点 4:1。"""
    if not text:
        return 0
    cjk = len(re.findall(r"[㐀-䶿一-鿿]", text))
    words = len(re.findall(r"[A-Za-z0-9_]+", text))
    punctuation = len(re.findall(r"[^\s㐀-䶿一-鿿A-Za-z0-9_]", text))
    return cjk + words + (punctuation + 3) // 4


def get_context_window(provider: dict | None) -> int:
    if not provider:
        return DEFAULT_CONTEXT_WINDOW
    pid = provider.get("id", "")
    return CONTEXT_WINDOWS.get(pid, DEFAULT_CONTEXT_WINDOW)


def count_system_tokens(memory_digest: str, emotion_guidance: str, lore_digest: str,
                        knowledge_block: str) -> int:
    system_text = memory_digest + emotion_guidance + lore_digest + knowledge_block
    return estimate_tokens(system_text) + SYSTEM_RESERVE


def count_history_tokens(history: list) -> int:
    return sum(estimate_tokens(dict(msg).get("content", "") if not isinstance(msg, dict) else msg.get("content", "")) for msg in history)


def trim_history(history: list, max_tokens: int, *, keep_min_rounds: int = 4) -> list:
    """从最早的轮次开始逐轮裁剪，保留最近 keep_min_rounds 轮。"""
    if not history:
        return history
    # Normalize to dicts (may receive sqlite3.Row from chat endpoint)
    normalized = [dict(msg) if not isinstance(msg, dict) else msg for msg in history]
    user_rounds: list[tuple[int, list[dict]]] = []
    current: list[dict] = []
    round_idx = 0
    for msg in normalized:
        current.append(msg)
        if msg.get("role") == "assistant":
            user_rounds.append((round_idx, list(current)))
            round_idx += 1
            current = []
    if current:
        user_rounds.append((round_idx, list(current)))
    if len(user_rounds) <= max(1, keep_min_rounds):
        return history
    total = sum(
        sum(estimate_tokens(msg.get("content", "")) for msg in round_msgs)
        for _, round_msgs in user_rounds
    )
    kept: list[dict] = []
    for idx, round_msgs in reversed(user_rounds):
        round_tokens = sum(estimate_tokens(msg.get("content", "")) for msg in round_msgs)
        if len([r for r in kept if any(m.get("role") == "assistant" for m in [r])]) < keep_min_rounds:
            kept = round_msgs + kept
            total -= round_tokens
            continue
        if total > max_tokens:
            total -= round_tokens
            continue
        kept = round_msgs + kept
    return kept


# 重用 knowledge_context 的 estimate_tokens（避免重复维护）
from . import knowledge_context

estimate_tokens = knowledge_context.estimate_tokens
