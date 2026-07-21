"""EAP 公共 run 账本工具：source_hash 计算、状态机常量、idempotency_key 生成。

按 spec 第 6.5 节"复用公共 DecisionRun"要求，本模块提供最小公共抽象：
- compute_source_hash：对输入消息列表做 JSON 规范化后 sha256，返回 64 字符 hex
- RunStatus：统一状态机常量（与 affect_observer_runs 对齐）
- make_idempotency_key：按 protocol + 关键标识生成幂等键

不强制现有 11 个 run 表迁移到此抽象；EAP 新建表时复用本模块。
"""

import hashlib
import json
from typing import Any, Iterable


# 状态机常量（与 affect_observer_runs.status 对齐，spec 第 5.7 节 ContactEpisode 状态机
# 由 EAP.E 阶段扩展为 10 值）
class RunStatus:
    QUEUED = "queued"
    RUNNING = "running"
    APPLIED = "applied"
    RECOVERY_PENDING = "recovery_pending"
    EXHAUSTED = "exhausted"
    SKIPPED = "skipped"


def compute_source_hash(messages: Iterable[dict[str, Any]]) -> str:
    """对消息列表做 JSON 规范化后 sha256，返回 64 字符 hex。

    参考 conversation_summaries._source_hash 的实现，独立定义以避免循环导入。
    输入消息字典的键应包含 id/role/content 等；排序后 JSON 序列化确保确定性。
    """
    normalized = [
        {"id": m.get("id"), "role": m.get("role"), "content": m.get("content")}
        for m in messages
    ]
    payload = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def make_idempotency_key(protocol: str, *parts: str) -> str:
    """按 protocol + 关键标识生成幂等键。

    例：make_idempotency_key(PROACTIVE_DECISION_V2, episode_id, turn_id)
    返回 "proactive-decision-v2:{episode_id}:{turn_id}"
    """
    return ":".join((protocol, *parts))
