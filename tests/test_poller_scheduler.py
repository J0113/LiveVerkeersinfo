from concurrent.futures import Future

from ndwinfo import poller
from ndwinfo.feeds import FEEDS
from ndwinfo.poller import _select_due_feeds


def _feed(name: str) -> dict:
    return next(feed for feed in FEEDS if feed["name"] == name)


def _select(
    names: list[str],
    *,
    idle_for_s: float,
    active_names: set[str] | None = None,
    workers: int = 3,
    bulk_limit: int = 1,
) -> list[str]:
    selected = _select_due_feeds(
        [_feed(name) for name in names],
        active_names=active_names or set(),
        idle_for_s=idle_for_s,
        max_workers=workers,
        bulk_max_inflight=bulk_limit,
        idle_timeout_s=300,
        maintenance_idle_s=900,
    )
    return [feed["name"] for feed in selected]


def test_active_mode_runs_only_realtime_in_explicit_priority_order():
    names = ["traveltime", "charging_geojson", "matrix_signs", "trafficspeed"]

    assert _select(names, idle_for_s=0) == [
        "trafficspeed",
        "matrix_signs",
        "traveltime",
    ]


def test_idle_mode_reserves_one_bounded_slot_for_background_work():
    names = [
        "charging_geojson",
        "measurement_site",
        "traveltime",
        "matrix_signs",
        "trafficspeed",
    ]

    assert _select(names, idle_for_s=301) == [
        "trafficspeed",
        "matrix_signs",
        "measurement_site",
    ]


def test_user_wake_does_not_start_overdue_bulk_feeds():
    overdue = [
        "measurement_site",
        "charging_geojson",
        "nwb_wegvakken",
        "trafficspeed",
    ]

    idle_selection = _select(overdue, idle_for_s=901)
    wake_selection = _select(overdue, idle_for_s=1)

    assert idle_selection == ["trafficspeed", "measurement_site"]
    assert wake_selection == ["trafficspeed"]


def test_maintenance_waits_for_extended_idle_period():
    overdue = ["nwb_wegvakken", "measurement_site"]

    assert _select(overdue, idle_for_s=301) == ["measurement_site"]
    # Measurement metadata remains the first bulk dependency by priority. Once
    # it is current, the maintenance import may use the same single bulk slot.
    assert _select(["nwb_wegvakken"], idle_for_s=901) == ["nwb_wegvakken"]


def test_inflight_bulk_work_cannot_queue_more_bulk_or_block_live_slots():
    overdue = ["charging_geojson", "matrix_signs", "trafficspeed"]

    assert _select(
        overdue,
        idle_for_s=901,
        active_names={"nwb_wegvakken"},
    ) == ["trafficspeed", "matrix_signs"]


def test_inflight_work_caps_submissions_to_actual_free_workers():
    overdue = ["traveltime", "matrix_signs", "trafficspeed"]

    assert _select(
        overdue,
        idle_for_s=0,
        active_names={"closures", "speed_limits"},
    ) == ["trafficspeed"]


def test_single_worker_still_prioritizes_live_speed_over_idle_bulk():
    assert _select(
        ["measurement_site", "trafficspeed"],
        idle_for_s=901,
        workers=1,
    ) == ["trafficspeed"]


def test_run_once_wait_drains_due_realtime_in_bounded_waves(monkeypatch):
    feeds = [
        {
            "name": f"live_{priority}",
            "cadence_s": 60,
            "schedule_class": "realtime",
            "priority": priority,
        }
        for priority in range(5)
    ]
    submitted: list[str] = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class ImmediateExecutor:
        def submit(self, _function, name):
            submitted.append(name)
            future = Future()
            future.set_result(None)
            return future

    monkeypatch.setattr(poller, "SessionLocal", FakeSession)
    monkeypatch.setattr(poller, "_ensure_current_binding_algorithms", lambda _session: None)
    monkeypatch.setattr(poller, "FEEDS", feeds)
    monkeypatch.setattr(poller, "INGESTERS", {feed["name"]: object() for feed in feeds})
    monkeypatch.setattr(poller, "_last_finished_per_feed", lambda _session: {})
    monkeypatch.setattr(poller, "_seconds_since_api_activity", lambda _session: 0)
    monkeypatch.setattr(poller, "_last_maintenance_at", poller.time.monotonic())
    monkeypatch.setattr(poller, "_executor", ImmediateExecutor())
    monkeypatch.setattr(poller, "_inflight", {})
    monkeypatch.setattr(poller.settings, "poller_max_workers", 2)

    poller.run_once(wait=True)

    assert submitted == ["live_0", "live_1", "live_2", "live_3", "live_4"]
