# Installation

## Docker Compose

```bash
docker compose up -d
```

Raw beacon recordings and the terrain cache are written to `./data/` on the host. The app container applies database migrations on startup. All containers restart
automatically unless explicitly stopped.

See [Deployment](deployment.md) for the production setup.

## Tests

```bash
dev/test.sh
```

Runs everything in Docker; nothing needs to be installed on the host.
