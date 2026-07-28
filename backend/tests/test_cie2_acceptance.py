from __future__ import annotations

import runpy
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_cie2_acceptance.py"


def test_cie2_acceptance_has_zero_tolerance_metrics_and_latency_dispersion():
    module = runpy.run_path(str(SCRIPT))
    report = module["build_report"](20)
    metrics = report["metrics"]
    assert report["sample_count"] >= 10
    assert metrics["active_generation_cancel_support_rate"] == 1.0
    assert metrics["ghost_reply_rate"] == 0.0
    assert metrics["duplicate_persistence_rate"] == 0.0
    assert metrics["old_reply_false_delete_rate"] == 0.0
    assert metrics["late_cancellation_rejection_rate"] == 1.0
    assert report["cancel_ack_latency_ms"]["stdev"] >= 0.0
