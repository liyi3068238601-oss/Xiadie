from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app import context_budget, db, persona, persona_v2


def _provider() -> dict[str, object]:
    return {
        "id": "deepseek", "base_url": "https://api.deepseek.com",
        "execution_location": "remote",
    }


def _use_isolated_db(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(db, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(db, "DB_PATH", str(data_dir / "xiadie.db"))
    db.init_db()


def _certify_v22(tmp_path: Path, monkeypatch) -> None:
    hashes = {}
    for mode in persona_v2.MODES:
        prompt, _, _ = persona_v2.compile_candidate(
            mode=mode, profile_version=persona_v2.DEFAULT_PROFILE_VERSION,
        )
        hashes[mode] = hashlib.sha256(prompt.encode()).hexdigest()
    path = tmp_path / "v22-certifications.json"
    path.write_text(json.dumps({
        "protocol_version": "persona-certifications-v1",
        "certifications": [{
            "model_fingerprint": persona_v2.model_fingerprint(
                _provider(), "deepseek-v4-flash",
            ),
            "profile_version": persona_v2.DEFAULT_PROFILE_VERSION,
            "compiler_version": "persona-prompt-compiler-v1",
            "compiled_hashes": hashes,
            "sampling_profile": {"temperature": 0.0},
            "output_guard_protocol": "persona-natural-dialogue-guard-v2",
            "status": "certified",
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(persona_v2, "CERTIFICATIONS_PATH", path)


def test_v22_resources_and_certified_hashes_remain_unchanged():
    expected = {
        "companionship": "aff81f21baf25004d052748997d087c54e0396e56f7559f0303dc21c2b28561f",
        "focused_work": "0e6fd222be57ffa6fd3544a853638420466028946d1b7bac76f04d8cf3d1416e",
    }
    assert persona_v2.PROFILE_DIR.name == "v2_2"
    for mode, digest in expected.items():
        prompt, manifest, _ = persona_v2.compile_candidate(
            mode=mode, profile_version=persona_v2.DEFAULT_PROFILE_VERSION,
        )
        assert manifest["profile_version"] == "persona-profile-v2.2"
        assert hashlib.sha256(prompt.encode()).hexdigest() == digest


def test_v23_candidate_is_deterministic_bounded_and_keeps_modern_identity_contract():
    projection = {
        "protocol_version": "inner-state-projection-v1",
        "source_snapshot_hash": "a" * 64,
        "relationship_boundary": "softly_guarded",
        "expression_flags": ["calm", "warm", "gently_curious", "offer_help"],
    }
    for mode in persona_v2.MODES:
        first, manifest, hashes = persona_v2.compile_candidate(
            mode=mode, profile_version=persona_v2.CANDIDATE_PROFILE_VERSION,
        )
        second, _, second_hashes = persona_v2.compile_candidate(
            mode=mode, profile_version=persona_v2.CANDIDATE_PROFILE_VERSION,
        )
        projected, _, _ = persona_v2.compile_candidate(
            mode=mode, projection=projection,
            profile_version=persona_v2.CANDIDATE_PROFILE_VERSION,
        )
        assert first == second and hashes == second_hashes
        assert manifest["profile_version"] == "persona-profile-v2.3"
        assert "你是遐蝶本人" in first
        assert "曾是奥赫玛的入殓师" in first
        assert "通用理解、推理、知识和任务能力" in first
        assert "不主动自称 AI、语言模型、通用助手" in first
        assert "如今你存在于《如我所书》中" not in first
        assert context_budget.estimate_tokens(first) <= persona_v2.PERSONA_TOKEN_LIMIT
        assert context_budget.estimate_tokens(projected) <= persona_v2.PERSONA_TOKEN_LIMIT
    work, _, _ = persona_v2.compile_candidate(
        mode="focused_work", profile_version=persona_v2.CANDIDATE_PROFILE_VERSION,
    )
    assert "不因遐蝶的来历缩小领域" in work
    assert "不切换成冷淡客服" in work


def test_internal_selector_defaults_v22_is_idempotent_and_rejects_unknown(
    monkeypatch, tmp_path,
):
    _use_isolated_db(monkeypatch, tmp_path)
    assert persona_v2.selected_profile_version() == persona_v2.DEFAULT_PROFILE_VERSION
    assert persona_v2.set_profile_version(persona_v2.DEFAULT_PROFILE_VERSION) == "persona-profile-v2.2"
    assert persona_v2.set_profile_version(persona_v2.CANDIDATE_PROFILE_VERSION) == "persona-profile-v2.3"
    assert persona_v2.set_profile_version(persona_v2.CANDIDATE_PROFILE_VERSION) == "persona-profile-v2.3"
    db.set_setting(persona_v2.PROFILE_SELECTOR_KEY, "persona-profile-v99")
    assert persona_v2.selected_profile_version() == persona_v2.DEFAULT_PROFILE_VERSION
    with pytest.raises(persona_v2.PersonaResourceError, match="persona_profile_invalid"):
        persona_v2.set_profile_version("persona-profile-v99")


def test_uncertified_v23_selector_falls_back_to_certified_v22(tmp_path, monkeypatch):
    _certify_v22(tmp_path, monkeypatch)
    result = persona_v2.compile_for_request(
        legacy_prompt=persona.PERSONA_PROMPT, mode="companionship", style=None,
        provider=_provider(), model="deepseek-v4-flash", rollout_mode="active",
        profile_version=persona_v2.CANDIDATE_PROFILE_VERSION,
    )
    v22, _, _ = persona_v2.compile_candidate(
        mode="companionship", profile_version=persona_v2.DEFAULT_PROFILE_VERSION,
    )
    assert result.selected_v2 and result.certified
    assert result.profile_version == persona_v2.DEFAULT_PROFILE_VERSION
    assert result.prompt == v22
    assert result.fallback_reason == "persona_profile_fallback:persona-profile-v2.3"


def test_broken_v23_resources_fall_back_v22_but_broken_v22_falls_back_legacy(
    tmp_path, monkeypatch,
):
    _certify_v22(tmp_path, monkeypatch)
    broken_v23 = tmp_path / "v2_3"
    broken_v23.mkdir()
    (broken_v23 / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setitem(
        persona_v2.PROFILE_DIRS, persona_v2.CANDIDATE_PROFILE_VERSION, broken_v23,
    )
    fallback = persona_v2.compile_for_request(
        legacy_prompt=persona.PERSONA_PROMPT, mode="focused_work", style=None,
        provider=_provider(), model="deepseek-v4-flash", rollout_mode="active",
        profile_version=persona_v2.CANDIDATE_PROFILE_VERSION,
    )
    assert fallback.selected_v2
    assert fallback.profile_version == persona_v2.DEFAULT_PROFILE_VERSION

    broken_v22 = tmp_path / "v2_2"
    broken_v22.mkdir()
    (broken_v22 / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(persona_v2, "PROFILE_DIR", broken_v22)
    monkeypatch.setattr(persona_v2, "MANIFEST_PATH", broken_v22 / "manifest.json")
    legacy = persona_v2.compile_for_request(
        legacy_prompt=persona.PERSONA_PROMPT, mode="focused_work", style=None,
        provider=_provider(), model="deepseek-v4-flash", rollout_mode="active",
        profile_version=persona_v2.CANDIDATE_PROFILE_VERSION,
    )
    assert legacy.prompt == persona.PERSONA_PROMPT
    assert legacy.profile_version == "legacy"
    assert not legacy.selected_v2


def test_v23_shadow_is_candidate_only_and_has_no_checked_in_certificate():
    result = persona_v2.compile_for_request(
        legacy_prompt=persona.PERSONA_PROMPT, mode="companionship", style=None,
        provider=_provider(), model="deepseek-v4-flash", rollout_mode="shadow",
        profile_version=persona_v2.CANDIDATE_PROFILE_VERSION,
    )
    assert result.prompt == persona.PERSONA_PROMPT
    assert result.candidate_prompt
    assert result.profile_version == persona_v2.CANDIDATE_PROFILE_VERSION
    assert not result.selected_v2 and not result.certified
    certificate_path = persona_v2.PROFILE_DIRS[
        persona_v2.CANDIDATE_PROFILE_VERSION
    ] / "certifications.json"
    assert json.loads(certificate_path.read_text(encoding="utf-8"))["certifications"] == []


def test_observer_summary_is_derived_from_one_explicit_profile_without_mixing():
    v22 = persona_v2.derive_observer_summary(
        fallback="fallback", profile_version=persona_v2.DEFAULT_PROFILE_VERSION,
    )
    v23 = persona_v2.derive_observer_summary(
        fallback="fallback", profile_version=persona_v2.CANDIDATE_PROFILE_VERSION,
    )
    assert "温柔、悲悯、安静、克制" in v22
    assert "温柔、悲悯、安静、克制" in v23
    assert "不默认是开拓者" in v22
    assert "不默认是开拓者" in v23
    assert "AI" not in v23 and "底层模型" not in v23
