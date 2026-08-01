"""LIFE2 ShortMemo: bounded, silent, source-backed near-term continuity."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from threading import RLock
import time
from typing import Mapping, Sequence

from . import db, llm

PROTOCOL_VERSION = "short-memo-v1"
ROLLOUT_MODES = ("off", "shadow", "active")
DEFAULT_ROLLOUT_MODE = "active"
MIN_TTL = 3_600
MAX_TTL = 1_209_600
DEFAULT_TTL = 259_200
HARD_MAX_ACTIVE = 10
HARD_MAX_RECALL = 3
MAX_CONTENT_CHARS = 240
METADATA_KEYS = frozenset({"protocol_version", "revision", "ttl_seconds", "rollout_epoch"})

_TIME_MARKER = re.compile(
    r"今天|今晚|明天|明早|明晚|后天|这周|本周|下周|周[一二三四五六日天]|"
    r"星期[一二三四五六日天]|晚点|稍后|过一会|之后|回头|\d{1,2}[点号日]|\d{1,2}天后"
)
_INTENT_MARKER = re.compile(r"我要|我会|我准备|我打算|我计划|我得|需要|记得|提醒我|别忘了|要去|预约")
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:sk|api|token|bearer)[-_=: ]+[a-z0-9_-]{8,}"),
    re.compile(r"(?i)(?:密码|口令|验证码|密钥|secret|password|passwd)\s*[:：=]?\s*\S{4,}"),
    re.compile(r"(?<!\d)\d{16,19}(?!\d)"),
    re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)"),
    re.compile(r"(?<!\d)\d{6}(?!\d).{0,8}(?:验证码|校验码)"),
    re.compile(r"(?:验证码|校验码).{0,8}(?<!\d)\d{6}(?!\d)"),
)
_SENSITIVE_MARKERS = (
    "医院", "复查", "诊断", "病", "药", "治疗", "心理咨询", "律师", "诉讼", "银行卡", "欠款",
)
_STOP_TOPICS = frozenset({
    "用户", "今天", "今晚", "明天", "明早", "明晚", "后天", "这周", "本周", "下周",
    "稍后", "之后", "回头", "准备", "打算", "计划", "记得", "提醒", "需要", "一个", "事情",
})


class ShortMemoError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RolloutSnapshot:
    enabled: bool
    rollout_mode: str
    rollout_epoch: int
    remote_extraction_enabled: bool
    default_ttl_seconds: int
    max_active: int
    max_recall: int

    def public(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "rollout_mode": self.rollout_mode,
            "rollout_epoch": self.rollout_epoch,
            "remote_extraction_enabled": self.remote_extraction_enabled,
            "default_ttl_seconds": self.default_ttl_seconds,
            "max_active": self.max_active,
            "max_recall": self.max_recall,
        }


@dataclass(frozen=True)
class Candidate:
    content: str
    topic_keys: tuple[str, ...]
    sensitivity: str
    ttl_seconds: int


_DIAGNOSTIC_LOCK = RLock()
_DIAGNOSTICS: dict[int, dict[str, int]] = {}


def rollout_snapshot(conn=None) -> RolloutSnapshot:
    owned = conn is None
    connection = conn or db.connect()
    try:
        rows = {
            row["key"]: row["value"] for row in connection.execute(
                "SELECT key,value FROM settings WHERE key LIKE 'life.short_memo.%'"
            )
        }
        rollout = rows.get("life.short_memo.rollout_mode", DEFAULT_ROLLOUT_MODE)
        if rollout not in ROLLOUT_MODES:
            rollout = "off"
        return RolloutSnapshot(
            enabled=rows.get("life.short_memo.enabled", "1") == "1",
            rollout_mode=rollout,
            rollout_epoch=max(0, _int(rows.get("life.short_memo.rollout_epoch"), 0)),
            remote_extraction_enabled=(
                rows.get("life.short_memo.remote_extraction_enabled", "0") == "1"
            ),
            default_ttl_seconds=_bounded(
                _int(rows.get("life.short_memo.default_ttl_seconds"), DEFAULT_TTL),
                MIN_TTL, MAX_TTL,
            ),
            max_active=_bounded(
                _int(rows.get("life.short_memo.max_active"), HARD_MAX_ACTIVE),
                1, HARD_MAX_ACTIVE,
            ),
            max_recall=_bounded(
                _int(rows.get("life.short_memo.max_recall"), HARD_MAX_RECALL),
                1, HARD_MAX_RECALL,
            ),
        )
    finally:
        if owned:
            connection.close()


def update_product_settings(
    *, enabled: bool | None = None, remote_extraction_enabled: bool | None = None,
    default_ttl_seconds: int | None = None,
) -> RolloutSnapshot:
    if default_ttl_seconds is not None and not MIN_TTL <= default_ttl_seconds <= MAX_TTL:
        raise ShortMemoError("short_memo_ttl_invalid")
    conn = db.connect()
    try:
        if enabled is not None:
            _set_locked(conn, "life.short_memo.enabled", "1" if enabled else "0")
        if remote_extraction_enabled is not None:
            _set_locked(
                conn, "life.short_memo.remote_extraction_enabled",
                "1" if remote_extraction_enabled else "0",
            )
        if default_ttl_seconds is not None:
            _set_locked(conn, "life.short_memo.default_ttl_seconds", str(default_ttl_seconds))
        conn.commit()
        return rollout_snapshot(conn)
    finally:
        conn.close()


def set_rollout_mode(mode: str) -> RolloutSnapshot:
    """Internal release operation; ordinary API/UI must never call this function."""
    if mode not in ROLLOUT_MODES:
        raise ShortMemoError("short_memo_rollout_invalid")
    conn = db.connect()
    try:
        current = rollout_snapshot(conn)
        if current.rollout_mode != mode:
            _set_locked(conn, "life.short_memo.rollout_mode", mode)
            _set_locked(conn, "life.short_memo.rollout_epoch", str(current.rollout_epoch + 1))
        conn.commit()
        return rollout_snapshot(conn)
    finally:
        conn.close()


def analyze_user_text(text: str, *, ttl_seconds: int = DEFAULT_TTL) -> tuple[Candidate | None, str]:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if not value:
        return None, "empty"
    if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
        return None, "secret_rejected"
    if not _TIME_MARKER.search(value) or not _INTENT_MARKER.search(value):
        return None, "not_near_term"
    if len(value) > 2_000:
        return None, "source_too_long"
    ttl = _ttl_from_text(value, ttl_seconds)
    sensitivity = "sensitive_minimized" if any(marker in value for marker in _SENSITIVE_MARKERS) else "normal"
    content = value[:MAX_CONTENT_CHARS]
    if sensitivity == "sensitive_minimized":
        time_hint = (_TIME_MARKER.search(value).group(0) if _TIME_MARKER.search(value) else "近期")
        domain = "健康" if any(marker in value for marker in ("医院", "复查", "诊断", "病", "药", "治疗", "心理咨询")) else "敏感"
        content = f"用户{time_hint}有一项{domain}相关安排。"
    topics = _topic_keys(value)
    if not topics:
        return None, "topic_missing"
    return Candidate(content=content, topic_keys=topics, sensitivity=sensitivity, ttl_seconds=ttl), "candidate"


async def validate_and_process_user_message(
    *, session_id: str, message_id: str, text: str,
    provider: Mapping[str, object] | None, model: str,
    snapshot: RolloutSnapshot | None = None, now: float | None = None,
) -> dict[str, object]:
    """Optional remote validation after deterministic secret/minimization gates.

    Only the bounded candidate is transmitted.  Failure is fail-closed and the
    model can veto a candidate but can never author or rewrite stored content.
    """
    snap = snapshot or rollout_snapshot()
    if not snap.enabled or snap.rollout_mode == "off":
        return process_user_message(
            session_id=session_id, message_id=message_id, text=text, snapshot=snap, now=now,
        )
    if not snap.remote_extraction_enabled:
        return process_user_message(
            session_id=session_id, message_id=message_id, text=text, snapshot=snap, now=now,
        )
    candidate, reason = analyze_user_text(text, ttl_seconds=snap.default_ttl_seconds)
    if candidate is None:
        _diagnose(snap.rollout_epoch, reason)
        return {"status": "rejected", "reason": reason}
    try:
        result = await llm.complete_json(
            dict(provider) if provider is not None else None,
            model,
            [
                {
                    "role": "system",
                    "content": (
                        "You validate a short-lived companionship reminder. "
                        "Return exactly one JSON object: {\"accept\":true} or "
                        "{\"accept\":false}. Accept only an explicit, near-term user plan "
                        "that would be useful to follow up. Never add or rewrite facts."
                    ),
                },
                {"role": "user", "content": candidate.content},
            ],
            max_tokens=32,
            timeout_seconds=15,
            temperature=0.0,
            json_mode=True,
        )
        payload = json.loads(str(result.get("text") or ""))
        if set(payload) != {"accept"} or not isinstance(payload.get("accept"), bool):
            raise ValueError("invalid validator payload")
    except Exception:
        _diagnose(snap.rollout_epoch, "remote_validation_failed")
        return {"status": "rejected", "reason": "remote_validation_failed"}
    if not payload["accept"]:
        _diagnose(snap.rollout_epoch, "remote_validation_rejected")
        return {"status": "rejected", "reason": "remote_validation_rejected"}
    _diagnose(snap.rollout_epoch, "remote_validation_accepted")
    return process_user_message(
        session_id=session_id, message_id=message_id, text=text, snapshot=snap, now=now,
        extraction_method="model_validated",
    )


def process_user_message(
    *, session_id: str, message_id: str, text: str, snapshot: RolloutSnapshot | None = None,
    now: float | None = None, extraction_method: str = "deterministic",
) -> dict[str, object]:
    snap = snapshot or rollout_snapshot()
    if extraction_method not in {"deterministic", "model_validated"}:
        raise ShortMemoError("short_memo_extraction_method_invalid")
    timestamp = float(now if now is not None else db.now())
    if not snap.enabled or snap.rollout_mode == "off":
        _diagnose(snap.rollout_epoch, "disabled")
        return {"status": "disabled"}
    candidate, reason = analyze_user_text(text, ttl_seconds=snap.default_ttl_seconds)
    _diagnose(snap.rollout_epoch, reason)
    if candidate is None:
        return {"status": "rejected", "reason": reason}
    if snap.rollout_mode == "shadow":
        _diagnose(snap.rollout_epoch, "shadow_candidate")
        return {"status": "shadow_candidate"}
    conn = db.connect()
    try:
        cleanup_expired(conn=conn, now=timestamp, rollout_epoch=snap.rollout_epoch)
        row = conn.execute(
            "SELECT session_id,role,content FROM messages WHERE id=?", (message_id,),
        ).fetchone()
        if not row or row["session_id"] != session_id or row["role"] != "user":
            _diagnose(snap.rollout_epoch, "source_invalid")
            return {"status": "rejected", "reason": "source_invalid"}
        source_hash = _sha256(str(row["content"]))
        if source_hash != _sha256(text):
            _diagnose(snap.rollout_epoch, "source_snapshot_mismatch")
            return {"status": "rejected", "reason": "source_snapshot_mismatch"}
        dedupe = _dedupe_key(session_id, candidate.content, timestamp)
        existing = conn.execute("SELECT * FROM short_memos WHERE dedupe_key=?", (dedupe,)).fetchone()
        if existing:
            expires_at = min(
                float(existing["created_at"]) + MAX_TTL,
                max(float(existing["expires_at"]), timestamp + candidate.ttl_seconds),
            )
            revision = int(existing["revision"]) + 1
            conn.execute(
                "UPDATE short_memos SET updated_at=?,expires_at=?,revision=? WHERE id=?",
                (timestamp, expires_at, revision, existing["id"]),
            )
            _event_locked(
                conn, str(existing["id"]), "deduplicated", "same_window",
                {"protocol_version": PROTOCOL_VERSION, "revision": revision,
                 "ttl_seconds": int(expires_at - float(existing["created_at"])),
                 "rollout_epoch": max(1, snap.rollout_epoch)},
                timestamp,
            )
            conn.commit()
            return {"status": "deduplicated", "id": existing["id"]}
        active_count = conn.execute("SELECT COUNT(*) FROM short_memos").fetchone()[0]
        if int(active_count) >= snap.max_active:
            _diagnose(snap.rollout_epoch, "capacity_rejected")
            return {"status": "rejected", "reason": "capacity_rejected"}
        memo_id = db.new_id()
        expires_at = timestamp + candidate.ttl_seconds
        content_hash = _sha256(_normalize(candidate.content))
        conn.execute(
            "INSERT INTO short_memos(id,content,content_hash,topic_keys_json,source_session_id,"
            "source_message_id,source_snapshot_hash,source_run_id,extraction_method,sensitivity,"
            "dedupe_key,revision,created_at,updated_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                memo_id, candidate.content, content_hash,
                json.dumps(candidate.topic_keys, ensure_ascii=False, separators=(",", ":")),
                session_id, message_id, source_hash, None, extraction_method,
                candidate.sensitivity, dedupe, 1, timestamp, timestamp, expires_at,
            ),
        )
        _event_locked(
            conn, memo_id, "created", "user_message",
            {"protocol_version": PROTOCOL_VERSION, "revision": 1,
             "ttl_seconds": candidate.ttl_seconds, "rollout_epoch": max(1, snap.rollout_epoch)},
            timestamp,
        )
        conn.commit()
        return {"status": "created", "id": memo_id}
    finally:
        conn.close()


def cleanup_expired(*, conn=None, now: float | None = None, rollout_epoch: int = 0) -> int:
    owned = conn is None
    connection = conn or db.connect()
    timestamp = float(now if now is not None else db.now())
    try:
        rows = connection.execute(
            "SELECT id FROM short_memos WHERE expires_at<=? ORDER BY expires_at,id", (timestamp,),
        ).fetchall()
        for row in rows:
            connection.execute("DELETE FROM short_memos WHERE id=?", (row["id"],))
            _event_locked(
                connection, str(row["id"]), "expired", "ttl_elapsed",
                {"protocol_version": PROTOCOL_VERSION, **(
                    {"rollout_epoch": rollout_epoch} if rollout_epoch >= 1 else {}
                )},
                timestamp,
            )
        if owned:
            connection.commit()
        return len(rows)
    finally:
        if owned:
            connection.close()


def list_active(*, limit: int = HARD_MAX_ACTIVE) -> list[dict[str, object]]:
    cleanup_expired()
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT sm.*,s.title AS source_session_title,m.created_at AS source_created_at "
            "FROM short_memos sm JOIN sessions s ON s.id=sm.source_session_id "
            "JOIN messages m ON m.id=sm.source_message_id "
            "ORDER BY sm.updated_at DESC,sm.id ASC LIMIT ?",
            (_bounded(limit, 1, HARD_MAX_ACTIVE),),
        ).fetchall()
        return [_public(row) for row in rows]
    finally:
        conn.close()


def update_expiry(memo_id: str, *, expected_revision: int, expires_at: float) -> dict[str, object]:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM short_memos WHERE id=?", (memo_id,)).fetchone()
        if not row:
            raise ShortMemoError("short_memo_not_found")
        if int(row["revision"]) != expected_revision:
            raise ShortMemoError("short_memo_revision_conflict")
        if not float(row["created_at"]) + MIN_TTL <= expires_at <= float(row["created_at"]) + MAX_TTL:
            raise ShortMemoError("short_memo_expiry_invalid")
        if expires_at <= db.now():
            raise ShortMemoError("short_memo_expiry_invalid")
        revision = expected_revision + 1
        timestamp = db.now()
        conn.execute(
            "UPDATE short_memos SET expires_at=?,updated_at=?,revision=? WHERE id=? AND revision=?",
            (float(expires_at), timestamp, revision, memo_id, expected_revision),
        )
        if conn.execute("SELECT changes()").fetchone()[0] != 1:
            raise ShortMemoError("short_memo_revision_conflict")
        snap = rollout_snapshot(conn)
        _event_locked(
            conn, memo_id, "expiry_changed", "user_changed_expiry",
            {"protocol_version": PROTOCOL_VERSION, "revision": revision,
             "ttl_seconds": int(expires_at - float(row["created_at"])),
             "rollout_epoch": max(1, snap.rollout_epoch)}, timestamp,
        )
        conn.commit()
        updated = conn.execute(
            "SELECT sm.*,s.title AS source_session_title,m.created_at AS source_created_at "
            "FROM short_memos sm JOIN sessions s ON s.id=sm.source_session_id "
            "JOIN messages m ON m.id=sm.source_message_id WHERE sm.id=?", (memo_id,),
        ).fetchone()
        return _public(updated)
    finally:
        conn.close()


def delete(memo_id: str) -> bool:
    conn = db.connect()
    try:
        row = conn.execute("SELECT id FROM short_memos WHERE id=?", (memo_id,)).fetchone()
        if not row:
            return False
        timestamp = db.now()
        conn.execute("DELETE FROM short_memos WHERE id=?", (memo_id,))
        _event_locked(conn, memo_id, "deleted", "user_deleted", {}, timestamp)
        conn.commit()
        return True
    finally:
        conn.close()


def clear(*, clear_events: bool = False, privacy: bool = False) -> int:
    conn = db.connect()
    try:
        rows = conn.execute("SELECT id FROM short_memos ORDER BY id").fetchall()
        timestamp = db.now()
        conn.execute("DELETE FROM short_memos")
        if clear_events or privacy:
            conn.execute("DELETE FROM short_memo_events")
        else:
            for row in rows:
                _event_locked(conn, str(row["id"]), "cleared", "user_cleared", {}, timestamp)
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def recall(query: str, *, snapshot: RolloutSnapshot | None = None) -> list[dict[str, object]]:
    snap = snapshot or rollout_snapshot()
    if not snap.enabled or snap.rollout_mode != "active":
        return []
    cleanup_expired(rollout_epoch=snap.rollout_epoch)
    clean_query = _normalize(query)
    if not clean_query:
        return []
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT sm.*,m.session_id AS actual_session_id,m.role AS source_role,m.content AS source_content "
            "FROM short_memos sm LEFT JOIN messages m ON m.id=sm.source_message_id "
            "ORDER BY sm.expires_at ASC,sm.updated_at DESC,sm.id ASC"
        ).fetchall()
        valid: list[tuple[int, Mapping[str, object]]] = []
        for row in rows:
            if not _source_valid(row):
                conn.execute("DELETE FROM short_memos WHERE id=?", (row["id"],))
                _event_locked(conn, str(row["id"]), "deleted", "source_invalid", {}, db.now())
                continue
            try:
                topics = json.loads(str(row["topic_keys_json"]))
            except (TypeError, ValueError):
                continue
            if not isinstance(topics, list) or not all(isinstance(item, str) for item in topics):
                continue
            hit = sum(1 for topic in topics if len(topic) >= 2 and _normalize(topic) in clean_query)
            if hit:
                valid.append((hit, row))
        conn.commit()
        valid.sort(key=lambda item: (
            -item[0], float(item[1]["expires_at"]), -float(item[1]["updated_at"]), str(item[1]["id"]),
        ))
        return [_public(row) for _, row in valid[:snap.max_recall]]
    finally:
        conn.close()


def render_recall(items: Sequence[Mapping[str, object]]) -> str:
    if not items:
        return ""
    return "\n".join(f"- {str(item['content'])}" for item in items[:HARD_MAX_RECALL])


def export_data() -> dict[str, object]:
    conn = db.connect()
    try:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "settings": rollout_snapshot(conn).public(),
            "items": list_active(),
            "events": [dict(row) for row in conn.execute(
                "SELECT id,memo_id,action,reason_code,metadata_json,created_at "
                "FROM short_memo_events ORDER BY created_at,id"
            )],
        }
    finally:
        conn.close()


def diagnostics() -> dict[str, object]:
    conn = db.connect()
    try:
        snap = rollout_snapshot(conn)
        counts = {
            "active": int(conn.execute("SELECT COUNT(*) FROM short_memos").fetchone()[0]),
            "events": int(conn.execute("SELECT COUNT(*) FROM short_memo_events").fetchone()[0]),
        }
    finally:
        conn.close()
    with _DIAGNOSTIC_LOCK:
        reasons = dict(_DIAGNOSTICS.get(snap.rollout_epoch, {}))
    return {
        "protocol_version": PROTOCOL_VERSION,
        "rollout_epoch": snap.rollout_epoch,
        "rollout_mode": snap.rollout_mode,
        "enabled": snap.enabled,
        "counts": counts,
        "reason_counts": reasons,
    }


def validate_event_metadata(metadata: Mapping[str, object] | None) -> str:
    value = dict(metadata or {})
    if set(value) - METADATA_KEYS:
        raise ShortMemoError("short_memo_event_metadata_invalid")
    if "protocol_version" in value and value["protocol_version"] != PROTOCOL_VERSION:
        raise ShortMemoError("short_memo_event_metadata_invalid")
    for key in ("revision", "rollout_epoch"):
        if key in value and (isinstance(value[key], bool) or not isinstance(value[key], int) or value[key] < 1):
            raise ShortMemoError("short_memo_event_metadata_invalid")
    if "ttl_seconds" in value and (
        isinstance(value["ttl_seconds"], bool) or not isinstance(value["ttl_seconds"], int)
        or not MIN_TTL <= value["ttl_seconds"] <= MAX_TTL
    ):
        raise ShortMemoError("short_memo_event_metadata_invalid")
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 256:
        raise ShortMemoError("short_memo_event_metadata_invalid")
    return encoded


def _event_locked(
    conn, memo_id: str, action: str, reason: str, metadata: Mapping[str, object], timestamp: float,
) -> None:
    encoded = validate_event_metadata(metadata)
    conn.execute(
        "INSERT INTO short_memo_events(id,memo_id,action,reason_code,metadata_json,created_at) "
        "VALUES(?,?,?,?,?,?)",
        (db.new_id(), memo_id, action, reason, encoded, timestamp),
    )


def _source_valid(row: Mapping[str, object]) -> bool:
    return (
        row["actual_session_id"] == row["source_session_id"]
        and row["source_role"] == "user"
        and _sha256(str(row["source_content"] or "")) == row["source_snapshot_hash"]
        and float(row["expires_at"]) > db.now()
    )


def _public(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "id": row["id"], "content": row["content"], "sensitivity": row["sensitivity"],
        "revision": int(row["revision"]), "created_at": float(row["created_at"]),
        "updated_at": float(row["updated_at"]), "expires_at": float(row["expires_at"]),
        "source_session_id": row["source_session_id"],
        "source_message_id": row["source_message_id"],
        "source_session_title": row["source_session_title"] if "source_session_title" in row.keys() else "",
        "source_created_at": (
            float(row["source_created_at"]) if "source_created_at" in row.keys() else float(row["created_at"])
        ),
    }


def _topic_keys(value: str) -> tuple[str, ...]:
    candidates: list[str] = []
    candidates.extend(re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,31}", value.casefold()))
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", value):
        # Strip the scheduling wrapper before producing bounded n-grams.  If the
        # wrapper is left in place it can consume the twelve-key cap before a
        # late sentence topic (for example "图书馆还书") is reached.
        clean = re.sub(
            r"^(?:(?:今天|今晚|明天|明早|明晚|后天|这周|本周|下周|稍后|回头))?"
            r"(?:我|用户)?(?:要|会|准备|打算|计划|得|需要|记得|别忘了)?",
            "", chunk,
        ) or chunk
        for size in (4, 3, 2):
            candidates.extend(clean[index:index + size] for index in range(0, len(clean) - size + 1))
    result: list[str] = []
    for item in candidates:
        if item in _STOP_TOPICS or item in result:
            continue
        result.append(item)
        if len(result) == 12:
            break
    return tuple(result)


def _ttl_from_text(value: str, default: int) -> int:
    match = re.search(r"(\d{1,2})天后", value)
    if match:
        return _bounded((int(match.group(1)) + 1) * 86_400, MIN_TTL, MAX_TTL)
    if "下周" in value:
        return min(MAX_TTL, 8 * 86_400)
    if "后天" in value:
        return 3 * 86_400
    return _bounded(default, MIN_TTL, MAX_TTL)


def _dedupe_key(session_id: str, content: str, timestamp: float) -> str:
    window = int(timestamp // 86_400)
    return _sha256(f"{session_id}|{_normalize(content)}|{window}")


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").casefold())


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _set_locked(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value),
    )


def _diagnose(epoch: int, reason: str) -> None:
    with _DIAGNOSTIC_LOCK:
        bucket = _DIAGNOSTICS.setdefault(max(0, int(epoch)), {})
        bucket[reason] = bucket.get(reason, 0) + 1


def _bounded(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
