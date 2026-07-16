import copy
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "benchmark_matching.py"
SPEC = importlib.util.spec_from_file_location("benchmark_matching", SCRIPT)
benchmark = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


EXPECTED_SCENARIOS = {
    "dual_carriageway_opposite",
    "parallel_service_road",
    "ramp_fork",
    "grade_separated_crossing",
    "stationary_heading_drift",
    "moving_gps_drift",
}


def test_fixture_is_synthetic_reviewable_and_covers_required_risks():
    fixture = benchmark.load_fixture()

    assert fixture["benchmark_version"] == 1
    assert "Synthetic" in fixture["privacy"]
    assert {scenario["id"] for scenario in fixture["scenarios"]} == EXPECTED_SCENARIOS
    assert all(scenario["description"] for scenario in fixture["scenarios"])
    assert all(scenario["safety_risk"] for scenario in fixture["scenarios"])

    grade_features = [
        feature
        for feature in fixture["features"]
        if feature["properties"]["scenario_id"] == "grade_separated_crossing"
    ]
    assert {feature["properties"]["layer"] for feature in grade_features} == {0, 1}
    assert not {
        feature["properties"]["from_node_id"] for feature in grade_features
    } & {feature["properties"]["to_node_id"] for feature in grade_features}


def test_reference_replay_is_fail_closed_for_direction_and_carriageway():
    fixture = benchmark.load_fixture()
    observations = benchmark.reference_replay(fixture)
    report = benchmark.evaluate(fixture, observations)

    assert report["totals"]["fixes"] == 28
    assert report["totals"]["accepted"] + report["totals"]["ambiguous"] == 28
    assert report["totals"]["unmatched"] == 0
    assert report["totals"]["wrong_road"] == 0
    assert report["totals"]["wrong_direction"] == 0
    assert report["totals"]["wrong_carriageway"] == 0
    assert report["totals"]["contract_violations"] == 0


def test_evaluator_counts_an_opposite_carriageway_as_two_hard_safety_errors():
    fixture = benchmark.load_fixture()
    observations = benchmark.reference_replay(fixture)
    unsafe = copy.deepcopy(observations)
    unsafe["dual_carriageway_opposite"][0] = {
        "status": "accepted",
        "segment_id": "dual-a4-south",
        "confidence": 0.99,
    }

    report = benchmark.evaluate(fixture, unsafe)

    assert report["totals"]["wrong_road"] == 0
    assert report["totals"]["wrong_direction"] == 1
    assert report["totals"]["wrong_carriageway"] == 1


def test_evaluator_distinguishes_a_same_direction_wrong_parallel_road():
    fixture = benchmark.load_fixture()
    observations = benchmark.reference_replay(fixture)
    unsafe = copy.deepcopy(observations)
    unsafe["parallel_service_road"][0] = {
        "status": "accepted",
        "segment_id": "parallel-service",
        "confidence": 0.99,
    }

    report = benchmark.evaluate(fixture, unsafe)

    assert report["totals"]["wrong_road"] == 1
    assert report["totals"]["wrong_direction"] == 0
    assert report["totals"]["wrong_carriageway"] == 1


def test_ambiguous_and_unmatched_outputs_are_never_classified_as_wrong_matches():
    fixture = benchmark.load_fixture()
    observations = benchmark.reference_replay(fixture)
    conservative = copy.deepcopy(observations)
    for scenario in fixture["scenarios"]:
        scenario_id = scenario["id"]
        conservative[scenario_id] = [
            {
                "status": "unmatched" if "unmatched" in fix["allowed_statuses"] else "ambiguous",
                "segment_id": None,
                "confidence": 0.0,
            }
            for fix in scenario["fixes"]
        ]

    report = benchmark.evaluate(fixture, conservative)

    assert report["totals"]["accepted"] == 0
    assert report["totals"]["unmatched"] == 2
    assert report["totals"]["wrong_road"] == 0
    assert report["totals"]["wrong_direction"] == 0
    assert report["totals"]["wrong_carriageway"] == 0
    assert report["totals"]["contract_violations"] == 0
