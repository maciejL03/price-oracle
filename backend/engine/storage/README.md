# Storage service

`StorageService` is the engine's single persistence boundary. Callers exchange domain
`Item` and `MarketQuote` objects and do not receive SQLAlchemy sessions or ORM records.

## Lifecycle

Construct the service with the configured SQLite path, initialize it during application
startup, and close it during shutdown:

```python
from engine.config import EngineSettings
from engine.storage import StorageService

settings = EngineSettings()
storage = StorageService(
    settings.database_path,
    busy_timeout_seconds=settings.database_busy_timeout_seconds,
)
await storage.initialize()
try:
    ...
finally:
    await storage.close()
```

`async with StorageService(...)` provides the same lifecycle. Initialization creates the
database's parent directory and the storage-owned tables. It is explicit so imports and
constructors never perform filesystem or database I/O.

## Quote persistence

`save_quotes()` inserts its complete input batch in one transaction and returns only after
commit. Any failed row rolls back the whole batch. Every operation creates a separate async
session, so concurrent jobs never share session state.

Quotes are append-only. `get_price_history()` reads that history in timestamp order.
`get_latest_quotes()` derives the newest timestamp for each marketplace/currency/quote-type
series and keeps rows tied at that timestamp. For individual listings, "latest" means most
recently observed; it does not claim the listing remains active.

Prices are stored as decimal text to avoid SQLite `REAL` conversion. Observation times are
stored as fixed UTC text with microsecond precision. ORM mappings remain private to this
package and are converted back to validated domain objects on every read.

## Alert persistence

`StorageService` also implements the persistence contract required by
[`AlertService`](../alerts/README.md). It stores alert rules, per-rule active state, and append-only
alert events. Rule-state updates use the observation timestamp as an ordering guard.

`commit_alert_evaluation()` atomically claims an inactive-to-active transition and inserts its event.
The event is returned only after commit, so concurrent evaluation cannot generate duplicate events
for one transition and an insert failure cannot leave the rule incorrectly active. Rules and states
can be deleted while historical events remain available through `list_alert_events()`.

## Notification persistence

`StorageService` implements the narrow persistence protocol used by
[`NotificationService`](../notifications/README.md). `claim_notification_delivery()` atomically
inserts one pending row per alert-event ID; a conflict reports that the event was already claimed.
`get_notification_delivery()` returns its validated state, and
`complete_notification_delivery()` changes a pending claim to a succeeded or failed outcome.

## Watchlist persistence

`StorageService` implements the `WatchlistStorage` contract used by `WatchlistService`. A watchlist
root and its item/marketplace associations are created or replaced in one transaction. Empty
watchlists are preserved, associations are deleted by foreign-key cascade, and startup can restore
recurring jobs from the database after a process restart.

The delivery row references the durable alert event and stores no webhook URL, token, provider
response body, or raw network exception. This database boundary suppresses duplicate callers, but
it cannot make an external Discord request and a SQLite transaction exactly once together.

## SQLite behavior

File-backed databases use WAL mode for concurrent readers and SQLite's normal single-writer
behavior. A bounded busy timeout lets brief writer contention settle without an unbounded
retry loop. Contention that outlasts the timeout raises `StorageBusyError`; other persistence
failures raise `StorageError`, preserving the original exception as their cause.

There is no application-wide database lock, in-memory mirror, write queue, repository hierarchy,
or migration framework.

## Testing

Run from `backend/` using the project environment:

```bash
python -m pytest -q tests/test_storage_service.py
python -m pytest -q
```

Tests use temporary SQLite files and require no external database server or credentials.
