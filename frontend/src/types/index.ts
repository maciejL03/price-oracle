export type CheaperMarket = "steam" | "skinport" | "same";

export interface CatalogItem {
  market_hash_name: string;
  name: string;
  image: string | null;
}

export interface SearchResponse {
  items: CatalogItem[];
}

export interface SteamPrice {
  lowest_price: number;
  median_price: number | null;
  volume: number | null;
  currency: string;
  source: string;
}

export interface SkinportPrice {
  min_price: number;
  median_price: number | null;
  mean_price: number | null;
  max_price: number | null;
  suggested_price: number | null;
  quantity: number | null;
  item_page: string | null;
  market_page: string | null;
  currency: string;
}

export interface ComparisonResult {
  market_hash_name: string;
  item_name: string;
  item_image: string | null;
  steam: SteamPrice;
  skinport: SkinportPrice;
  cheaper: CheaperMarket;
  difference: number;
  percentage: number;
}

export interface ApiError {
  detail: string;
}

export interface SystemStatus {
  status: string;
  operating_mode: "demo" | "public";
  data_kind: "simulated" | "live";
  marketplaces: string[];
  notifications: "simulated" | "discord" | "disabled";
}

export interface EngineQuote {
  marketplace: string;
  market_hash_name: string;
  price: string;
  currency: string;
  observed_at: string;
  quote_type: string;
  external_listing_id: string | null;
}

export interface StoredComparison {
  market_hash_name: string;
  currency: string;
  quote_type: string;
  simulated: boolean;
  quotes: EngineQuote[];
  lowest_price: string | null;
  highest_price: string | null;
  spread: string | null;
  excluded_count: number;
}

export interface WatchlistSummary {
  id: string;
  name: string;
  interval_seconds: number;
  items: Array<{ market_hash_name: string; marketplaces: string[] }>;
}

export interface AlertEventSummary {
  id: string;
  rule_id: string;
  quote: EngineQuote;
  condition: string;
  threshold: string;
  triggered_at: string;
}
