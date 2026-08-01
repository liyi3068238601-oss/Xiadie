"""Request-local LIFE2 inner-state projection over existing authoritative objects."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping, Sequence

from . import db

PROTOCOL_VERSION = "inner-state-projection-v1"
ROLLOUT_KEY = "life.inner_state_projection.rollout_mode"
ROLLOUT_MODES = ("off", "shadow", "active")
DEFAULT_ROLLOUT_MODE = "active"
AFFECT_BANDS = frozenset({
    "bright", "serene", "agitated", "melancholic", "focused",
    "contemplative", "pleased", "subdued", "neutral",
})
RELATIONSHIP_BOUNDARIES = frozenset({
    "defensive", "highly_guarded", "default_distance", "softly_guarded", "relaxed",
})
EXPRESSION_FLAGS = frozenset({"calm", "warm", "concise", "gently_curious", "offer_help"})


class ProjectionRolloutError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class InnerStateProjection:
    source_snapshot_hash: str
    affect_band: str | None
    relationship_boundary: str | None
    open_goal_ids: tuple[str, ...]
    open_saga_ids: tuple[str, ...]
    recent_life_event_ids: tuple[str, ...]
    relevant_short_memo_ids: tuple[str, ...]
    expression_flags: tuple[str, ...]

    def as_mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "protocol_version": PROTOCOL_VERSION,
            "source_snapshot_hash": self.source_snapshot_hash,
        }
        if self.affect_band:
            result["affect_band"] = self.affect_band
        if self.relationship_boundary:
            result["relationship_boundary"] = self.relationship_boundary
        for key in (
            "open_goal_ids", "open_saga_ids", "recent_life_event_ids",
            "relevant_short_memo_ids", "expression_flags",
        ):
            value = getattr(self, key)
            if value:
                result[key] = list(value)
        return result


def rollout_mode(conn=None) -> str:
    owned = conn is None
    connection = conn or db.connect()
    try:
        row = connection.execute(
            "SELECT value FROM settings WHERE key = ?", (ROLLOUT_KEY,)
        ).fetchone()
        value = row["value"] if row else DEFAULT_ROLLOUT_MODE
        return value if value in ROLLOUT_MODES else "off"
    finally:
        if owned:
            connection.close()


def set_rollout_mode(mode: str) -> str:
    """Internal release operation; ordinary API/UI must never call this function."""
    if mode not in ROLLOUT_MODES:
        raise ProjectionRolloutError("inner_state_projection_rollout_invalid")
    conn = db.connect()
    try:
        if rollout_mode(conn) != mode:
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (ROLLOUT_KEY, mode),
            )
        conn.commit()
        return rollout_mode(conn)
    finally:
        conn.close()


def build(
    *, state: Mapping[str, object] | None,
    goals: Sequence[Mapping[str, object]] = (),
    sagas: Sequence[Mapping[str, object]] = (),
    life_events: Sequence[Mapping[str, object]] = (),
    short_memos: Sequence[Mapping[str, object]] = (),
    request_mode: str = "companionship",
    current_intent: str = "statement",
) -> InnerStateProjection | None:
    """Build an immutable projection without persisting, caching or free-form text."""
    state_map = dict(state or {})
    derived = dict(state_map.get("derived") or {})
    affect = dict(state_map.get("affect") or {})
    relationship = dict(state_map.get("relationship") or {})
    affect_band = _enum(derived.get("cluster"), AFFECT_BANDS)
    boundary = _enum(derived.get("guardedness_band"), RELATIONSHIP_BOUNDARIES)

    open_goal_ids = _selected_ids(
        (item for item in goals if item.get("status") == "active"), limit=3,
        order=lambda item: (-_number(item.get("priority")), _number(item.get("updated_at")), str(item.get("id") or "")),
    )
    open_saga_ids = _selected_ids(
        (item for item in sagas if item.get("status") == "active"), limit=2,
        order=lambda item: (-_number(item.get("end_at")), str(item.get("id") or "")),
    )
    recent_event_ids = _selected_ids(
        (item for item in life_events if item.get("lifecycle_status") == "active"), limit=3,
        order=lambda item: (-_number(item.get("created_at")), str(item.get("id") or "")),
    )
    memo_ids = _selected_ids(
        short_memos, limit=3,
        order=lambda item: (_number(item.get("expires_at")), -_number(item.get("updated_at")), str(item.get("id") or "")),
    )
    if not any((affect_band, boundary, open_goal_ids, open_saga_ids, recent_event_ids, memo_ids)):
        return None
    flags = _expression_flags(
        affect_band=affect_band, boundary=boundary,
        request_mode=request_mode, current_intent=current_intent,
    )
    if not any((affect_band, boundary, open_goal_ids, open_saga_ids, recent_event_ids, memo_ids, flags)):
        return None
    snapshot = {
        "affect": {key: affect.get(key) for key in ("valence", "arousal", "contact_need", "updated_at")},
        "relationship": {key: relationship.get(key) for key in ("bond", "trust", "interaction_count", "updated_at")},
        "affect_band": affect_band,
        "relationship_boundary": boundary,
        "goal_sources": _source_versions(goals, open_goal_ids, "revision"),
        "saga_sources": _source_versions(sagas, open_saga_ids, "revision"),
        "life_event_sources": _source_versions(life_events, recent_event_ids, "revision"),
        "short_memo_sources": _source_versions(short_memos, memo_ids, "revision"),
        "request_mode": request_mode,
        "current_intent": current_intent,
    }
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return InnerStateProjection(
        source_snapshot_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        affect_band=affect_band,
        relationship_boundary=boundary,
        open_goal_ids=open_goal_ids,
        open_saga_ids=open_saga_ids,
        recent_life_event_ids=recent_event_ids,
        relevant_short_memo_ids=memo_ids,
        expression_flags=flags,
    )


def classify_current_intent(text: str, *, request_mode: str) -> str:
    value = str(text or "").strip()
    if request_mode == "focused_work":
        return "focused_work"
    if any(marker in value for marker in ("怎么办", "能帮", "帮我", "需要帮助", "很难", "不知所措")):
        return "support_request"
    if value.endswith(("?", "？")) or any(marker in value for marker in ("为什么", "是什么", "怎么", "是否")):
        return "question"
    if any(marker in value for marker in ("我发现", "我看到", "我在想", "好奇", "有趣")):
        return "open_conversation"
    return "statement"


def _expression_flags(*, affect_band: str | None, boundary: str | None,
                      request_mode: str, current_intent: str) -> tuple[str, ...]:
    flags = {"calm"}
    if request_mode == "focused_work" or current_intent == "focused_work":
        flags.add("concise")
    if boundary in {"default_distance", "softly_guarded", "relaxed"}:
        flags.add("warm")
        if current_intent in {"statement", "open_conversation", "question"}:
            flags.add("gently_curious")
        if current_intent in {"statement", "open_conversation", "support_request"}:
            flags.add("offer_help")
    if affect_band in {"agitated", "melancholic", "subdued"}:
        flags.add("concise")
    return tuple(sorted(flags))


def _selected_ids(items, *, limit: int, order) -> tuple[str, ...]:
    rows = sorted((dict(item) for item in items), key=order)
    selected: list[str] = []
    for row in rows:
        item_id = str(row.get("id") or "")
        if not item_id or item_id in selected:
            continue
        selected.append(item_id)
        if len(selected) == limit:
            break
    return tuple(selected)


def _source_versions(items: Sequence[Mapping[str, object]], selected_ids: Sequence[str],
                     revision_key: str) -> list[list[object]]:
    selected = set(selected_ids)
    return sorted([
        [str(item.get("id") or ""), item.get(revision_key), item.get("status") or item.get("lifecycle_status")]
        for item in items if str(item.get("id") or "") in selected
    ], key=lambda item: item[0])


def _enum(value: object, allowed: frozenset[str]) -> str | None:
    text = str(value or "")
    return text if text in allowed else None


def _number(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
