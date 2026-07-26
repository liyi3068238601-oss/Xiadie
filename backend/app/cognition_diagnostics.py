"""CDS.13 body-free diagnostic view and aggregate health summary."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from . import cognitive_decision as cds
from . import cognition_calibration, cognition_settings

DIAGNOSTIC_VERSION = "cognition-diagnostics-v2"


def read(*, decision_kind: str | None = None, limit: int = 100) -> dict[str, Any]:
    base = cds.diagnostics(decision_kind=decision_kind, limit=limit)
    groups: dict[str, list[dict]] = defaultdict(list)
    for run in base["runs"]:
        groups[run["decision_kind"]].append(run)
    summaries = []
    for kind in sorted(groups):
        runs = groups[kind]
        latencies = sorted(
            int(run["latency_ms"]) for run in runs if run["latency_ms"] is not None
        )
        errors = Counter(
            str(run["error_code"]) for run in runs if run.get("error_code")
        )
        summaries.append({
            "decision_kind": kind,
            "run_count": len(runs),
            "fallback_count": sum(bool(run["fallback_used"]) for run in runs),
            "latency_ms_median": (
                latencies[len(latencies) // 2] if latencies else None
            ),
            "latency_ms_max": max(latencies) if latencies else None,
            "error_codes": dict(sorted(errors.items())),
        })
    return {
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "protocol_version": base["protocol_version"],
        "registry_version": base["registry_version"],
        "settings": cognition_settings.get_settings(),
        "summaries": summaries,
        "runs": base["runs"],
        "events": base["events"],
        "calibration_profiles": cognition_calibration.list_profiles(),
        "privacy": {
            "body_persisted": False, "prompt_persisted": False,
            "raw_output_persisted": False, "candidate_ids_exposed": False,
        },
    }
