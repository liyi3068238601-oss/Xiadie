"""F.5 本地知识 FTS：索引只存检索词项，正文与定位以 knowledge_chunks 为准。"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable

from . import db, knowledge_chunker

INDEX_VERSION = "knowledge-fts-terms-v1"
MAX_QUERY_CHARS = 256
MAX_QUERY_TERMS = 16
MAX_DOCUMENT_FILTERS = 20
MAX_TAG_FILTERS = 10
MAX_LIMIT = 12
MAX_RESULT_CHARS = 8_000
_CJK_OR_WORD = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+|[A-Za-z0-9_]+")
_CJK = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff]+$")


class SearchError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class IndexingCancelled(RuntimeError):
    pass


def terms_for_text(text: str) -> str:
    terms: list[str] = []
    for match in _CJK_OR_WORD.finditer(text):
        token = match.group(0).casefold()
        if _CJK.fullmatch(token):
            for index, char in enumerate(token):
                terms.append(char)
                if index + 1 < len(token):
                    terms.append(token[index:index + 2])
        else:
            terms.append(token)
    return " ".join(terms)


def prepare_document_index(
    document_id: str, *, should_cancel: Callable[[], bool] | None = None,
) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT rowid,* FROM knowledge_chunks WHERE document_id=? ORDER BY ordinal",
            (document_id,),
        ).fetchall()
    finally:
        conn.close()
    prepared: list[dict] = []
    for expected, row in enumerate(rows):
        if should_cancel and should_cancel():
            raise IndexingCancelled()
        item = dict(row)
        if (
            item["ordinal"] != expected
            or item["chunker_version"] != knowledge_chunker.CHUNKER_VERSION
            or hashlib.sha256(item["content"].encode("utf-8")).hexdigest()
            != item["content_sha256"]
        ):
            raise SearchError("knowledge_chunk_invalid", "知识切片校验失败")
        prepared.append({
            "rowid": item["rowid"], "id": item["id"],
            "content_sha256": item["content_sha256"],
            "terms": terms_for_text(item["content"]),
        })
    return prepared


def apply_document_index_locked(conn, document_id: str, prepared: list[dict]) -> None:
    current = conn.execute(
        "SELECT rowid,id,content_sha256 FROM knowledge_chunks"
        " WHERE document_id=? ORDER BY ordinal", (document_id,),
    ).fetchall()
    signature = [(row["rowid"], row["id"], row["content_sha256"]) for row in current]
    expected = [(row["rowid"], row["id"], row["content_sha256"]) for row in prepared]
    if signature != expected or not prepared:
        raise SearchError("knowledge_chunks_changed", "知识切片在索引前发生变化")
    clear_document_index_locked(conn, document_id)
    for item in prepared:
        conn.execute(
            "INSERT INTO knowledge_chunks_fts(rowid,terms) VALUES(?,?)",
            (item["rowid"], item["terms"]),
        )
    indexed = conn.execute(
        "SELECT COUNT(*) FROM knowledge_chunks_fts f"
        " JOIN knowledge_chunks c ON c.rowid=f.rowid WHERE c.document_id=?",
        (document_id,),
    ).fetchone()[0]
    if indexed != len(prepared):
        raise SearchError("knowledge_index_count_mismatch", "知识索引数量不一致")


def clear_document_index_locked(conn, document_id: str) -> None:
    rowids = conn.execute(
        "SELECT rowid FROM knowledge_chunks WHERE document_id=?", (document_id,)
    ).fetchall()
    for row in rowids:
        conn.execute("DELETE FROM knowledge_chunks_fts WHERE rowid=?", (row["rowid"],))


def search(
    query: str, *, collection_id: str | None = None,
    document_ids: list[str] | None = None, tags: list[str] | None = None, limit: int = 6,
    context_window: int = 0, max_chars: int = 4_000,
) -> dict:
    value = str(query or "").strip()
    if not value or len(value) > MAX_QUERY_CHARS:
        raise SearchError("knowledge_query_invalid", "检索词不能为空且最多 256 字符")
    filters = list(dict.fromkeys(document_ids or []))
    if len(filters) > MAX_DOCUMENT_FILTERS:
        raise SearchError("knowledge_document_filter_too_large", "文档筛选最多 20 项")
    tag_filters = [str(tag).strip() for tag in dict.fromkeys(tags or []) if str(tag).strip()]
    if len(tag_filters) > MAX_TAG_FILTERS or any(len(tag) > 40 for tag in tag_filters):
        raise SearchError("knowledge_tag_filter_invalid", "标签筛选最多 10 项且每项最多 40 字符")
    limit = max(1, min(int(limit), MAX_LIMIT))
    context_window = max(0, min(int(context_window), 1))
    max_chars = max(256, min(int(max_chars), MAX_RESULT_CHARS))
    match_query = _match_query(value)

    where = [
        "d.status='indexed'", "d.indexed_at IS NOT NULL", "d.index_version=?",
        "co.status='active'",
    ]
    params: list[object] = [match_query, INDEX_VERSION]
    if collection_id:
        where.append("d.collection_id=?")
        params.append(collection_id)
    if filters:
        where.append("d.id IN (" + ",".join("?" for _ in filters) + ")")
        params.extend(filters)
    if tag_filters:
        where.append(
            "EXISTS (SELECT 1 FROM json_each(d.tags_json) jt WHERE jt.value IN ("
            + ",".join("?" for _ in tag_filters) + "))"
        )
        params.extend(tag_filters)
    params.append(limit)
    conn = db.connect()
    try:
        primary = conn.execute(
            "SELECT c.*,d.collection_id,d.original_name,d.tags_json,"
            "bm25(knowledge_chunks_fts) AS text_rank"
            " FROM knowledge_chunks_fts"
            " JOIN knowledge_chunks c ON c.rowid=knowledge_chunks_fts.rowid"
            " JOIN knowledge_documents d ON d.id=c.document_id"
            " JOIN knowledge_collections co ON co.id=d.collection_id"
            " WHERE knowledge_chunks_fts MATCH ? AND " + " AND ".join(where) +
            " ORDER BY text_rank,c.document_id,c.ordinal LIMIT ?",
            params,
        ).fetchall()
        primary_candidates: list[tuple[dict, str, str | None, float | None]] = []
        context_candidates: list[tuple[dict, str, str | None, float | None]] = []
        for row in primary:
            item = dict(row)
            primary_candidates.append((item, "primary", None, float(item.pop("text_rank"))))
            if context_window:
                neighbors = conn.execute(
                    "SELECT c.*,d.collection_id,d.original_name,d.tags_json FROM knowledge_chunks c"
                    " JOIN knowledge_documents d ON d.id=c.document_id"
                    " JOIN knowledge_collections co ON co.id=d.collection_id"
                    " WHERE c.document_id=? AND c.ordinal BETWEEN ? AND ?"
                    " AND c.ordinal!=? AND d.status='indexed' AND d.index_version=?"
                    " AND co.status='active'"
                    " ORDER BY c.ordinal",
                    (item["document_id"], item["ordinal"] - 1, item["ordinal"] + 1,
                     item["ordinal"], INDEX_VERSION),
                ).fetchall()
                context_candidates.extend(
                    (dict(neighbor), "context", item["id"], None) for neighbor in neighbors
                )
    finally:
        conn.close()

    results: list[dict] = []
    seen: set[str] = set()
    used_chars = 0
    for item, match_type, context_of, rank in primary_candidates + context_candidates:
        if (
            item["id"] in seen or used_chars + len(item["content"]) > max_chars
            or (match_type == "context" and context_of not in seen)
        ):
            continue
        seen.add(item["id"])
        used_chars += len(item["content"])
        results.append(_public_result(item, match_type, context_of, rank))
    return {
        "query": value, "results": results, "result_count": len(results),
        "used_chars": used_chars, "context_window": context_window,
    }


def hybrid_search(
    query: str, *, collection_id: str | None = None,
    document_ids: list[str] | None = None, tags: list[str] | None = None,
    limit: int = 6, context_window: int = 0, max_chars: int = 4_000,
    mode: str = "auto",
) -> dict:
    """RRF 合并本地 FTS 与 dense；向量不可用/失败时 FTS 仍完整工作。"""
    if mode not in {"auto", "fts", "vector"}:
        raise SearchError("knowledge_search_mode_invalid", "检索模式无效")
    value = str(query or "").strip()
    if not value or len(value) > MAX_QUERY_CHARS:
        raise SearchError("knowledge_query_invalid", "检索词不能为空且最大 256 字符")
    # Vector-only requests still obey the same filter and textual-input contract as FTS.
    filters = list(dict.fromkeys(document_ids or []))
    if len(filters) > MAX_DOCUMENT_FILTERS:
        raise SearchError("knowledge_document_filter_too_large", "文档筛选最大 20 项")
    tag_filters = [str(tag).strip() for tag in dict.fromkeys(tags or []) if str(tag).strip()]
    if len(tag_filters) > MAX_TAG_FILTERS or any(len(tag) > 40 for tag in tag_filters):
        raise SearchError("knowledge_tag_filter_invalid", "标签筛选最大 10 项且每项最大 40 字符")
    lexical = {"results": [], "result_count": 0}
    if mode != "vector":
        lexical = search(
            query, collection_id=collection_id, document_ids=document_ids, tags=tags,
            limit=MAX_LIMIT, context_window=context_window, max_chars=MAX_RESULT_CHARS,
        )
    vector = {"results": [], "available": False, "error_code": None}
    if mode != "fts":
        try:
            from . import knowledge_embeddings
            vector = knowledge_embeddings.search(
                query, collection_id=collection_id, document_ids=document_ids,
                tags=tags, limit=MAX_LIMIT,
            )
        except Exception:  # noqa: BLE001 - 向量旁路不能使 FTS 热路径失败
            vector = {"results": [], "available": False, "error_code": "embedding_search_failed"}
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}
    for results in (lexical["results"], vector["results"]):
        for rank, item in enumerate(results, start=1):
            scores[item["chunk_id"]] = scores.get(item["chunk_id"], 0.0) + 1.0 / (60 + rank)
            if item["chunk_id"] not in items or item.get("match_type") != "vector":
                items[item["chunk_id"]] = dict(item)
    ordered = sorted(items.values(), key=lambda item: (-scores[item["chunk_id"]], item["chunk_id"]))
    selected: list[dict] = []
    used_chars = 0
    for item in ordered:
        if len(selected) >= max(1, min(int(limit), MAX_LIMIT)):
            break
        if used_chars + len(item["content"]) > max(256, min(int(max_chars), MAX_RESULT_CHARS)):
            continue
        item["fusion_score"] = scores[item["chunk_id"]]
        if item["chunk_id"] in {row["chunk_id"] for row in lexical["results"]} and any(
            row["chunk_id"] == item["chunk_id"] for row in vector["results"]
        ):
            item["match_type"] = "hybrid"
        selected.append(item)
        used_chars += len(item["content"])
    retrieval_mode = "fts"
    if mode == "vector":
        retrieval_mode = "vector" if vector["available"] else "fts_unavailable"
    elif vector["available"]:
        retrieval_mode = "hybrid"
    return {
        "query": value, "results": selected, "result_count": len(selected),
        "used_chars": used_chars, "context_window": context_window,
        "retrieval_mode": retrieval_mode, "vector_available": bool(vector["available"]),
        "vector_error_code": vector.get("error_code"),
    }


def _match_query(query: str) -> str:
    terms = terms_for_text(query).split()
    if not terms:
        raise SearchError("knowledge_query_has_no_terms", "检索词不包含可搜索文字")
    terms = list(dict.fromkeys(terms))[:MAX_QUERY_TERMS]
    return " AND ".join(f'"{term}"' for term in terms)


def _public_result(item: dict, match_type: str, context_of: str | None, rank: float | None) -> dict:
    return {
        "chunk_id": item["id"], "document_id": item["document_id"],
        "collection_id": item["collection_id"], "original_name": item["original_name"],
        "ordinal": item["ordinal"], "content": item["content"],
        "content_sha256": item["content_sha256"],
        "tags": json.loads(item["tags_json"]),
        "heading_path": json.loads(item["heading_path_json"]),
        "paragraph_start": item["paragraph_start"], "paragraph_end": item["paragraph_end"],
        "line_start": item["line_start"], "line_end": item["line_end"],
        "char_start": item["char_start"], "char_end": item["char_end"],
        "page_start": item["page_start"], "page_end": item["page_end"],
        "match_type": match_type, "context_of": context_of, "rank": rank,
    }
