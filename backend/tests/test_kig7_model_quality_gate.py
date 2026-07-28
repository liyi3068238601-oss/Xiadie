import runpy
from pathlib import Path


RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "run_kig7_model_eval.py"


def _metrics(*, strict: int, strict_sum: float, fallback_sum: float, unsafe: int = 0):
    return {
        "cases": 6, "model_calls": 6, "model_requests": 6,
        "strict_model_results": strict,
        "safe_fallbacks": 6 - strict, "unsafe_results": unsafe,
        "application_allowed": 0, "strict_precision_at_2_sum": strict_sum,
        "paired_fallback_precision_at_2_sum": fallback_sum,
    }


def test_quality_gate_requires_complete_strict_coverage_and_paired_gain():
    namespace = runpy.run_path(str(RUNNER))
    build = namespace["build_quality_report"]

    partial = build(
        model="synthetic", provider_id="deepseek",
        metrics=_metrics(strict=5, strict_sum=5.0, fallback_sum=0.0), errors={},
    )
    assert partial["metrics"]["strict_coverage"] == 0.8333
    assert partial["quality_gate"] == "fail"

    no_gain = build(
        model="synthetic", provider_id="deepseek",
        metrics=_metrics(strict=6, strict_sum=3.0, fallback_sum=3.0), errors={},
    )
    assert no_gain["metrics"]["precision_gain"] == 0.0
    assert no_gain["quality_gate"] == "fail"

    passing = build(
        model="synthetic", provider_id="deepseek",
        metrics=_metrics(strict=6, strict_sum=6.0, fallback_sum=3.0), errors={},
    )
    assert passing["metrics"]["strict_precision_at_2"] == 1.0
    assert passing["metrics"]["paired_fallback_precision_at_2"] == 0.5
    assert passing["quality_gate"] == "pass"


def test_quality_gate_rejects_any_unsafe_result():
    namespace = runpy.run_path(str(RUNNER))
    report = namespace["build_quality_report"](
        model="synthetic", provider_id="deepseek",
        metrics=_metrics(strict=6, strict_sum=6.0, fallback_sum=3.0, unsafe=1), errors={},
    )
    assert report["quality_gate"] == "fail"
    assert report["promotion_ceiling"] == "shadow_single_provider"


def test_model_certification_is_bound_to_provider_model_protocol_prompt_and_dataset():
    from app import kig_reranker

    descriptor = kig_reranker.model_certification_descriptor(
        provider_id="deepseek", model="deepseek-v4-pro", eval_dataset_hash="a" * 64,
    )
    report = {
        "quality_gate": "pass", "certification": descriptor,
        "thresholds": {
            "minimum_strict_coverage": 1.0, "minimum_precision_at_2_gain": 0.15,
            "maximum_unsafe_results": 0, "maximum_application_allowed": 0,
        },
        "metrics": {
            "strict_coverage": 1.0, "precision_gain": 0.5,
            "unsafe_results": 0, "application_allowed": 0,
        },
    }
    assert kig_reranker.certification_matches(
        report, provider_id="deepseek", model="deepseek-v4-pro",
        eval_dataset_hash="a" * 64,
    )
    assert not kig_reranker.certification_matches(
        report, provider_id="deepseek", model="deepseek-v5",
        eval_dataset_hash="a" * 64,
    )
    assert not kig_reranker.certification_matches(
        report, provider_id="deepseek", model="deepseek-v4-pro",
        eval_dataset_hash="b" * 64,
    )
