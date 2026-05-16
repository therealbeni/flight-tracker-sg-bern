# Installation

## Docker Compose

```bash
docker compose up -d
```

CSV files are written to `./data/` on the host. The container restarts automatically unless explicitly stopped.

## Running directly

```bash
pip install ogn-client ogn-parser
CSV_PATH=lszb_movements.csv python tracker/run.py
```
