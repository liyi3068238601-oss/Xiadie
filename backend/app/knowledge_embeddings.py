"""F.8 可插拔 Embedding：本地 BGE-M3 dense ONNX，远程路径默认拒绝。"""
from __future__ import annotations

import json
import hashlib
import os
import threading
from pathlib import Path
from typing import Protocol

from . import db

PROVIDER_ID = "local-bge-m3-onnx"
MODEL_NAME = "BAAI/bge-m3"
EMBEDDING_VERSION = "bge-m3-onnx-int8-dense-cls-v1:0826f8c1ab9e"
MODEL_SHA256 = "0826f8c1ab9edf1801db86c61919d4d108e8bfc0b809ec823ad366882ff0b77d"
DIMENSION = 1024
MAX_TOKENS = 512
MAX_VECTOR_CANDIDATES = 10_000
BATCH_SIZE = 8
_provider = None
_provider_lock = threading.Lock()


class EmbeddingError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class EmbeddingProvider(Protocol):
    provider_id: str
    model: str
    version: str
    dimension: int

    def encode(self, texts: list[str]) -> "object": ...


class RemoteEmbeddingProvider:
    """接口边界；没有逐次授权对象时绝不发送正文。"""
    def encode(self, _texts: list[str], *, consent_id: str | None = None):
        if not consent_id:
            raise EmbeddingError("remote_embedding_consent_required", "远程向量处理需要本次明确授权")
        raise EmbeddingError("remote_embedding_not_configured", "尚未配置远程向量供应商")


class LocalBgeM3Provider:
    provider_id = PROVIDER_ID
    model = MODEL_NAME
    version = EMBEDDING_VERSION
    dimension = DIMENSION

    def __init__(self, root: Path):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        model_path = root / "onnx" / "model_quantized.onnx"
        digest = hashlib.sha256()
        with model_path.open("rb") as handle:
            for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != MODEL_SHA256:
            raise EmbeddingError("embedding_model_hash_mismatch", "本地向量模型指纹不匹配")
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(model_path),
            sess_options=options, providers=["CPUExecutionProvider"],
        )
        inputs = {item.name for item in self._session.get_inputs()}
        outputs = self._session.get_outputs()
        if inputs != {"input_ids", "attention_mask"} or not outputs or outputs[0].name != "last_hidden_state":
            raise EmbeddingError("embedding_model_contract_invalid", "本地向量模型输入输出不兼容")
        self._tokenizer = Tokenizer.from_file(str(root / "tokenizer.json"))
        self._tokenizer.enable_truncation(max_length=MAX_TOKENS)

    def encode(self, texts: list[str]):
        import numpy as np

        vectors = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start:start + BATCH_SIZE]
            self._tokenizer.enable_padding()
            encoded = self._tokenizer.encode_batch(batch)
            ids = np.asarray([item.ids for item in encoded], dtype=np.int64)
            mask = np.asarray([item.attention_mask for item in encoded], dtype=np.int64)
            hidden = self._session.run(None, {"input_ids": ids, "attention_mask": mask})[0]
            dense = hidden[:, 0, :].astype(np.float32, copy=False)
            norms = np.linalg.norm(dense, axis=1, keepdims=True)
            dense = dense / np.maximum(norms, 1e-12)
            if dense.shape[1] != DIMENSION or not np.isfinite(dense).all():
                raise EmbeddingError("embedding_output_invalid", "本地向量模型输出无效")
            vectors.append(dense)
        return np.concatenate(vectors, axis=0) if vectors else np.empty((0, DIMENSION), dtype=np.float32)


def model_root() -> Path | None:
    candidates = []
    if os.environ.get("XIADIE_BGE_M3_DIR"):
        candidates.append(Path(os.environ["XIADIE_BGE_M3_DIR"]))
    candidates.append(Path(__file__).resolve().parents[3] / "bge-m3")
    for root in candidates:
        if all((root / relative).is_file() for relative in (
            "config.json", "tokenizer.json", "onnx/model_quantized.onnx",
        )):
            return root.resolve()
    return None


def availability() -> dict:
    root = model_root()
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
        dependencies = True
    except ImportError:
        dependencies = False
    return {
        "available": bool(root and dependencies), "provider_id": PROVIDER_ID,
        "model": MODEL_NAME, "embedding_version": EMBEDDING_VERSION,
        "model_sha256": MODEL_SHA256,
        "dimension": DIMENSION, "local_only": True,
        "model_path_configured": bool(root), "dependencies_available": dependencies,
        "remote_requires_per_request_consent": True,
    }


def provider() -> LocalBgeM3Provider:
    global _provider
    if _provider is not None:
        return _provider
    root = model_root()
    if not root:
        raise EmbeddingError("embedding_model_missing", "未找到本地 BGE-M3 模型")
    with _provider_lock:
        if _provider is None:
            try:
                _provider = LocalBgeM3Provider(root)
            except ImportError as error:
                raise EmbeddingError("embedding_runtime_missing", "本地向量运行依赖未安装") from error
    return _provider


