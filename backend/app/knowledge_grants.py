"""K.4 一次性知识远传授权：只存哈希/绑定/计数，不存查询或知识正文。"""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass

from . import db, knowledge_context, knowledge_recall_thresholds

GRANT_PROTOCOL_VERSION = "knowledge-transmission-grant-v1"
GRANT_TTL_SECONDS = 5 * 60
TOKEN_MIN_BYTES = 32


class GrantError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class Plan:
    prepared: dict | None
    allowed: tuple[dict, ...]
    ask: tuple[dict, ...]
    local_only: tuple[dict, ...]
    documents: dict[str, dict]


def preflight(*, session_id: str, request_nonce: str, content: str,
              provider: dict | None, model: str, recall_decision_id: str | None = None) -> dict:
    _validate_nonce(request_nonce)
    _require_session(session_id)
    provider = _provider_snapshot(provider)
    prepared = knowledge_context.prepare(content)
    plan = _plan(prepared, provider["execution_location"])
    if provider["execution_location"] == "local" or not (plan.ask or plan.local_only):
        return _not_needed(provider, model, plan)
    now = db.now()
    grant_id = db.new_id()
    binding = _binding(
        session_id=session_id, request_nonce=request_nonce, content=content,
        provider=provider, model=model, plan=plan,
    )
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM knowledge_transmission_grants WHERE session_id=? AND request_nonce=?",
            (session_id, request_nonce),
        ).fetchone()
        if existing:
            if not _same_request(dict(existing), binding):
                raise GrantError("grant_nonce_reused", "本次请求标识已经绑定到其他内容")
            conn.commit()
            return _public_grant(conn, dict(existing), provider=provider, model=model)
        conn.execute(
            "INSERT INTO knowledge_transmission_grants("
            "id,recall_decision_id,session_id,request_nonce,user_content_sha256,query_sha256,"
            "provider_id,model,provider_location,provider_location_revision,plan_sha256,"
            "policy_snapshot_sha256,threshold_version,status,document_count,chunk_count,"
            "token_min,token_max,expires_at,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?,?,?,?,?)",
            (
                grant_id, recall_decision_id, session_id, request_nonce,
                binding["user_content_sha256"], binding["query_sha256"], provider["id"], model,
                provider["execution_location"], provider["location_revision"],
                binding["plan_sha256"], binding["policy_snapshot_sha256"],
                knowledge_recall_thresholds.THRESHOLD_VERSION,
                len({item["document_id"] for item in plan.ask + plan.local_only}),
                len(plan.ask + plan.local_only), binding["token_min"], binding["token_max"],
                now + GRANT_TTL_SECONDS, now, now,
            ),
        )
        for item in plan.ask + plan.local_only:
            document = plan.documents[item["document_id"]]
            conn.execute(
                "INSERT INTO knowledge_transmission_grant_items("
                "grant_id,document_id,chunk_id,content_sha256,transmission_policy,policy_revision,"
                "sensitivity,token_estimate) VALUES(?,?,?,?,?,?,?,?)",
                (
                    grant_id, item["document_id"], item["chunk_id"], _verified_content_sha(item),
                    document["transmission_policy"], document["policy_revision"],
                    document["sensitivity"], _item_tokens(item),
                ),
            )
        _event(conn, grant_id, "preflight_created", None, "pending", "restricted_candidates",
               len(plan.ask + plan.local_only))
        row = dict(conn.execute(
            "SELECT * FROM knowledge_transmission_grants WHERE id=?", (grant_id,),
        ).fetchone())
        conn.commit()
        return _public_grant(conn, row, provider=provider, model=model)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def resolve(*, grant_id: str, action: str, session_id: str, request_nonce: str,
            content: str, provider: dict | None, model: str) -> dict:
    if action not in {"allow_once", "always_allow", "local_only"}:
        raise GrantError("grant_action_invalid", "授权操作无效", status_code=400)
    provider = _provider_snapshot(provider)
    prepared = knowledge_context.prepare(content)
    plan = _plan(prepared, provider["execution_location"])
    binding = _binding(
        session_id=session_id, request_nonce=request_nonce, content=content,
        provider=provider, model=model, plan=plan,
    )
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM knowledge_transmission_grants WHERE id=?", (grant_id,),
        ).fetchone()
        if not row:
            raise GrantError("grant_missing", "授权请求不存在", status_code=404)
        grant = dict(row)
        if grant["status"] == "pending" and grant["expires_at"] <= db.now():
            _expire_locked(conn, grant_id, "pending")
            conn.commit()
            raise GrantError("grant_expired", "授权请求已经过期")
        _validate_pending(conn, grant, binding)
        items = [dict(item) for item in conn.execute(
            "SELECT * FROM knowledge_transmission_grant_items WHERE grant_id=? ORDER BY chunk_id",
            (grant_id,),
        ).fetchall()]
        if action == "allow_once":
            if any(item["transmission_policy"] == "local_only" for item in items):
                raise GrantError("local_only_cannot_grant", "仅限本机的资料不能签发在线发送授权")
            token = secrets.token_urlsafe(TOKEN_MIN_BYTES)
            token_hash = _hash(token)
            now = db.now()
            updated = conn.execute(
                "UPDATE knowledge_transmission_grants SET token_hash=?,status='issued',issued_at=?,"
                "updated_at=? WHERE id=? AND status='pending'",
                (token_hash, now, now, grant_id),
            )
            if updated.rowcount != 1:
                raise GrantError("grant_state_changed", "授权状态已经变化")
            _event(conn, grant_id, "grant_issued", "pending", "issued", "user_allow_once", len(items))
            conn.commit()
            return {
                "id": grant_id, "status": "issued", "token": token,
                "expires_at": grant["expires_at"], "single_use": True,
            }

        target_policy = "remote_allowed" if action == "always_allow" else "local_only"
        _update_policies_locked(conn, items, target_policy)
        now = db.now()
        conn.execute(
            "UPDATE knowledge_transmission_grants SET status='revoked',revoked_at=?,updated_at=?,"
            "error_code='policy_changed_by_user' WHERE id=? AND status='pending'",
            (now, now, grant_id),
        )
        _event(conn, grant_id, "policy_changed", "pending", "revoked",
               "user_always_allow" if action == "always_allow" else "user_local_only", len(items))
        conn.commit()
        return {"id": grant_id, "status": "policy_updated", "token": None,
                "transmission_policy": target_policy}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def deny(grant_id: str) -> dict:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status,expires_at FROM knowledge_transmission_grants WHERE id=?", (grant_id,),
        ).fetchone()
        if not row:
            raise GrantError("grant_missing", "授权请求不存在", status_code=404)
        if row["status"] != "pending":
            raise GrantError("grant_state_changed", "授权状态已经变化")
        if row["expires_at"] <= db.now():
            _expire_locked(conn, grant_id, "pending")
            conn.commit()
            raise GrantError("grant_expired", "授权请求已经过期")
        now = db.now()
        conn.execute(
            "UPDATE knowledge_transmission_grants SET status='denied',denied_at=?,updated_at=? WHERE id=?",
            (now, now, grant_id),
        )
        _event(conn, grant_id, "grant_denied", "pending", "denied", "user_denied", 0)
        conn.commit()
        return {"id": grant_id, "status": "denied"}
    except GrantError:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def authorize_chat_locked(
    conn, *, prepared: dict | None, session_id: str, user_message_id: str,
    request_nonce: str | None, content: str, provider: dict | None, model: str,
    grant_token: str | None, skip_restricted: bool,
) -> dict | None:
    provider = _provider_snapshot(provider)
    plan = _plan(prepared, provider["execution_location"])
    if provider["execution_location"] == "local" or not (plan.ask or plan.local_only):
        return prepared
    allowed_ids = {item["chunk_id"] for item in plan.allowed}
    if skip_restricted:
        return knowledge_context.filter_prepared(prepared, allowed_ids)
    if not grant_token or not request_nonce:
        raise GrantError("knowledge_grant_required", "这些资料发送给当前模型前需要你的确认")
    binding = _binding(
        session_id=session_id, request_nonce=request_nonce, content=content,
        provider=provider, model=model, plan=plan,
    )
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        "SELECT * FROM knowledge_transmission_grants WHERE token_hash=?", (_hash(grant_token),),
    ).fetchone()
    if not row:
        raise GrantError("grant_token_invalid", "一次性授权无效或已经不可用")
    grant = dict(row)
    if grant["status"] == "issued" and grant["expires_at"] <= db.now():
        _expire_locked(conn, grant["id"], "issued")
        conn.commit()
        raise GrantError("grant_expired", "一次性授权已经过期")
    if grant["status"] != "issued":
        raise GrantError("grant_replayed", "一次性授权已经使用或失效")
    try:
        _validate_binding(grant, binding)
    except GrantError:
        _revoke_locked(conn, grant["id"], "grant_binding_changed")
        conn.commit()
        raise
    current_items = _restricted_item_signature(plan)
    stored_items = [tuple(item) for item in conn.execute(
        "SELECT document_id,chunk_id,content_sha256,transmission_policy,policy_revision,sensitivity,"
        "token_estimate FROM knowledge_transmission_grant_items WHERE grant_id=? ORDER BY chunk_id",
        (grant["id"],),
    ).fetchall()]
    if current_items != stored_items:
        _revoke_locked(conn, grant["id"], "grant_source_or_policy_changed")
        conn.commit()
        raise GrantError("grant_source_or_policy_changed", "资料来源或远传策略已经变化，请重新确认")
    now = db.now()
    updated = conn.execute(
        "UPDATE knowledge_transmission_grants SET status='consumed',user_message_id=?,consumed_at=?,"
        "updated_at=? WHERE id=? AND status='issued'",
        (user_message_id, now, now, grant["id"]),
    )
    if updated.rowcount != 1:
        raise GrantError("grant_replayed", "一次性授权已经被其他请求使用")
    _event(conn, grant["id"], "grant_consumed", "issued", "consumed", "chat_request_started",
           len(stored_items))
    granted_ids = {item[1] for item in stored_items}
    return knowledge_context.filter_prepared(prepared, allowed_ids | granted_ids)


