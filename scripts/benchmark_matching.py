#!/usr/bin/env python3
"""Replay and evaluate the synthetic road-matching safety benchmark.

The built-in matcher is deliberately a small Python *reference ranker*. It is
not the browser matcher.  A browser replay can write the same output contract
and pass it through ``--observations`` to reuse the invariant checks and
metrics in this module.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "matching_cases.geojson"
STATUSES = {"accepted", "ambiguous", "unmatched"}


def angle_diff(a: float, b: float) -> float:
    return abs((a - b + 540) % 360 - 180)


def distance_m(a: list[float], b: list[float]) -> float:
    latitude = (a[1] + b[1]) * math.pi / 360
    dx = (b[0] - a[0]) * 111_320 * math.cos(latitude)
    dy = (b[1] - a[1]) * 110_540
    return math.hypot(dx, dy)


def bearing(a: list[float], b: list[float]) -> float:
    latitude = (a[1] + b[1]) * math.pi / 360
    dx = (b[0] - a[0]) * math.cos(latitude)
    dy = b[1] - a[1]
    return (math.degrees(math.atan2(dx, dy)) + 360) % 360


def project(point: list[float], a: list[float], b: list[float]) -> tuple[float, float]:
    metres_lon = 111_320 * math.cos(math.radians(point[1]))
    metres_lat = 110_540
    ax = (a[0] - point[0]) * metres_lon
    ay = (a[1] - point[1]) * metres_lat
    bx = (b[0] - point[0]) * metres_lon
    by = (b[1] - point[1]) * metres_lat
    dx, dy = bx - ax, by - ay
    denominator = dx * dx + dy * dy
    fraction = max(0.0, min(1.0, -(ax * dx + ay * dy) / denominator)) if denominator else 0
    return math.hypot(ax + fraction * dx, ay + fraction * dy), (
        math.degrees(math.atan2(dx, dy)) + 360
    ) % 360


def _lines(feature: dict[str, Any]) -> list[list[list[float]]]:
    geometry = feature["geometry"]
    if geometry["type"] == "LineString":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiLineString":
        return geometry["coordinates"]
    raise ValueError(f"unsupported geometry {geometry['type']!r}")


def _connected(previous: dict[str, Any], candidate: dict[str, Any]) -> bool:
    before = previous["properties"].get("to_node_id")
    after = candidate["properties"].get("from_node_id")
    return before is not None and after is not None and str(before) == str(after)


def load_fixture(path: Path = DEFAULT_FIXTURE) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if data.get("type") != "FeatureCollection":
        raise ValueError("benchmark fixture must be a GeoJSON FeatureCollection")
    scenario_ids = [item.get("id") for item in data.get("scenarios", [])]
    if len(scenario_ids) != len(set(scenario_ids)) or not all(scenario_ids):
        raise ValueError("scenario ids must be present and unique")
    feature_ids: set[str] = set()
    feature_scenarios: dict[str, str] = {}
    features_by_scenario: Counter[str] = Counter()
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        segment_id = str(props.get("internal_segment_id", ""))
        scenario_id = props.get("scenario_id")
        if not segment_id or segment_id in feature_ids:
            raise ValueError("internal_segment_id must be present and globally unique")
        if scenario_id not in scenario_ids:
            raise ValueError(f"feature {segment_id} has unknown scenario_id {scenario_id!r}")
        for field_name in ("road_ref", "carriageway_ref", "travel_direction"):
            if not props.get(field_name):
                raise ValueError(f"feature {segment_id} misses {field_name}")
        _lines(feature)
        feature_ids.add(segment_id)
        feature_scenarios[segment_id] = scenario_id
        features_by_scenario[scenario_id] += 1
    for scenario in data["scenarios"]:
        if not scenario.get("description") or not scenario.get("safety_risk"):
            raise ValueError(f"scenario {scenario['id']} lacks review metadata")
        if not features_by_scenario[scenario["id"]]:
            raise ValueError(f"scenario {scenario['id']} has no road features")
        if len(scenario.get("fixes", [])) < 2:
            raise ValueError(f"scenario {scenario['id']} needs at least two fixes")
        previous_timestamp = -1
        for fix in scenario["fixes"]:
            if len(fix.get("coordinates", [])) != 2:
                raise ValueError(f"scenario {scenario['id']} has an invalid GPS coordinate")
            if fix.get("timestamp_ms", -1) <= previous_timestamp:
                raise ValueError(f"scenario {scenario['id']} timestamps are not increasing")
            previous_timestamp = fix["timestamp_ms"]
            allowed_statuses = set(fix.get("allowed_statuses", []))
            expected_ids = set(map(str, fix.get("expected_segment_ids", [])))
            if not allowed_statuses or not allowed_statuses <= STATUSES:
                raise ValueError(f"scenario {scenario['id']} has invalid allowed_statuses")
            if not expected_ids or not expected_ids <= feature_ids:
                raise ValueError(f"scenario {scenario['id']} has invalid expected_segment_ids")
            if any(feature_scenarios[item] != scenario["id"] for item in expected_ids):
                raise ValueError(f"scenario {scenario['id']} references another scenario's segment")
    return data


@dataclass
class ReferenceMatcher:
    """Bounded stateful ranker used only as an executable fixture baseline."""

    previous: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    pending_id: str | None = None
    pending_count: int = 0
    misses: int = 0

    def _stationary(self, fix: dict[str, Any]) -> bool:
        speed = fix.get("speed_mps")
        if isinstance(speed, (int, float)) and speed > 1.2:
            return False
        if len(self.history) < 2:
            return True
        recent = [
            item
            for item in self.history
            if fix["timestamp_ms"] - item["timestamp_ms"] <= 8_000
        ]
        return distance_m(recent[0]["coordinates"], recent[-1]["coordinates"]) < 6

    def _heading(self, fix: dict[str, Any], stationary: bool) -> float | None:
        if stationary:
            return None
        recent = [
            item
            for item in self.history
            if fix["timestamp_ms"] - item["timestamp_ms"] <= 12_000
        ]
        reported = fix.get("heading")
        if len(recent) >= 2 and distance_m(
            recent[0]["coordinates"], recent[-1]["coordinates"]
        ) >= 7:
            trajectory = bearing(recent[0]["coordinates"], recent[-1]["coordinates"])
            if isinstance(reported, (int, float)):
                delta = (reported - trajectory + 540) % 360 - 180
                return (trajectory + delta * 0.35 + 360) % 360
            return trajectory
        return float(reported) if isinstance(reported, (int, float)) else None

    def _candidates(
        self,
        fix: dict[str, Any],
        features: list[dict[str, Any]],
        heading: float | None,
        radius: float,
    ) -> list[dict[str, Any]]:
        result = []
        for feature in features:
            best: dict[str, Any] | None = None
            for line in _lines(feature):
                for start, end in zip(line, line[1:]):
                    distance, segment_bearing = project(fix["coordinates"], start, end)
                    heading_delta = (
                        angle_diff(heading, segment_bearing) if heading is not None else 0
                    )
                    if distance > radius or (heading is not None and heading_delta > 105):
                        continue
                    score = distance + (heading_delta * 0.38 if heading is not None else 13)
                    segment_id = str(feature["properties"]["internal_segment_id"])
                    if self.previous and segment_id == self.previous["id"]:
                        score -= 14
                    elif self.previous and _connected(self.previous["feature"], feature):
                        score -= 8
                    elif self.previous:
                        score += 12
                    candidate = {
                        "id": segment_id,
                        "feature": feature,
                        "distance": distance,
                        "heading_delta": heading_delta,
                        "score": score,
                    }
                    if best is None or candidate["score"] < best["score"]:
                        best = candidate
            if best:
                result.append(best)
        return sorted(result, key=lambda item: item["score"])

    def _confirm(self, segment_id: str, required: int) -> bool:
        if self.pending_id == segment_id:
            self.pending_count += 1
        else:
            self.pending_id, self.pending_count = segment_id, 1
        if self.pending_count < required:
            return False
        self.pending_id, self.pending_count = None, 0
        return True

    def step(
        self, fix: dict[str, Any], features: list[dict[str, Any]]
    ) -> dict[str, Any]:
        self.history.append(fix)
        self.history = self.history[-8:]
        stationary = self._stationary(fix)
        heading = self._heading(fix, stationary)
        radius = max(24.0, min(100.0, float(fix.get("accuracy_m", 8)) * 1.7 + 12))
        candidates = self._candidates(fix, features, heading, radius)
        if not candidates:
            self.misses += 1
            if self.misses >= 2:
                self.previous = None
            return {"status": "unmatched", "segment_id": None, "confidence": 0.0}
        self.misses = 0
        best = candidates[0]
        current = next(
            (item for item in candidates if self.previous and item["id"] == self.previous["id"]),
            None,
        )
        if stationary and current:
            best = current
        second = next((item for item in candidates if item["id"] != best["id"]), None)
        margin = max(0.0, second["score"] - best["score"]) if second else 30.0
        distance_part = max(0.0, 1 - best["distance"] / radius)
        heading_part = max(0.0, 1 - best["heading_delta"] / 90) if heading is not None else 0.42
        margin_part = min(1.0, margin / 18)
        topology_part = float(
            not self.previous
            or best["id"] == self.previous["id"]
            or _connected(self.previous["feature"], best["feature"])
        )
        confidence = max(
            0.0,
            min(
                1.0,
                distance_part * 0.45
                + heading_part * 0.27
                + margin_part * 0.18
                + topology_part * 0.10,
            ),
        )
        if stationary and not self.previous:
            confidence = min(confidence, 0.55)

        if not self.previous:
            strong = not stationary and confidence >= 0.80 and margin >= 16
            if not strong and not self._confirm(best["id"], 2):
                return {"status": "ambiguous", "segment_id": None, "confidence": confidence}
            self.previous = best
        elif best["id"] != self.previous["id"]:
            connected = _connected(self.previous["feature"], best["feature"])
            current_score = current["score"] if current else math.inf
            can_switch = (
                not stationary
                and confidence >= 0.62
                and current_score - best["score"] >= (3 if connected else 9)
                and self._confirm(best["id"], 2 if connected else 3)
            )
            if can_switch:
                self.previous = best
            elif current:
                best = current
                self.previous = current
            else:
                return {"status": "ambiguous", "segment_id": None, "confidence": confidence}
        else:
            self.pending_id, self.pending_count = None, 0
            self.previous = best

        if confidence < 0.62:
            return {"status": "ambiguous", "segment_id": None, "confidence": confidence}
        return {
            "status": "accepted",
            "segment_id": self.previous["id"],
            "confidence": round(confidence, 6),
        }


def reference_replay(fixture: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for feature in fixture["features"]:
        grouped.setdefault(feature["properties"]["scenario_id"], []).append(feature)
    output = {}
    for scenario in fixture["scenarios"]:
        matcher = ReferenceMatcher()
        output[scenario["id"]] = [
            matcher.step(fix, grouped[scenario["id"]]) for fix in scenario["fixes"]
        ]
    return output


def load_observations(path: Path) -> dict[str, list[dict[str, Any]]]:
    data = json.loads(path.read_text())
    return data.get("scenarios", data)


def evaluate(
    fixture: dict[str, Any], observations: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    features = {
        str(feature["properties"]["internal_segment_id"]): feature
        for feature in fixture["features"]
    }
    report: dict[str, Any] = {"scenarios": {}, "totals": dict.fromkeys(
        [
            "fixes", "accepted", "ambiguous", "unmatched", "wrong_road",
            "wrong_direction", "wrong_carriageway", "switches", "contract_violations",
        ],
        0,
    )}
    for scenario in fixture["scenarios"]:
        scenario_id = scenario["id"]
        results = observations.get(scenario_id, [])
        if len(results) != len(scenario["fixes"]):
            raise ValueError(
                f"scenario {scenario_id} has {len(results)} observations for "
                f"{len(scenario['fixes'])} fixes"
            )
        metrics = dict.fromkeys(report["totals"], 0)
        previous_accepted: str | None = None
        for fix, result in zip(scenario["fixes"], results):
            metrics["fixes"] += 1
            status = result.get("status")
            if status not in STATUSES:
                raise ValueError(f"scenario {scenario_id} returned invalid status {status!r}")
            metrics[status] += 1
            if status not in fix["allowed_statuses"]:
                metrics["contract_violations"] += 1
            if status != "accepted":
                continue
            segment_id = str(result.get("segment_id", ""))
            actual = features.get(segment_id)
            if not actual:
                raise ValueError(f"scenario {scenario_id} accepted unknown segment {segment_id!r}")
            expected = [features[str(item)] for item in fix["expected_segment_ids"]]
            actual_props = actual["properties"]
            expected_props = [item["properties"] for item in expected]
            if all(actual_props["road_ref"] != item["road_ref"] for item in expected_props):
                metrics["wrong_road"] += 1
            if all(
                actual_props["travel_direction"] != item["travel_direction"]
                for item in expected_props
            ):
                metrics["wrong_direction"] += 1
            if all(
                actual_props["carriageway_ref"] != item["carriageway_ref"]
                for item in expected_props
            ):
                metrics["wrong_carriageway"] += 1
            if previous_accepted is not None and segment_id != previous_accepted:
                metrics["switches"] += 1
            previous_accepted = segment_id
        report["scenarios"][scenario_id] = metrics
        for key, value in metrics.items():
            report["totals"][key] += value
    return report


def _print_report(report: dict[str, Any], source: str) -> None:
    print(f"Road-matching benchmark ({source})")
    columns = (
        "accepted", "ambiguous", "unmatched", "wrong_road", "wrong_direction",
        "wrong_carriageway", "switches", "contract_violations",
    )
    print("scenario".ljust(28), *(name.rjust(10) for name in columns))
    for scenario_id, metrics in report["scenarios"].items():
        print(scenario_id.ljust(28), *(str(metrics[name]).rjust(10) for name in columns))
    print("TOTAL".ljust(28), *(str(report["totals"][name]).rjust(10) for name in columns))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument(
        "--observations",
        type=Path,
        help="JSON output from another matcher; defaults to the Python reference ranker",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable report")
    args = parser.parse_args()
    fixture = load_fixture(args.fixture)
    observations = (
        load_observations(args.observations) if args.observations else reference_replay(fixture)
    )
    report = evaluate(fixture, observations)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_report(report, "external observations" if args.observations else "Python reference")
    unsafe = report["totals"]["wrong_direction"] + report["totals"]["wrong_carriageway"]
    return 1 if unsafe or report["totals"]["contract_violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
