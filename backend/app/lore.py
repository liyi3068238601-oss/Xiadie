"""内置角色设定知识库：按关键词召回 Markdown 小节。"""
from __future__ import annotations

from functools import lru_cache
import hashlib
from pathlib import Path
import re

LORE_PATH = Path(__file__).with_name("knowledge") / "xiadie_lore.md"
MAX_SECTIONS = 3
MAX_CHARS = 3600


@lru_cache(maxsize=1)
def _sections() -> list[dict]:
    if not LORE_PATH.exists():
        return []
    text = LORE_PATH.read_text(encoding="utf-8")
    parts = re.split(r"(?m)^##\s+", text)
    result = []
    for part in parts[1:]:
        title, _, body = part.partition("\n")
        keyword_match = re.search(r"(?m)^关键词：(.+)$", body)
        keywords = []
        if keyword_match:
            keywords = [item.strip() for item in re.split(r"[,，、]", keyword_match.group(1))]
            body = body[:keyword_match.start()] + body[keyword_match.end():]
        result.append({"title": title.strip(), "body": body.strip(), "keywords": keywords})
    return result


def retrieve_lore_candidates(
    query: str, max_sections: int = MAX_SECTIONS, max_chars: int = MAX_CHARS,
) -> list[dict]:
    clean_query = re.sub(r"\s+", "", query.casefold())
    if not clean_query:
        return []
    ranked = []
    for index, section in enumerate(_sections()):
        score = 0
        for keyword in section["keywords"]:
            folded = re.sub(r"\s+", "", keyword.casefold())
            if folded and folded in clean_query:
                score += 4 + min(len(folded), 8)
        if section["title"].casefold() in query.casefold():
            score += 8
        if score:
            ranked.append((score, -index, section))
    ranked.sort(reverse=True, key=lambda item: (item[0], item[1]))
    candidates = []
    used = 0
    for legacy_rank, (_, _, section) in enumerate(ranked[:max_sections]):
        content = f"## {section['title']}\n{section['body']}"
        if candidates and used + len(content) > max_chars:
            break
        content = content[:max_chars - used]
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        section_id = hashlib.sha256(section["title"].encode("utf-8")).hexdigest()
        candidates.append({
            "section_id": section_id,
            "revision": content_hash,
            "content_sha256": content_hash,
            "content": content,
            "legacy_rank": legacy_rank,
            "source_available": True,
        })
        used += len(content)
    return candidates


def retrieve_lore(query: str, max_sections: int = MAX_SECTIONS, max_chars: int = MAX_CHARS) -> str:
    """返回与问题相关的设定小节；无明确命中时不注入任何背景。"""
    return "\n\n".join(
        item["content"]
        for item in retrieve_lore_candidates(query, max_sections=max_sections, max_chars=max_chars)
    )