def expire_due(*, limit: int = 100) -> int:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT id,status FROM knowledge_transmission_grants WHERE status IN ('pending','issued')"
            " AND expires_at<=? ORDER BY expires_at,id LIMIT ?", (db.now(), max(1, min(limit, 500))),
        ).fetchall()
        for row in rows:
            _expire_locked(conn, row["id"], row["status"])
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def get_grant(grant_id: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM knowledge_transmission_grants WHERE id=?", (grant_id,),
        ).fetchone()
        return _public_grant(conn, dict(row)) if row else None
    finally:
        conn.close()


def _plan(prepared: dict | None, provider_location: str) -> Plan:
    results = tuple((prepared or {}).get("results", []))
    document_ids = {item["document_id"] for item in results}
    conn = db.connect()
    try:
        documents = {
            row["id"]: dict(row) for row in conn.execute(
                "SELECT id,original_name,sensitivity,transmission_policy,policy_revision FROM "
                "knowledge_documents WHERE id IN (" + ",".join("?" for _ in document_ids) + ")",
                sorted(document_ids),
            ).fetchall()
        } if document_ids else {}
    finally:
        conn.close()
    allowed, ask, local_only = [], [], []
    for item in results:
        document = documents.get(item["document_id"])
        if not document:
            raise GrantError(
                "grant_source_changed",
                "资料来源已经变化，请重新检索后再试",
            )
        if provider_location == "local" or document["transmission_policy"] == "remote_allowed":
            allowed.append(item)
        elif document["transmission_policy"] == "ask_each_time":
            ask.append(item)
        else:
            local_only.append(item)
    return Plan(prepared, tuple(allowed), tuple(ask), tuple(local_only), documents)


