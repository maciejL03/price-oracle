import { useCallback, useEffect, useRef, useState } from "react";
import { searchItems } from "../api/client";
import type { CatalogItem } from "../types";

const DEBOUNCE_MS = 250;

export function useItemSearch() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CatalogItem[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);

  useEffect(() => {
    const trimmed = query.trim();

    if (trimmed.length < 2) {
      setResults([]);
      setIsSearching(false);
      setError(null);
      return;
    }

    const currentRequest = ++requestId.current;
    setIsSearching(true);
    setError(null);

    const timer = window.setTimeout(async () => {
      try {
        const response = await searchItems(trimmed);
        if (currentRequest !== requestId.current) return;
        setResults(response.items);
      } catch (err) {
        if (currentRequest !== requestId.current) return;
        setResults([]);
        setError(err instanceof Error ? err.message : "Search failed");
      } finally {
        if (currentRequest === requestId.current) {
          setIsSearching(false);
        }
      }
    }, DEBOUNCE_MS);

    return () => window.clearTimeout(timer);
  }, [query]);

  const clear = useCallback(() => {
    setQuery("");
    setResults([]);
    setError(null);
  }, []);

  return {
    query,
    setQuery,
    results,
    isSearching,
    error,
    clear,
  };
}
