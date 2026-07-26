"""Run synthetic CDS.12 structured-output checks against configured real models.

No user data, prompt text, API key or raw model output is written to the report.
"""
from __future__ import annotations

import asyncio
import collections
import json
import statistics
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import cognitive_decision as cds  # noqa: E402
from app import cognition_runtime as runtime  # noqa: E402
from app import db, llm  # noqa: E402

REPORT_PATH = PROJECT_DIR / "docs" / "reports" / "cds-12-provider-consistency.json"
SAMPLE_COUNT = 6


def _configured_real_providers() -> list[dict]:
    conn = db.connect()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM providers WHERE enabled=1 AND id<>'mock' AND base_url<>'' "
            "AND api_key<>'' ORDER BY sort,id"
        ).fetchall()]
    finally:
        conn.close()


def _binding(provider: dict, model: str) -> runtime.ModelBinding:
    role = runtime.LogicalRole.REASONING
    location = str(provider.get("execution_location") or "unknown")
    location_revision = max(1, int(provider.get("location_revision") or 1))
    return runtime.ModelBinding(
        provider=provider, model_id=model, logical_role=role,
        revision=runtime._binding_revision(  # noqa: SLF001 - certification identity
            str(provider["id"]), model, role, location, location_revision,
        ),
    )


async def _evaluate_model(provider: dict, model: str) -> dict:
    binding = _binding(provider, model)
    probe_passed = await runtime.run_structured_probe(binding, "protocol_probe")
    outcomes: list[tuple] = []
    latencies: list[int] = []
    prompt_tokens = 0
    completion_tokens = 0
    error_codes: collections.Counter[str] = collections.Counter()
    valid = 0
    exact = 0
    for index in range(SAMPLE_COUNT):
        candidate_id = f"synthetic-{index}"
        payload = cds.ProtocolProbeInput(candidate_ids=(candidate_id,))
        messages = [{
            "role": "user",
            "content": (
                "Synthetic CDS consistency check with no user data. Return only exact JSON: "
                f'{{"action":"select","selected_ids":["{candidate_id}"],'
                '"reason_codes":["directly_relevant"],"confidence_band":"high"}}'
            ),
        }]
        try:
            completion = await llm.complete_json(
                provider, model, messages, timeout_seconds=30.0, temperature=0.0, top_p=1.0,
            )
            result, repaired = cds._decode_result_once(  # noqa: SLF001
                completion["text"], cds.ProtocolProbeResult,
            )
            cds.REGISTRY.get("protocol_probe").validator(payload, result)
            normalized = (
                result.action, result.selected_ids, result.reason_codes, result.confidence_band,
            )
            outcomes.append(normalized)
            valid += 1
            exact += int(not repaired and result.selected_ids == (candidate_id,))
            latencies.append(int(completion.get("latency_ms") or 0))
            prompt_tokens += int(completion.get("prompt_tokens") or 0)
            completion_tokens += int(completion.get("completion_tokens") or 0)
        except Exception as exc:  # report only stable error code/type, never body
            error_code = getattr(exc, "code", None) or type(exc).__name__
            error_codes[error_code] += 1
            outcomes.append(("error", error_code))
    return {
        "provider_id": provider["id"], "model_id": model,
        "structured_probe_passed": probe_passed,
        "samples": SAMPLE_COUNT, "valid": valid, "exact": exact,
        "valid_rate": valid / SAMPLE_COUNT, "exact_rate": exact / SAMPLE_COUNT,
        "latency_ms_median": int(statistics.median(latencies)) if latencies else None,
        "latency_ms_max": max(latencies) if latencies else None,
        "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
        "error_codes": dict(sorted(error_codes.items())),
        "outcomes": outcomes,
    }


async def run() -> dict:
    db.init_db()
    providers = _configured_real_providers()
    model_results = []
    for provider in providers:
        try:
            models = json.loads(provider.get("models") or "[]")
        except (TypeError, ValueError):
            models = []
        for model in [item for item in models if isinstance(item, str) and item][:2]:
            model_results.append(await _evaluate_model(provider, model))
    paired = []
    for left_index, left in enumerate(model_results):
        for right in model_results[left_index + 1:]:
            comparable = min(len(left["outcomes"]), len(right["outcomes"]))
            agreement = sum(
                left["outcomes"][index] == right["outcomes"][index]
                for index in range(comparable)
            )
            paired.append({
                "left": f'{left["provider_id"]}/{left["model_id"]}',
                "right": f'{right["provider_id"]}/{right["model_id"]}',
                "samples": comparable,
                "agreement_rate": agreement / comparable if comparable else 0.0,
            })
    real_provider_ids = sorted({item["provider_id"] for item in model_results})
    report = {
        "evaluation_version": "cds12-provider-consistency-v1",
        "synthetic_only": True, "contains_user_data": False,
        "raw_output_persisted": False,
        "configured_real_provider_ids": real_provider_ids,
        "configured_real_provider_count": len(real_provider_ids),
        "model_results": [{key: value for key, value in item.items() if key != "outcomes"}
                          for item in model_results],
        "paired_results": paired,
        "promotion_provider_gate_met": len(real_provider_ids) >= 2,
        "mode_decision": "remain_shadow",
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2))
