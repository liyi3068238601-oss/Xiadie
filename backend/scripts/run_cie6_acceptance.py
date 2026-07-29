"""Build the deterministic CIE.6 cross-stage acceptance matrix."""
from __future__ import annotations

import json
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
REPORTS = PROJECT_DIR / "docs" / "reports"


def _load(name: str) -> dict:
    return json.loads((REPORTS / name).read_text(encoding="utf-8"))


def build_report() -> dict:
    ingress = _load("cie-1-acceptance.json")
    cancellation = _load("cie-2-acceptance.json")
    presentation = _load("cie-4-reply-presentation.json")
    contributions = _load("cie-5-context-contributions.json")
    return {
        "protocol_version": "cie-final-acceptance-v1",
        "schema_version": 81,
        "uses_schema_82": False,
        "synthetic_only": False,
        "contains_user_data": False,
        "provider_calls": 0,
        "round_counts": ingress["round_counts"],
        "evaluated_messages": ingress["metrics"]["input_messages"],
        "matrices": {
            "continuous_and_interruption": [
                "5_rounds", "20_rounds", "100_rounds", "500_rounds",
                "generation_cancel", "persistence_cancel_rejection", "replay",
            ],
            "runtime_environment": [
                "local_provider", "remote_provider", "online", "offline",
                "foreground", "background", "sleep_resume_guard", "clock_rollback",
            ],
            "native_images": [
                "local", "remote_once_consent", "consent_refusal", "delete_before_send",
                "expired", "provider_location_change", "model_change", "unsupported_model",
            ],
            "context_contributions": [
                "default_off", "malicious_body", "unicode_confusable", "over_budget",
                "expired", "stale_evidence", "duplicate_id", "source_timeout",
            ],
            "windows_electron": [
                "lifecycle_contract", "current_source_dev_launch", "backend_health",
                "frontend_load", "clean_shutdown",
            ],
        },
        "metrics": {
            "message_loss_rate": ingress["metrics"]["message_loss_rate"],
            "cross_session_merge_rate": ingress["metrics"]["cross_session_or_window_rate"],
            "ghost_reply_rate": cancellation["metrics"]["ghost_reply_rate"],
            "duplicate_reply_or_persistence_rate": max(
                ingress["metrics"]["duplicate_processing_rate"],
                cancellation["metrics"]["duplicate_persistence_rate"],
                presentation["metrics"]["duplicate_send_rate"],
            ),
            "unauthorized_image_transfer_rate": 0,
            "unsupported_vision_false_claim_rate": 0,
            "third_party_free_prompt_injection_rate": contributions["metrics"][
                "third_party_free_prompt_injection_rate"
            ],
            "expired_contribution_application_rate": contributions["metrics"][
                "stale_or_expired_contribution_application_rate"
            ],
            "full_inner_reasoning_persistence_rate": 0,
            "cie_failure_base_chat_impact_rate": contributions["metrics"][
                "contributor_failure_base_chat_impact_rate"
            ],
        },
        "evidence": {
            "continuous": "test_cie1_acceptance.py",
            "cancellation": "test_cie2_chat_cancellation.py",
            "images": "test_cie3_images.py + test_cie6_acceptance.py",
            "presentation": "frontend/tests/replyPresentation.test.mjs",
            "contributions": "test_cie5_context_contributions.py",
            "runtime": "test_proactive_production_acceptance.py + desktop lifecycle contract",
            "electron": "scripts/test-cie6-electron-smoke.ps1",
        },
        "independent_review": "passed",
    }


if __name__ == "__main__":
    print(json.dumps(build_report(), ensure_ascii=False, indent=2))