def enqueue(document_id: str) -> dict | None:
    if db.get_setting("knowledge_local_embedding_enabled", "1") != "1" or not availability()["available"]:
        return None
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        document = conn.execute(
            "SELECT status FROM knowledge_documents WHERE id=?", (document_id,),
        ).fetchone()
        if not document or document["status"] != "indexed":
            conn.rollback()
            return None
        active = conn.execute(
            "SELECT * FROM knowledge_embedding_runs WHERE document_id=? AND status IN ('queued','running')"
            " ORDER BY created_at DESC LIMIT 1", (document_id,),
        ).fetchone()
        if active:
            conn.rollback()
            return dict(active)
        now, run_id = db.now(), db.new_id()
        conn.execute(
            "INSERT INTO knowledge_embedding_runs("
            "id,document_id,provider_id,model,embedding_version,status,created_at,updated_at)"
            " VALUES(?,?,?,?,?,'queued',?,?)",
            (run_id, document_id, PROVIDER_ID, MODEL_NAME, EMBEDDING_VERSION, now, now),
        )
        _event(conn, run_id, "embedding_queued", None, "queued", None, now)
        conn.commit()
        return dict(conn.execute("SELECT * FROM knowledge_embedding_runs WHERE id=?", (run_id,)).fetchone())
    finally:
        conn.close()


def process_due(*, limit: int = 1) -> int:
    count = 0
    for _ in range(max(1, min(int(limit), 3))):
        run = _claim()
        if not run:
            break
        try:
            _build(run)
        except EmbeddingError as error:
            _fail(run, error.code)
        except Exception:  # noqa: BLE001 - 不记录模型/路径底层详情
            _fail(run, "embedding_build_failed")
        count += 1
    return count


def search(query: str, *, collection_id: str | None = None,
           document_ids: list[str] | None = None, tags: list[str] | None = None,
           limit: int = 6) -> dict:
    import numpy as np

    if not availability()["available"]:
        return {"results": [], "available": False, "error_code": "embedding_unavailable"}
    filters = list(dict.fromkeys(document_ids or []))
    tag_filters = [str(tag).strip() for tag in dict.fromkeys(tags or []) if str(tag).strip()]
    where = [
        "d.status='indexed'", "d.embedding_version=?", "d.embedding_indexed_at IS NOT NULL",
        "d.index_version IN ('knowledge-fts-terms-v1','knowledge-fts-terms-v2')",
        "d.governance_status='active'",
        "co.status='active'", "e.embedding_version=?",
        "e.dimension=?",
    ]
    params: list[object] = [EMBEDDING_VERSION, EMBEDDING_VERSION, DIMENSION]
    if collection_id:
        where.append("d.collection_id=?")
        params.append(collection_id)
    if filters:
        where.append("d.id IN (" + ",".join("?" for _ in filters) + ")")
        params.extend(filters)
    if tag_filters:
        where.append("EXISTS (SELECT 1 FROM json_each(d.tags_json) jt WHERE jt.value IN (" +
                     ",".join("?" for _ in tag_filters) + "))")
        params.extend(tag_filters)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT c.*,d.collection_id,d.original_name,d.tags_json,e.vector_blob "
            "FROM knowledge_chunk_embeddings e JOIN knowledge_chunks c ON c.id=e.chunk_id "
            "JOIN knowledge_documents d ON d.id=e.document_id "
            "JOIN knowledge_collections co ON co.id=d.collection_id WHERE " + " AND ".join(where) +
            " ORDER BY e.document_id,e.chunk_id LIMIT ?", params + [MAX_VECTOR_CANDIDATES],
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return {"results": [], "available": True, "error_code": None}
    query_vector = provider().encode([query])[0]
    matrix = np.stack([np.frombuffer(row["vector_blob"], dtype="<f4") for row in rows])
    scores = matrix @ query_vector
    order = np.argsort(-scores, kind="stable")[:max(1, min(int(limit), 12))]
    return {
        "available": True, "error_code": None,
        "results": [_public_vector_result(dict(rows[int(i)]), float(scores[int(i)])) for i in order],
    }


def clear_document_locked(conn, document_id: str) -> None:
    conn.execute("DELETE FROM knowledge_chunk_embeddings WHERE document_id=?", (document_id,))
    conn.execute(
        "UPDATE knowledge_embedding_runs SET status='skipped',error_code='document_rebuild',"
        "finished_at=?,updated_at=? WHERE document_id=? AND status IN ('queued','running')",
        (db.now(), db.now(), document_id),
    )
    conn.execute(
        "UPDATE knowledge_documents SET embedding_mode='none',embedding_provider_id=NULL,"
        "embedding_model=NULL,embedding_version=NULL,embedding_indexed_at=NULL,"
        "embedding_dimension=NULL,embedding_error_code=NULL WHERE id=?", (document_id,),
    )


