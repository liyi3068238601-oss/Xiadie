"""F.3 确定性 TXT/Markdown 解析；不切片、不索引、不执行文档中的指令。"""
from __future__ import annotations

import hashlib
import json
import re

PARSER_VERSION = "knowledge-text-parser-v1"
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)\s*$")
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


def parse(data: bytes, *, extension: str) -> dict:
    text = data.decode("utf-8-sig", errors="strict")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    headings = _markdown_headings(normalized) if extension == ".md" else []
    return {
        "parser_version": PARSER_VERSION,
        "normalized_text": normalized,
        "normalized_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "char_count": len(normalized),
        "line_count": 0 if not normalized else normalized.count("\n") + 1,
        "headings": headings,
        "heading_count": len(headings),
    }


def artifact_bytes(result: dict) -> bytes:
    payload = {
        "parser_version": result["parser_version"],
        "normalized_text": result["normalized_text"],
        "normalized_sha256": result["normalized_sha256"],
        "char_count": result["char_count"],
        "line_count": result["line_count"],
        "headings": result["headings"],
    }
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _markdown_headings(text: str) -> list[dict]:
    headings: list[dict] = []
    fence: str | None = None
    for line_number, line in enumerate(text.splitlines(), start=1):
        fence_match = _FENCE.match(line)
        if fence_match:
            marker = fence_match.group(1)[0]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is not None:
            continue
        match = _HEADING.match(line)
        if not match:
            continue
        title = re.sub(r"[ \t]+#+[ \t]*$", "", match.group(2)).strip()
        if title:
            headings.append({"level": len(match.group(1)), "title": title, "line": line_number})
    return headings
