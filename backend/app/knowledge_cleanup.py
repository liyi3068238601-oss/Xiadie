"""K.8 审计数据生命周期清理：保留期定义与过期行物理删除。

只清理审计元数据，不删除文档、索引或引用。
每 60 秒与 grant 过期清扫在同一个空闲维护周期执行。
"""
from __future__ import annotations

from . import db

# 保留期（天）
RETENTION_RECALL_DECISIONS = 90
RETENTION_GRANTS = 30
RETENTION_CHAT_RETRIEVALS = 180
# knowledge_message_citations 不自动删除（绑定消息生命周期）

CLEANUP_LIMIT = 100


def run_once() -> int:
    deleted = 0
    deleted += _cleanup_table(
        "knowledge_recall_decisions",
        retention_days=RETENTION_RECALL_DECISIONS,
        extra_where="",
    )
    deleted += _cleanup_table(
        "knowledge_transmission_grants",
        retention_days=RETENTION_GRANTS,
        extra_where="AND status IN ('consumed','expired','revoked','denied')",
    )
    deleted += _cleanup_table(
        "knowledge_chat_retrievals",
        retention_days=RETENTION_CHAT_RETRIEVALS,
        extra_where="",
    )
    return deleted


def _cleanup_table(table: str, retention_days: int, extra_where: str) -> int:
    cutoff = db.now() - retention_days * 86400
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            f"DELETE FROM {table} WHERE created_at < ? {extra_where} LIMIT ?",
            (cutoff, CLEANUP_LIMIT),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
