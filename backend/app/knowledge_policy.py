"""K.1 文档远传策略与 Provider 执行位置的保守边界。"""
from __future__ import annotations

from urllib.parse import urlsplit

from . import db, knowledge

TRANSMISSION_POLICIES = frozenset({"remote_allowed", "ask_each_time", "local_only"})
EXECUTION_LOCATIONS = frozenset({"local", "remote", "unknown"})
REMOTE_PROVIDER_IDS = frozenset({
    "deepseek", "openai", "glm", "qwen", "kimi", "openrouter", "siliconflow",
})
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class KnowledgePolicyError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def is_loopback_url(base_url: str) -> bool:
    try:
        parsed = urlsplit(str(base_url or "").strip())
        return (
            parsed.scheme in {"http", "https"}
            and parsed.hostname in LOOPBACK_HOSTS
            and not parsed.username
            and not parsed.password
        )
    except ValueError:
        return False


def automatic_provider_location(provider_id: str, base_url: str) -> str:
    if provider_id == "mock":
        return "local"
    if provider_id in REMOTE_PROVIDER_IDS:
        return "remote"
    if provider_id == "ollama":
        return "local" if is_loopback_url(base_url) else "remote"
    return "unknown"


def requires_remote_controls(execution_location: str) -> bool:
    """unknown 与 remote 都必须经过远传策略/授权，只有 local 可以绕过远传判断。"""
    return execution_location != "local"


def provider_location_update(
    row: dict, *, base_url: str, requested_location: str | None, location_was_requested: bool,
) -> dict:
    current_url = str(row.get("base_url") or "")
    current_location = str(row.get("execution_location") or "unknown")
    address_changed = base_url != current_url

    if location_was_requested:
        if requested_location not in EXECUTION_LOCATIONS:
            raise KnowledgePolicyError("provider_location_invalid", "Provider 数据位置无效")
        if row["id"] == "mock" and requested_location != "local":
            raise KnowledgePolicyError(
                "provider_location_invalid", "内置演示模型始终在本机运行",
            )
        if requested_location == "local" and row["id"] != "mock" and not is_loopback_url(base_url):
            raise KnowledgePolicyError(
                "provider_local_boundary_invalid", "只有明确的本机回环地址才能确认成 local",
            )
        location = requested_location
        confirmed_at = db.now()
    elif address_changed:
        location = automatic_provider_location(str(row["id"]), base_url)
        confirmed_at = None
    else:
        location = current_location
        confirmed_at = row.get("location_confirmed_at")

    boundary_changed = address_changed or location != current_location
    return {
        "execution_location": location,
        "location_revision": int(row.get("location_revision") or 1) + (1 if boundary_changed else 0),
        "location_confirmed_at": confirmed_at,
    }


def update_document_policy(document_id: str, transmission_policy: str) -> dict | None:
    if transmission_policy not in TRANSMISSION_POLICIES:
        raise KnowledgePolicyError("transmission_policy_invalid", "文档远传策略无效")
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM knowledge_documents WHERE id=?", (document_id,),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        if row["status"] in {"delete_pending", "delete_failed"}:
            raise KnowledgePolicyError("document_deleting", "删除中的文档不能修改远传策略")
        if row["sensitivity"] == "sensitive" and transmission_policy == "remote_allowed":
            raise KnowledgePolicyError(
                "sensitive_remote_forbidden", "敏感文档不能设为允许自动发送给在线模型",
            )
        before = str(row["transmission_policy"])
        if before == transmission_policy:
            conn.commit()
            return knowledge.public_document(dict(row))
        revision = int(row["policy_revision"]) + 1
        now = db.now()
        conn.execute(
            "UPDATE knowledge_documents SET transmission_policy=?,policy_revision=?,"
            "policy_updated_at=?,updated_at=? WHERE id=?",
            (transmission_policy, revision, now, now, document_id),
        )
        conn.execute(
            "INSERT INTO knowledge_document_policy_events("
            "id,document_id,before_policy,after_policy,policy_revision,actor,reason_code,created_at)"
            " VALUES(?,?,?,?,?,'user','user_policy_change',?)",
            (db.new_id(), document_id, before, transmission_policy, revision, now),
        )
        updated = conn.execute(
            "SELECT * FROM knowledge_documents WHERE id=?", (document_id,),
        ).fetchone()
        conn.commit()
        return knowledge.public_document(dict(updated))
    finally:
        conn.close()


def list_document_policy_events(document_id: str, limit: int = 50) -> list[dict] | None:
    conn = db.connect()
    try:
        exists = conn.execute(
            "SELECT 1 FROM knowledge_documents WHERE id=?", (document_id,),
        ).fetchone()
        if not exists:
            return None
        return [dict(row) for row in conn.execute(
            "SELECT id,document_id,before_policy,after_policy,policy_revision,actor,"
            "reason_code,created_at FROM knowledge_document_policy_events WHERE document_id=?"
            " ORDER BY created_at DESC,id DESC LIMIT ?", (document_id, max(1, min(limit, 100))),
        ).fetchall()]
    finally:
        conn.close()
