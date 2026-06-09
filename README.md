# Upcoming Games data scraper

Standalone Python scraper container for official source collection, IGDB discovery, raw R2 archiving, and Neon writes.

## Files

- `Dockerfile`: builds a one-shot scraper image.
- `compose.yaml`: provides runnable commands for config checks and scraper jobs.
- `.env.example`: scraper-only environment variables.
- `requirements.txt`: Python dependencies needed by the scraper.

## Setup

Copy `.env.example` to `.env`, then fill in at least:

- `DATABASE_URL`
- `R2_BUCKET_NAME`
- `R2_ACCOUNT_ID` or `R2_ENDPOINT_URL`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`

For IGDB discovery also fill:

- `IGDB_CLIENT_ID`
- `IGDB_ACCESS_TOKEN`

Production should keep `REQUIRE_R2_ARCHIVE=1` and `R2_WRANGLER_UPLOAD=0`.

## Docker

Build:

```bash
docker build -t upcoming-games-scraper .
```

Check config:

```bash
docker run --rm --env-file .env upcoming-games-scraper
```

Run official source sync:

```bash
docker run --rm --env-file .env upcoming-games-scraper \
  python official_release_sync.py --collectors steam,nintendo,playstation,xbox,epic,gog --limit 50
```

Run IGDB discovery:

```bash
docker run --rm --env-file .env upcoming-games-scraper \
  python igdb_discovery_sync.py --limit 50
```

Run Steam metadata backfill:

```bash
docker run --rm --env-file .env upcoming-games-scraper \
  python steam_metadata_backfill.py --limit 80
```

Run Steam tracked refresh:

```bash
docker run --rm --env-file .env upcoming-games-scraper \
  python steam_tracked_refresh.py --limit 80
```

Run PlayStation tracked refresh:

```bash
docker run --rm --env-file .env upcoming-games-scraper \
  python playstation_tracked_refresh.py --limit 80
```

Run Nintendo tracked refresh:

```bash
docker run --rm --env-file .env upcoming-games-scraper \
  python nintendo_tracked_refresh.py --limit 80
```

Run Xbox tracked refresh:

```bash
docker run --rm --env-file .env upcoming-games-scraper \
  python xbox_tracked_refresh.py --limit 80
```

Run Epic tracked refresh:

```bash
docker run --rm --env-file .env upcoming-games-scraper \
  python epic_tracked_refresh.py --limit 80
```

Run GOG tracked refresh:

```bash
docker run --rm --env-file .env upcoming-games-scraper \
  python tracked/gog_tracked_refresh.py --limit 80
```

## Docker Compose

Check config:

```bash
docker compose run --rm check-config
```

Run jobs:

```bash
docker compose run --rm official-release-sync
docker compose run --rm igdb-discovery-sync
docker compose run --rm steam-metadata-backfill
docker compose run --rm steam-tracked-refresh
docker compose run --rm playstation-tracked-refresh
docker compose run --rm nintendo-tracked-refresh
docker compose run --rm xbox-tracked-refresh
docker compose run --rm epic-tracked-refresh
docker compose run --rm gog-tracked-refresh
```

The compose file mounts `./var/raw` into `/app/var/raw` for local fallback archives and Wrangler temp files.
