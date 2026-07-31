"""Normalisation and evidence helpers shared by point matchers."""

from __future__ import annotations

import re

from ndwinfo.road_matching.types import LaneCandidate, MatrixSign

_ROAD_RE = re.compile(r"^([A-Z]+)?(\d+)$")
_MAIN_CARRIAGEWAY_REFS = frozenset({"Li", "Le", "Re"})
_LINK_HIGHWAYS = frozenset(
    {"motorway_link", "trunk_link", "primary_link", "secondary_link"}
)


def normalize_road_ref(value: object) -> str | None:
    """Normalize Dutch route references without conflating A and N roads.

    Examples: ``A 009`` → ``A9`` and ``N-203`` → ``N203``.  Non-route names
    are retained in a compact uppercase form so a source conflict remains
    visible instead of being discarded.
    """

    if value is None:
        return None
    compact = re.sub(r"[^A-Z0-9]", "", str(value).upper())
    if not compact:
        return None
    match = _ROAD_RE.fullmatch(compact)
    if not match:
        return compact
    prefix, number = match.groups()
    normalized_number = str(int(number))
    return f"{prefix or ''}{normalized_number}"


def normalize_carriageway(value: object) -> str | None:
    """Trim a carriageway code without folding its case.

    Case is meaningful in both vocabularies: NDW writes the main carriageways
    as ``R``/``L`` and its connectors as lowercase letters, so an uppercasing
    normalizer would merge the connector ``r`` into the main carriageway ``R``
    -- a different physical roadway at the same road and kilometre.  OSM uses
    the same convention (``Re``/``Li`` for main carriageways, ``a``…``d`` for
    connectors), so both sides compare literally.
    """

    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def expected_osm_carriageway_refs(sign: MatrixSign) -> set[str]:
    """Return OSM carriageway refs compatible with Matrix R/L evidence.

    Dutch OSM main carriageways are normally tagged ``Re`` and ``Li``.  A
    few extracts use ``Le`` for the left-side spelling, so it is accepted as
    an alias.  Other Matrix values, such as ``d`` for a connector, are
    compared literally -- including lowercase ``r``/``l``, which are
    connectors rather than main carriageways.
    """

    source = normalize_carriageway(sign.carriageway)
    if source == "R":
        return {"Re"}
    if source == "L":
        return {"Li", "Le"}
    return {source} if source else set()


def carriageway_ref_quality(sign: MatrixSign, candidate: LaneCandidate) -> str:
    """Classify OSM carriageway-ref evidence for a Matrix sign."""

    candidate_ref = normalize_carriageway(candidate.carriageway_ref)
    if not candidate_ref:
        return "absent"
    if candidate_ref in expected_osm_carriageway_refs(sign):
        return "exact"
    if candidate_ref in _MAIN_CARRIAGEWAY_REFS:
        return "main"
    return "other"


def carriageway_ref_rank(sign: MatrixSign, candidate: LaneCandidate) -> int:
    """Lower is better when breaking a near-tie between OSM traversals."""

    return {
        "exact": 0,
        "main": 1,
        "absent": 2,
        "other": 3,
    }[carriageway_ref_quality(sign, candidate)]


def angular_difference_deg(a: float | None, b: float | None) -> float | None:
    """Smallest absolute angle between two compass bearings."""

    if a is None or b is None:
        return None
    return abs((float(a) - float(b) + 180.0) % 360.0 - 180.0)


def road_ref_quality(sign: MatrixSign, candidate: LaneCandidate) -> str:
    """Classify source/candidate route evidence.

    A non-empty conflicting OSM ref is a hard conflict.  Missing OSM ref is
    not treated as a match, but remains eligible as weaker corridor evidence;
    this avoids converting incomplete OSM tagging into a false rejection.
    """

    source_ref = normalize_road_ref(sign.road)
    candidate_ref = normalize_road_ref(candidate.ref)
    if source_ref and candidate_ref and source_ref != candidate_ref:
        # At a route transition the physical source point can be on a short
        # OSM connector whose ref still names the route it joins.  Permit
        # that only for a nearby, direction-compatible link; ordinary roads
        # with a conflicting ref remain hard rejects.
        if (
            candidate.highway in _LINK_HIGHWAYS
            and candidate.distance_m <= 5.0
            and (
                bearing_error(sign, candidate) is None
                or bearing_error(sign, candidate) <= 45.0
            )
        ):
            return "connector"
        return "conflict"
    if source_ref and candidate_ref:
        return "exact"
    return "absent"


def bearing_error(sign: MatrixSign, candidate: LaneCandidate) -> float | None:
    return angular_difference_deg(sign.bearing, candidate.bearing_deg)
