# CS2 Price Oracle

A small web app that compares Counter-Strike 2 item prices between **Steam Community Market** (GB / GBP) and **Skinport**.

## Architecture

The project is split into orthogonal layers so each concern can change independently:

```
frontend/          React UI (search, cards, comparison)
    └── api/       HTTP client only

backend/
    ├── domain/    Pure models + comparison logic (no I/O)
    ├── ports/     Abstract marketplace interfaces
    ├── adapters/  Steam, Skinport, ByMykel implementations
    ├── services/  Orchestration (search + compare)
    └── api/       FastAPI routes + response schemas
```

| Layer | Responsibility |
|-------|----------------|
| **domain** | `CatalogItem`, `SteamPrice`, `SkinportPrice`, `PriceComparison` |
| **ports** | `ItemCatalog`, `SteamMarket`, `SkinportMarket` interfaces |
| **adapters** | External API clients (ByMykel, Steam, Skinport) |
| **services** | Business workflows built from ports |
| **api** | HTTP transport; maps domain → JSON |

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

Backend settings live in `backend/app/config.py` (overridable via environment variables):

| Variable | Default | Description |
|----------|---------|-------------|
| `STEAM_COUNTRY` | `GB` | Steam market region |
| `STEAM_CURRENCY` | `2` | Steam currency code (GBP) |
| `SKINPORT_CURRENCY` | `GBP` | Skinport currency |
| `ITEMS_CACHE_TTL_SECONDS` | `86400` | ByMykel cache TTL |
| `SKINPORT_CACHE_TTL_SECONDS` | `300` | Skinport cache TTL |

## License

See [LICENSE](LICENSE).
