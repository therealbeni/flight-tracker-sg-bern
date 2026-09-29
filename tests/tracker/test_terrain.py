"""Terrain must never block beacon processing on a tile download."""

import threading

import srtm

from terrain import Terrain


class FakeSrtm:
    """Stands in for srtm.GeoElevationData: one tile per whole degree."""

    def __init__(self, fail=False):
        self.files = {}
        self.fail = fail
        self.downloads = 0
        self.release = threading.Event()

    def get_file_name(self, lat, lon):
        return f"N{int(lat):02d}E{int(lon):03d}.hgt"

    def get_file(self, lat, lon):
        self.downloads += 1
        self.release.wait(5)
        if self.fail:
            raise OSError("timeout")
        self.files[self.get_file_name(lat, lon)] = object()
        return self.files[self.get_file_name(lat, lon)]

    def get_elevation(self, lat, lon):
        return 510.0


def make(monkeypatch, fake, **kwargs) -> Terrain:
    monkeypatch.setattr(srtm, "get_data", lambda **_: fake)
    return Terrain(cache_dir="", **kwargs)


def wait_until(condition):
    for _ in range(500):
        if condition():
            return
        threading.Event().wait(0.01)
    raise AssertionError("timed out")


def test_missing_tile_returns_none_immediately_and_downloads_in_background(monkeypatch):
    fake = FakeSrtm()
    t = make(monkeypatch, fake)
    assert t.elevation(46.9, 7.5) is None  # would block here without the background thread
    fake.release.set()
    wait_until(lambda: t.elevation(46.9, 7.5) == 510.0)
    assert fake.downloads == 1


def test_tile_is_requested_once_while_downloading(monkeypatch):
    fake = FakeSrtm()
    t = make(monkeypatch, fake)
    for _ in range(50):
        t.elevation(46.9, 7.5)
    fake.release.set()
    wait_until(lambda: t.elevation(46.9, 7.5) is not None)
    assert fake.downloads == 1


def test_failed_download_is_not_retried_on_every_beacon(monkeypatch):
    fake = FakeSrtm(fail=True)
    fake.release.set()
    t = make(monkeypatch, fake, retry_after_s=3600)
    t.elevation(46.9, 7.5)
    wait_until(lambda: not t._pending)
    for _ in range(50):
        assert t.elevation(46.9, 7.5) is None
    wait_until(lambda: not t._pending)
    assert fake.downloads == 1


def test_synchronous_mode_for_replay(monkeypatch):
    fake = FakeSrtm()
    fake.release.set()
    t = make(monkeypatch, fake, background=False)
    assert t.elevation(46.9, 7.5) == 510.0
