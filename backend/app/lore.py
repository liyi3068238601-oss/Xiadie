"""内置角色设定知识库：按关键词召回 Markdown 小节。"""
from __future__ import annotations

from functools import lru_cache
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


def retrieve_lore(query: str, max_sections: int = MAX_SECTIONS, max_chars: int = MAX_CHARS) -> str:
    """返回与问题相关的设定小节；无明确命中时不注入任何背景。"""
    clean_query = re.sub(r"\s+", "", query.casefold())
    if not clean_query:
        return ""
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
    blocks = []
    used = 0
    for _, _, section in ranked[:max_sections]:
        block = f"## {section['title']}\n{section['body']}"
        if blocks and used + len(block) > max_chars:
            break
        blocks.append(block[:max_chars - used])
        used += len(block)
    return "\n\n".join(blocks)