def _binding(*, session_id: str, request_nonce: str, content: str, provider: dict,
             model: str, plan: Plan) -> dict:
    restricted = plan.ask + plan.local_only
    query_sha = (plan.prepared or {}).get("query_sha256") or _hash("")
    policy_payload = [
        [item["document_id"], plan.documents[item["document_id"]]["transmission_policy"],
         plan.documents[item["document_id"]]["policy_revision"]]
        for item in sorted(restricted, key=lambda value: value["chunk_id"])
    ]
    plan_payload = [
        [item["document_id"], item["chunk_id"], _verified_content_sha(item),
         plan.documents[item["document_id"]]["policy_revision"], _item_tokens(item)]
        for item in sorted(restricted, key=lambda value: value["chunk_id"])
    ]
    token_total = sum(_item_tokens(item) for item in restricted)
    return {
        "session_id": session_id, "request_nonce": request_nonce,
        "user_content_sha256": _hash(_normalize(content)), "query_sha256": query_sha,
        "provider_id": provider["id"], "model": model,
        "provider_location": provider["execution_location"],
        "provider_location_revision": provider["location_revision"],
        "plan_sha256": _json_hash(plan_payload), "policy_snapshot_sha256": _json_hash(policy_payload),
        "token_min": max(0, int(token_total * 0.9)), "token_max": max(0, int(token_total * 1.1) + 1),
    }