def _claim() -> dict | None:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT r.* FROM knowledge_embedding_runs r JOIN knowledge_documents d ON d.id=r.document_id "
            "WHERE r.status='queued' AND r.attempt_count<r.max_attempts AND d.status='indexed' "
            "ORDER BY r.created_at,r.id LIMIT 1"
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        now = db.now()
        cursor = conn.execute(
            "UPDATE knowledge_embedding_runs SET status='running',attempt_count=attempt_count+1,"
            "started_at=COALESCE(started_at,?),error_code=NULL,updated_at=? WHERE id=? AND status='queued'",
            (now, now, row["id"]),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            return None
        _event(conn, row["id"], "embedding_started", "queued", "running", None, now)
        conn.commit()
        result = dict(row)
        result["attempt_count"] += 1
        return result
    finally:
        conn.close()


def _build(run: dict) -> None:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id,content,content_sha256 FROM knowledge_chunks WHERE document_id=? ORDER BY ordinal",
            (run["document_id"],),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        raise EmbeddingError("embedding_chunks_missing", "没有可建立向量的切片")
    vectors = provider().encode([row["content"] for row in rows])
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT status FROM knowledge_documents WHERE id=?", (run["document_id"],),
        ).fetchone()
        chunks = conn.execute(
            "SELECT id,content_sha256 FROM knowledge_chunks WHERE document_id=? ORDER BY ordinal",
            (run["document_id"],),
        ).fetchall()
        if not current or current["status"] != "indexed" or [tuple(row) for row in chunks] != [
            (row["id"], row["content_sha256"]) for row in rows
        ]:
            raise EmbeddingError("embedding_source_changed", "向量建立期间来源发生变化")
        conn.execute("DELETE FROM knowledge_chunk_embeddings WHERE document_id=?", (run["document_id"],))
        now = db.now()
        for row, vector in zip(rows, vectors, strict=True):
            conn.execute(
                "INSERT INTO knowledge_chunk_embeddings("
                "chunk_id,document_id,provider_id,model,embedding_version,dimension,vector_blob,"
                "content_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (row["id"], run["document_id"], PROVIDER_ID, MODEL_NAME, EMBEDDING_VERSION,
                 DIMENSION, vector.astype("<f4", copy=False).tobytes(), row["content_sha256"], now),
            )
        # The original privacy invariant deliberately reserves provider/model columns
        # for remote transmission.  Local provenance lives on the run/vector rows and
        # the independently versioned document marker, so setting local mode must keep
        # those two remote-only columns NULL.
        conn.execute(
            "UPDATE knowledge_documents SET embedding_mode='local',embedding_provider_id=NULL,"
            "embedding_model=NULL,embedding_version=?,embedding_indexed_at=?,embedding_dimension=?,"
            "embedding_error_code=NULL,updated_at=? WHERE id=?",
            (EMBEDDING_VERSION, now, DIMENSION, now, run["document_id"]),
        )
        conn.execute(
            "UPDATE knowledge_embedding_runs SET status='completed',vector_count=?,error_code=NULL,"
            "finished_at=?,updated_at=? WHERE id=? AND status='running'",
            (len(rows), now, now, run["id"]),
        )
        _event(conn, run["id"], "embedding_completed", "running", "completed", None, now,
               {"vector_count": len(rows), "dimension": DIMENSION})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _fail(run: dict, code: str) -> None:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT * FROM knowledge_embedding_runs WHERE id=?", (run["id"],)).fetchone()
        if not current or current["status"] != "running":
            conn.rollback()
            return
        exhausted = int(current["attempt_count"]) >= int(current["max_attempts"])
        after, now = ("failed" if exhausted else "queued"), db.now()
        conn.execute(
            "UPDATE knowledge_embedding_runs SET status=?,error_code=?,finished_at=?,updated_at=? WHERE id=?",
            (after, code, now if exhausted else None, now, run["id"]),
        )
        conn.execute(
            "UPDATE knowledge_documents SET embedding_mode='none',embedding_error_code=?,updated_at=? WHERE id=?",
            (code, now, run["document_id"]),
        )
        _event(conn, run["id"], "embedding_failed" if exhausted else "embedding_retry", "running", after, code, now)
        conn.commit()
    finally:
        conn.close()


def _public_vector_result(item: dict, score: float) -> dict:
    item.pop("vector_blob", None)
    return {
        "chunk_id": item["id"], "document_id": item["document_id"],
        "collection_id": item["collection_id"], "original_name": item["original_name"],
        "ordinal": item["ordinal"], "content": item["content"],
        "content_sha256": item["content_sha256"], "tags": json.loads(item["tags_json"]),
        "heading_path": json.loads(item["heading_path_json"]),
        "paragraph_start": item["paragraph_start"], "paragraph_end": item["paragraph_end"],
        "line_start": item["line_start"], "line_end": item["line_end"],
        "char_start": item["char_start"], "char_end": item["char_end"],
        "page_start": item["page_start"], "page_end": item["page_end"],
        "match_type": "vector", "context_of": None, "rank": None, "vector_score": score,
    }


def _event(conn, run_id: str, action: str, before: str | None, after: str,
           error: str | None, now: float, metadata: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO knowledge_embedding_events("
        "id,run_id,action,before_status,after_status,error_code,metadata_json,created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (db.new_id(), run_id, action, before, after, error,
         json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")), now),
    )
