# Flight Tracker SG Bern

[![Documentation](https://img.shields.io/badge/docs-readthedocs-blue)](https://flight-tracker-sg-bern.readthedocs.io/en/latest/)

Automated start/landing logging for SG Bern. Live at **https://flight.clanker.ch** (pilot
accounts, QR-code glider claiming, dashboard, personal logbook - see
[`docs/roadmap.md`](docs/roadmap.md) for the full plan and
[`docs/deployment.md`](docs/deployment.md) for how it's hosted).

Underneath, `tracker/` logs takeoffs and landings at Bern Belp Airport (LSZB) and other
airfields in range by listening to live [OGN](https://www.glidernet.org/) APRS beacons.
Each detected movement is written to a daily CSV file and, for known club gliders, into
the app's database.

## Quick start

```bash
docker compose up -d
```

See the [documentation](https://flight-tracker-sg-bern.readthedocs.io/en/latest/) for configuration, output format, and how it works.
