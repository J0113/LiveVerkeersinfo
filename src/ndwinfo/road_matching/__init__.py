"""Source-neutral road matching primitives.

The first slice is deliberately independent from the ORM.  Source adapters
turn database rows into the small dataclasses in :mod:`road_matching.types`,
which keeps the matcher testable without a PostGIS database.
"""

from ndwinfo.road_matching.points import (
    dedupe_matrix_signs,
    group_matrix_signs,
    match_matrix_gantry,
)
from ndwinfo.road_matching.types import (
    LaneCandidate,
    MatrixGantry,
    MatrixSign,
    MatrixSignMatch,
)

__all__ = [
    "LaneCandidate",
    "MatrixGantry",
    "MatrixSign",
    "MatrixSignMatch",
    "dedupe_matrix_signs",
    "group_matrix_signs",
    "match_matrix_gantry",
]
