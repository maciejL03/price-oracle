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