def _validate_pending(conn, grant: dict, binding: dict) -> None:
    if grant["status"] != "pending":
        raise GrantError("grant_state_changed", "授权请求已经处理")
    _validate_binding(grant, binding)


def _validate_binding(grant: dict, binding: dict) -> None:
    for key in (
        "session_id", "request_nonce", "user_content_sha256", "query_sha256", "provider_id",
        "model", "provider_location", "provider_location_revision", "plan_sha256",
        "policy_snapshot_sha256",
    ):
        if grant.get(key) != binding.get(key):
            raise GrantError("grant_binding_changed", "请求、模型、位置或资料计划已经变化，请重新确认")


def _same_request(grant: dict, binding: dict) -> bool:
    try:
        _validate_binding(grant, binding)
        return True
    except GrantError:
        return False


def _restricted_item_signature(plan: Plan) -> list[tuple]:
    return [
        (
            item["document_id"], item["chunk_id"], _verified_content_sha(item),
            plan.documents[item["document_id"]]["transmission_policy"],
            plan.documents[item["document_id"]]["policy_revision"],
            plan.documents[item["document_id"]]["sensitivity"], _item_tokens(item),
        )
        for item in sorted(plan.ask + plan.local_only, key=lambda value: value["chunk_id"])
    ]


def _update_policies_locked(conn, items: list[dict], target_policy: str) -> None:
    document_ids = sorted({item["document_id"] for item in items})
    for document_id in document_ids:
        row = conn.execute(
            "SELECT sensitivity,transmission_policy,policy_revision FROM knowledge_documents WHERE id=?",
            (document_id,),
        ).fetchone()
        if not row:
            raise GrantError("grant_source_changed", "资料来源已经变化")
        if target_policy == "remote_allowed" and row["sensitivity"] == "sensitive":
            raise GrantError("sensitive_remote_forbidden", "敏感资料不能设为始终允许在线发送")
        if row["transmission_policy"] == target_policy:
            continue
        now, revision = db.now(), int(row["policy_revision"]) + 1
        conn.execute(
            "UPDATE knowledge_documents SET transmission_policy=?,policy_revision=?,policy_updated_at=?,"
            "updated_at=? WHERE id=?", (target_policy, revision, now, now, document_id),
        )
        conn.execute(
            "INSERT INTO knowledge_document_policy_events("
            "id,document_id,before_policy,after_policy,policy_revision,actor,reason_code,created_at)"
            " VALUES(?,?,?,?,?,'user','grant_ui_policy_change',?)",
            (db.new_id(), document_id, row["transmission_policy"], target_policy, revision, now),
        )


