"""Statistik: the club's numbers and each pilot's own."""

from datetime import timedelta

from flight_stats import club_stats, nice_max, pilot_stats
from models import AircraftKind, Glider, LaunchMethod
from test_flugbuch import add_flight, login, world  # noqa: F401 - world is a fixture
from timeutil import today_local


def test_club_counts_flights_hours_launches_and_tows(db_session, world):
    tow = add_flight(db_session, world["tow"], world["bob"], "10:00", "10:10", launch_method=LaunchMethod.SELF,
                     flight_type="F")
    add_flight(db_session, world["glider"], world["pia"], "10:00", "11:00", launch_method=LaunchMethod.AEROTOW,
               tow_flight_id=tow.id)
    add_flight(db_session, world["glider"], world["pia"], "12:00", "12:05", launch_method=LaunchMethod.WINCH,
               companion_id=world["bob"].id)
    add_flight(db_session, world["glider"], world["bob"], "15:00", None)  # in the air: no time yet
    from flight_stats import flights_in
    stats = club_stats(flights_in(db_session, today_local().year))
    assert (stats.total.flights, stats.total.minutes) == (4, 75)
    assert stats.launches == {"E": 1, "F": 1, "W": 1}
    assert stats.tows == 1 and stats.flying_days == 1
    glider, tug = stats.aircraft  # most hours first
    assert glider.glider.registration == "HB-1811" and (glider.total.flights, glider.total.minutes) == (3, 65)
    assert tug.tows == 1
    pia = next(p for p in stats.pilots if p.pilot.id == world["pia"].id)
    bob = next(p for p in stats.pilots if p.pilot.id == world["bob"].id)
    assert (pia.as_pilot.flights, pia.as_pilot.minutes) == (2, 65)
    assert (bob.as_pilot.flights, bob.as_companion.flights) == (2, 1)
    assert stats.months[today_local().month - 1].flights == 4


def test_private_aircraft_count_and_are_marked(client, db_session, world):
    own = Glider(registration="HB-3407", model="LS 8", kind=AircraftKind.GLIDER, owners=[world["bob"]])
    db_session.add(own)
    db_session.commit()
    add_flight(db_session, own, world["bob"], "10:00", "13:00")
    login(client, "pia")
    page = client.get("/statistik?ansicht=verein").text
    assert "HB-3407" in page and "LS 8, privat" in page


def test_personal_statistics_and_recency(db_session, world):
    from flight_stats import flights_in
    for start in ("09:00", "10:00"):
        add_flight(db_session, world["glider"], world["pia"], start, start[:3] + "30")
    add_flight(db_session, world["glider"], world["bob"], "11:00", "11:20", companion_id=world["pia"].id)
    stats = pilot_stats(flights_in(db_session, None, pilot=world["pia"]), world["pia"])
    assert (stats.as_pilot.flights, stats.as_companion.flights, stats.total.minutes) == (2, 1, 80)
    assert stats.recent.flights == 2  # Begleiter flights don't count for recency
    assert stats.longest_flight.duration_min == 30


def test_statistik_page(client, db_session, world):
    add_flight(db_session, world["glider"], world["pia"], "10:00", "11:00", launch_method=LaunchMethod.WINCH)
    login(client, "pia")
    page = client.get("/statistik").text
    assert "Meine Flugstunden pro Monat" in page and "1 Start als Pilot in den letzten 90 Tagen" in page
    assert "Für Passagierflüge braucht es mindestens 3" in page
    club = client.get(f"/statistik?ansicht=verein&jahr={today_local().year}").text
    assert "Piloten" in club and "Pia Pilot" in club
    assert client.get("/statistik?jahr=alle").status_code == 200
    assert client.get("/statistik?jahr=1800").status_code == 200  # nothing flown: an empty page
    login(client, "desk")
    assert "Piloten" in client.get("/statistik").text  # the FDL: the club's numbers only


def test_flights_count_in_their_local_year(db_session, world):
    from flight_stats import flights_in
    from timeutil import combine_local
    from datetime import date
    f = add_flight(db_session, world["glider"], world["pia"])
    f.takeoff_time = combine_local(date(2026, 1, 1), "00:30")  # 31.12.2025 23:30 UTC
    f.landing_time = f.takeoff_time + timedelta(minutes=30)
    db_session.commit()
    assert [x.id for x in flights_in(db_session, 2026)] == [f.id]
    assert flights_in(db_session, 2025) == []


def test_nice_axis_maxima():
    assert [nice_max(v) for v in (0, 0.4, 3, 7, 12, 48, 51, 260)] == [1, 1, 5, 10, 20, 50, 100, 500]
