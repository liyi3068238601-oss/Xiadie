from __future__ import annotations

import hashlib
import json

import pytest

from app import context_budget, persona, persona_v2


def _provider() -> dict[str, object]:
    return {
        "id": "deepseek", "base_url": "https://api.deepseek.com",
        "execution_location": "remote",
    }


def test_candidate_is_deterministic_bounded_and_keeps_core_identity() -> None:
    first, manifest, hashes = persona_v2.compile_candidate(mode="companionship")
    second, _, second_hashes = persona_v2.compile_candidate(mode="companionship")
    work, _, _ = persona_v2.compile_candidate(mode="focused_work")

    assert first == second
    assert hashes == second_hashes
    assert manifest["profile_version"] == "persona-profile-v2"
    assert "你是遐蝶本人" in first
    assert "《如我所书》" in first
    assert "曾是奥赫玛的入殓师" in first
    assert "你是遐蝶本人" in work
    assert "先解决任务" in work
    assert context_budget.estimate_tokens(first) <= 1200
    assert context_budget.estimate_tokens(work) <= 1200
    assert len(first) < len(persona.PERSONA_PROMPT)
    assert len(work) < len(persona.PERSONA_PROMPT)
    assert "温柔、悲悯、安静、克制" in persona.OBSERVER_PERSONA_SUMMARY
    assert "不默认是开拓者" in persona.OBSERVER_PERSONA_SUMMARY


def test_unknown_mode_and_style_are_rejected_at_request_boundary() -> None:
    with pytest.raises(persona_v2.PersonaResourceError, match="persona_mode_invalid"):
        persona_v2.compile_for_request(
            legacy_prompt=persona.PERSONA_PROMPT, mode="auto", style=None,
            provider=_provider(), model="deepseek-v4-flash", rollout_mode="shadow",
        )
    with pytest.raises(persona_v2.PersonaResourceError, match="persona_style_invalid"):
        persona_v2.compile_for_request(
            legacy_prompt=persona.PERSONA_PROMPT, mode="companionship",
            style={"poetic_level": "always"}, provider=_provider(),
            model="deepseek-v4-flash", rollout_mode="shadow",
        )


def test_shadow_and_uncertified_active_keep_legacy_prompt() -> None:
    shadow = persona_v2.compile_for_request(
        legacy_prompt=persona.PERSONA_PROMPT, mode="companionship", style=None,
        provider=_provider(), model="deepseek-v4-flash", rollout_mode="shadow",
    )
    active = persona_v2.compile_for_request(
        legacy_prompt=persona.PERSONA_PROMPT, mode="focused_work", style=None,
        provider=_provider(), model="deepseek-v4-flash", rollout_mode="active",
    )
    assert shadow.prompt == persona.PERSONA_PROMPT
    assert shadow.candidate_prompt and not shadow.selected_v2
    assert active.prompt == persona.PERSONA_PROMPT
    assert active.fallback_reason == "persona_model_uncertified"


def test_certificate_is_bound_to_model_mode_and_compiled_hash(tmp_path, monkeypatch) -> None:
    candidate, manifest, _ = persona_v2.compile_candidate(mode="focused_work")
    compiled_hash = hashlib.sha256(candidate.encode()).hexdigest()
    fingerprint = persona_v2.model_fingerprint(_provider(), "deepseek-v4-flash")
    certification = tmp_path / "certifications.json"
    certification.write_text(json.dumps({
        "protocol_version": "persona-certifications-v1",
        "certifications": [{
            "model_fingerprint": fingerprint,
            "profile_version": manifest["profile_version"],
            "compiler_version": manifest["compiler_version"],
            "compiled_hashes": {"focused_work": compiled_hash},
            "sampling_profile": {"temperature": 0.0},
            "status": "certified",
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(persona_v2, "CERTIFICATIONS_PATH", certification)

    result = persona_v2.compile_for_request(
        legacy_prompt=persona.PERSONA_PROMPT, mode="focused_work", style=None,
        provider=_provider(), model="deepseek-v4-flash", rollout_mode="active",
    )
    other_mode = persona_v2.compile_for_request(
        legacy_prompt=persona.PERSONA_PROMPT, mode="companionship", style=None,
        provider=_provider(), model="deepseek-v4-flash", rollout_mode="active",
    )
    assert result.selected_v2 and result.prompt == candidate
    assert not other_mode.selected_v2


def test_resource_corruption_falls_back_without_exposing_prompt(tmp_path, monkeypatch) -> None:
    broken = tmp_path / "v2"
    broken.mkdir()
    (broken / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(persona_v2, "PROFILE_DIR", broken)
    monkeypatch.setattr(persona_v2, "MANIFEST_PATH", broken / "manifest.json")

    result = persona_v2.compile_for_request(
        legacy_prompt=persona.PERSONA_PROMPT, mode="companionship", style=None,
        provider=_provider(), model="deepseek-v4-flash", rollout_mode="active",
    )
    meta = result.public_meta()
    assert result.prompt == persona.PERSONA_PROMPT
    assert result.fallback_reason == "persona_resource_invalid"
    assert "prompt" not in json.dumps(meta).casefold()