def _public_grant(conn, grant: dict, *, provider: dict | None = None, model: str | None = None) -> dict:
    items = [dict(item) for item in conn.execute(
        "SELECT i.document_id,i.transmission_policy,i.sensitivity,COUNT(*) chunk_count,"
        "SUM(i.token_estimate) token_estimate,d.original_name FROM knowledge_transmission_grant_items i"
        " LEFT JOIN knowledge_documents d ON d.id=i.document_id WHERE i.grant_id=?"
        " GROUP BY i.document_id,i.transmission_policy,i.sensitivity,d.original_name ORDER BY i.document_id",
        (grant["id"],),
    ).fetchall()]
    return {
        "id": grant["id"], "status": grant["status"], "protocol_version": GRANT_PROTOCOL_VERSION,
        "provider": {
            "id": grant["provider_id"], "model": model or grant["model"],
            "location": grant["provider_location"],
            "location_revision": grant["provider_location_revision"],
        },
        "documents": [{
            "id": item["document_id"], "name": item["original_name"] or "来源已变化",
            "policy": item["transmission_policy"], "sensitivity": item["sensitivity"],
            "chunk_count": item["chunk_count"], "token_estimate": item["token_estimate"],
        } for item in items],
        "document_count": grant["document_count"], "chunk_count": grant["chunk_count"],
        "token_range": {"min": grant["token_min"], "max": grant["token_max"]},
        "expires_at": grant["expires_at"], "single_use": True,
        "can_allow_once": not any(item["transmission_policy"] == "local_only" for item in items),
        "can_always_allow": not any(item["sensitivity"] == "sensitive" for item in items),
        "stores_content": False,
    }


def _not_needed(provider: dict, model: str, plan: Plan) -> dict:
    return {
        "status": "not_needed", "id": None,
        "provider": {"id": provider["id"], "model": model,
                     "location": provider["execution_location"],
                     "location_revision": provider["location_revision"]},
        "document_count": 0, "chunk_count": 0, "documents": [],
        "token_range": {"min": 0, "max": 0}, "single_use": True,
        "can_allow_once": False, "can_always_allow": False, "stores_content": False,
        "allowed_chunk_count": len(plan.allowed),
    }


def _provider_snapshot(provider: dict | None) -> dict:
    provider = provider or {}
    location = str(provider.get("execution_location") or "unknown")
    if location not in {"local", "remote", "unknown"}:
        location = "unknown"
    return {"id": provider.get("id"), "execution_location": location,
            "location_revision": max(1, int(provider.get("location_revision") or 1))}


def _require_session(session_id: str) -> None:
    conn = db.connect()
    try:
        if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
            raise GrantError("session_missing", "会话不存在", status_code=404)
    finally:
        conn.close()


def _validate_nonce(value: str) -> None:
    if not 16 <= len(str(value or "")) <= 64 or any(
        not (char.isalnum() or char in "-_") for char in value
    ):
        raise GrantError("request_nonce_invalid", "请求标识无效", status_code=400)


def _item_tokens(item: dict) -> int:
    return knowledge_context.estimate_tokens(str(item.get("content") or "")) + 12


def _verified_content_sha(item: dict) -> str:
    actual = _hash(str(item.get("content") or ""))
    if actual != item.get("content_sha256"):
        raise GrantError("grant_source_changed", "资料内容或索引指纹已经变化，请重新索引后再试")
    return actual


def _normalize(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_hash(value: object) -> str:
    return _hash(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _event(conn, grant_id: str, action: str, before: str | None, after: str,
           reason: str, item_count: int) -> None:
    conn.execute(
        "INSERT INTO knowledge_transmission_grant_events("
        "id,grant_id,action,before_status,after_status,reason_code,item_count,created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (db.new_id(), grant_id, action, before, after, reason, item_count, db.now()),
    )


def _expire_locked(conn, grant_id: str, before: str) -> None:
    now = db.now()
    conn.execute(
        "UPDATE knowledge_transmission_grants SET status='expired',token_hash=NULL,updated_at=?,"
        "error_code='grant_expired' WHERE id=? AND status=?", (now, grant_id, before),
    )
    _event(conn, grant_id, "grant_expired", before, "expired", "ttl_elapsed", 0)


def _revoke_locked(conn, grant_id: str, reason: str) -> None:
    now = db.now()
    conn.execute(
        "UPDATE knowledge_transmission_grants SET status='revoked',token_hash=NULL,revoked_at=?,"
        "updated_at=?,error_code=? WHERE id=?", (now, now, reason, grant_id),
    )
    _event(conn, grant_id, "grant_revoked", "issued", "revoked", reason, 0)
