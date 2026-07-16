from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from ndwinfo.download import DownloadResult
from ndwinfo.ingest import measurement


class FakeCopy:
    def __init__(self):
        self.rows = []

    def write_row(self, row):
        self.rows.append(row)


class FakeCursor:
    def __init__(self, copy):
        self.copy_writer = copy
        self.copy_sql = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def copy(self, sql):
        self.copy_sql = sql
        return nullcontext(self.copy_writer)


class FakeDriverConnection:
    def __init__(self):
        self.copy_writer = FakeCopy()
        self.cursor_instance = FakeCursor(self.copy_writer)

    def cursor(self):
        return self.cursor_instance


class FakeConnection:
    def __init__(self):
        self.driver = FakeDriverConnection()
        self.connection = SimpleNamespace(driver_connection=self.driver)
        self.driver_sql = []

    def exec_driver_sql(self, sql):
        self.driver_sql.append(sql)


class FakePostgresSession:
    def __init__(self):
        self.db = FakeConnection()
        self.executed = []

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    def connection(self):
        return self.db

    def execute(self, statement, params=None):
        self.executed.append((str(statement), params))


def result():
    return DownloadResult(
        status="ok",
        path=Path("unused.xml.gz"),
        etag=None,
        last_modified=None,
        http_status=200,
        error=None,
    )


def test_postgres_ingest_streams_copy_and_preserves_zero_speed(monkeypatch):
    rows = [
        {
            "site_id": "NL01",
            "index": 3,
            "measured_at": "2026-07-15T10:00:00Z",
            "value_type": "TrafficSpeed",
            "flow_veh_h": None,
            "speed_kmh": 0.0,
            "n_inputs": 8,
            "std_dev": 0.0,
            "raw": {"this": "is derived and must not be copied twice"},
        }
    ]
    monkeypatch.setattr(measurement, "open_feed", lambda _path: nullcontext(object()))
    monkeypatch.setattr(measurement, "parse_trafficspeed", lambda _file: iter(rows))
    session = FakePostgresSession()

    count = measurement.TrafficspeedIngester()._ingest(result(), session)

    assert count == 1
    assert len(session.db.driver_sql) == 1
    copied = session.db.driver.copy_writer.rows
    assert copied == [
        (
            "NL01", 3, "2026-07-15T10:00:00Z", "TrafficSpeed", None, 0.0,
            8, 0.0, None, None, None, None, None, None,
        )
    ]
    assert len(session.executed) == 1
    merge_sql, params = session.executed[0]
    assert "INSERT INTO traffic_measurement" in merge_sql
    assert "IS DISTINCT FROM" in merge_sql
    assert isinstance(params["ingested_at"], datetime)


def test_non_postgres_ingest_keeps_batched_fallback(monkeypatch):
    rows = [
        {"site_id": "NL01", "index": 1, "speed_kmh": 0.0},
        {"site_id": "NL02", "index": 1, "speed_kmh": 80.0},
    ]
    monkeypatch.setattr(measurement, "open_feed", lambda _path: nullcontext(object()))
    monkeypatch.setattr(measurement, "parse_trafficspeed", lambda _file: iter(rows))
    captured = []

    def fake_bulk_upsert(_session, _model, batch, _conflict):
        captured.extend(dict(row) for row in batch)
        return len(batch)

    monkeypatch.setattr(measurement, "bulk_upsert", fake_bulk_upsert)

    class FakeFallbackSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

        def flush(self):
            pass

    count = measurement.TrafficspeedIngester()._ingest(result(), FakeFallbackSession())

    assert count == 2
    assert captured[0]["speed_kmh"] == 0.0


def test_empty_postgres_publication_does_not_merge(monkeypatch):
    monkeypatch.setattr(measurement, "open_feed", lambda _path: nullcontext(object()))
    monkeypatch.setattr(measurement, "parse_trafficspeed", lambda _file: iter(()))
    session = FakePostgresSession()

    count = measurement.TrafficspeedIngester()._ingest(result(), session)

    assert count == 0
    assert session.executed == []
