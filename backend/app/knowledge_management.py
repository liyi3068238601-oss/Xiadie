"""F.7 知识文档管理：标签、重建和可重试的异步删除闭环。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import db, knowledge, knowledge_search

MAX_TAGS = 10
MAX_TAG_CHARS = 40
DELETE_RUNNING_STALE_SECONDS = 5 * 60


def update_tags(document_id: str, tags: list[str]) -> dict | None:
    normalized = _normalize_tags(tags)
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM knowledge_documents WHERE id=?", (document_id,)).fetchone()
        if not row:
            conn.rollback()
            return None
        if row["status"] in {"delete_pending", "delete_failed"}:
            raise knowledge.KnowledgeImportError("document_deleting", "删除中的文档不能修改标签")
        now = db.now()
        conn.execute(
            "UPDATE knowledge_documents SET tags_json=?,updated_at=? WHERE id=?",
            (json.dumps(normalized, ensure_ascii=False), now, document_id),
        )
        conn.commit()
        return knowledge.public_document({**dict(row), "tags_json": json.dumps(normalized, ensure_ascii=False), "updated_at": now})
    finally:
        conn.close()


def enqueue_reindex(document_id: str) -> dict | None:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM knowledge_documents WHERE id=?", (document_id,)).fetchone()
        if not row:
            conn.rollback()
            return None
        if row["status"] in {"delete_pending", "delete_failed", "queued", "parsing"}:
            raise knowledge.KnowledgeImportError("document_not_reindexable", "当前状态不能重建索引")
        active = conn.execute(
            "SELECT id FROM knowledge_import_runs WHERE document_id=? AND status IN "
            "('queued','running','cancel_requested','recovery_pending') LIMIT 1", (document_id,),
        ).fetchone()
        if active:
            raise knowledge.KnowledgeImportError("knowledge_run_active", "该文档已有处理任务")
        knowledge.assert_document_transition(row["status"], "queued")
        run_id, now = db.new_id(), db.now()
        knowledge_search.clear_document_index_locked(conn, document_id)
        conn.execute("DELETE FROM knowledge_chunks WHERE document_id=?", (document_id,))
        conn.execute(
            "UPDATE knowledge_documents SET status='queued',index_version=NULL,indexed_at=NULL,"
            "chunker_version=NULL,chunked_at=NULL,chunk_count=0,error_code=NULL,updated_at=? WHERE id=?",
            (now, document_id),
        )
        conn.execute(
            "INSERT INTO knowledge_import_runs("
            "id,document_id,idempotency_key,trigger,status,current_stage,progress,created_at,updated_at)"
            " VALUES(?,?,?,'reindex','queued','validation',0,?,?)",
            (run_id, document_id, f"reindex:{document_id}:{run_id}", now, now),
        )
        _import_event(conn, run_id, "reindex_requested", None, "queued", "validation", now)
        conn.commit()
    finally:
        conn.close()
    _wake()
    from . import knowledge_worker
    return knowledge_worker.get_run(run_id)


def enqueue_delete(document_id: str) -> dict | None:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM knowledge_documents WHERE id=?", (document_id,)).fetchone()
        if not row:
            conn.rollback()
            return None
        existing = conn.execute(
            "SELECT * FROM knowledge_deletion_runs WHERE document_id=? AND status IN ('queued','running')"
            " ORDER BY created_at DESC,id DESC LIMIT 1", (document_id,),
        ).fetchone()
        if existing:
            conn.rollback()
            return get_deletion_run(existing["id"])
        if row["status"] == "delete_failed":
            raise knowledge.KnowledgeImportError("delete_retry_required", "请使用重试删除操作")
        if row["status"] != "delete_pending":
            knowledge.assert_document_transition(row["status"], "delete_pending")
        now, run_id = db.now(), db.new_id()
        knowledge_search.clear_document_index_locked(conn, document_id)
        conn.execute(
            "UPDATE knowledge_documents SET status='delete_pending',index_version=NULL,indexed_at=NULL,"
            "error_code=NULL,updated_at=? WHERE id=?", (now, document_id),
        )
        _stop_import_runs_locked(conn, document_id, now)
        conn.execute(
            "INSERT INTO knowledge_deletion_runs("
            "id,document_id,collection_id,content_sha256,status,created_at,updated_at)"
            " VALUES(?,?,?,?,'queued',?,?)",
            (run_id, document_id, row["collection_id"], row["content_sha256"], now, now),
        )
        _delete_event(conn, run_id, "delete_requested", row["status"], "queued", None, now)
        conn.commit()
    finally:
        conn.close()
    _wake()
    return get_deletion_run(run_id)


def retry_delete(run_id: str) -> dict | None:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        run = conn.execute("SELECT * FROM knowledge_deletion_runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            conn.rollback()
            return None
        document = conn.execute(
            "SELECT status FROM knowledge_documents WHERE id=?", (run["document_id"],),
        ).fetchone()
        if run["status"] != "failed" or not document or document["status"] != "delete_failed":
            raise knowledge.KnowledgeImportError("delete_not_retryable", "该删除任务当前不能重试")
        knowledge.assert_document_transition("delete_failed", "delete_pending")
        now = db.now()
        conn.execute(
            "UPDATE knowledge_documents SET status='delete_pending',error_code=NULL,updated_at=? WHERE id=?",
            (now, run["document_id"]),
        )
        conn.execute(
            "UPDATE knowledge_deletion_runs SET status='queued',error_code=NULL,started_at=NULL,"
            "finished_at=NULL,updated_at=? WHERE id=?", (now, run_id),
        )
        _delete_event(conn, run_id, "delete_retried", "failed", "queued", None, now)
        conn.commit()
    finally:
        conn.close()
    _wake()
    return get_deletion_run(run_id)


def process_delete_due(*, limit: int = 3) -> int:
    recover_stale_deletions()
    count = 0
    for _ in range(max(1, min(int(limit), 10))):
        run = _claim_delete()
        if not run:
            break
        try:
            _delete_claimed(run)
        except (OSError, knowledge.KnowledgeImportError) as error:
            _fail_delete(run, getattr(error, "code", "knowledge_delete_io_failed"))
        except Exception:  # noqa: BLE001 - 错误详情不得进入审计
            _fail_delete(run, "knowledge_delete_failed")
        count += 1
    return count


def recover_stale_deletions(*, now: float | None = None) -> int:
    at = db.now() if now is None else float(now)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM knowledge_deletion_runs WHERE status='running' AND updated_at<?",
            (at - DELETE_RUNNING_STALE_SECONDS,),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        _fail_delete(dict(row), "knowledge_delete_interrupted")
    return len(rows)


def get_deletion_run(run_id: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM knowledge_deletion_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["events"] = [
            {**dict(event), "metadata": json.loads(event["metadata_json"])}
            for event in conn.execute(
                "SELECT * FROM knowledge_deletion_events WHERE run_id=? ORDER BY created_at,id", (run_id,),
            ).fetchall()
        ]
        for event in result["events"]:
            event.pop("metadata_json", None)
        return result
    finally:
        conn.close()


def list_collections() -> list[dict]:
    conn = db.connect()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT id,name,description,status,created_at,updated_at FROM knowledge_collections ORDER BY name,id"
        ).fetchall()]
    finally:
        conn.close()


def list_retrieval_audits(*, session_id: str | None = None, limit: int = 30) -> list[dict]:
    conn = db.connect()
    try:
        where, params = (" WHERE r.session_id=?", [session_id]) if session_id else ("", [])
        params.append(max(1, min(int(limit), 100)))
        return [dict(row) for row in conn.execute(
            "SELECT r.id,r.session_id,r.user_message_id,r.assistant_message_id,r.trigger_reason,"
            "r.query_sha256,r.candidate_count,r.injected_count,r.knowledge_tokens,"
            "r.knowledge_token_budget,r.lore_tokens,r.memory_tokens,r.status,r.created_at,r.finished_at,"
            "CASE WHEN s.id IS NULL THEN 0 ELSE 1 END AS session_available "
            "FROM knowledge_chat_retrievals r LEFT JOIN sessions s ON s.id=r.session_id" + where +
            " ORDER BY r.created_at DESC,r.id DESC LIMIT ?", params,
        ).fetchall()]
    finally:
        conn.close()


def _claim_delete() -> dict | None:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT r.* FROM knowledge_deletion_runs r JOIN knowledge_documents d ON d.id=r.document_id "
            "WHERE r.status='queued' AND d.status='delete_pending' AND NOT EXISTS ("
            "SELECT 1 FROM knowledge_import_runs ir WHERE ir.document_id=r.document_id "
            "AND ir.status IN ('running','cancel_requested')) ORDER BY r.created_at,r.id LIMIT 1"
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        now = db.now()
        cursor = conn.execute(
            "UPDATE knowledge_deletion_runs SET status='running',attempt_count=attempt_count+1,"
            "started_at=?,updated_at=? WHERE id=? AND status='queued'", (now, now, row["id"]),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            return None
        _delete_event(conn, row["id"], "delete_started", "queued", "running", None, now)
        conn.commit()
        result = dict(row)
        result["attempt_count"] = int(row["attempt_count"]) + 1
        return result
    finally:
        conn.close()


def _delete_claimed(run: dict) -> None:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT d.*,a.artifact_key FROM knowledge_documents d LEFT JOIN knowledge_parse_artifacts a "
            "ON a.document_id=d.id WHERE d.id=?", (run["document_id"],),
        ).fetchone()
    finally:
        conn.close()
    if not row or row["status"] != "delete_pending":
        raise knowledge.KnowledgeImportError("delete_source_missing", "删除来源状态无效")
    _unlink_strict(knowledge.storage_path_for(dict(row)))
    if row["artifact_key"]:
        _unlink_strict(_artifact_path(row["artifact_key"]))

    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT status FROM knowledge_documents WHERE id=?", (run["document_id"],),
        ).fetchone()
        if not current or current["status"] != "delete_pending":
            raise knowledge.KnowledgeImportError("delete_source_changed", "删除期间文档状态变化")
        knowledge_search.clear_document_index_locked(conn, run["document_id"])
        conn.execute("DELETE FROM knowledge_chunks WHERE document_id=?", (run["document_id"],))
        conn.execute("DELETE FROM knowledge_parse_artifacts WHERE document_id=?", (run["document_id"],))
        conn.execute("DELETE FROM knowledge_documents WHERE id=?", (run["document_id"],))
        now = db.now()
        conn.execute(
            "UPDATE knowledge_deletion_runs SET status='completed',error_code=NULL,finished_at=?,updated_at=? "
            "WHERE id=? AND status='running'", (now, now, run["id"]),
        )
        _delete_event(conn, run["id"], "delete_completed", "running", "completed", None, now)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _fail_delete(run: dict, code: str) -> None:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = db.now()
        document = conn.execute(
            "SELECT status FROM knowledge_documents WHERE id=?", (run["document_id"],),
        ).fetchone()
        if document and document["status"] == "delete_pending":
            knowledge.assert_document_transition("delete_pending", "delete_failed")
            conn.execute(
                "UPDATE knowledge_documents SET status='delete_failed',error_code=?,updated_at=? WHERE id=?",
                (code, now, run["document_id"]),
            )
        conn.execute(
            "UPDATE knowledge_deletion_runs SET status='failed',error_code=?,finished_at=?,updated_at=? "
            "WHERE id=?", (code, now, now, run["id"]),
        )
        _delete_event(conn, run["id"], "delete_failed", "running", "failed", code, now)
        conn.commit()
    finally:
        conn.close()


def _stop_import_runs_locked(conn, document_id: str, now: float) -> None:
    rows = conn.execute(
        "SELECT * FROM knowledge_import_runs WHERE document_id=? AND status IN "
        "('queued','running','cancel_requested','recovery_pending')", (document_id,),
    ).fetchall()
    for row in rows:
        after = "cancel_requested" if row["status"] in {"running", "cancel_requested"} else "cancelled"
        conn.execute(
            "UPDATE knowledge_import_runs SET status=?,cancel_requested_at=?,error_code='document_delete_requested',"
            "finished_at=?,updated_at=? WHERE id=?",
            (after, now, None if after == "cancel_requested" else now, now, row["id"]),
        )
        _import_event(conn, row["id"], "delete_requested", row["status"], after, row["current_stage"], now)


def _normalize_tags(tags: list[str]) -> list[str]:
    if len(tags) > MAX_TAGS:
        raise knowledge.KnowledgeImportError("knowledge_tags_invalid", "标签最多 10 项")
    result: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        value = str(raw).strip()
        if not value or len(value) > MAX_TAG_CHARS or re.search(r"[\x00-\x1f\x7f]", value):
            raise knowledge.KnowledgeImportError("knowledge_tags_invalid", "标签不能为空、含控制字符或超过 40 字符")
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _artifact_path(key: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}\.json", str(key)):
        raise knowledge.KnowledgeImportError("artifact_key_invalid", "解析产物键无效")
    root = knowledge.PARSED_DIR.resolve()
    path = (root / key).resolve()
    if path.parent != root:
        raise knowledge.KnowledgeImportError("artifact_key_invalid", "解析产物路径越界")
    return path


def _unlink_strict(path: Path) -> None:
    path.unlink(missing_ok=True)


def _delete_event(conn, run_id: str, action: str, before: str | None, after: str,
                  error: str | None, now: float) -> None:
    conn.execute(
        "INSERT INTO knowledge_deletion_events("
        "id,run_id,action,before_status,after_status,error_code,metadata_json,created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (db.new_id(), run_id, action, before, after, error, "{}", now),
    )


def _import_event(conn, run_id: str, action: str, before: str | None, after: str,
                  stage: str, now: float) -> None:
    conn.execute(
        "INSERT INTO knowledge_import_events("
        "id,run_id,action,before_status,after_status,stage,metadata_json,created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (db.new_id(), run_id, action, before, after, stage, "{}", now),
    )


def _wake() -> None:
    try:
        from . import knowledge_worker
        knowledge_worker.wake_worker()
    except Exception:  # noqa: BLE001 - 唤醒失败不回滚已提交请求
        pass
