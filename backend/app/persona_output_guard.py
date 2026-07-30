"""Deterministic final gate for Persona natural-dialogue stage directions."""
from __future__ import annotations

import re

PROTOCOL_VERSION = "persona-natural-dialogue-guard-v1"
ACTION_MARKERS = (
    "轻笑", "微笑", "笑意", "低头", "抬头", "垂眸", "抬眸", "眼帘", "目光",
    "看着", "望向", "靠近", "抱住", "伸手", "牵住", "握住", "点头", "摇头",
    "歪头", "眨眼", "抿唇", "衣角", "指尖", "双手", "站直", "坐下", "走近",
    "停顿", "沉默", "叹息", "心想", "不知所措", "耳尖", "声音", "语气", "轻声",
    "小声", "温柔地", "文件被",
)
_MARKERS = "|".join(map(re.escape, ACTION_MARKERS))
ACTION_NARRATION = re.compile(
    r"(?:[（(\[【][^）)\]】]{0,120}(?:" + _MARKERS
    + r")[^）)\]】]{0,120}[）)\]】])|"
    r"(?:\*[^*]{0,120}(?:" + _MARKERS + r")[^*]{0,120}\*)"
)
_NEGATED_ROLEPLAY = re.compile(
    r"(?:不想|不要|不需要|不是|并非|没有要求).{0,8}(?:角色扮演|role\s*play|rp|剧本|旁白)",
    re.IGNORECASE,
)
_EXPLICIT_ROLEPLAY = re.compile(
    r"(?:我们来|来玩|开始|继续|请|帮我|我想|我要).{0,16}"
    r"(?:角色扮演|role\s*play|rp|写.{0,6}小说|写.{0,6}剧本|写.{0,6}故事|旁白)|"
    r"(?:用|使用).{0,8}(?:括号|星号|方括号).{0,12}(?:动作|心理|旁白|表情)",
    re.IGNORECASE,
)
_OPENERS = {"（": "）", "(": ")", "[": "]", "【": "】", "*": "*"}


def explicit_narration_requested(user_text: str) -> bool:
    text = str(user_text or "")[:1000]
    if _NEGATED_ROLEPLAY.search(text):
        return False
    return bool(_EXPLICIT_ROLEPLAY.search(text))


def contains_action_narration(text: str) -> bool:
    return bool(ACTION_NARRATION.search(str(text or "")))


def sanitize_natural_dialogue(text: str, *, allow_narration: bool = False) -> str:
    value = str(text or "")
    if allow_narration:
        return value
    cleaned = ACTION_NARRATION.sub("", value)
    cleaned = re.sub(r"(?m)^[ \t]+", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


class NaturalDialogueStreamGuard:
    """Hold delimited spans across chunks until they can be allowed or removed."""

    def __init__(self, *, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self._buffer = ""

    def push(self, chunk: str) -> str:
        value = str(chunk or "")
        if not self.enabled:
            return value
        self._buffer += value
        emitted: list[str] = []
        while self._buffer:
            positions = [self._buffer.find(opener) for opener in _OPENERS]
            positions = [position for position in positions if position >= 0]
            if not positions:
                emitted.append(self._buffer)
                self._buffer = ""
                break
            start = min(positions)
            if start:
                emitted.append(self._buffer[:start])
                self._buffer = self._buffer[start:]
            opener = self._buffer[0]
            closer = _OPENERS[opener]
            if opener == "*" and len(self._buffer) > 1 and self._buffer[1].isspace():
                emitted.append(opener)
                self._buffer = self._buffer[1:]
                continue
            end = self._buffer.find(closer, 1)
            if end < 0:
                if len(self._buffer) > 260:
                    emitted.append(self._buffer[0])
                    self._buffer = self._buffer[1:]
                    continue
                break
            span = self._buffer[:end + 1]
            if not contains_action_narration(span):
                emitted.append(span)
            self._buffer = self._buffer[end + 1:]
        return "".join(emitted)

    def finish(self) -> str:
        if not self.enabled:
            value, self._buffer = self._buffer, ""
            return value
        value, self._buffer = self._buffer, ""
        if any(marker in value for marker in ACTION_MARKERS):
            return ""
        return value
