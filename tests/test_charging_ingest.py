from contextlib import contextmanager
from types import SimpleNamespace

from ndwinfo.ingest import charging


class FakeSession:
    def __init__(self):
        self.executions = []
        self.flushes = 0

    def execute(self, statement, parameters=None):
        captured = list(parameters) if isinstance(parameters, list) else parameters
        self.executions.append((statement, captured))

    def flush(self):
        self.flushes += 1


def test_charging_ingest_flushes_nationwide_data_in_bounded_batches(monkeypatch):
    @contextmanager
    def fake_open_feed(_path):
        yield object()

    def fake_parser(_file):
        for index in range(2_501):
            yield (
                {"id": f"cp-{index}", "geom": None},
                [{"cp_id": f"cp-{index}", "idx": 0, "available": index % 2}],
            )

    batch_sizes = []

    def fake_bulk_upsert(_session, _model, rows, _conflicts):
        batch_sizes.append(len(rows))
        return len(rows)

    monkeypatch.setattr(charging, "open_feed", fake_open_feed)
    monkeypatch.setattr(charging, "parse_charging_geojson", fake_parser)
    monkeypatch.setattr(charging, "bulk_upsert", fake_bulk_upsert)
    monkeypatch.setattr(charging, "wkt_geom", lambda value: value)

    session = FakeSession()
    count = charging.ChargingGeojsonIngester()._ingest(
        SimpleNamespace(path="ignored"), session
    )

    assert count == 2_501
    assert batch_sizes == [1_000, 1_000, 501]
    assert session.flushes == 3
    # One bounded delete and one bounded availability insert per charge-point batch.
    assert len(session.executions) == 6
    availability_batches = [params for _statement, params in session.executions if params]
    assert [len(batch) for batch in availability_batches] == [1_000, 1_000, 501]
