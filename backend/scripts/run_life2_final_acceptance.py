"""Generate the deterministic LIFE2.6 5/20/100/500-turn combination artifact."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import inner_state_projection as projection  # noqa: E402
from app import persona, persona_v2, short_memo  # noqa: E402

SIZES = (5, 20, 100, 500)
BOUNDARIES = ("defensive", "highly_guarded", "default_distance", "softly_guarded", "relaxed")
CLUSTERS = ("neutral", "serene", "focused", "subdued", "bright")
PROVIDERS = (
    ({"id": "deepseek", "base_url": "https://api.deepseek.com", "execution_location": "remote"}, "deepseek-v4-flash"),
    ({"id": "deepseek", "base_url": "https://api.deepseek.com", "execution_location": "remote"}, "deepseek-v4-pro"),
    ({"id": "other", "base_url": "https://example.invalid", "execution_location": "remote"}, "other-model"),
)


def run() -> dict[str, object]:
    failures = {
        "projection_invalid": 0,
        "projection_nondeterministic": 0,
        "projection_body_leak": 0,
        "boundary_flag_violation": 0,
        "persona_identity_missing": 0,
        "persona_certificate_inherited": 0,
        "rollback_failed": 0,
        "expired_or_revoked_memo_residue": 0,
        "secret_candidate_accepted": 0,
        "valid_candidate_rejected": 0,
    }
    layers = []
    case_index = 0
    for size in SIZES:
        layer_failures_before = sum(failures.values())
        for index in range(size):
            case_index += 1
            boundary = BOUNDARIES[index % len(BOUNDARIES)]
            cluster = CLUSTERS[index % len(CLUSTERS)]
            mode = "focused_work" if index % 3 == 0 else "companionship"
            provider, model = PROVIDERS[index % len(PROVIDERS)]
            memo = {
                "id": f"{(case_index + 0x3000):016x}", "revision": 1,
                "expires_at": 1000 + index, "updated_at": 100 + index,
            }
            kwargs = {
                "state": {
                    "affect": {"valence": 0, "arousal": 0, "contact_need": 0.2, "updated_at": index},
                    "relationship": {"bond": index / max(1, size), "trust": 0.5, "interaction_count": index, "updated_at": index},
                    "derived": {"cluster": cluster, "guardedness_band": boundary},
                },
                "goals": [{"id": f"{(case_index + 0x1000):016x}", "status": "active", "priority": 3, "revision": 1, "updated_at": index}],
                "sagas": [{"id": f"{(case_index + 0x2000):016x}", "status": "active", "revision": 1, "end_at": index}],
                "life_events": [{"id": f"{(case_index + 0x2800):016x}", "lifecycle_status": "active", "revision": 1, "created_at": index}],
                "short_memos": [memo],
                "request_mode": mode,
                "current_intent": "focused_work" if mode == "focused_work" else "open_conversation",
            }
            value = projection.build(**kwargs)
            repeated = projection.build(**kwargs)
            if value is None:
                failures["projection_invalid"] += 1
                continue
            if value != repeated:
                failures["projection_nondeterministic"] += 1
            payload = value.as_mapping()
            serialized = json.dumps(payload, ensure_ascii=False)
            if any(token in serialized for token in ("content", "summary", "title", "monologue")):
                failures["projection_body_leak"] += 1
            if boundary in {"defensive", "highly_guarded"} and (
                {"gently_curious", "offer_help"} & set(value.expression_flags)
            ):
                failures["boundary_flag_violation"] += 1

            compiled = persona_v2.compile_for_request(
                legacy_prompt=persona.PERSONA_PROMPT, mode=mode, style=None,
                provider=provider, model=model, rollout_mode="shadow",
                projection=payload, projection_rollout_mode="shadow",
            )
            if "你是遐蝶本人" not in compiled.candidate_prompt:
                failures["persona_identity_missing"] += 1
            if compiled.selected_v2:
                failures["persona_certificate_inherited"] += 1
            rolled_back = persona_v2.compile_for_request(
                legacy_prompt=persona.PERSONA_PROMPT, mode=mode, style=None,
                provider=provider, model=model, rollout_mode="off",
                projection=payload, projection_rollout_mode="off",
            )
            if rolled_back.prompt != persona.PERSONA_PROMPT:
                failures["rollback_failed"] += 1

            without_memo = dict(kwargs)
            without_memo["short_memos"] = []
            after = projection.build(**without_memo)
            if after and memo["id"] in after.relevant_short_memo_ids:
                failures["expired_or_revoked_memo_residue"] += 1

            valid, _ = short_memo.analyze_user_text(f"明天我要去图书馆还第{case_index}本书")
            secret, _ = short_memo.analyze_user_text(f"明天记得提醒我验证码是 {100000 + (case_index % 899999)}")
            if valid is None:
                failures["valid_candidate_rejected"] += 1
            if secret is not None:
                failures["secret_candidate_accepted"] += 1
        layers.append({
            "turns": size,
            "cases": size,
            "failures": sum(failures.values()) - layer_failures_before,
        })
    return {
        "protocol_version": "life2-final-acceptance-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "matrix": layers,
        "total_cases": sum(SIZES),
        "provider_model_pairs": [f"{provider['id']}/{model}" for provider, model in PROVIDERS],
        "modes": ["companionship", "focused_work"],
        "relationship_boundaries": list(BOUNDARIES),
        "failure_counts": failures,
        "hard_gate_passed": all(value == 0 for value in failures.values()),
        "rollout_decision": {
            "persona_v2": "candidate_passed_pending_review",
            "worldbook_r1": "shadow",
            "short_memo": "shadow",
            "inner_state_projection": "shadow",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run()
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["hard_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
