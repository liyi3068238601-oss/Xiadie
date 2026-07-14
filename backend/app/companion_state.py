"""遐蝶的连续伴侣状态。

设计受 jiwen 启发，但采用适合当前 Python/SQLite 架构的独立实现。
状态只影响表达方式，不改变事实、权限或安全边界。
"""
from __future__ import annotations

from . import db

DEFAULT_STATE = {
    "connection": 0.35,
    "pride": 0.0,
    "valence": 0.1,
    "arousal": -0.1,
    "immersion": 0.25,
}

POSITIVE_HINTS = ("谢谢", "感谢", "喜欢", "开心", "很好", "不错", "成功", "太棒")
NEGATIVE_HINTS = ("报错", "失败", "难受", "生气", "烦", "糟糕", "不行", "崩溃")
APPRECIATION_HINTS = ("谢谢你", "辛苦", "做得好", "帮大忙", "厉害")


def get_state() -> dict:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM companion_state WHERE id = 1").fetchone()
        if not row:
            _insert(conn, DEFAULT_STATE)
            conn.commit()
            row = conn.execute("SELECT * FROM companion_state WHERE id = 1").fetchone()
        return _row(row)
    finally:
        conn.close()


def preview_interaction(user_text: str, current: dict | None = None) -> dict:
    """计算本轮成功完成后应达到的状态，不立即写入数据库。"""
    state = dict(current or get_state())
    text = user_text.strip()
    length_signal = min(len(text) / 400, 0.18)
    positive = any(hint in text for hint in POSITIVE_HINTS)
    negative = any(hint in text for hint in NEGATIVE_HINTS)
    appreciated = any(hint in text for hint in APPRECIATION_HINTS)

    state["connection"] = _clamp(state["connection"] + 0.008 + (0.012 if appreciated else 0), 0, 1)
    state["pride"] = _clamp(state["pride"] * 0.9 + (0.07 if appreciated else 0), -1, 1)
    state["valence"] = _clamp(
        state["valence"] * 0.88 + (0.08 if positive else 0) - (0.08 if negative else 0),
        -1,
        1,
    )
    state["arousal"] = _clamp(state["arousal"] * 0.78 - 0.01 + length_signal, -1, 1)
    state["immersion"] = _clamp(state["immersion"] * 0.82 + 0.04 + length_signal, 0, 1)
    state.pop("updated_at", None)
    return state


def save_state(state: dict) -> dict:
    clean = {
        "connection": _clamp(float(state["connection"]), 0, 1),
        "pride": _clamp(float(state["pride"]), -1, 1),
        "valence": _clamp(float(state["valence"]), -1, 1),
        "arousal": _clamp(float(state["arousal"]), -1, 1),
        "immersion": _clamp(float(state["immersion"]), 0, 1),
    }
    conn = db.connect()
    try:
        _insert(conn, clean)
        conn.commit()
        return _row(conn.execute("SELECT * FROM companion_state WHERE id = 1").fetchone())
    finally:
        conn.close()


def reset_state() -> dict:
    return save_state(DEFAULT_STATE)


def get_style_guidance(state: dict) -> str:
    """把数值状态转换为短小、可审查的模型语气指导。"""
    guidance: list[str] = []
    if state["connection"] < 0.45:
        guidance.append("保持友好但不过分熟络，尊重用户的表达空间")
    elif state["connection"] > 0.75:
        guidance.append("可以自然地延续共同语境，但不要刻意强调亲密度")
    if state["valence"] < -0.15:
        guidance.append("语气稍显沉静，仍要清楚、可靠，不向用户索取安慰")
    elif state["valence"] > 0.35:
        guidance.append("语气可以更轻快一些，但不要喧闹或过度兴奋")
    if state["arousal"] < -0.25:
        guidance.append("使用舒缓、简洁的节奏")
    elif state["arousal"] > 0.35:
        guidance.append("回应可以更有行动感，优先给出明确下一步")
    if state["immersion"] > 0.55:
        guidance.append("对当前话题保持专注，主动衔接刚才的细节")
    if state["pride"] > 0.3:
        guidance.append("表现出安静的自信，不自夸")
    return "；".join(guidance) or "保持温和、清楚、克制的默认语气"


def _insert(conn, state: dict) -> None:
    conn.execute(
        "INSERT INTO companion_state(id, connection, pride, valence, arousal, immersion, updated_at)"
        " VALUES(1,?,?,?,?,?,?)"
        " ON CONFLICT(id) DO UPDATE SET connection=excluded.connection, pride=excluded.pride,"
        " valence=excluded.valence, arousal=excluded.arousal, immersion=excluded.immersion,"
        " updated_at=excluded.updated_at",
        (
            state["connection"],
            state["pride"],
            state["valence"],
            state["arousal"],
            state["immersion"],
            db.now(),
        ),
    )


def _row(row) -> dict:
    return {key: row[key] for key in (*DEFAULT_STATE.keys(), "updated_at")}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
