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
        _revoke_changed_document_grants_locked(conn, [document_id], now)
        updated = conn.execute(
            "SELECT * FROM knowledge_documents WHERE id=?", (document_id,),
        ).fetchone()
        conn.commit()
        return knowledge.public_document(dict(updated))
    finally:
        conn.close()


def update_collection_policy(
    collection_id: str, transmission_policy: str, *, apply_existing: bool,
) -> dict | None:
    """原子修改集合默认策略；可选批量应用，任何不安全文档都会使整批回滚。"""
    if transmission_policy not in TRANSMISSION_POLICIES:
        raise KnowledgePolicyError("transmission_policy_invalid", "集合远传策略无效")
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        collection = conn.execute(
            "SELECT * FROM knowledge_collections WHERE id=?", (collection_id,),
        ).fetchone()
        if not collection:
            conn.rollback()
            return None
        documents = conn.execute(
            "SELECT id,sensitivity,status,transmission_policy,policy_revision"
            " FROM knowledge_documents WHERE collection_id=? ORDER BY id",
            (collection_id,),
        ).fetchall() if apply_existing else []
        if any(row["status"] in {"delete_pending", "delete_failed"} for row in documents):
            raise KnowledgePolicyError(
                "collection_contains_deleting_document",
                "集合中有正在删除的文档，不能执行批量策略修改",
            )
        if transmission_policy == "remote_allowed" and any(
            row["sensitivity"] == "sensitive" for row in documents
        ):
            raise KnowledgePolicyError(
                "sensitive_remote_forbidden",
                "集合中有敏感文档，不能整批设为允许在线发送",
            )
        now = db.now()
        collection_revision = int(collection["policy_revision"])
        if collection["default_transmission_policy"] != transmission_policy:
            collection_revision += 1
            conn.execute(
                "UPDATE knowledge_collections SET default_transmission_policy=?,policy_revision=?,"
                "policy_updated_at=?,updated_at=? WHERE id=?",
                (transmission_policy, collection_revision, now, now, collection_id),
            )
        changed_ids: list[str] = []
        for row in documents:
            if row["transmission_policy"] == transmission_policy:
                continue
            revision = int(row["policy_revision"]) + 1
            conn.execute(
                "UPDATE knowledge_documents SET transmission_policy=?,policy_revision=?,"
                "policy_updated_at=?,updated_at=? WHERE id=?",
                (transmission_policy, revision, now, now, row["id"]),
            )
            conn.execute(
                "INSERT INTO knowledge_document_policy_events("
                "id,document_id,before_policy,after_policy,policy_revision,actor,reason_code,created_at)"
                " VALUES(?,?,?,?,?,'user','collection_policy_change',?)",
                (db.new_id(), row["id"], row["transmission_policy"], transmission_policy,
                 revision, now),
            )
            changed_ids.append(str(row["id"]))
        revoked_count = _revoke_changed_document_grants_locked(conn, changed_ids, now)
        updated = conn.execute(
            "SELECT id,name,description,status,default_transmission_policy,policy_revision,"
            "policy_updated_at,created_at,updated_at FROM knowledge_collections WHERE id=?",
            (collection_id,),
        ).fetchone()
        conn.commit()
        return {**dict(updated), "updated_document_count": len(changed_ids),
                "revoked_grant_count": revoked_count}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _revoke_changed_document_grants_locked(conn, document_ids: list[str], now: float) -> int:
    if not document_ids:
        return 0
    rows = conn.execute(
        "SELECT DISTINCT g.id,g.status FROM knowledge_transmission_grants g"
        " JOIN knowledge_transmission_grant_items i ON i.grant_id=g.id"
        " WHERE g.status IN ('pending','issued') AND i.document_id IN ("
        + ",".join("?" for _ in document_ids) + ")",
        document_ids,
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE knowledge_transmission_grants SET status='revoked',token_hash=NULL,"
            "revoked_at=?,updated_at=? WHERE id=?", (now, now, row["id"]),
        )
        conn.execute(
            "INSERT INTO knowledge_transmission_grant_events("
            "id,grant_id,action,before_status,after_status,reason_code,item_count,created_at)"
            " VALUES(?,?,'policy_changed',?,'revoked','collection_policy_change',0,?)",
            (db.new_id(), row["id"], row["status"], now),
        )
    return len(rows)


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
