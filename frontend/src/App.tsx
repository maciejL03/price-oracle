import { useCallback } from "react";
import { ComparisonView } from "./components/ComparisonView";
import { Layout } from "./components/Layout";
import { SearchBox } from "./components/SearchBox";
import { useItemSearch } from "./hooks/useItemSearch";
import { usePriceComparison } from "./hooks/usePriceComparison";
import type { CatalogItem } from "./types";

export default function App() {
  const { query, setQuery, results, isSearching, error: searchError } =
    useItemSearch();
  const {
    comparison,
    isLoading,
    error: compareError,
    compare,
    reset,
  } = usePriceComparison();

  const handleSelect = useCallback(
    (item: CatalogItem) => {
      setQuery(item.market_hash_name);
      compare(item.market_hash_name);
    },
    [compare, setQuery],
  );

  const showComparison = comparison && !isLoading;
  const showError = compareError && !isLoading;
  const showLoading = isLoading;

  return (
    <Layout>
      <section className="mb-8">
        <SearchBox
          query={query}
          onQueryChange={(value) => {
            reset();
            setQuery(value);
          }}
          results={results}
          isSearching={isSearching}
          searchError={searchError}
          onSelect={handleSelect}
        />
      </section>

      {showLoading && (
        <div className="animate-fade-in-up rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-card)] p-10 text-center">
          <div className="mx-auto mb-4 h-8 w-8 animate-spin rounded-full border-2 border-[var(--border-subtle)] border-t-[var(--accent-steam)]" />
          <p className="text-[var(--text-secondary)]">
            Fetching prices from Steam and Skinport…
          </p>
        </div>
      )}

      {showError && (
        <div
          role="alert"
          className="animate-fade-in-up rounded-2xl border border-[var(--accent-red)]/30 bg-[var(--accent-red)]/10 p-5 text-sm text-[var(--accent-red)]"
        >
          {compareError}
        </div>
      )}

      {showComparison && <ComparisonView comparison={comparison} />}

      {!showLoading && !showComparison && !showError && (
        <div className="rounded-2xl border border-dashed border-[var(--border-subtle)] p-10 text-center text-[var(--text-muted)]">
          <p>Search for a CS2 item to compare marketplace prices.</p>
          <p className="mt-2 text-sm">
            Try{" "}
            <button
              type="button"
              className="text-[var(--accent-steam)] hover:underline"
              onClick={() => handleSelect({
                market_hash_name: "AK-47 | Redline (Field-Tested)",
                name: "AK-47 | Redline (Field-Tested)",
                image: null,
              })}
            >
              AK-47 | Redline (Field-Tested)
            </button>
          </p>
        </div>
      )}
    </Layout>
  );
}
