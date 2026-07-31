"""ORM-free types used by the road matching proof slices."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class MatrixSign:
    """The source evidence needed to match one Matrix sign."""

    uuid: str
    road: str | None
    carriageway: str | None
    lane: int | None
    km: float | None
    bearing: float | None
    state_timestamp: datetime | str | None = None
    has_value: bool = False
    lon: float | None = None
    lat: float | None = None


@dataclass(frozen=True)
class LaneCandidate:
    """One directed OSM lane candidate for a source point.

    ``bearing_deg`` is the local travel bearing of the lane at the projected
    source point.  ``position_fraction`` is measured on this lane's
    travel-ordered geometry.
    """

    lane_id: str
    road_id: int
    segment_id: str
    direction: str
    lane_nr: int
    lane_count: int
    ref: str | None
    highway: str | None
    distance_m: float
    bearing_deg: float | None
    position_fraction: float | None = None
    projected: tuple[float, float] | None = None
    carriageway_ref: str | None = None
    name: str | None = None

    @property
    def traversal_id(self) -> str:
        return f"{self.segment_id}@{self.direction}"


@dataclass(frozen=True)
class MatrixSignMatch:
    """Explainable result for one sign after gantry consensus."""

    uuid: str
    gantry_id: str
    status: str
    confidence: str | None
    method: str
    failure_reason: str | None
    candidate_count: int
    road_id: int | None = None
    segment_id: str | None = None
    direction: str | None = None
    anchor_lane_id: str | None = None
    applies_to_lane_id: str | None = None
    position_fraction: float | None = None
    matched_point: tuple[float, float] | None = None
    source_distance_m: float | None = None
    bearing_error_deg: float | None = None
    road_ref_quality: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-friendly representation for dry-run reports."""

        return {
            "uuid": self.uuid,
            "gantry_id": self.gantry_id,
            "status": self.status,
            "confidence": self.confidence,
            "method": self.method,
            "failure_reason": self.failure_reason,
            "candidate_count": self.candidate_count,
            "road_id": self.road_id,
            "segment_id": self.segment_id,
            "direction": self.direction,
            "anchor_lane_id": self.anchor_lane_id,
            "applies_to_lane_id": self.applies_to_lane_id,
            "position_fraction": self.position_fraction,
            "matched_point": list(self.matched_point) if self.matched_point else None,
            "source_distance_m": self.source_distance_m,
            "bearing_error_deg": self.bearing_error_deg,
            "road_ref_quality": self.road_ref_quality,
            "diagnostics": self.diagnostics,
        }


@dataclass(frozen=True)
class DripSign:
    """The source evidence needed to match one DRIP/VMS panel."""

    controller_id: str
    vms_index: int
    description: str | None
    physical_support: str | None
    bearing: float | None
    working_status: str | None = None
    display_text: str | None = None
    updated_at: datetime | str | None = None
    lon: float | None = None
    lat: float | None = None

    @property
    def source_key(self) -> str:
        # The shared point schema uses a canonical JSON composite key for
        # DRIPs.  `api/routers/signs.py` rebuilds this exact string in SQL to
        # join assignments back onto `drip`, so the separators are a contract.
        return json.dumps(
            [self.controller_id, self.vms_index],
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class DripSignMatch:
    """Explainable result for one DRIP after directed point matching."""

    source_key: str
    controller_id: str
    vms_index: int
    status: str
    confidence: str | None
    method: str
    failure_reason: str | None
    candidate_count: int
    road_id: int | None = None
    segment_id: str | None = None
    direction: str | None = None
    anchor_lane_id: str | None = None
    position_fraction: float | None = None
    matched_point: tuple[float, float] | None = None
    source_distance_m: float | None = None
    bearing_error_deg: float | None = None
    road_ref_quality: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_key": self.source_key,
            "controller_id": self.controller_id,
            "vms_index": self.vms_index,
            "status": self.status,
            "confidence": self.confidence,
            "method": self.method,
            "failure_reason": self.failure_reason,
            "candidate_count": self.candidate_count,
            "road_id": self.road_id,
            "segment_id": self.segment_id,
            "direction": self.direction,
            "anchor_lane_id": self.anchor_lane_id,
            "position_fraction": self.position_fraction,
            "matched_point": list(self.matched_point) if self.matched_point else None,
            "source_distance_m": self.source_distance_m,
            "bearing_error_deg": self.bearing_error_deg,
            "road_ref_quality": self.road_ref_quality,
            "diagnostics": self.diagnostics,
        }


@dataclass(frozen=True)
class MatrixGantry:
    """A physical gantry group after ghost de-duplication."""

    gantry_id: str
    key: tuple[str | None, str | None, float | None]
    signs: tuple[MatrixSign, ...]
