"""Deterministic Persona v2 resource compiler and fail-closed rollout gate."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Mapping

from . import context_budget, db, persona_output_guard

PROFILE_DIR = Path(__file__).with_name("persona_profiles") / "v2"
MANIFEST_PATH = PROFILE_DIR / "manifest.json"
CERTIFICATIONS_PATH = PROFILE_DIR / "certifications.json"
MODES = ("companionship", "focused_work")
ROLLOUT_MODES = ("off", "shadow", "active")
DEFAULT_STYLE = {
    "address_style": "natural",
    "detail_level": "balanced",
    "poetic_level": "balanced",
    "proactivity_level": "balanced",
}
STYLE_OPTIONS = {
    "address_style": frozenset({"natural", "ge_xia_low", "name_if_known", "none"}),
    "detail_level": frozenset({"concise", "balanced", "detailed"}),
    "poetic_level": frozenset({"low", "balanced", "high"}),
    "proactivity_level": frozenset({"reserved", "balanced", "engaged"}),
}
ROLLOUT_KEY = "life.persona_v2.rollout_mode"
PERSONA_TOKEN_LIMIT = 1450


@dataclass(frozen=True)
class PersonaCompilation:
    prompt: str
    candidate_prompt: str
    profile_version: str
    compiler_version: str
    mode: str
    rollout_mode: str
    selected_v2: bool
    certified: bool
    section_hashes: Mapping[str, str]
    compiled_hash: str
    candidate_tokens: int
    fallback_reason: str | None

    def public_meta(self) -> dict[str, object]:
        return {
            "profile_version": self.profile_version,
            "compiler_version": self.compiler_version,
            "persona_mode": self.mode,
            "persona_rollout_mode": self.rollout_mode,
            "persona_v2_selected": self.selected_v2,
            "persona_model_certified": self.certified,
            "persona_section_hashes": dict(self.section_hashes),
            "persona_compiled_hash": self.compiled_hash,
            "persona_candidate_tokens": self.candidate_tokens,
            "persona_fallback_reason": self.fallback_reason,
        }


class PersonaResourceError(ValueError):
    pass


def model_fingerprint(provider: Mapping[str, object] | None, model: str) -> str:
    payload = {
        "provider_id": str((provider or {}).get("id") or "mock"),
        "base_url": str((provider or {}).get("base_url") or "").rstrip("/"),
        "model": str(model),
        "execution_location": str((provider or {}).get("execution_location") or "unknown"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def compile_for_request(
    *, legacy_prompt: str, mode: str | None, style: Mapping[str, str] | None,
    provider: Mapping[str, object] | None, model: str,
    projection: Mapping[str, object] | None = None,
    projection_rollout_mode: str = "off",
    rollout_mode: str | None = None,
) -> PersonaCompilation:
    selected_mode = mode or "companionship"
    if selected_mode not in MODES:
        raise PersonaResourceError("persona_mode_invalid")
    if style:
        for key, value in style.items():
            if key not in STYLE_OPTIONS or value not in STYLE_OPTIONS[key]:
                raise PersonaResourceError("persona_style_invalid")
    selected_rollout = rollout_mode or db.get_setting(ROLLOUT_KEY, "off")
    if selected_rollout not in ROLLOUT_MODES:
        selected_rollout = "off"
    if projection_rollout_mode not in ROLLOUT_MODES:
        projection_rollout_mode = "off"
    try:
        static_candidate, manifest, section_hashes = compile_candidate(
            mode=selected_mode, style=style, projection=None,
        )
        projected_candidate = static_candidate
        if projection is not None and projection_rollout_mode in {"shadow", "active"}:
            projected_candidate, _, _ = compile_candidate(
                mode=selected_mode, style=style, projection=projection,
            )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return PersonaCompilation(
            prompt=legacy_prompt, candidate_prompt="", profile_version="legacy",
            compiler_version="legacy", mode=selected_mode, rollout_mode=selected_rollout,
            selected_v2=False, certified=False, section_hashes={},
            compiled_hash=hashlib.sha256(legacy_prompt.encode()).hexdigest(),
            candidate_tokens=0, fallback_reason="persona_resource_invalid",
        )
    fingerprint = model_fingerprint(provider, model)
    # Certification binds reviewed static resources.  A validated request-local
    # projection may alter the selected prompt but must not invalidate or inherit
    # that static model certificate.
    compiled_hash = hashlib.sha256(static_candidate.encode()).hexdigest()
    certified = is_certified(
        fingerprint, manifest["profile_version"], manifest["compiler_version"],
        selected_mode, compiled_hash,
    )
    selected = selected_rollout == "active" and certified
    fallback = None
    if not selected:
        fallback = "persona_rollout_inactive" if selected_rollout != "active" else "persona_model_uncertified"
    selected_candidate = projected_candidate if projection_rollout_mode == "active" else static_candidate
    comparison_candidate = projected_candidate if projection_rollout_mode == "shadow" else selected_candidate
    return PersonaCompilation(
        prompt=selected_candidate if selected else legacy_prompt,
        candidate_prompt=comparison_candidate,
        profile_version=manifest["profile_version"],
        compiler_version=manifest["compiler_version"], mode=selected_mode,
        rollout_mode=selected_rollout, selected_v2=selected, certified=certified,
        section_hashes=section_hashes,
        compiled_hash=compiled_hash,
        candidate_tokens=context_budget.estimate_tokens(comparison_candidate), fallback_reason=fallback,
    )


def compile_candidate(
    *, mode: str, style: Mapping[str, str] | None = None,
    projection: Mapping[str, object] | None = None,
) -> tuple[str, dict, dict[str, str]]:
    if mode not in MODES:
        raise PersonaResourceError("persona_mode_invalid")
    manifest, loaded, hashes = _load_profile_resources()
    style_map = json.loads(loaded["styles"])
    chosen = dict(DEFAULT_STYLE)
    if style:
        for key, value in style.items():
            if key not in DEFAULT_STYLE or value not in style_map[key]:
                raise PersonaResourceError("persona_style_invalid")
            chosen[key] = value
    style_lines = [style_map[key][chosen[key]] for key in DEFAULT_STYLE]
    parts = [loaded["core"], loaded[mode], "# 用户表达偏好\n\n" + "\n".join(f"- {line}" for line in style_lines)]
    projection_text = _render_projection(projection)
    if projection_text:
        parts.append(projection_text)
    parts.append(loaded["output_contract"])
    prompt = "\n\n".join(parts).strip()
    if context_budget.estimate_tokens(prompt) > PERSONA_TOKEN_LIMIT:
        raise PersonaResourceError("persona_token_budget_exceeded")
    return prompt, manifest, hashes


def derive_observer_summary(*, fallback: str) -> str:
    """Derive the observer's compact persona anchor from the verified Core resource."""
    try:
        _, loaded, _ = _load_profile_resources()
        sections = {
            heading.strip(): body.strip()
            for heading, body in re.findall(
                r"(?ms)^# ([^\n]+)\n\n(.*?)(?=^# |\Z)", loaded["core"],
            )
        }
        personality = sections["核心人格"].split("。", 1)[0].strip()
        relationship_sentences = [
            item.strip() for item in sections["你与用户"].split("。") if item.strip()
        ][:2]
        summary = "。".join([personality, *relationship_sentences]).strip() + "。"
        return summary if summary else fallback
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return fallback


