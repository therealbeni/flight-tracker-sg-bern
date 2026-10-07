"""Karte: aircraft in the air on the live map, a flight's track for the replay."""

from datetime import datetime, timedelta, timezone

from models import FlightTrack
from shared import tracks
from test_flugbuch import add_flight, login, world  # noqa: F401 - world is a fixture


def points(start, n, step_s=4):
    return [tracks.Point(start + timedelta(seconds=step_s * i), 46.91 + i * 1e-3, 7.49, 600.0 + i, 510.0, 90.0, 1.5)
            for i in range(n)]


def test_live_map_shows_aircraft_in_the_air_and_only_new_positions(client, db_session, world):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    flight = add_flight(db_session, world["glider"], world["pia"], "00:00", None)
    flight.takeoff_time = now - timedelta(minutes=10)
    landed = add_flight(db_session, world["tow"], world["bob"], "00:01", "00:10")
    landed.takeoff_time = now - timedelta(minutes=10)
    db_session.commit()
    tracks.store(db_session, flight.id, points(now - timedelta(minutes=2), 10))
    tracks.store(db_session, landed.id, points(now - timedelta(minutes=2), 10))
    db_session.commit()
    login(client, "bob")
    assert "Karte" in client.get("/karte").text

    data = client.get("/karte/live.json").json()
    (aircraft,) = data["aircraft"]  # the landed one isn't in the air
    assert aircraft["registration"] == "HB-1811" and aircraft["pilot"] == "Pia Pilot" and len(aircraft["points"]) == 10
    t, lat, lon, alt, gnd, v, vz = aircraft["points"][-1]
    assert (alt, gnd, v, vz) == (609, 510, 90, 1.5)

    since = aircraft["points"][4][0]
    newer = client.get(f"/karte/live.json?seit={since}&bekannt={flight.id}").json()["aircraft"][0]["points"]
    assert len(newer) == 5
    # Not known to the page yet: the whole track, however old.
    assert len(client.get(f"/karte/live.json?seit={since}&bekannt=").json()["aircraft"][0]["points"]) == 10


def test_lost_contact_long_ago_is_not_on_the_live_map(client, db_session, world):
    now = datetime.now(timezone.utc)
    flight = add_flight(db_session, world["glider"], world["pia"], "00:00", None)
    flight.takeoff_time = now - timedelta(hours=2)
    db_session.commit()
    tracks.store(db_session, flight.id, points(now - timedelta(hours=1), 5))
    db_session.commit()
    login(client, "pia")
    assert client.get("/karte/live.json").json()["aircraft"] == []


def test_flight_page_shows_the_track_of_a_landed_flight(client, db_session, world):
    flight = add_flight(db_session, world["glider"], world["pia"], "11:00", "11:45")
    login(client, "bob")
    assert "data-track" not in client.get(f"/flights/{flight.id}").text  # no track: no map
    tracks.store(db_session, flight.id, points(flight.takeoff_time, 20))
    db_session.flush()
    tracks.pack_flight(db_session, flight.id)
    db_session.commit()
    assert db_session.get(FlightTrack, flight.id).point_count == 20
    assert f'data-track="/flights/{flight.id}/track.json"' in client.get(f"/flights/{flight.id}").text
    data = client.get(f"/flights/{flight.id}/track.json").json()
    assert data["landed"] and len(data["points"]) == 20 and data["points"][0][1] == 46.91


def test_map_needs_an_approved_account(client, db_session, world):
    flight = add_flight(db_session, world["glider"], world["pia"], "11:00", "11:45")
    for path in ("/karte/live.json", f"/flights/{flight.id}/track.json"):
        assert client.get(path, follow_redirects=False).status_code in (303, 401, 403)
    login(client, "pia")
    assert client.get("/flights/999999/track.json").status_code == 404


def test_map_tiles_are_allowed_by_the_security_policy(client, world):
    login(client, "pia")
    assert "img-src 'self' data: https://wmts.geo.admin.ch" in client.get("/karte").headers["content-security-policy"]
