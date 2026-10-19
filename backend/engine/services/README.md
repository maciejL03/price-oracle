# Collection service

`CollectionService` is the small orchestration boundary between future fetch jobs and registered
marketplace adapters. It selects one marketplace, applies that provider's request policy, awaits
its `fetch()` operation, and returns the resulting `MarketQuote` objects unchanged.

```text
FetchMarketJob -> CollectionService -> MarketplaceRegistry -> Marketplace.fetch(Item)
```

The service does not create marketplace implementations, normalize source payloads, store quotes,
rank prices, schedule recurring work, or create another worker pool. `JobHandler` remains responsible
for overall engine concurrency; collection policies govern only provider-specific requests.

## Public interface

```python
from engine.services import CollectionPolicy, CollectionService

service = CollectionService(
    marketplace_registry,
    policies={
        "documented-provider-id": CollectionPolicy(
            max_concurrency=2,
            minimum_interval_seconds=0.5,
        )
    },
)

quotes = await service.collect("documented-provider-id", item)
```

The registry is injected and looked up for every collection call. The service never constructs a
concrete adapter. Registry lookup errors propagate unchanged, and an empty list is a successful
marketplace response rather than an error.

One long-lived service instance should be shared by all collection jobs in an engine process. Its
per-provider semaphores, admission times, and retry delays are then shared across those calls.
Constructing a separate service per job would intentionally create separate controls.

## Provider policies

`CollectionPolicy` is immutable and can be supplied independently for each registry identifier.

| Field | Default | Meaning |
|---|---:|---|
| `max_concurrency` | `None` | Maximum in-flight requests for this provider; `None` adds no assumed quota |
| `minimum_interval_seconds` | `0` | Minimum spacing between request starts for this provider |
| `max_attempts` | `3` | Total attempts, including the initial request |
| `initial_backoff_seconds` | `0.25` | Delay after the first retryable failure |
| `backoff_multiplier` | `2` | Exponential backoff factor |
| `max_backoff_seconds` | `5` | Maximum local retry delay |
| `retryable_exceptions` | connection and timeout errors | Failures eligible for retry |

There are no built-in marketplace quotas. Concurrency is unlimited and request spacing is disabled
unless configuration supplies documented restrictions. A semaphore limits simultaneous requests;
the monotonic admission gate separately implements request spacing. Controls are independent, so a
limited provider never holds a global lock or prevents another provider from running.

The mapping is constructor configuration rather than part of `EngineSettings` because provider
quotas and exception types belong to marketplace integration/bootstrap configuration. No shared
configuration files or interfaces are changed by this module.

## Failures, retries, and `Retry-After`

Only configured retryable exception classes are retried. Authentication, invalid input, parsing,
and other failures therefore propagate immediately unless a caller explicitly misclassifies them.
Retries are bounded and use capped exponential backoff. When a retryable exception exposes a valid
non-negative `retry_after` or `retry_after_seconds` value, the provider's shared admission time is
deferred so subsequent requests respect it. Invalid retry metadata is ignored rather than masking
the original provider failure.

After the final attempt, the final provider exception is re-raised with its original type and
traceback. The service never turns a failure into an empty list.

HTTP-client timeouts remain owned by marketplace adapters. CollectionService does not add an outer
timeout that could conflict with connect/read/pool timeout behavior. Built-in `TimeoutError` is
retryable by default; marketplace-specific timeout types can be included in that provider's policy.

`asyncio.CancelledError` is not caught or retried. Cancellation while waiting for a permit, request
spacing, retry delay, or `fetch()` propagates to the job system. Async context managers release any
acquired semaphore permit on cancellation and on ordinary failures.

## Testing

Collection tests register fake async marketplaces through the concrete `MarketplaceRegistry`. They
cover unchanged and empty results, lookup errors, retry classification and exhaustion, timeout and
retry-delay handling, cancellation, per-provider concurrency and spacing, cross-provider isolation,
and resource release. Clocks and sleep are injected in timing tests, so no network, credentials,
database, scheduler, job handler, or wall-clock delays are involved.

Run from `backend/`:

```bash
python -m pytest -q tests/test_collection_service.py
python -m pytest -q
```

The marketplace package is developed on a separate branch and is a direct dependency of this
service. Integration testing should include both branches until they are merged into their common
base.
