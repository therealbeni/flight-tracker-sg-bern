"""Times are stored in UTC and shown in Swiss local time; a flying day is a local day."""

from datetime import date, datetime, timedelta, timezone

import pytest

from models import Flight, Glider
from timeutil import combine_local, fmt_duration, fmt_time, local_day_bounds

UTC = timezone.utc


def test_summer_time_is_shown_as_local_time():
    assert fmt_time(datetime(2026, 7, 1, 12, 5, tzinfo=UTC)) == "14:05"


def test_winter_time_is_shown_as_local_time():
    assert fmt_time(datetime(2026, 1, 15, 12, 5, tzinfo=UTC)) == "13:05"


def test_naive_datetimes_from_sqlite_are_treated_as_utc():
    assert fmt_time(datetime(2026, 7, 1, 12, 5)) == "14:05"


def test_local_day_starts_at_local_midnight():
    start, end = local_day_bounds(date(2026, 9, 29))
    assert start == datetime(2026, 9, 28, 22, 0, tzinfo=UTC)
    assert end == datetime(2026, 9, 29, 22, 0, tzinfo=UTC)


def test_day_with_clock_change_is_23_hours():
    start, end = local_day_bounds(date(2026, 3, 29))
    assert end - start == timedelta(hours=23)


def test_entered_local_time_becomes_utc():
    assert combine_local(date(2026, 9, 29), "14:05") == datetime(2026, 9, 29, 12, 5, tzinfo=UTC)
    assert combine_local(date(2026, 9, 29), "9.30") == datetime(2026, 9, 29, 7, 30, tzinfo=UTC)
    with pytest.raises(ValueError):
        combine_local(date(2026, 9, 29), "25:00")


def test_flight_time_is_hours_and_minutes():
    assert fmt_duration(65.4) == "1:05"
    assert fmt_duration(7) == "0:07"
    assert fmt_duration(None) == ""


def test_dashboard_shows_local_times_and_local_day(client, db_session, monkeypatch):
    import routers.dashboard as dashboard
    from test_flights import signup_and_login

    signup_and_login(client, "Alice Admin", "alice@example.com")
    glider = Glider(registration="HB-1811", ogn_device_id="4B4BBA")
    db_session.add(glider)
    db_session.commit()
    # 23:30 UTC on 28.09. is 01:30 local on 29.09. - belongs to the 29th.
    late = Flight(record_id="a", glider_id=glider.id, takeoff_time=datetime(2026, 9, 28, 23, 30, tzinfo=UTC))
    # 21:00 UTC on 28.09. is 23:00 local on the 28th - not today.
    yesterday = Flight(record_id="b", glider_id=glider.id, takeoff_time=datetime(2026, 9, 28, 21, 0, tzinfo=UTC))
    db_session.add_all([late, yesterday])
    db_session.commit()
    monkeypatch.setattr(dashboard, "today_local", lambda: date(2026, 9, 29))

    page = client.get("/dashboard").text
    assert "01:30" in page
    assert "23:00" not in page
    assert "29.09.2026" in page
