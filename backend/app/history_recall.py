"""CTX.5 本地跨会话历史回忆。

先选择少量相关会话，再在这些会话内选择匹配消息并扩展为完整 user/assistant 轮次。
所有索引和诊断均在本地；事件只保存 query hash、计数、版本和状态，不保存查询或召回正文。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from . import db

INDEX_VERSION = "conversation-history-index-v1"
SCORE_VERSION = "conversation-history-score-v1-shadow"
DEFAULT_MODE = "explicit_only"
MAX_SESSION_CANDIDATES = 6
MAX_TURNS = 4
MAX_TURNS_PER_SESSION = 2
EXPLICIT_MIN_SCORE = 1.0
AUTOMATIC_MIN_SCORE = 8.0
AUTOMATIC_RECALL_CALIBRATED = False
SCORE_WEIGHTS = {
    "title_match": 3.0,
    "active_summary_match": 1.5,
    "message_match": 1.25,
    "turn_term_match": 2.0,
    "explicit_title_reference": 4.0,
    "explicit_recall_intent": 1.0,
}

_EXPLICIT_RECALL = re.compile(
    r"(?:以前|之前|过去|曾经|上次|当时|那次|还记得|记不记得|"
    r"我们.{0,8}(?:聊过|说过|决定过)|我.{0,8}(?:问过|说过)|原话|哪个会话|哪次)",
    re.IGNORECASE,
)
_RECENT_HINT = re.compile(r"(?:最近|上次|刚才|前几天|昨天|上周|上个月)")
_TERM_RE = re.compile(r"[\u3400-\u9fff]{2,}|[A-Za-z0-9_][A-Za-z0-9_+.#-]{1,}")
_STOP_TERMS = frozenset({
    "以前", "之前", "过去", "曾经", "上次", "当时", "那次", "还记得", "记不记得",
    "我们", "我问过", "我说过", "聊过", "说过", "决定过", "什么", "哪个", "一下",
})
_QUERY_NOISE = (
    "我们", "我的", "我", "你", "讨论的", "聊的", "说的", "问过", "说过", "聊过",
    "是什么", "有什么", "哪一个", "哪个", "什么", "那个", "关于", "请", "一下", "还记得",
)
_FACET_EXPANSIONS = (
    (re.compile(r"算术|数学|计算|乘法|除法"), ("多少", "等于", "计算")),
    (re.compile(r"界面|UI|窗口|布局", re.IGNORECASE), ("界面", "窗口", "布局", "设计")),
    (re.compile(r"代码|编程|报错|bug", re.IGNORECASE), ("代码", "函数", "报错", "修复")),
    (re.compile(r"模型|供应商|provider", re.IGNORECASE), ("模型", "供应商", "provider")),
)


@dataclass(frozen=True)
class SessionCandidate:
    session_id: str
    title: str
    archived: bool
    updated_at: float
    score: float
    signals: tuple[str, ...]


@dataclass(frozen=True)
class RecallTurn:
    source_type: str
    session_id: str
    session_title: str
    session_archived: bool
    user_message_id: str
    assistant_message_id: str
    user_text: str
    assistant_text: str
    user_created_at: float
    assistant_created_at: float
    locator: str
    score: float

    def as_candidate(self) -> dict[str, object]:
        return {
            "source_type": self.source_type,
            "session_id": self.session_id,
            "session_title": self.session_title,
            "session_archived": self.session_archived,
            "user_message_id": self.user_message_id,
            "assistant_message_id": self.assistant_message_id,
            "user_text": self.user_text,
            "assistant_text": self.assistant_text,
            "user_created_at": self.user_created_at,
            "assistant_created_at": self.assistant_created_at,
            "locator": self.locator,
            "score": self.score,
        }


def settings() -> dict[str, str]:
    mode = db.get_setting("conversation_history_recall_mode", DEFAULT_MODE)
    return {"mode": mode if mode in {"off", "explicit_only", "shadow", "on"} else DEFAULT_MODE}


def prepare_locked(conn, query: str, *, current_session_id: str) -> dict[str, object]:
    mode = settings()["mode"]
    explicit = bool(_EXPLICIT_RECALL.search(query or ""))
    intent = "explicit_recall" if explicit else "ordinary"
    if mode == "off":
        event_id = _record_event(
            conn, current_session_id=current_session_id, query=query, intent=intent,
            mode=mode, status="off", session_count=0, turn_count=0,
            diagnostic={
                "reason": "disabled", "explicit": explicit, "term_count": 0,
                "signal_counts": {}, "weights": SCORE_WEIGHTS,
            },
        )
        return {
            "event_id": event_id, "protocol_version": INDEX_VERSION,
            "score_version": SCORE_VERSION, "mode": mode, "intent": intent,
            "status": "off", "candidate_session_count": 0,
            "candidate_turn_count": 0, "turns": [],
        }
    terms = _query_terms(query)
    sessions = _select_sessions(conn, query, terms, current_session_id)
    turns = _select_turns(conn, sessions, terms)
    best_score = turns[0].score if turns else 0.0
    should_inject = bool(
        mode == "on" and (
            explicit and best_score >= EXPLICIT_MIN_SCORE
            or AUTOMATIC_RECALL_CALIBRATED and best_score >= AUTOMATIC_MIN_SCORE
        )
        or mode == "explicit_only" and explicit and best_score >= EXPLICIT_MIN_SCORE
    )
    if should_inject:
        status = "injected"
    elif turns:
        status = "shadow"
    else:
        status = "no_candidates"
    event_id = _record_event(
        conn,
        current_session_id=current_session_id,
        query=query,
        intent=intent,
        mode=mode,
        status=status,
        session_count=len(sessions),
        turn_count=len(turns),
        diagnostic=_diagnostic(
            terms=terms, sessions=sessions, turns=turns,
            explicit=explicit, should_inject=should_inject,
        ),
    )
    return {
        "event_id": event_id,
        "protocol_version": INDEX_VERSION,
        "score_version": SCORE_VERSION,
        "mode": mode,
        "intent": intent,
        "status": status,
        "candidate_session_count": len(sessions),
        "candidate_turn_count": len(turns),
        "turns": [turn.as_candidate() for turn in turns] if should_inject else [],
    }


def record_injected_locked(conn, event_id: str | None, injected_count: int) -> None:
    if not event_id:
        return
    count = max(0, int(injected_count))
    conn.execute(
        "UPDATE conversation_history_recall_events SET injected_turn_count=?,"
        "status=CASE WHEN status IN ('injected','shadow') THEN ? ELSE status END WHERE id=?",
        (count, "injected" if count else "shadow", event_id),
    )


def record_injected(event_id: str | None, injected_count: int) -> None:
    if not event_id:
        return
    conn = db.connect()
    try:
        record_injected_locked(conn, event_id, injected_count)
        conn.commit()
    finally:
        conn.close()


def list_events(*, session_id: str | None = None, limit: int = 50) -> list[dict]:
    conn = db.connect()
    try:
        sql = "SELECT * FROM conversation_history_recall_events"
        params: list[object] = []
        if session_id:
            sql += " WHERE current_session_id=?"
            params.append(session_id)
        sql += " ORDER BY created_at DESC,id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 200)))
        results = []
        for row in conn.execute(sql, params).fetchall():
            item = dict(row)
            item["diagnostic"] = json.loads(item.pop("diagnostic_json") or "{}")
            results.append(item)
        return results
    finally:
        conn.close()


def rebuild_index() -> dict[str, int]:
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM conversation_history_sessions_fts")
        conn.execute("DELETE FROM conversation_history_messages_fts")
        conn.execute(
            "INSERT INTO conversation_history_sessions_fts(session_id,title,summary_text)"
            " SELECT s.id,s.title,COALESCE((SELECT r.summary_text"
            " FROM conversation_summary_revisions r WHERE r.session_id=s.id"
            " AND r.status='active' LIMIT 1),'') FROM sessions s"
        )
        conn.execute(
            "INSERT INTO conversation_history_messages_fts(message_id,session_id,content)"
            " SELECT id,session_id,content FROM messages"
        )
        sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        conn.commit()
        return {"sessions": sessions, "messages": messages}
    finally:
        conn.close()


def _select_sessions(conn, query: str, terms: Sequence[str], current_session_id: str) -> list[SessionCandidate]:
    if not terms:
        return []
    candidate_ids = _candidate_session_ids(conn, terms, current_session_id)
    if not candidate_ids:
        return []
    placeholders = ",".join("?" for _ in candidate_ids)
    rows = conn.execute(
        "SELECT s.id,s.title,s.archived,s.updated_at,"
        " COALESCE((SELECT r.summary_text FROM conversation_summary_revisions r"
        " WHERE r.session_id=s.id AND r.status='active' LIMIT 1),'') summary_text"
        f" FROM sessions s WHERE s.id IN ({placeholders}) AND s.id!=?",
        (*candidate_ids, current_session_id),
    ).fetchall()
    query_folded = (query or "").casefold()
    explicit = bool(_EXPLICIT_RECALL.search(query or ""))
    recent_hint = bool(_RECENT_HINT.search(query or ""))
    candidates: list[SessionCandidate] = []
    for row in rows:
        message_hits = _message_hits(conn, row["id"], terms)
        title = str(row["title"] or "")
        summary = str(row["summary_text"] or "")
        title_hits = _term_hits(title, terms)
        summary_hits = _term_hits(summary, terms)
        if not (message_hits or title_hits or summary_hits):
            continue
        signals: list[str] = []
        score = (
            title_hits * SCORE_WEIGHTS["title_match"]
            + summary_hits * SCORE_WEIGHTS["active_summary_match"]
            + min(message_hits, 4) * SCORE_WEIGHTS["message_match"]
        )
        if title_hits:
            signals.append("title_match")
        if summary_hits:
            signals.append("active_summary_match")
        if message_hits:
            signals.append("message_match")
        if title.casefold() in query_folded and title not in {"", "新对话"}:
            score += SCORE_WEIGHTS["explicit_title_reference"]
            signals.append("explicit_title_reference")
        if explicit:
            score += SCORE_WEIGHTS["explicit_recall_intent"]
            signals.append("explicit_recall_intent")
        if recent_hint:
            score += min(0.5, max(0.0, float(row["updated_at"])) / 10**11)
            signals.append("recent_hint")
        candidates.append(SessionCandidate(
            session_id=row["id"], title=title, archived=bool(row["archived"]),
            updated_at=float(row["updated_at"]), score=round(score, 4),
            signals=tuple(signals),
        ))
    candidates.sort(key=lambda item: (item.score, item.updated_at), reverse=True)
    return candidates[:MAX_SESSION_CANDIDATES]


def _select_turns(conn, sessions: Sequence[SessionCandidate], terms: Sequence[str]) -> list[RecallTurn]:
    ranked: list[RecallTurn] = []
    for session in sessions:
        rows = [dict(row) for row in conn.execute(
            "SELECT id,role,content,created_at FROM messages WHERE session_id=?"
            " ORDER BY created_at,id", (session.session_id,),
        ).fetchall()]
        for index in range(0, len(rows) - 1):
            user, assistant = rows[index], rows[index + 1]
            if user["role"] != "user" or assistant["role"] != "assistant":
                continue
            hits = _term_hits(user["content"], terms) + _term_hits(assistant["content"], terms)
            if not hits:
                continue
            score = session.score + hits * SCORE_WEIGHTS["turn_term_match"]
            ranked.append(RecallTurn(
                source_type="cross_session_history",
                session_id=session.session_id,
                session_title=session.title,
                session_archived=session.archived,
                user_message_id=user["id"], assistant_message_id=assistant["id"],
                user_text=user["content"], assistant_text=assistant["content"],
                user_created_at=float(user["created_at"]),
                assistant_created_at=float(assistant["created_at"]),
                locator=(f"session:{session.session_id}/messages:"
                         f"{user['id']}:{assistant['id']}"),
                score=round(score, 4),
            ))
    ranked.sort(key=lambda item: (item.score, item.assistant_created_at), reverse=True)
    selected: list[RecallTurn] = []
    per_session: dict[str, int] = {}
    for item in ranked:
        if per_session.get(item.session_id, 0) >= MAX_TURNS_PER_SESSION:
            continue
        selected.append(item)
        per_session[item.session_id] = per_session.get(item.session_id, 0) + 1
        if len(selected) >= MAX_TURNS:
            break
    return selected


def _message_hits(conn, session_id: str, terms: Sequence[str]) -> int:
    match_query = _fts_match(terms)
    fts_count = 0
    if match_query:
        fts_count = int(conn.execute(
            "SELECT COUNT(*) FROM conversation_history_messages_fts"
            " WHERE conversation_history_messages_fts MATCH ? AND session_id=?",
            (match_query, session_id),
        ).fetchone()[0])
    clauses = " OR ".join("content LIKE ?" for _ in terms)
    params = [f"%{term}%" for term in terms]
    like_count = int(conn.execute(
        f"SELECT COUNT(*) FROM conversation_history_messages_fts"
        f" WHERE session_id=? AND ({clauses})",
        (session_id, *params),
    ).fetchone()[0])
    return max(fts_count, like_count)


def _candidate_session_ids(conn, terms: Sequence[str], current_session_id: str) -> tuple[str, ...]:
    ids: list[str] = []
    match_query = _fts_match(terms)
    if match_query:
        ids.extend(row[0] for row in conn.execute(
            "SELECT DISTINCT session_id FROM conversation_history_sessions_fts"
            " WHERE conversation_history_sessions_fts MATCH ? AND session_id!=? LIMIT 30",
            (match_query, current_session_id),
        ).fetchall())
        ids.extend(row[0] for row in conn.execute(
            "SELECT DISTINCT session_id FROM conversation_history_messages_fts"
            " WHERE conversation_history_messages_fts MATCH ? AND session_id!=? LIMIT 30",
            (match_query, current_session_id),
        ).fetchall())
    clauses = " OR ".join("title LIKE ? OR summary_text LIKE ?" for _ in terms)
    params = [value for term in terms for value in (f"%{term}%", f"%{term}%")]
    ids.extend(row[0] for row in conn.execute(
        f"SELECT DISTINCT session_id FROM conversation_history_sessions_fts"
        f" WHERE session_id!=? AND ({clauses}) LIMIT 30",
        (current_session_id, *params),
    ).fetchall())
    message_clauses = " OR ".join("content LIKE ?" for _ in terms)
    ids.extend(row[0] for row in conn.execute(
        f"SELECT DISTINCT session_id FROM conversation_history_messages_fts"
        f" WHERE session_id!=? AND ({message_clauses}) LIMIT 30",
        (current_session_id, *(f"%{term}%" for term in terms)),
    ).fetchall())
    return tuple(dict.fromkeys(str(value) for value in ids if value))


def _fts_match(terms: Sequence[str]) -> str:
    values = [term.replace('"', '""') for term in terms if len(term) >= 3]
    return " OR ".join(f'"{value}"' for value in values[:8])


def _query_terms(query: str) -> tuple[str, ...]:
    values: list[str] = []
    cleaned = _EXPLICIT_RECALL.sub(" ", query or "")
    for noise in _QUERY_NOISE:
        cleaned = cleaned.replace(noise, " ")
    # Chinese recall questions are commonly one continuous regex match.  Split
    # grammatical particles before extracting terms so a query such as
    # “之前决定的项目名字吗” can match the archived phrase “项目名字确定为…”.
    for chunk in re.split(r"[\s,，。！？!?：:；;的了吗呢啊呀吧]+", cleaned):
        for term in _TERM_RE.findall(chunk):
            value = term.strip().casefold()
            if value not in _STOP_TERMS and len(value) >= 2:
                values.append(value)
                if re.fullmatch(r"[\u3400-\u9fff]+", value) and len(value) >= 4:
                    values.extend(value[index:index + 2] for index in range(len(value) - 1))
    for pattern, expansions in _FACET_EXPANSIONS:
        if pattern.search(query or ""):
            values.extend(expansions)
    return tuple(dict.fromkeys(values))[:12]


def _term_hits(text: str, terms: Sequence[str]) -> int:
    folded = (text or "").casefold()
    return sum(1 for term in terms if term.casefold() in folded)


def _diagnostic(*, terms: Sequence[str], sessions: Sequence[SessionCandidate],
                turns: Sequence[RecallTurn], explicit: bool,
                should_inject: bool) -> dict[str, object]:
    signals: dict[str, int] = {}
    for session in sessions:
        for signal in session.signals:
            signals[signal] = signals.get(signal, 0) + 1
    if not terms:
        reason = "no_search_terms"
    elif not sessions:
        reason = "no_session_match"
    elif not turns:
        reason = "no_complete_turn_match"
    elif should_inject:
        reason = "explicit_or_calibrated_injection"
    elif explicit:
        reason = "below_explicit_threshold"
    else:
        reason = "ordinary_query_shadow_only"
    return {
        "reason": reason,
        "explicit": explicit,
        "term_count": len(terms),
        "signal_counts": signals,
        "weights": SCORE_WEIGHTS,
    }


def _record_event(conn, *, current_session_id: str, query: str, intent: str,
                  mode: str, status: str, session_count: int, turn_count: int,
                  diagnostic: Mapping[str, object]) -> str:
    event_id = db.new_id()
    digest = hashlib.sha256((query or "").encode("utf-8")).hexdigest()
    conn.execute(
        "INSERT INTO conversation_history_recall_events("
        "id,current_session_id,query_sha256,intent,mode,status,score_version,"
        "candidate_session_count,candidate_turn_count,injected_turn_count,diagnostic_json,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,0,?,?)",
        (event_id, current_session_id, digest, intent, mode, status, SCORE_VERSION,
         session_count, turn_count, json.dumps(diagnostic, ensure_ascii=False), db.now()),
    )
    return event_id
