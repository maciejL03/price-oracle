import { useCallback, useState } from "react";
import { comparePrices } from "../api/client";
import type { ComparisonResult } from "../types";

export function usePriceComparison() {
  const [comparison, setComparison] = useState<ComparisonResult | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const compare = useCallback(async (marketHashName: string) => {
    setIsLoading(true);
    setError(null);
    setComparison(null);

    try {
      const result = await comparePrices(marketHashName);
      setComparison(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Comparison failed");
    } finally {
      setIsLoading(false);
    }
  }, []);

  const reset = useCallback(() => {
    setComparison(null);
    setError(null);
    setIsLoading(false);
  }, []);

  return {
    comparison,
    isLoading,
    error,
    compare,
    reset,
  };
}
