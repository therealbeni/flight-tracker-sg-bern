# Flight Tracker SG Bern

[![Documentation](https://img.shields.io/badge/docs-readthedocs-blue)](https://flight-tracker-sg-bern.readthedocs.io/en/latest/)

Logs takeoffs and landings at Bern Belp Airport (LSZB) by listening to live [OGN](https://www.glidernet.org/) APRS beacons. Each detected movement is written to a daily CSV file.

## Quick start

```bash
docker compose up -d
```

See the [documentation](https://flight-tracker-sg-bern.readthedocs.io/en/latest/) for configuration, output format, and how it works.
