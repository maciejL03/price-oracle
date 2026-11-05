import { useCallback, useEffect, useState } from "react";
import {
  getAlertEvents,
  getStoredComparison,
  getSystemStatus,
  getWatchlists,
} from "./api/client";
import { Layout } from "./components/Layout";
import { SearchBox } from "./components/SearchBox";
import { useItemSearch } from "./hooks/useItemSearch";
import type {
  AlertEventSummary,
  CatalogItem,
  StoredComparison,
  SystemStatus,
  WatchlistSummary,
} from "./types";

const DEMO_ITEM = "AK-47 | Redline (Field-Tested)";

function money(value: string | null, currency = "GBP") {
  if (value === null) return "—";
  return new Intl.NumberFormat("en-GB", { style: "currency", currency }).format(
    Number(value),
  );
}

export default function App() {
  const { query, setQuery, results, isSearching, error: searchError } =
    useItemSearch();
  const [selectedItem, setSelectedItem] = useState(DEMO_ITEM);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [comparison, setComparison] = useState<StoredComparison | null>(null);
  const [watchlists, setWatchlists] = useState<WatchlistSummary[]>([]);
  const [events, setEvents] = useState<AlertEventSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [nextStatus, nextComparison, nextWatchlists, nextEvents] =
        await Promise.all([
          getSystemStatus(),
          getStoredComparison(selectedItem),
          getWatchlists(),
          getAlertEvents(),
        ]);
      setStatus(nextStatus);
      setComparison(nextComparison);
      setWatchlists(nextWatchlists);
      setEvents(nextEvents);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load engine state");
    }
  }, [selectedItem]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  function select(item: CatalogItem) {
    setSelectedItem(item.market_hash_name);
    setQuery(item.market_hash_name);
  }

  const selectedEvents = events.filter(
    (event) => event.quote.market_hash_name === selectedItem,
  );

  return (
    <Layout>
      <section className="mb-6 rounded-2xl border border-[var(--accent-green)]/30 bg-[var(--accent-green)]/10 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-widest text-[var(--accent-green)]">
              {status?.operating_mode === "demo" ? "Offline demo" : "Public marketplace mode"}
            </p>
            <p className="mt-1 text-sm text-[var(--text-secondary)]">
              {status?.data_kind === "simulated"
                ? "All displayed prices are deterministic simulations. No marketplace network calls are made."
                : "Live providers are best-effort and may be unavailable or rate limited."}
            </p>
          </div>
          <span className="rounded-full bg-[var(--bg-base)] px-3 py-1 text-xs font-medium">
            API {status?.status ?? "connecting…"}
          </span>
        </div>
      </section>

      <section className="mb-8">
        <SearchBox
          query={query}
          onQueryChange={setQuery}
          results={results}
          isSearching={isSearching}
          searchError={searchError}
          onSelect={select}
        />
      </section>

      {error && (
        <div role="alert" className="mb-6 rounded-xl border border-[var(--accent-red)]/30 bg-[var(--accent-red)]/10 p-4 text-sm text-[var(--accent-red)]">
          {error}
        </div>
      )}

      <section className="mb-8">
        <div className="mb-4 flex flex-wrap items-end justify-between gap-2">
          <div>
            <p className="text-sm text-[var(--text-muted)]">Latest stored observations</p>
            <h2 className="text-xl font-semibold">{selectedItem}</h2>
          </div>
          {comparison?.simulated && (
            <span className="rounded-full bg-[var(--accent-amber)]/15 px-3 py-1 text-xs font-semibold text-[var(--accent-amber)]">
              SIMULATED DATA
            </span>
          )}
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          {comparison?.quotes.map((quote) => (
            <article key={`${quote.marketplace}-${quote.observed_at}`} className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-card)] p-6">
              <p className="text-sm font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
                {quote.marketplace}
              </p>
              <p className="mt-3 font-mono text-3xl font-semibold">
                {money(quote.price, quote.currency)}
              </p>
              <p className="mt-2 text-xs text-[var(--text-muted)]">
                Lowest ask · observed {new Date(quote.observed_at).toLocaleString()}
              </p>
            </article>
          ))}
        </div>
        {comparison && comparison.quotes.length === 0 && (
          <p className="rounded-xl border border-dashed border-[var(--border-subtle)] p-6 text-center text-[var(--text-muted)]">
            Waiting for the scheduled collection job…
          </p>
        )}
        {comparison?.spread !== null && comparison?.spread !== undefined && (
          <p className="mt-4 text-sm text-[var(--text-secondary)]">
            Stored pre-fee spread: {money(comparison.spread, comparison.currency)}. This is not a profit estimate.
          </p>
        )}
      </section>

      <section className="grid gap-4 sm:grid-cols-3">
        <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4">
          <p className="text-xs uppercase tracking-wider text-[var(--text-muted)]">Providers</p>
          <p className="mt-2 text-lg font-semibold">{status?.marketplaces.length ?? 0}</p>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">{status?.marketplaces.join(", ")}</p>
        </div>
        <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4">
          <p className="text-xs uppercase tracking-wider text-[var(--text-muted)]">Persistent watchlists</p>
          <p className="mt-2 text-lg font-semibold">{watchlists.length}</p>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">Restored after container restart</p>
        </div>
        <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4">
          <p className="text-xs uppercase tracking-wider text-[var(--text-muted)]">Alert events</p>
          <p className="mt-2 text-lg font-semibold">{selectedEvents.length}</p>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">Notifications: {status?.notifications ?? "—"}</p>
        </div>
      </section>
    </Layout>
  );
}