def _load_profile_resources() -> tuple[dict, dict[str, str], dict[str, str]]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    files = manifest["files"]
    loaded: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for key, spec in files.items():
        raw = (PROFILE_DIR / spec["path"]).read_text(encoding="utf-8")
        digest = hashlib.sha256(raw.encode()).hexdigest()
        if digest != spec["sha256"]:
            raise PersonaResourceError(f"persona_hash_mismatch:{key}")
        loaded[key] = raw.strip()
        hashes[key] = digest
    return manifest, loaded, hashes


def is_certified(
    fingerprint: str, profile_version: str, compiler_version: str,
    mode: str, compiled_hash: str,
) -> bool:
    try:
        payload = json.loads(CERTIFICATIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return any(
        item.get("model_fingerprint") == fingerprint
        and item.get("profile_version") == profile_version
        and item.get("compiler_version") == compiler_version
        and isinstance(item.get("compiled_hashes"), dict)
        and item["compiled_hashes"].get(mode) == compiled_hash
        and item.get("sampling_profile") == {"temperature": 0.0}
        and item.get("output_guard_protocol") == persona_output_guard.PROTOCOL_VERSION
        and item.get("status") == "certified"
        for item in payload.get("certifications", []) if isinstance(item, dict)
    )


def _render_projection(projection: Mapping[str, object] | None) -> str:
    if not projection:
        return ""
    allowed_keys = {
        "protocol_version", "source_snapshot_hash", "affect_band", "relationship_boundary",
        "open_goal_ids", "open_saga_ids", "recent_life_event_ids",
        "relevant_short_memo_ids", "expression_flags",
    }
    if set(projection) - allowed_keys:
        raise PersonaResourceError("inner_state_projection_invalid")
    if projection.get("protocol_version") != "inner-state-projection-v1":
        raise PersonaResourceError("inner_state_projection_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(projection.get("source_snapshot_hash") or "")):
        raise PersonaResourceError("inner_state_projection_invalid")
    scalar_values = {
        "affect_band": {
            "bright", "serene", "agitated", "melancholic", "focused",
            "contemplative", "pleased", "subdued", "neutral",
        },
        "relationship_boundary": {
            "defensive", "highly_guarded", "default_distance", "softly_guarded", "relaxed",
        },
    }
    list_limits = {
        "open_goal_ids": 3, "open_saga_ids": 2, "recent_life_event_ids": 3,
        "relevant_short_memo_ids": 3, "expression_flags": 5,
    }
    expression_values = {"calm", "warm", "concise", "gently_curious", "offer_help"}
    allowed_scalars = ("affect_band", "relationship_boundary")
    allowed_lists = (
        "open_goal_ids", "open_saga_ids", "recent_life_event_ids",
        "relevant_short_memo_ids", "expression_flags",
    )
    lines = []
    for key in allowed_scalars:
        value = projection.get(key)
        if value is not None and value not in scalar_values[key]:
            raise PersonaResourceError("inner_state_projection_invalid")
        # Affect/boundary are validated and used by the deterministic projection
        # builder to derive flags.  Rendering the opaque enum names again adds no
        # behavior but would waste the bounded Persona budget.
    for key in allowed_lists:
        value = projection.get(key)
        if value is not None and not isinstance(value, (list, tuple)):
            raise PersonaResourceError("inner_state_projection_invalid")
        if isinstance(value, (list, tuple)) and value:
            if len(value) > list_limits[key] or len(set(value)) != len(value):
                raise PersonaResourceError("inner_state_projection_invalid")
            safe = [str(item) for item in value if isinstance(item, str) and item]
            if len(safe) != len(value):
                raise PersonaResourceError("inner_state_projection_invalid")
            if key == "expression_flags":
                if any(item not in expression_values for item in safe):
                    raise PersonaResourceError("inner_state_projection_invalid")
            elif any(not re.fullmatch(r"[0-9a-f]{16}", item) for item in safe):
                raise PersonaResourceError("inner_state_projection_invalid")
            # Source IDs remain in the request-local projection for deterministic
            # provenance and replay, but opaque IDs provide no semantic value to
            # the model.  Only the bounded expression flags are rendered.
            if safe and key == "expression_flags":
                lines.append(",".join(safe))
    if not lines:
        return ""
    return "# 表达提示\n\n不改事实/关系/权限：" + ",".join(lines)
