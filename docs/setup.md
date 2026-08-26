# Setup

## Prerequisites
- `uv` for Python environment management.
- Docker with Compose support for Postgres/pgvector.
- Optional for Cactus model serving: upstream `cactus` CLI and a downloaded or converted Gemma model.

## Python Environment
```bash
cp .env.example .env
uv sync
```

The base project depends on `psycopg`, `pgvector`, `requests`, `python-dotenv`, and `rich`.

Install the heavier Needle/JAX stack only when you want real Needle 2 routing:
```bash
uv sync --extra needle
```

## Database
The launcher starts Postgres automatically:
```bash
uv run python scripts/launch.py
```

Manual DB start:
```bash
docker compose up -d postgres
```

If Docker Hub has TLS/network problems, the TUI can still be tested without DB auto-start:
```text
POSTGRES_AUTO_START=0
```

Then run:
```bash
uv run python scripts/launch.py
```

Tool execution that writes/searches data needs a reachable `DATABASE_URL`; routing, approval UI, and Cactus fallback can still be inspected without it.

If a different pgvector image is reachable from your network, override it in `.env`:
```text
PGVECTOR_IMAGE=ankane/pgvector:latest
```

Connection defaults are in `.env.example`:
```text
DATABASE_URL=postgresql://cactus:cactus@localhost:5433/cactus
```

## Cactus And Gemma
Clone Cactus and install its Python CLI into the `uv` environment:
```bash
mkdir -p vendor
git clone https://github.com/cactus-compute/cactus vendor/cactus
uv sync --extra cactus
```

If `CACTUS_AUTO_START=0`, start Cactus yourself before launching the app:
```bash
cactus serve ./models/gemma-4-e2b --host 127.0.0.1 --port 8080 --no-cloud-handoff
```

If `CACTUS_AUTO_START=1`, `scripts/launch.py` runs that command for `CACTUS_MODEL`.

## Environment Flags
- `CACTUS_BASE_URL`: default `http://127.0.0.1:8080/v1`.
- `POSTGRES_AUTO_START`: set `0` to skip Docker Compose and use an already running `DATABASE_URL` or DB-offline mode.
- `PGVECTOR_IMAGE`: Docker image used by Compose; default `pgvector/pgvector:pg16`.
- `POSTGRES_HOST_PORT`: host port for this repo's Postgres; default `5433` to avoid common local `5432` conflicts.
- `CACTUS_MODEL`: model path or id passed to `cactus serve` when auto-starting.
- `CACTUS_AUTO_START`: set `1` to let the launcher start Cactus.
- `NEEDLE_CONFIDENCE_THRESHOLD`: minimum confidence before approval is shown.
- `NEEDLE_TOOL_INDEX`: persistent Needle tool embedding cache path.

## WSL x86_64 Cactus Build Note
On this machine, Cactus CLI and the Gemma CQ4 bundle installed, but the local Cactus `run` binary did not build on x86_64 WSL because upstream kernels include ARM/NEON types such as `arm_neon.h` and `__fp16`. Use ARM64 Linux/macOS for the cleanest local Cactus test, or wait for/patch upstream x86 kernel support.
