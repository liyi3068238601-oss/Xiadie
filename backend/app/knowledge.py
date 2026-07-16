"""用户知识 F.2：TXT/Markdown 安全准入与应用管理的原文副本。"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import zipfile
from contextlib import suppress
from pathlib import Path

from . import db

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_DECODED_CHARS = 2_000_000
MAX_DOCUMENTS = 100
MAX_TOTAL_BYTES = 250 * 1024 * 1024
MAX_FILENAME_CHARS = 240
STORAGE_DIR = Path(db.DATA_DIR) / "knowledge" / "originals"
PARSED_DIR = Path(db.DATA_DIR) / "knowledge" / "parsed"
ALLOWED_MIME_TYPES = {
    ".txt": frozenset({"text/plain", "application/octet-stream"}),
    ".md": frozenset({"text/markdown", "text/plain", "application/octet-stream"}),
    ".pdf": frozenset({"application/pdf", "application/octet-stream"}),
    ".docx": frozenset({
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
    }),
}
DOCUMENT_TRANSITIONS = {
    "staged": frozenset({"queued", "delete_pending"}),
    "queued": frozenset({"parsing", "cancelled", "failed", "delete_pending"}),
    "parsing": frozenset({"indexed", "cancelled", "failed", "delete_pending"}),
    "indexed": frozenset({"queued", "delete_pending"}),
    "failed": frozenset({"queued", "delete_pending"}),
    "cancelled": frozenset({"queued", "delete_pending"}),
    "delete_pending": frozenset({"delete_failed"}),
    "delete_failed": frozenset({"delete_pending"}),
}


class KnowledgeImportError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def assert_document_transition(before: str, after: str) -> None:
    if after not in DOCUMENT_TRANSITIONS.get(before, frozenset()):
        raise KnowledgeImportError("document_transition_invalid", "非法的知识文档状态转换")


def validate_file(filename: str, mime_type: str, data: bytes) -> dict:
    name = _validate_filename(filename)
    extension = Path(name).suffix.casefold()
    if extension not in ALLOWED_MIME_TYPES:
        raise KnowledgeImportError("file_type_unsupported", "目前只支持 TXT、Markdown、PDF 和 DOCX 文件")
    mime = (mime_type or "application/octet-stream").split(";", 1)[0].strip().casefold()
    if mime not in ALLOWED_MIME_TYPES[extension]:
        raise KnowledgeImportError("mime_type_mismatch", "文件类型与声明的 MIME 不一致")
    if not data:
        raise KnowledgeImportError("file_empty", "不能导入空文件")
    if len(data) > MAX_FILE_BYTES:
        raise KnowledgeImportError("file_too_large", "文件超过 10 MiB 限制")
    if extension == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise KnowledgeImportError("pdf_signature_invalid", "PDF 文件头无效")
        decoded_chars = 0
    elif extension == ".docx":
        _validate_docx_archive(data)
        decoded_chars = 0
    elif data.startswith((b"\xff\xfe", b"\xfe\xff", b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        raise KnowledgeImportError("encoding_unsupported", "目前只支持 UTF-8 或 UTF-8 BOM 编码")
    elif b"\x00" in data:
        raise KnowledgeImportError("binary_content_rejected", "检测到二进制内容，不能作为文本知识导入")
    else:
        try:
            text = data.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as error:
            raise KnowledgeImportError("encoding_unsupported", "文件不是有效的 UTF-8 文本") from error
        if len(text) > MAX_DECODED_CHARS:
            raise KnowledgeImportError("decoded_text_too_large", "解码后的文本超过 2,000,000 字符限制")
        if _control_ratio(text) > 0.02:
            raise KnowledgeImportError("binary_content_rejected", "文件包含过多不可见控制字符")
        decoded_chars = len(text)
    return {
        "original_name": name,
        "extension": extension,
        "mime_type": mime,
        "size_bytes": len(data),
        "decoded_chars": decoded_chars,
        "content_sha256": hashlib.sha256(data).hexdigest(),
    }


def import_file(
    filename: str, mime_type: str, data: bytes, *, collection_id: str = "default",
    sensitivity: str = "normal",
) -> dict:
    metadata = validate_file(filename, mime_type, data)
    if sensitivity not in {"normal", "sensitive"}:
        raise KnowledgeImportError("sensitivity_invalid", "敏感级别无效")
    conn = db.connect()
    final_path: Path | None = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        collection = conn.execute(
            "SELECT id FROM knowledge_collections WHERE id=? AND status='active'",
            (collection_id,),
        ).fetchone()
        if not collection:
            raise KnowledgeImportError("collection_missing", "知识库集合不存在或已停用")
        existing = conn.execute(
            "SELECT * FROM knowledge_documents WHERE collection_id=? AND content_sha256=?",
            (collection_id, metadata["content_sha256"]),
        ).fetchone()
        if existing:
            conn.commit()
            return {"document": _document_row(existing), "run": None, "already_exists": True}
        quota = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN status NOT IN ('delete_pending','delete_failed')"
            " THEN 1 ELSE 0 END),0) AS count,COALESCE(SUM(size_bytes),0) AS total"
            " FROM knowledge_documents"
        ).fetchone()
        if int(quota["count"]) >= MAX_DOCUMENTS:
            raise KnowledgeImportError("document_quota_exceeded", "知识库最多保存 100 个文档")
        if int(quota["total"]) + metadata["size_bytes"] > MAX_TOTAL_BYTES:
            raise KnowledgeImportError("storage_quota_exceeded", "知识库原文件总量超过 250 MiB")

        storage_key = f"{secrets.token_hex(16)}{metadata['extension']}"
        final_path = _storage_path(storage_key)
        _atomic_write(final_path, data)
        now = db.now()
        document_id, run_id = db.new_id(), db.new_id()
        assert_document_transition("staged", "queued")
        conn.execute(
            "INSERT INTO knowledge_documents("
            "id,collection_id,original_name,extension,mime_type,size_bytes,content_sha256,"
            "storage_key,status,sensitivity,embedding_mode,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,'queued',?,'none',?,?)",
            (
                document_id, collection_id, metadata["original_name"], metadata["extension"],
                metadata["mime_type"], metadata["size_bytes"], metadata["content_sha256"],
                storage_key, sensitivity, now, now,
            ),
        )
        conn.execute(
            "INSERT INTO knowledge_import_runs("
            "id,document_id,idempotency_key,trigger,status,current_stage,progress,created_at,updated_at)"
            " VALUES(?,?,?,'import','queued','validation',0,?,?)",
            (run_id, document_id, f"import:{collection_id}:{metadata['content_sha256']}", now, now),
        )
        conn.execute(
            "INSERT INTO knowledge_import_events("
            "id,run_id,action,before_status,after_status,stage,metadata_json,created_at)"
            " VALUES(?,?,'admitted',NULL,'queued','validation',?,?)",
            (
                db.new_id(), run_id,
                json.dumps({"size_bytes": metadata["size_bytes"], "decoded_chars": metadata["decoded_chars"]}),
                now,
            ),
        )
        document = conn.execute(
            "SELECT * FROM knowledge_documents WHERE id=?", (document_id,)
        ).fetchone()
        run = conn.execute("SELECT * FROM knowledge_import_runs WHERE id=?", (run_id,)).fetchone()
        result = {"document": _document_row(document), "run": dict(run), "already_exists": False}
        conn.commit()
        # 唤醒只用于降低排队延迟，不能反向破坏已经提交的本地副本。
        with suppress(Exception):
            from . import knowledge_worker
            knowledge_worker.wake_worker()
        return result
    except Exception:
        conn.rollback()
        if final_path is not None:
            _safe_unlink(final_path)
        raise
    finally:
        conn.close()


def list_documents(*, collection_id: str | None = None, status: str | None = None,
                   query: str | None = None) -> list[dict]:
    conn = db.connect()
    try:
        clauses: list[str] = []
        params: list[object] = []
        if collection_id:
            clauses.append("collection_id=?")
            params.append(collection_id)
        if status:
            clauses.append("status=?")
            params.append(status)
        if query:
            clauses.append("original_name LIKE ? ESCAPE '\\'")
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{escaped}%")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = conn.execute(
            "SELECT * FROM knowledge_documents" + where + " ORDER BY updated_at DESC,id",
            params,
        ).fetchall()
        documents = []
        for row in rows:
            item = _document_row(row)
            latest = conn.execute(
                "SELECT id,status,current_stage,progress,error_code,attempt_count,max_attempts"
                " FROM knowledge_import_runs WHERE document_id=?"
                " ORDER BY created_at DESC,id DESC LIMIT 1", (item["id"],),
            ).fetchone()
            item["latest_run"] = dict(latest) if latest else None
            deletion = conn.execute(
                "SELECT id,status,attempt_count,error_code,created_at,updated_at FROM knowledge_deletion_runs"
                " WHERE document_id=? ORDER BY created_at DESC,id DESC LIMIT 1", (item["id"],),
            ).fetchone()
            item["latest_deletion"] = dict(deletion) if deletion else None
            embedding = conn.execute(
                "SELECT id,status,attempt_count,max_attempts,vector_count,error_code,created_at,updated_at"
                " FROM knowledge_embedding_runs WHERE document_id=?"
                " ORDER BY created_at DESC,id DESC LIMIT 1", (item["id"],),
            ).fetchone()
            item["latest_embedding"] = dict(embedding) if embedding else None
            documents.append(item)
        return documents
    finally:
        conn.close()


def storage_path_for(document: dict) -> Path:
    return _storage_path(str(document["storage_key"]))


def public_document(document: dict) -> dict:
    result = {
        key: value for key, value in document.items()
        if key not in {"storage_key", "tags_json"}
    }
    result["tags"] = json.loads(document.get("tags_json") or "[]")
    return result


def public_import_result(result: dict) -> dict:
    run = result["run"]
    public_run = None if run is None else {
        key: run.get(key) for key in ("id", "status", "current_stage", "progress", "error_code")
    }
    return {
        "document": public_document(result["document"]),
        "run": public_run,
        "already_exists": result["already_exists"],
    }


def _validate_filename(filename: str) -> str:
    value = str(filename or "").strip()
    if not value or len(value) > MAX_FILENAME_CHARS:
        raise KnowledgeImportError("filename_invalid", "文件名为空或过长")
    if value in {".", ".."} or any(char in value for char in ("/", "\\", "\x00")):
        raise KnowledgeImportError("filename_invalid", "文件名不能包含路径或非法字符")
    if re.search(r"[\x00-\x1f\x7f]", value):
        raise KnowledgeImportError("filename_invalid", "文件名包含不可见控制字符")
    return value


def _control_ratio(text: str) -> float:
    controls = sum(1 for char in text if ord(char) < 32 and char not in "\n\r\t")
    return controls / max(1, len(text))


def _storage_path(storage_key: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}\.(?:txt|md|pdf|docx)", storage_key):
        raise KnowledgeImportError("storage_key_invalid", "知识文件存储键无效")
    root = STORAGE_DIR.resolve()
    path = (root / storage_key).resolve()
    if path.parent != root:
        raise KnowledgeImportError("storage_key_invalid", "知识文件路径越界")
    return path


def _validate_docx_archive(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            names = {item.filename for item in infos}
            total = sum(item.file_size for item in infos)
            compressed = sum(max(1, item.compress_size) for item in infos)
            if (
                len(infos) > 2_000 or total > 50 * 1024 * 1024 or total / max(1, compressed) > 200
                or "[Content_Types].xml" not in names or "word/document.xml" not in names
                or any(name.startswith("../") or "/../" in name or name.startswith("/") for name in names)
            ):
                raise ValueError("unsafe_docx")
    except (zipfile.BadZipFile, ValueError) as error:
        raise KnowledgeImportError("docx_archive_invalid", "DOCX 结构无效或解压规模不安全") from error


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        _safe_unlink(temporary)
        raise


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _document_row(row) -> dict:
    return dict(row)
