"""CIE.1 deterministic 5/20/100/500 acceptance report."""
from __future__ import annotations

import json
import runpy
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
REPORT_PATH = PROJECT_DIR / "docs" / "reports" / "cie-1-acceptance.json"
SCRIPT_PATH = PROJECT_DIR / "backend" / "scripts" / "run_cie1_acceptance.py"


def test_cie1_acceptance_covers_scale_matrix_with_zero_tolerance_metrics():
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    evaluated = runpy.run_path(str(SCRIPT_PATH))["evaluate"]()
    assert report == evaluated
    assert report["round_counts"] == [5, 20, 100, 500]
    assert report["metrics"]["input_messages"] == 625
    assert report["metrics"]["output_messages"] == 625
    for key, value in report["metrics"].items():
        if key.endswith("_rate"):
            assert value == 0.0
    assert report["schema_version"] == 80 and report["uses_schema_81"] is False
