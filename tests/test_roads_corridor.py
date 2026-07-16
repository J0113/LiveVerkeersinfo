from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from ndwinfo.api.routers import roads


class _Rows:
    def all(self):
        return []


class _Db:
    def __init__(self):
        self.query = None

    def execute(self, query):
        self.query = query
        return _Rows()


def test_corridor_geography_cast_matches_expression_index(monkeypatch):
    """A bare SRID -1 cast makes PostgreSQL scan the complete road graph."""
    db = _Db()
    graph = SimpleNamespace(id=1, graph_version="graph-v1")
    monkeypatch.setattr(roads, "_active_graph", lambda _db: graph)
    monkeypatch.setattr(roads, "_road_response", lambda *_args, **_kwargs: None)

    roads.get_road_corridor(
        db=db,
        lon=4.6258,
        lat=52.3080,
        heading=45.0,
        accuracy_m=12.0,
        radius_m=None,
        lookahead_m=2500.0,
        limit=5000,
    )

    sql = str(
        db.query.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "ST_DWithin(osm_road_segment.geom::geography" in sql
    assert "geography(GEOMETRY,4326)" in sql
    assert "geography(GEOMETRY,-1)" not in sql
