"""F.4 稳定知识切片协议：结构边界优先、无重叠、来源定位可复现。"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable

CHUNKER_VERSION = "knowledge-structure-chunker-v2"
TARGET_CHARS = 800
MAX_CHARS = 1200
MAX_STRUCTURED_CHARS = 4000
_SENTENCE_END = frozenset("。！？!?；;.!?")


class ChunkingCancelled(RuntimeError):
    pass


def chunk_artifact(payload: dict, *, should_cancel: Callable[[], bool] | None = None) -> list[dict]:
    text, headings, page_spans = _validate_payload(payload)
    paragraphs = _paragraphs(text, headings)
    units: list[dict] = []
    for paragraph in paragraphs:
        _checkpoint(should_cancel)
        units.extend(_split_paragraph(text, paragraph))

    chunks: list[dict] = []
    pending: list[dict] = []
    for unit in units:
        _checkpoint(should_cancel)
        if pending:
            combined_length = unit["end"] - pending[0]["start"]
            if (unit["heading_path"] != pending[0]["heading_path"]
                    or unit["chunk_kind"] != pending[0]["chunk_kind"]
                    or combined_length > TARGET_CHARS):
                chunks.append(_materialize(text, pending, len(chunks), page_spans))
                pending = []
        pending.append(unit)
    if pending:
        chunks.append(_materialize(text, pending, len(chunks), page_spans))
    for index, chunk in enumerate(chunks):
        chunk["previous_ordinal"] = index - 1 if index > 0 else None
        chunk["next_ordinal"] = index + 1 if index + 1 < len(chunks) else None
    return chunks


def chunk_id(document_id: str, chunk: dict) -> str:
    stable = (
        f"{document_id}:{CHUNKER_VERSION}:{chunk['ordinal']}:"
        f"{chunk['char_start']}:{chunk['char_end']}:{chunk['content_sha256']}"
    )
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def _validate_payload(payload: dict) -> tuple[str, list[dict], list[dict]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("normalized_text"), str):
        raise ValueError("parse_artifact_invalid")
    text = payload["normalized_text"]
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if payload.get("normalized_sha256") != digest or payload.get("char_count") != len(text):
        raise ValueError("parse_artifact_invalid")
    headings = payload.get("headings")
    if not isinstance(headings, list):
        raise ValueError("parse_artifact_invalid")
    line_count = 0 if not text else text.count("\n") + 1
    for item in headings:
        if (
            not isinstance(item, dict) or not isinstance(item.get("title"), str)
            or not isinstance(item.get("level"), int) or not 1 <= item["level"] <= 6
            or not isinstance(item.get("line"), int) or not 1 <= item["line"] <= line_count
        ):
            raise ValueError("parse_artifact_invalid")
    page_spans = payload.get("page_spans", [])
    if not isinstance(page_spans, list):
        raise ValueError("parse_artifact_invalid")
    previous_end = 0
    for expected, item in enumerate(page_spans, start=1):
        if (
            not isinstance(item, dict) or item.get("page") != expected
            or not isinstance(item.get("start"), int) or not isinstance(item.get("end"), int)
            or item["start"] < previous_end or item["end"] < item["start"] or item["end"] > len(text)
        ):
            raise ValueError("parse_artifact_invalid")
        previous_end = item["end"]
    if payload.get("page_count", 0) != len(page_spans):
        raise ValueError("parse_artifact_invalid")
    return text, headings, page_spans


def _paragraphs(text: str, headings: list[dict]) -> list[dict]:
    heading_by_line = {item["line"]: item for item in headings}
    line_offsets = [0]
    line_offsets.extend(index + 1 for index, char in enumerate(text) if char == "\n")
    path: list[str] = []
    result: list[dict] = []
    segments: list[tuple[int, int, str]] = []
    for start, end, kind in _structural_spans(text):
        cuts = [start]
        cuts.extend(
            line_offsets[line - 1] for line in sorted(heading_by_line)
            if start < line_offsets[line - 1] < end
        )
        cuts.append(end)
        segments.extend((cuts[i], cuts[i + 1], kind) for i in range(len(cuts) - 1))
    for index, (start, end, kind) in enumerate(segments, start=1):
        while end > start and text[end - 1] == "\n":
            end -= 1
        if end <= start:
            continue
        line_start = text.count("\n", 0, start) + 1
        line_end = text.count("\n", 0, end - 1) + 1
        heading = heading_by_line.get(line_start)
        if heading:
            path = path[: heading["level"] - 1]
            path.append(heading["title"])
        result.append({
            "start": start, "end": end, "paragraph": index,
            "line_start": line_start, "line_end": line_end, "heading_path": list(path),
            "chunk_kind": kind,
        })
    return result


def _structural_spans(text: str) -> list[tuple[int, int, str]]:
    """Split Markdown structures before size splitting; returned spans are exact substrings."""
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)
    result: list[tuple[int, int, str]] = []
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        start_index = index
        stripped = lines[index].lstrip()
        fence = re.match(r"(`{3,}|~{3,})", stripped)
        if fence:
            marker = fence.group(1)[0]
            index += 1
            while index < len(lines):
                if re.match(rf"^[ \t]{{0,3}}{re.escape(marker)}{{3,}}", lines[index]):
                    index += 1
                    break
                index += 1
            kind = "code"
        elif _table_line(lines[index]) and index + 1 < len(lines) and _table_line(lines[index + 1]):
            index += 2
            while index < len(lines) and lines[index].strip() and _table_line(lines[index]):
                index += 1
            kind = "table"
        elif _list_line(lines[index]):
            index += 1
            while index < len(lines) and lines[index].strip() and (
                _list_line(lines[index]) or lines[index].startswith((" ", "\t"))
            ):
                index += 1
            kind = "list"
        else:
            index += 1
            while index < len(lines) and lines[index].strip():
                if re.match(r"^[ \t]{0,3}(`{3,}|~{3,})", lines[index]):
                    break
                if _list_line(lines[index]):
                    break
                if (_table_line(lines[index]) and index + 1 < len(lines)
                        and _table_line(lines[index + 1])):
                    break
                index += 1
            kind = "heading" if re.match(r"^#{1,6}[ \t]+", stripped) else "prose"
        start = offsets[start_index]
        end = offsets[index] if index < len(offsets) else len(text)
        while end > start and text[end - 1] == "\n":
            end -= 1
        if end > start:
            result.append((start, end, kind))
    return result


def _table_line(line: str) -> bool:
    stripped = line.strip()
    return bool("\t" in stripped or (stripped.count("|") >= 2 and len(stripped) >= 3))


def _list_line(line: str) -> bool:
    return bool(re.match(r"^[ \t]*(?:[-*+] |\d+[.)] )", line))


def _base_paragraph_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start: int | None = None
    end = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.strip(" \t\n"):
            if start is None:
                start = offset
            end = offset + len(line) - (1 if line.endswith("\n") else 0)
        elif start is not None:
            spans.append((start, end))
            start = None
        offset += len(line)
    if start is not None:
        spans.append((start, end))
    return spans


def _split_paragraph(text: str, paragraph: dict) -> list[dict]:
    start, end = paragraph["start"], paragraph["end"]
    pieces: list[dict] = []
    cursor = start
    hard_limit = MAX_STRUCTURED_CHARS if paragraph["chunk_kind"] in {"code", "table"} else MAX_CHARS
    while end - cursor > hard_limit:
        limit = min(cursor + hard_limit, end)
        preferred = min(cursor + TARGET_CHARS, limit)
        split = (
            _line_boundary(text, cursor, preferred, limit)
            if paragraph["chunk_kind"] in {"code", "table", "list"}
            else _sentence_boundary(text, cursor, preferred, limit)
        )
        if split <= cursor:
            split = limit
        pieces.append(_unit(paragraph, cursor, split, text))
        cursor = split
        while cursor < end and text[cursor].isspace() and text[cursor] != "\n":
            cursor += 1
    if cursor < end:
        pieces.append(_unit(paragraph, cursor, end, text))
    return pieces


def _sentence_boundary(text: str, start: int, preferred: int, limit: int) -> int:
    for position in range(preferred, limit):
        if text[position] in _SENTENCE_END:
            return position + 1
    for position in range(preferred - 1, start, -1):
        if text[position] in _SENTENCE_END:
            return position + 1
    return limit


def _line_boundary(text: str, start: int, preferred: int, limit: int) -> int:
    after = text.find("\n", preferred, limit)
    if after >= 0:
        return after + 1
    before = text.rfind("\n", start, preferred)
    return before + 1 if before >= start else limit


def _unit(paragraph: dict, start: int, end: int, text: str) -> dict:
    return {
        "start": start, "end": end, "paragraph": paragraph["paragraph"],
        "line_start": text.count("\n", 0, start) + 1,
        "line_end": text.count("\n", 0, end - 1) + 1,
        "heading_path": paragraph["heading_path"],
        "chunk_kind": paragraph["chunk_kind"],
    }


def _materialize(text: str, units: list[dict], ordinal: int, page_spans: list[dict]) -> dict:
    start, end = units[0]["start"], units[-1]["end"]
    content = text[start:end]
    pages = [item["page"] for item in page_spans if item["end"] > start and item["start"] < end]
    return {
        "ordinal": ordinal,
        "content": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "heading_path_json": json.dumps(
            units[0]["heading_path"], ensure_ascii=False, separators=(",", ":")
        ),
        "paragraph_start": units[0]["paragraph"],
        "paragraph_end": units[-1]["paragraph"],
        "line_start": units[0]["line_start"],
        "line_end": units[-1]["line_end"],
        "char_start": start,
        "char_end": end,
        "page_start": min(pages) if pages else None,
        "page_end": max(pages) if pages else None,
        "chunker_version": CHUNKER_VERSION,
        "chunk_kind": units[0]["chunk_kind"],
    }


def _checkpoint(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel and should_cancel():
        raise ChunkingCancelled()
