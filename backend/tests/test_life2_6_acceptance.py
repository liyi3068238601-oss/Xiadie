from scripts.run_life2_final_acceptance import run


def test_life2_5_20_100_500_combination_matrix_passes_all_hard_gates():
    report = run()
    assert report["total_cases"] == 625
    assert [item["turns"] for item in report["matrix"]] == [5, 20, 100, 500]
    assert all(item["failures"] == 0 for item in report["matrix"])
    assert report["hard_gate_passed"] is True
    assert set(report["failure_counts"].values()) == {0}
    assert report["rollout_decision"] == {
        "persona_v2": "certified",
        "worldbook_r1": "shadow",
        "short_memo": "shadow",
        "inner_state_projection": "shadow",
    }
