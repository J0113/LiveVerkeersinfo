"""FastAPI dependencies: bbox parser, db session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Generator

from fastapi import Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ndwinfo.config import settings
from ndwinfo.db import get_session


@dataclass
class BBox:
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float


def _parse_bbox_str(bbox: str) -> BBox:
    try:
        parts = [float(x) for x in bbox.split(",")]
    except ValueError:
        raise HTTPException(400, "bbox: expected 4 comma-separated floats")
    if len(parts) != 4:
        raise HTTPException(400, "bbox: expected exactly 4 values")
    min_lon, min_lat, max_lon, max_lat = parts
    if min_lon >= max_lon or min_lat >= max_lat:
        raise HTTPException(400, "bbox: min values must be less than max values")
    area = (max_lon - min_lon) * (max_lat - min_lat)
    if area > settings.max_bbox_area:
        raise HTTPException(
            400,
            f"bbox area {area:.2f} deg² exceeds maximum {settings.max_bbox_area} deg²",
        )
    return BBox(min_lon, min_lat, max_lon, max_lat)


def parse_bbox(
    bbox: Annotated[str, Query(description="minLon,minLat,maxLon,maxLat")]
) -> BBox:
    return _parse_bbox_str(bbox)


def parse_bbox_optional(
    bbox: Annotated[
        str | None,
        Query(description="minLon,minLat,maxLon,maxLat — omit when another scope is given"),
    ] = None,
) -> BBox | None:
    return _parse_bbox_str(bbox) if bbox is not None else None


def get_db() -> Generator[Session, None, None]:
    yield from get_session()


BBoxDep = Annotated[BBox, Depends(parse_bbox)]
OptionalBBoxDep = Annotated[BBox | None, Depends(parse_bbox_optional)]
DbDep = Annotated[Session, Depends(get_db)]
