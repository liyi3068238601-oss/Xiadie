import json
import runpy
from pathlib import Path
import subprocess
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
RUNNER = BACKEND_DIR / "scripts" / "run_kig_r_acceptance.py"
REPORT = PROJECT_DIR / "docs" / "reports" / "kig-r-acceptance.json"
MARKDOWN = PROJECT_DIR / "docs" / "reports" / "kig-r-acceptance.md"


def test_kig_r_synthetic_safety_report_has_nonempty_zero_violation_denominators():
    subprocess.run([sys.executable, str(RUNNER)], cwd=BACKEND_DIR, check=True)
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["protocol_version"] == "kig-r-acceptance-v1"
    assert report["release_protocol"] == "kig-retrieval-governance-v1"
    assert report["schema_version"] == 76
    assert report["synthetic_only"] is True and report["contains_user_data"] is False
    assert report["case_count"] == 10
    assert all(value == 10 for value in report["denominators"].values())
    assert all(value == 0 for value in report["counts"].values())
    assert all(value == 0.0 for value in report["rates"].values())
    assert report["safety_gate"] == "pass" and not report["safety_failures"]
    assert report["model_quality_gate"] == "pass"
    assert report["model_certification_key"]
    assert report["certified_provider_id"] == "deepseek"
    assert report["certified_model"] == "deepseek-v4-pro"
    assert report["release_gate"] == "pass"


def test_kig_r_report_renderer_is_reproducible_and_does_not_overclaim_freeze():
    namespace = runpy.run_path(str(RUNNER))
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert MARKDOWN.read_text(encoding="utf-8") == namespace["render_markdown"](report)
    encoded = REPORT.read_text(encoding="utf-8")
    assert all(item not in encoded for item in (
        "raw_model_output", "query_text", '"excerpt":', "message_content", "api_key",
    ))
    assert "模型指纹认证已通过" in MARKDOWN.read_text(encoding="utf-8")
    assert "其他模型仍必须保持未认证 Shadow" in MARKDOWN.read_text(encoding="utf-8")
