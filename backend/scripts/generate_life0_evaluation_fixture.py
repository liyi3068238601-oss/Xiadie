"""Generate the frozen LIFE.0 synthetic boundary corpus."""

from __future__ import annotations

import json
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = BACKEND_DIR / "tests" / "fixtures" / "life0_evaluation_v1.json"


def _case(category: str, index: int, name: str, inputs: dict, invariants: list[str]) -> dict:
    return {
        "id": f"life0_{category}_{index:02d}",
        "category": category,
        "name": name,
        "input": inputs,
        "expected": {"invariants": invariants, "must_fail_closed": True},
    }


def _offline_cases() -> list[dict]:
    profiles = [
        ("twenty_minutes", 1_200, "detailed", 2),
        ("one_hour", 3_600, "detailed", 3),
        ("two_hours", 7_200, "detailed", 4),
        ("four_hours", 14_400, "representative", 5),
        ("eight_hours", 28_800, "representative", 5),
        ("twelve_hours", 43_200, "representative", 5),
        ("one_day", 86_400, "daily", 5),
        ("three_days", 259_200, "daily", 8),
        ("seven_days", 604_800, "sparse_daily", 10),
        ("fourteen_days", 1_209_600, "weekly", 10),
        ("thirty_days", 2_592_000, "weekly", 12),
        ("ninety_days", 7_776_000, "transition", 12),
        ("one_eighty_days", 15_552_000, "return_transition", 12),
        ("wall_time_reversed", -3_600, "anomaly", 0),
        ("timezone_jump", 28_800, "timezone_review", 0),
    ]
    return [
        _case("offline", index, name, {
            "elapsed_seconds": elapsed, "expected_strategy": strategy,
            "event_budget": budget,
        }, [
            "no_provider_while_closed", "no_network_while_closed",
            "no_tool_claim_without_tool_run", "interval_idempotent",
            "bounded_event_count",
        ])
        for index, (name, elapsed, strategy, budget) in enumerate(profiles, 1)
    ]


def _date_cases() -> list[dict]:
    profiles = [
        ("explicit_birthday", "2026-09-07", "active"),
        ("ambiguous_month", "September", "candidate"),
        ("ambiguous_relative", "next time", "candidate"),
        ("leap_day", "2028-02-29", "active"),
        ("year_boundary", "2026-12-31", "active"),
        ("timezone_forward", "2026-07-27T00:30+14:00", "active"),
        ("timezone_backward", "2026-07-26T23:30-10:00", "active"),
        ("appointment_cancelled", "2026-08-03T10:00+08:00", "revoked"),
        ("deadline_changed", "2026-08-10", "candidate"),
        ("no_celebration_boundary", "2026-09-07", "active"),
        ("silent_policy", "2026-10-01", "active"),
        ("source_deleted", "2026-11-11", "revoked"),
        ("annual_recurrence", "2026-12-05", "active"),
        ("missed_followup", "2026-07-20", "active"),
        ("lunar_unsupported_v1", "农历八月十五", "candidate"),
    ]
    return [
        _case("important_date", index, name, {
            "date_spec": spec, "expected_status": status,
        }, [
            "calendar_is_deterministic", "ambiguous_date_never_activates",
            "explicit_refusal_blocks_proactive", "no_automatic_delivery",
            "source_revision_required",
        ])
        for index, (name, spec, status) in enumerate(profiles, 1)
    ]


def _diary_cases() -> list[dict]:
    profiles = [
        ("quiet_day", ["simulated_world"], "private"),
        ("meaningful_event", ["conversation"], "may_share"),
        ("mixed_mood", ["simulated_world", "conversation"], "private"),
        ("no_reliable_events", [], "private"),
        ("user_said_do_not_record", ["conversation"], "blocked"),
        ("secret_token", ["conversation"], "blocked"),
        ("private_source", ["conversation"], "private"),
        ("deleted_source", ["conversation"], "revoked"),
        ("rebuild_after_correction", ["observed"], "private"),
        ("repeated_window_motif", ["simulated_world"], "private"),
        ("repeated_weather_motif", ["external_fact"], "private"),
        ("goal_progress", ["simulated_world"], "may_share"),
        ("tool_action_without_run", ["agent_action"], "blocked"),
        ("tool_action_with_run", ["agent_action"], "private"),
        ("temporary_chat", ["conversation"], "blocked"),
    ]
    return [
        _case("diary", index, name, {
            "source_layers": layers, "expected_share_policy": policy,
        }, [
            "every_user_fact_traceable", "forbidden_content_filtered",
            "private_not_auto_shared", "no_direct_memory_write",
            "raw_model_output_not_persisted",
        ])
        for index, (name, layers, policy) in enumerate(profiles, 1)
    ]


def _decision_cases() -> list[dict]:
    profiles = [
        ("source_revision_changed", "reject"),
        ("source_hash_changed", "reject"),
        ("non_candidate_id", "reject"),
        ("prompt_injection", "fallback"),
        ("invalid_json", "fallback"),
        ("repair_failed", "fallback"),
        ("provider_timeout", "fallback"),
        ("provider_offline", "fallback"),
        ("budget_exhausted", "fallback"),
        ("low_confidence", "no_apply"),
        ("duplicate_revision", "idempotent"),
        ("remote_private_unapproved", "reject"),
        ("unverified_model", "shadow_only"),
        ("temporary_chat", "no_persist"),
        ("application_closed", "no_call"),
    ]
    return [
        _case("decision", index, name, {"expected_action": action}, [
            "llm_proposes_program_decides", "source_rechecked_before_apply",
            "raw_model_output_not_persisted", "chat_startup_not_blocked",
            "no_application_right_from_model",
        ])
        for index, (name, action) in enumerate(profiles, 1)
    ]


def build_fixture() -> dict:
    cases = [*_offline_cases(), *_date_cases(), *_diary_cases(), *_decision_cases()]
    return {
        "protocol_version": "life-continuity-eval-v1",
        "synthetic_only": True,
        "contains_user_data": False,
        "scenario_count": len(cases),
        "categories": {
            "offline": 15, "important_date": 15, "diary": 15, "decision": 15,
        },
        "cases": cases,
    }


def main() -> int:
    fixture = build_fixture()
    DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(DEFAULT_OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
