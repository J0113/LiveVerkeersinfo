"""Enrich fixed traffic sensors with a VILD-oriented travel bearing.

``tmc_direction`` is relative to the VILD POS_OFF/NEG_OFF chain.  VILD line
coordinate order is not authoritative, so a neighbouring TMC point first
establishes which direction along the local line component is positive.  The
sensor's own projection then supplies a local tangent rather than a whole-line
start/end bearing.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass

from pyproj import Transformer
from shapely import wkt as shapely_wkt
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import transform
from sqlalchemy import func, select

from ndwinfo.models import MeasurementCharacteristic, MeasurementSite, VildLine, VildPoint, VildTmc

logger = logging.getLogger(__name__)

_WGS84_TO_RD = Transformer.from_crs(4326, 28992, always_xy=True)
TANGENT_HALF_SPAN_M = 20.0
MAX_TMC_POINT_DISTANCE_M = 50.0
COMPONENT_TIE_M = 0.5


@dataclass(frozen=True)
class DirectionResult:
    bearing: float | None
    derived_carriageway: str | None
    conflict: bool | None


def _parts(geometry) -> list[LineString]:
    if isinstance(geometry, LineString):
        return [geometry]
    if isinstance(geometry, MultiLineString):
        return [part for part in geometry.geoms if not part.is_empty]
    return []


def _select_component(geometry, current: Point, neighbour: Point) -> LineString | None:
    scored = sorted(
        (
            (part.distance(current) + part.distance(neighbour), part)
            for part in _parts(geometry)
            if part.distance(current) <= MAX_TMC_POINT_DISTANCE_M
            and part.distance(neighbour) <= MAX_TMC_POINT_DISTANCE_M
        ),
        key=lambda item: item[0],
    )
    if not scored:
        return None
    if len(scored) > 1 and scored[1][0] - scored[0][0] <= COMPONENT_TIE_M:
        return None
    return scored[0][1]


def _local_bearing(line: LineString, point: Point, coordinate_sign: int) -> float | None:
    if line.length <= 0:
        return None
    position = line.project(point)
    start = max(0.0, position - TANGENT_HALF_SPAN_M)
    end = min(line.length, position + TANGENT_HALF_SPAN_M)
    if end - start < 0.5:
        return None
    a = line.interpolate(start)
    b = line.interpolate(end)
    if coordinate_sign < 0:
        a, b = b, a
    dx, dy = b.x - a.x, b.y - a.y
    if math.hypot(dx, dy) < 0.01:
        return None
    return math.degrees(math.atan2(dx, dy)) % 360


def derive_direction(
    *,
    line,
    tmc_point: Point,
    neighbour_point: Point,
    neighbour_is_positive: bool,
    sensor_point: Point,
    tmc_direction: str | None,
    hecto_dir: int | None,
    explicit_carriageway: str | None,
) -> DirectionResult:
    """Return travel bearing and provenance-safe derived R/L for one site.

    Inputs are expected in a metric CRS. ``neighbour_is_positive`` says the
    neighbour was reached through POS_OFF; otherwise it is the NEG_OFF node.
    """
    derived = None
    if tmc_direction in {"positive", "negative"} and hecto_dir in {-1, 1}:
        direction_sign = 1 if tmc_direction == "positive" else -1
        derived = "R" if direction_sign == hecto_dir else "L"
    conflict = (
        explicit_carriageway != derived
        if explicit_carriageway in {"R", "L"} and derived in {"R", "L"}
        else None
    )

    component = _select_component(line, tmc_point, neighbour_point)
    if component is None or tmc_direction not in {"positive", "negative"}:
        return DirectionResult(None, derived, conflict)

    current_pos = component.project(tmc_point)
    neighbour_pos = component.project(neighbour_point)
    delta = neighbour_pos - current_pos
    if abs(delta) < 0.5:
        return DirectionResult(None, derived, conflict)

    # POS neighbour means current→neighbour is positive; NEG means
    # neighbour→current is positive.
    coordinate_sign = 1 if (delta > 0) == neighbour_is_positive else -1
    bearing = _local_bearing(component, sensor_point, coordinate_sign)
    if bearing is not None and tmc_direction == "negative":
        bearing = (bearing + 180) % 360
    return DirectionResult(round(bearing, 1) if bearing is not None else None, derived, conflict)


def _load_geometry(session, model) -> dict[int, object]:
    geometries: dict[int, object] = {}
    for identifier, wkt_text in session.execute(
        select(model.id, func.ST_AsText(model.geom)).where(model.geom.isnot(None))
    ).all():
        try:
            geometry = shapely_wkt.loads(wkt_text)
            geometries[int(identifier)] = transform(_WGS84_TO_RD.transform, geometry)
        except (TypeError, ValueError):
            continue
    return geometries


def rebuild_speed_site_directions(session) -> int:
    """Recompute VILD direction fields for fixed speed/flow measurement sites."""
    chain = {
        row.loc_nr: row
        for row in session.execute(
            select(
                VildTmc.loc_nr,
                VildTmc.lin_ref,
                VildTmc.pos_off,
                VildTmc.neg_off,
                VildTmc.hecto_dir,
            )
        ).all()
    }
    if not chain:
        return 0

    lines = _load_geometry(session, VildLine)
    points = _load_geometry(session, VildPoint)
    rows = session.execute(
        select(
            MeasurementSite.id,
            MeasurementSite.tmc_primary,
            MeasurementSite.tmc_direction,
            MeasurementSite.carriageway,
            func.ST_AsText(MeasurementSite.geom).label("geom_wkt"),
        )
        .join(
            MeasurementCharacteristic,
            MeasurementCharacteristic.site_id == MeasurementSite.id,
        )
        .where(
            MeasurementSite.geom.isnot(None),
            MeasurementSite.tmc_primary.isnot(None),
            MeasurementCharacteristic.value_type.in_(["TrafficSpeed", "TrafficFlow"]),
        )
        .distinct()
    ).all()

    updates: list[dict] = []
    for row in rows:
        node = chain.get(row.tmc_primary)
        result = DirectionResult(None, None, None)
        if node is not None and row.tmc_primary in points:
            neighbour = None
            neighbour_is_positive = True
            if node.pos_off in points and chain.get(node.pos_off, None) is not None:
                candidate = chain[node.pos_off]
                if candidate.lin_ref == node.lin_ref:
                    neighbour = points[node.pos_off]
            if (
                neighbour is None
                and node.neg_off in points
                and chain.get(node.neg_off, None) is not None
            ):
                candidate = chain[node.neg_off]
                if candidate.lin_ref == node.lin_ref:
                    neighbour = points[node.neg_off]
                    neighbour_is_positive = False
            try:
                sensor = transform(_WGS84_TO_RD.transform, shapely_wkt.loads(row.geom_wkt))
            except (TypeError, ValueError):
                sensor = None
            line = lines.get(node.lin_ref)
            if line is None and neighbour is not None:
                # Some LIN_REF values have no vild_line feature in the WGS84
                # package. Consecutive same-line TMC points still provide a
                # conservative local segment and an independently oriented
                # tangent instead of dropping otherwise complete direction.
                line = LineString([points[row.tmc_primary], neighbour])
            if line is not None and neighbour is not None and sensor is not None:
                result = derive_direction(
                    line=line,
                    tmc_point=points[row.tmc_primary],
                    neighbour_point=neighbour,
                    neighbour_is_positive=neighbour_is_positive,
                    sensor_point=sensor,
                    tmc_direction=row.tmc_direction,
                    hecto_dir=node.hecto_dir,
                    explicit_carriageway=row.carriageway,
                )
            else:
                derived = None
                if row.tmc_direction in {"positive", "negative"} and node.hecto_dir in {-1, 1}:
                    sign = 1 if row.tmc_direction == "positive" else -1
                    derived = "R" if sign == node.hecto_dir else "L"
                conflict = (
                    row.carriageway != derived
                    if row.carriageway in {"R", "L"} and derived in {"R", "L"}
                    else None
                )
                result = DirectionResult(None, derived, conflict)

        updates.append(
            {
                "id": row.id,
                "vild_bearing": result.bearing,
                "vild_carriageway": result.derived_carriageway,
                "vild_carriageway_source": (
                    "vild_hecto_dir+tmc_direction"
                    if result.derived_carriageway is not None
                    else None
                ),
                "carriageway_direction_conflict": result.conflict,
            }
        )

    for start in range(0, len(updates), 1000):
        session.bulk_update_mappings(MeasurementSite, updates[start:start + 1000])
    session.flush()
    logger.info("VILD sensor direction: rebuilt %d fixed sites", len(updates))
    return len(updates)


@dataclass(frozen=True)
class EffectiveRoadInput:
    id: str
    road: str | None
    carriageway: str | None
    vild_carriageway: str | None
    vild_road_number: str | None
    side: str | None
    tmc_direction: str | None
    coords: tuple[float, float] | None  # (lon, lat) rounded to ~1m, or None if no geom


def compute_effective_road(
    inputs: list[EffectiveRoadInput],
) -> dict[str, tuple[str | None, str | None, str | None]]:
    """Pure resolution: explicit > VILD-derived > co-located-inherited.

    Precedence: explicit ``road``/``carriageway``, else VILD-derived
    (``vild_road_number`` from the site's VILD TMC chain point,
    ``vild_carriageway`` as already computed by
    :func:`rebuild_speed_site_directions`), else inherited from another site
    at the exact same coordinates/side/direction — the same physical gantry
    measured by a second system (e.g. MONICA next to a MONIBAS aggregate)
    that happens to carry the road/carriageway metadata this one lacks.
    Inherit only when every resolved sibling at that position agrees.

    Returns ``{id: (effective_road, effective_carriageway, effective_source)}``.
    ``effective_source`` tracks *road* provenance only (explicit /
    vild_road_number / inherited); carriageway provenance already has its own
    ``carriageway_source`` / ``vild_carriageway_source`` columns.
    """
    resolved: dict[str, list] = {}
    for item in inputs:
        if item.road:
            road, road_source = item.road, "explicit"
        elif item.vild_road_number:
            road, road_source = item.vild_road_number, "vild_road_number"
        else:
            road, road_source = None, None
        carriageway = item.carriageway or item.vild_carriageway
        resolved[item.id] = [road, carriageway, road_source]

    groups: dict[tuple, list[str]] = defaultdict(list)
    for item in inputs:
        if item.coords is None:
            continue
        groups[(item.coords, item.side, item.tmc_direction)].append(item.id)

    for ids in groups.values():
        if len(ids) < 2:
            continue
        candidates = {
            (resolved[i][0], resolved[i][1]) for i in ids if resolved[i][0] is not None
        }
        if len(candidates) != 1:
            continue
        road, carriageway = next(iter(candidates))
        for i in ids:
            if resolved[i][0] is None:
                resolved[i][0], resolved[i][1], resolved[i][2] = road, carriageway, "inherited"

    return {site_id: tuple(values) for site_id, values in resolved.items()}


def resolve_effective_road(session) -> int:
    """Materialize effective_road/effective_carriageway/effective_source.

    Loads sites (+ VILD road_number via tmc_primary), delegates the actual
    resolution to the pure :func:`compute_effective_road`, and writes the
    result back. Must run after :func:`rebuild_speed_site_directions` in the
    same session — it depends on that job's freshly written
    ``vild_carriageway``.
    """
    rows = session.execute(
        select(
            MeasurementSite.id,
            MeasurementSite.road,
            MeasurementSite.carriageway,
            MeasurementSite.vild_carriageway,
            MeasurementSite.side,
            MeasurementSite.tmc_direction,
            VildTmc.road_number.label("vild_road_number"),
            func.ST_AsText(MeasurementSite.geom).label("geom_wkt"),
        ).outerjoin(VildTmc, MeasurementSite.tmc_primary == VildTmc.loc_nr)
    ).all()

    inputs = []
    for row in rows:
        coords = None
        if row.geom_wkt:
            try:
                point = shapely_wkt.loads(row.geom_wkt)
                coords = (round(point.x, 5), round(point.y, 5))
            except (TypeError, ValueError):
                coords = None
        inputs.append(
            EffectiveRoadInput(
                id=row.id,
                road=row.road,
                carriageway=row.carriageway,
                vild_carriageway=row.vild_carriageway,
                vild_road_number=row.vild_road_number,
                side=row.side,
                tmc_direction=row.tmc_direction,
                coords=coords,
            )
        )

    resolved = compute_effective_road(inputs)
    updates = [
        {
            "id": site_id,
            "effective_road": road,
            "effective_carriageway": carriageway,
            "effective_source": source,
        }
        for site_id, (road, carriageway, source) in resolved.items()
    ]
    for start in range(0, len(updates), 1000):
        session.bulk_update_mappings(MeasurementSite, updates[start:start + 1000])
    session.flush()
    logger.info("Effective road/carriageway: resolved %d sites", len(updates))
    return len(updates)
