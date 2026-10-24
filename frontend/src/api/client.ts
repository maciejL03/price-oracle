import type {
  ApiError,
  AlertEventSummary,
  ComparisonResult,
  SearchResponse,
  StoredComparison,
  SystemStatus,
  WatchlistSummary,
} from "../types";

async function request<T>(url: string): Promise<T> {
  const response = await fetch(url);

  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as ApiError;
      if (body.detail) message = body.detail;
    } catch {
      // ignore parse errors
    }
    throw new Error(message);
  }

  return response.json() as Promise<T>;
}

export function searchItems(query: string, limit = 20): Promise<SearchResponse> {
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  return request<SearchResponse>(`/api/items/search?${params}`);
}

export function comparePrices(marketHashName: string): Promise<ComparisonResult> {
  const params = new URLSearchParams({ market_hash_name: marketHashName });
  return request<ComparisonResult>(`/api/prices/compare?${params}`);
}

export function getSystemStatus(): Promise<SystemStatus> {
  return request<SystemStatus>("/api/status");
}

export function getStoredComparison(marketHashName: string): Promise<StoredComparison> {
  return request<StoredComparison>(
    `/api/items/${encodeURIComponent(marketHashName)}/comparison`,
  );
}

export async function getWatchlists(): Promise<WatchlistSummary[]> {
  const response = await request<{ watchlists: WatchlistSummary[] }>("/api/watchlists");
  return response.watchlists;
}

export async function getAlertEvents(): Promise<AlertEventSummary[]> {
  const response = await request<{ events: AlertEventSummary[] }>("/api/alerts/events");
  return response.events;
}
