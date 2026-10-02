# Flight Tracker SG Bern

[![Documentation](https://img.shields.io/badge/docs-readthedocs-blue)](https://flight-tracker-sg-bern.readthedocs.io/en/latest/)

Automated start/landing logging for SG Bern. Live at **https://flight.clanker.ch** (pilot
accounts, QR-code glider claiming, dashboard, personal logbook - see
[`docs/roadmap.md`](docs/roadmap.md) for the full plan and
[`docs/deployment.md`](docs/deployment.md) for how it's hosted).

Underneath, `tracker/` logs the takeoffs and landings of the club's aircraft by listening
to live [OGN](https://www.glidernet.org/) APRS beacons, and writes them into the app's
database.

## Quick start

```bash
docker compose up -d
```

See the [documentation](https://flight-tracker-sg-bern.readthedocs.io/en/latest/) for configuration, output format, and how it works.

## Development

Run all tests (in Docker, nothing to install on the host):

```bash
dev/test.sh
```

Takeoff/landing detection lives in `tracker/src/detection.py` and is tested scenario by
scenario in `tests/tracker/test_detection.py` plus a randomised stress test over hundreds
of simulated flying days (`tests/tracker/test_detection_stress.py`).

Click through the whole web app in a real browser (phone and club PC, incl. QR scan):

```bash
dev/ui/run.sh
```
