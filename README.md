# CS2 Price Oracle

A self-hosted Counter-Strike 2 marketplace monitoring engine with a React price-comparison UI.

## Architecture

The project is split into orthogonal layers so each concern can change independently:

```
frontend/              React UI (unchanged)
    └── api/           HTTP client only

backend/
    ├── engine/        Engine services, storage, marketplaces, jobs, and API
    │   └── api/       FastAPI routes and HTTP schemas
    └── app/main.py    Compatibility ASGI entry point
```

| Layer | Responsibility |
|-------|----------------|
| **engine** | Collection, pricing, storage, watchlists, alerts, notifications, and lifecycle |
| **engine/catalog** | Canonical item metadata and deterministic search |
| **engine/marketplaces** | CSFloat, Steam, and Skinport provider normalization |
| **engine/api** | Thin FastAPI transport and website-contract adapters |

## Data sources

- **Item names**: [ByMykel/counter-strike-items](https://github.com/ByMykel/counter-strike-items) (`all.json`) — resolves user search to canonical `market_hash_name`
- **Steam**: Community Market `priceoverview` (country `GB`, currency `2` = GBP)
- **Skinport**: [`/v1/items`](https://docs.skinport.com/items) with `app_id=730`, `currency=GBP`

> **Note:** Steam's `pricehistory` endpoint requires a logged-in session and returns errors for anonymous requests. This app uses `priceoverview` instead, which provides the **current lowest listing** — a better apples-to-apples comparison against Skinport's `min_price`.

## Quick start

### 1. Backend

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173).

## API

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Health check |
| `GET /api/items/search?q=redline&limit=20` | Autocomplete item search |
| `GET /api/prices/compare?market_hash_name=...` | Full price comparison |

## Configuration

Engine settings live in `backend/engine/config/settings.py` and use the `CS2_` environment prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `CS2_STEAM_COUNTRY` | `GB` | Steam market region |
| `CS2_STEAM_CURRENCY` | `GBP` | Steam market currency |
| `CS2_SKINPORT_CURRENCY` | `GBP` | Skinport currency |
| `CS2_CACHE_DIR` | `.cache` | Catalog and marketplace cache directory |
| `CS2_CATALOG_CACHE_TTL_SECONDS` | `86400` | ByMykel cache TTL |
| `CS2_SKINPORT_CACHE_TTL_SECONDS` | `300` | Skinport cache TTL |

See the [engine API README](backend/engine/api/README.md) for lifecycle ownership, full routes,
contracts, engine integration, and migration status.

## License

See [LICENSE](LICENSE).
