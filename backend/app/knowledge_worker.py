"""F.3 用户知识本地解析 worker：协作取消、有限重试和陈旧恢复。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from contextlib import suppress
from pathlib import Path

from . import db, knowledge, knowledge_chunker, knowledge_parser

RUNNING_STALE_SECONDS = 5 * 60
FIRST_RETRY_DELAY_SECONDS = 30
WORKER_IDLE_SECONDS = 60
PARSING_PROGRESS = 25
PARSED_PROGRESS = 45
CHUNKING_PROGRESS = 50
CHUNKED_PROGRESS = 65
_worker_task: asyncio.Task | None = None
_wake_event: asyncio.Event | None = None
_logger = logging.getLogger(__name__)


async def start_worker() -> None:
    global _worker_task, _wake_event
    if _worker_task and not _worker_task.done():
        return
    recover_stale_runs()
    _wake_event = asyncio.Event()
    _worker_task = asyncio.create_task(_worker_loop(), name="xiadie-knowledge-parser")
    wake_worker()


async def stop_worker() -> None:
    global _worker_task, _wake_event
    task = _worker_task
    _worker_task = None
    _wake_event = None
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def wake_worker() -> None:
    if _wake_event:
        _wake_event.set()


async def _worker_loop() -> None:
    while True:
        if _wake_event:
            _wake_event.clear()
        try:
            processed = await process_due(limit=3)
        except Exception:  # noqa: BLE001
            _logger.exception("Knowledge parser worker loop failed")
            processed = 0
        if processed:
            continue
        try:
            if _wake_event:
                await asyncio.wait_for(_wake_event.wait(), timeout=WORKER_IDLE_SECONDS)
            else:
                await asyncio.sleep(WORKER_IDLE_SECONDS)
        except asyncio.TimeoutError:
            pass


async def process_due(*, limit: int = 3) -> int:
    recover_stale_runs()
    count = 0
    for _ in range(max(1, min(int(limit), 10))):
        row = _claim_next()
        if not row:
            break
        await _process_claimed(row)
        count += 1
    return count


async def _process_claimed(row: dict) -> None:
    if _finish_cancel(row["id"]):
        return
    try:
        if row["current_stage"] == "chunking":
            await asyncio.to_thread(_chunk_run, row)
        else:
            await asyncio.to_thread(_parse_run, row)
    except knowledge_chunker.ChunkingCancelled:
        _finish_cancel(row["id"])
    except asyncio.CancelledError:
        _mark_interrupted(row["id"])
        raise
    except knowledge.KnowledgeImportError as error:
        _mark_failure(row, error.code)
    except (OSError, UnicodeError, ValueError):
        _mark_failure(
            row, "knowledge_chunk_failed" if row["current_stage"] == "chunking"
            else "knowledge_parse_failed",
        )
    except Exception as error:  # noqa: BLE001
        _logger.error("Knowledge pipeline failed with error type=%s", type(error).__name__)
        _mark_failure(
            row, "knowledge_chunk_failed" if row["current_stage"] == "chunking"
            else "knowledge_parse_failed",
        )


def _claim_next() -> dict | None:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = db.now()
        row = conn.execute(
            "SELECT r.*,d.status AS document_status FROM knowledge_import_runs r"
            " JOIN knowledge_documents d ON d.id=r.document_id"
            " WHERE (r.status='queued' OR (r.status='recovery_pending'"
            " AND COALESCE(r.next_attempt_at,0)<=?))"
            " AND r.current_stage IN ('validation','copy','parsing','chunking')"
            " AND r.attempt_count<r.max_attempts"
            " AND d.status IN ('queued','parsing')"
            " ORDER BY r.created_at,r.id LIMIT 1", (now,),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        attempt = int(row["attempt_count"]) + 1
        stage = "chunking" if row["current_stage"] == "chunking" else "parsing"
        progress = CHUNKING_PROGRESS if stage == "chunking" else PARSING_PROGRESS
        cursor = conn.execute(
            "UPDATE knowledge_import_runs SET status='running',current_stage=?,"
            "progress=?,attempt_count=?,started_at=COALESCE(started_at,?),next_attempt_at=NULL,"
            "error_code=NULL,updated_at=? WHERE id=? AND status=?",
            (stage, progress, attempt, now, now, row["id"], row["status"]),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            return None
        if row["document_status"] == "queued":
            knowledge.assert_document_transition("queued", "parsing")
            conn.execute(
                "UPDATE knowledge_documents SET status='parsing',updated_at=?"
                " WHERE id=? AND status='queued'", (now, row["document_id"]),
            )
        _event(conn, row["id"], f"{stage}_started", row["status"], "running", stage, None,
               {"attempt": attempt}, now)
        conn.commit()
        result = dict(row)
        result.update(status="running", current_stage=stage, attempt_count=attempt)
        return result
    finally:
        conn.close()


def _parse_run(row: dict) -> None:
    document = _get_document(row["document_id"])
    if not document:
        raise knowledge.KnowledgeImportError("document_missing", "知识文档不存在")
    if _current_status(row["id"]) == "cancel_requested":
        _finish_cancel(row["id"])
        return
    source = knowledge.storage_path_for(document)
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != document["content_sha256"]:
        raise knowledge.KnowledgeImportError("source_hash_mismatch", "知识原文件校验失败")
    parsed = knowledge_parser.parse(data, extension=document["extension"])
    if _current_status(row["id"]) == "cancel_requested":
        _finish_cancel(row["id"])
        return
    artifact_key = f"{secrets.token_hex(16)}.json"
    artifact_path = _artifact_path(artifact_key)
    knowledge._atomic_write(artifact_path, knowledge_parser.artifact_bytes(parsed))
    old_key: str | None = None
    try:
        conn = db.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT status FROM knowledge_import_runs WHERE id=?", (row["id"],)
            ).fetchone()
            if not current or current["status"] != "running":
                conn.rollback()
                knowledge._safe_unlink(artifact_path)
                if current and current["status"] == "cancel_requested":
                    _finish_cancel(row["id"])
                return
            existing = conn.execute(
                "SELECT artifact_key FROM knowledge_parse_artifacts WHERE document_id=?",
                (document["id"],),
            ).fetchone()
            old_key = existing["artifact_key"] if existing else None
            now = db.now()
            conn.execute(
                "INSERT INTO knowledge_parse_artifacts("
                "document_id,artifact_key,parser_version,normalized_sha256,char_count,line_count,"
                "heading_count,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(document_id) DO UPDATE SET artifact_key=excluded.artifact_key,"
                "parser_version=excluded.parser_version,normalized_sha256=excluded.normalized_sha256,"
                "char_count=excluded.char_count,line_count=excluded.line_count,"
                "heading_count=excluded.heading_count,updated_at=excluded.updated_at",
                (
                    document["id"], artifact_key, parsed["parser_version"],
                    parsed["normalized_sha256"], parsed["char_count"], parsed["line_count"],
                    parsed["heading_count"], now, now,
                ),
            )
            conn.execute(
                "UPDATE knowledge_documents SET parser_version=?,parsed_at=?,parse_char_count=?,"
                "parse_line_count=?,parse_heading_count=?,error_code=NULL,updated_at=? WHERE id=?",
                (
                    parsed["parser_version"], now, parsed["char_count"], parsed["line_count"],
                    parsed["heading_count"], now, document["id"],
                ),
            )
            conn.execute(
                "UPDATE knowledge_import_runs SET status='queued',current_stage='chunking',"
                "progress=?,attempt_count=0,error_code=NULL,updated_at=?"
                " WHERE id=? AND status='running'",
                (PARSED_PROGRESS, now, row["id"]),
            )
            _event(
                conn, row["id"], "parsing_completed", "running", "queued", "chunking", None,
                {"char_count": parsed["char_count"], "line_count": parsed["line_count"],
                 "heading_count": parsed["heading_count"]}, now,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        knowledge._safe_unlink(artifact_path)
        raise
    if old_key and old_key != artifact_key:
        knowledge._safe_unlink(_artifact_path(old_key))


def _chunk_run(row: dict) -> None:
    document = _get_document(row["document_id"])
    artifact = artifact_for_document(row["document_id"])
    if not document or not artifact:
        raise knowledge.KnowledgeImportError("parse_artifact_missing", "解析产物不存在")
    if _current_status(row["id"]) == "cancel_requested":
        _finish_cancel(row["id"])
        return
    try:
        payload = json.loads(artifact_path_for(artifact).read_text("utf-8"))
        reparsed = knowledge_parser.parse(
            payload.get("normalized_text", "").encode("utf-8"),
            extension=document["extension"],
        )
        chunks = knowledge_chunker.chunk_artifact(
            payload, should_cancel=lambda: _current_status(row["id"]) == "cancel_requested"
        )
        if (
            payload.get("parser_version") != artifact["parser_version"]
            or payload.get("parser_version") != knowledge_parser.PARSER_VERSION
            or payload.get("normalized_sha256") != artifact["normalized_sha256"]
            or payload.get("char_count") != artifact["char_count"]
            or len(payload.get("headings", [])) != artifact["heading_count"]
            or payload.get("headings") != reparsed["headings"]
        ):
            raise ValueError("parse_artifact_invalid")
    except knowledge_chunker.ChunkingCancelled:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise knowledge.KnowledgeImportError(
            "parse_artifact_invalid", "解析产物校验失败"
        ) from error
    if not chunks:
        raise knowledge.KnowledgeImportError("knowledge_no_chunkable_content", "没有可切片的文本内容")
    if _current_status(row["id"]) == "cancel_requested":
        _finish_cancel(row["id"])
        return

    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT status,current_stage FROM knowledge_import_runs WHERE id=?", (row["id"],)
        ).fetchone()
        if not current or current["status"] != "running" or current["current_stage"] != "chunking":
            conn.rollback()
            if current and current["status"] == "cancel_requested":
                _finish_cancel(row["id"])
            return
        now = db.now()
        conn.execute("DELETE FROM knowledge_chunks WHERE document_id=?", (document["id"],))
        for chunk in chunks:
            conn.execute(
                "INSERT INTO knowledge_chunks("
                "id,document_id,ordinal,content,content_sha256,heading_path_json,"
                "paragraph_start,paragraph_end,line_start,line_end,char_start,char_end,"
                "page_start,page_end,chunker_version,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    knowledge_chunker.chunk_id(document["id"], chunk), document["id"],
                    chunk["ordinal"], chunk["content"], chunk["content_sha256"],
                    chunk["heading_path_json"], chunk["paragraph_start"],
                    chunk["paragraph_end"], chunk["line_start"], chunk["line_end"],
                    chunk["char_start"], chunk["char_end"], chunk["page_start"],
                    chunk["page_end"], chunk["chunker_version"], now,
                ),
            )
        conn.execute(
            "UPDATE knowledge_documents SET chunker_version=?,chunked_at=?,chunk_count=?,"
            "error_code=NULL,updated_at=? WHERE id=?",
            (knowledge_chunker.CHUNKER_VERSION, now, len(chunks), now, document["id"]),
        )
        conn.execute(
            "UPDATE knowledge_import_runs SET status='queued',current_stage='indexing',"
            "progress=?,attempt_count=0,error_code=NULL,updated_at=?"
            " WHERE id=? AND status='running' AND current_stage='chunking'",
            (CHUNKED_PROGRESS, now, row["id"]),
        )
        _event(
            conn, row["id"], "chunking_completed", "running", "queued", "indexing", None,
            {"chunk_count": len(chunks)}, now,
        )
        conn.commit()
    finally:
        conn.close()


def recover_stale_runs(*, now: float | None = None) -> int:
    at = db.now() if now is None else float(now)
    conn = db.connect()
    cleanup: list[str] = []
    rows = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT r.*,a.artifact_key FROM knowledge_import_runs r"
            " LEFT JOIN knowledge_parse_artifacts a ON a.document_id=r.document_id"
            " WHERE r.status IN ('running','cancel_requested') AND r.updated_at<?",
            (at - RUNNING_STALE_SECONDS,),
        ).fetchall()
        for row in rows:
            if row["status"] == "cancel_requested":
                _cancel_locked(conn, row, at)
                if row["artifact_key"]:
                    cleanup.append(row["artifact_key"])
                continue
            exhausted = int(row["attempt_count"]) >= int(row["max_attempts"])
            after = "failed" if exhausted else "recovery_pending"
            error = "knowledge_parse_interrupted"
            conn.execute(
                "UPDATE knowledge_import_runs SET status=?,error_code=?,next_attempt_at=?,"
                "finished_at=?,updated_at=? WHERE id=?",
                (after, error, None if exhausted else at, at if exhausted else None, at, row["id"]),
            )
            if exhausted:
                _set_document_terminal_locked(conn, row["document_id"], "failed", error, at)
            _event(conn, row["id"], "failed" if exhausted else "recovery_scheduled",
                   "running", after, row["current_stage"], error, {}, at)
        conn.commit()
    finally:
        conn.close()
    for key in cleanup:
        knowledge._safe_unlink(_artifact_path(key))
    return len(rows)


def cancel(run_id: str) -> dict | None:
    conn = db.connect()
    cleanup: str | None = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT r.*,a.artifact_key FROM knowledge_import_runs r"
            " LEFT JOIN knowledge_parse_artifacts a ON a.document_id=r.document_id WHERE r.id=?",
            (run_id,),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        if row["status"] in {"cancelled", "completed", "failed"}:
            conn.rollback()
            return _run_row(conn, row, include_events=True)
        now = db.now()
        if row["status"] == "running":
            conn.execute(
                "UPDATE knowledge_import_runs SET status='cancel_requested',cancel_requested_at=?,"
                "updated_at=? WHERE id=?", (now, now, run_id),
            )
            _event(conn, run_id, "cancel_requested", "running", "cancel_requested",
                   row["current_stage"], "user_cancelled", {}, now)
        else:
            _cancel_locked(conn, row, now)
            cleanup = row["artifact_key"]
        conn.commit()
        updated = conn.execute("SELECT * FROM knowledge_import_runs WHERE id=?", (run_id,)).fetchone()
        result = _run_row(conn, updated, include_events=True)
    finally:
        conn.close()
    if cleanup:
        knowledge._safe_unlink(_artifact_path(cleanup))
    return result


def get_run(run_id: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM knowledge_import_runs WHERE id=?", (run_id,)).fetchone()
        return _run_row(conn, row, include_events=True) if row else None
    finally:
        conn.close()


def artifact_for_document(document_id: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM knowledge_parse_artifacts WHERE document_id=?", (document_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def chunks_for_document(document_id: str) -> list[dict]:
    conn = db.connect()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM knowledge_chunks WHERE document_id=? ORDER BY ordinal",
            (document_id,),
        ).fetchall()]
    finally:
        conn.close()


def artifact_path_for(artifact: dict) -> Path:
    return _artifact_path(artifact["artifact_key"])


def _finish_cancel(run_id: str) -> bool:
    conn = db.connect()
    cleanup: str | None = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT r.*,a.artifact_key FROM knowledge_import_runs r"
            " LEFT JOIN knowledge_parse_artifacts a ON a.document_id=r.document_id WHERE r.id=?",
            (run_id,),
        ).fetchone()
        if not row or row["status"] != "cancel_requested":
            conn.rollback()
            return False
        _cancel_locked(conn, row, db.now())
        cleanup = row["artifact_key"]
        conn.commit()
    finally:
        conn.close()
    if cleanup:
        knowledge._safe_unlink(_artifact_path(cleanup))
    return True


def _cancel_locked(conn, row, now: float) -> None:
    conn.execute("DELETE FROM knowledge_chunks WHERE document_id=?", (row["document_id"],))
    conn.execute(
        "DELETE FROM knowledge_parse_artifacts WHERE document_id=?", (row["document_id"],)
    )
    conn.execute(
        "UPDATE knowledge_documents SET parser_version=NULL,parsed_at=NULL,parse_char_count=0,"
        "parse_line_count=0,parse_heading_count=0,chunker_version=NULL,chunked_at=NULL,"
        "chunk_count=0 WHERE id=?", (row["document_id"],),
    )
    _set_document_terminal_locked(conn, row["document_id"], "cancelled", "user_cancelled", now)
    conn.execute(
        "UPDATE knowledge_import_runs SET status='cancelled',error_code='user_cancelled',"
        "next_attempt_at=NULL,finished_at=?,updated_at=? WHERE id=?",
        (now, now, row["id"]),
    )
    _event(conn, row["id"], "cancelled", row["status"], "cancelled",
           row["current_stage"], "user_cancelled", {}, now)


def _mark_failure(row: dict, code: str) -> None:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT * FROM knowledge_import_runs WHERE id=?", (row["id"],)
        ).fetchone()
        if not current:
            conn.rollback()
            return
        if current["status"] == "cancel_requested":
            conn.rollback()
            _finish_cancel(row["id"])
            return
        if current["status"] != "running":
            conn.rollback()
            return
        exhausted = int(current["attempt_count"]) >= int(current["max_attempts"])
        after = "failed" if exhausted else "recovery_pending"
        now = db.now()
        next_at = None if exhausted else now + FIRST_RETRY_DELAY_SECONDS * 2 ** (
            int(current["attempt_count"]) - 1
        )
        conn.execute(
            "UPDATE knowledge_import_runs SET status=?,error_code=?,next_attempt_at=?,"
            "finished_at=?,updated_at=? WHERE id=?",
            (after, code, next_at, now if exhausted else None, now, row["id"]),
        )
        if exhausted:
            conn.execute("DELETE FROM knowledge_chunks WHERE document_id=?", (row["document_id"],))
            _set_document_terminal_locked(conn, row["document_id"], "failed", code, now)
        _event(conn, row["id"], "failed" if exhausted else "retry_scheduled", "running",
               after, current["current_stage"], code,
               {"attempt": current["attempt_count"]}, now)
        conn.commit()
    finally:
        conn.close()


def _mark_interrupted(run_id: str) -> None:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM knowledge_import_runs WHERE id=? AND status='running'", (run_id,)
        ).fetchone()
        if not row:
            conn.rollback()
            return
        now = db.now()
        conn.execute(
            "UPDATE knowledge_import_runs SET status='recovery_pending',"
            "error_code='worker_stopped',next_attempt_at=?,updated_at=? WHERE id=?",
            (now, now, run_id),
        )
        _event(conn, run_id, "recovery_scheduled", "running", "recovery_pending",
               row["current_stage"], "worker_stopped", {}, now)
        conn.commit()
    finally:
        conn.close()


def _set_document_terminal_locked(conn, document_id: str, status: str, code: str, now: float) -> None:
    row = conn.execute(
        "SELECT status FROM knowledge_documents WHERE id=?", (document_id,)
    ).fetchone()
    if not row:
        return
    if row["status"] != status:
        knowledge.assert_document_transition(row["status"], status)
    conn.execute(
        "UPDATE knowledge_documents SET status=?,error_code=?,updated_at=? WHERE id=?",
        (status, code, now, document_id),
    )


def _get_document(document_id: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM knowledge_documents WHERE id=?", (document_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _current_status(run_id: str) -> str | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT status FROM knowledge_import_runs WHERE id=?", (run_id,)).fetchone()
        return row["status"] if row else None
    finally:
        conn.close()


def _artifact_path(key: str) -> Path:
    if len(key) != 37 or not key.endswith(".json") or any(
        char not in "0123456789abcdef" for char in key[:-5]
    ):
        raise knowledge.KnowledgeImportError("artifact_key_invalid", "解析产物存储键无效")
    root = knowledge.PARSED_DIR.resolve()
    path = (root / key).resolve()
    if path.parent != root:
        raise knowledge.KnowledgeImportError("artifact_key_invalid", "解析产物路径越界")
    return path


def _event(conn, run_id: str, action: str, before: str | None, after: str, stage: str,
           error_code: str | None, metadata: dict, now: float) -> None:
    conn.execute(
        "INSERT INTO knowledge_import_events("
        "id,run_id,action,before_status,after_status,stage,error_code,metadata_json,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (db.new_id(), run_id, action, before, after, stage, error_code,
         json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), now),
    )


def _run_row(conn, row, *, include_events: bool = False) -> dict:
    result = dict(row)
    if include_events:
        events = conn.execute(
            "SELECT * FROM knowledge_import_events WHERE run_id=? ORDER BY created_at,rowid",
            (result["id"],),
        ).fetchall()
        result["events"] = []
        for event in events:
            item = dict(event)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result["events"].append(item)
    return result
