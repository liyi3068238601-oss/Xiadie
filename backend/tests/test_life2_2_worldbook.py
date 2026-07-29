from __future__ import annotations

import hashlib
import json

from app import worldbook_r1


def _rewrite_payload(path, mutate) -> None:
    payload = json.loads(worldbook_r1.WORLD_BOOK_PATH.read_text(encoding="utf-8"))
    mutate(payload)
    canonical = json.dumps(payload["entries"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["entries_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_package_has_30_unique_hash_valid_non_resident_entries() -> None:
    payload = json.loads(worldbook_r1.WORLD_BOOK_PATH.read_text(encoding="utf-8"))
    assert payload["entry_count"] == len(payload["entries"]) == 30
    assert len({item["entry_id"] for item in payload["entries"]}) == 30
    assert not any(item["always_on"] for item in payload["entries"])
    ids = {item["entry_id"] for item in payload["entries"]}
    for item in payload["entries"]:
        digest = hashlib.sha256(item["body"].encode()).hexdigest()
        assert item["body_sha256"] == digest
        assert item["revision"] == f"r1-{digest[:16]}"
        assert set(item["related_entry_ids"]) <= ids


def test_alias_collisions_and_cycles_still_produce_stable_bounded_results() -> None:
    queries = ("冥河", "死亡", "奥赫玛", "遐蝶", "泰坦", "火种")
    for query in queries:
        first = worldbook_r1.retrieve_for_request(
            query, legacy_content="legacy", rollout_mode="shadow",
        )
        second = worldbook_r1.retrieve_for_request(
            query, legacy_content="legacy", rollout_mode="shadow",
        )
        assert first.candidate_entry_ids == second.candidate_entry_ids
        assert first.candidate_content == second.candidate_content
        assert len(first.candidate_entry_ids) == len(set(first.candidate_entry_ids)) <= 3
        assert len(first.candidate_content) <= 3600


def test_shadow_reports_candidates_but_keeps_legacy_content() -> None:
    recall = worldbook_r1.retrieve_for_request(
        "遐蝶和玻吕茜亚是什么关系", legacy_content="legacy", rollout_mode="shadow",
    )
    assert recall.content == "legacy"
    assert not recall.selected_r1
    assert recall.candidate_content
    assert 1 <= len(recall.candidate_entry_ids) <= 3
    assert not recall.entry_ids
    assert "body" not in json.dumps(recall.public_meta()).casefold()


def test_active_never_promotes_b_or_local_sources() -> None:
    recall = worldbook_r1.retrieve_for_request(
        "毛绒玩偶和诗歌", legacy_content="legacy", rollout_mode="active",
    )
    assert recall.content == "legacy"
    assert not recall.selected_r1
    assert recall.fallback_reason == "worldbook_no_verified_source"


def test_verified_source_can_activate_and_related_order_is_deterministic(tmp_path, monkeypatch) -> None:
    package = tmp_path / "worldbook.json"
    _rewrite_payload(package, lambda payload: [
        item.update(source_status="verified_a") for item in payload["entries"]
    ])
    monkeypatch.setattr(worldbook_r1, "WORLD_BOOK_PATH", package)
    worldbook_r1.clear_cache()
    first = worldbook_r1.retrieve_for_request(
        "玻吕茜亚", legacy_content="legacy", rollout_mode="active",
    )
    second = worldbook_r1.retrieve_for_request(
        "玻吕茜亚", legacy_content="legacy", rollout_mode="active",
    )
    assert first.selected_r1
    assert first.content == second.content
    assert first.entry_ids == second.entry_ids
    assert len(first.entry_ids) <= 3
    assert len(first.content) <= 3600


def test_corrupt_body_hash_fails_closed_to_legacy(tmp_path, monkeypatch) -> None:
    package = tmp_path / "worldbook.json"
    _rewrite_payload(package, lambda payload: payload["entries"][0].update(body="tampered"))
    monkeypatch.setattr(worldbook_r1, "WORLD_BOOK_PATH", package)
    worldbook_r1.clear_cache()
    recall = worldbook_r1.retrieve_for_request(
        "死亡之触", legacy_content="legacy", rollout_mode="active",
    )
    assert recall.content == "legacy"
    assert recall.fallback_reason == "worldbook_resource_invalid"


def test_query_does_not_inject_without_explicit_hit_or_map_user_identity() -> None:
    empty = worldbook_r1.retrieve_for_request(
        "今天天气不错", legacy_content="", rollout_mode="shadow",
    )
    identity = worldbook_r1.retrieve_for_request(
        "我是开拓者吗", legacy_content="", rollout_mode="shadow",
    )
    assert not empty.candidate_content
    assert "当前用户" in identity.candidate_content
    assert "开拓者" in identity.candidate_content
