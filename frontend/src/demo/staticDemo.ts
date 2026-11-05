import type {
  AlertEventSummary,
  CatalogItem,
  SearchResponse,
  StoredComparison,
  SystemStatus,
  WatchlistSummary,
} from "../types";

export const STATIC_DEMO = import.meta.env.VITE_STATIC_DEMO === "true";

const DEMO_ITEMS: CatalogItem[] = [
  {
    market_hash_name: "AK-47 | Redline (Field-Tested)",
    name: "AK-47 | Redline",
    image: null,
  },
  {
    market_hash_name: "StatTrak™ AK-47 | Redline (Field-Tested)",
    name: "StatTrak™ AK-47 | Redline",
    image: null,
  },
  {
    market_hash_name: "Souvenir AWP | Pink DDPAT (Field-Tested)",
    name: "Souvenir AWP | Pink DDPAT",
    image: null,
  },
  {
    market_hash_name: "Dreams & Nightmares Case",
    name: "Dreams & Nightmares Case",
    image: null,
  },
];

const DEMO_PRICES: Record<string, [string, string]> = {
  "AK-47 | Redline (Field-Tested)": ["12.40", "13.15"],
  "StatTrak™ AK-47 | Redline (Field-Tested)": ["31.50", "33.00"],
  "Souvenir AWP | Pink DDPAT (Field-Tested)": ["75.00", "78.25"],
  "Dreams & Nightmares Case": ["0.80", "0.85"],
};

export function staticStatus(): SystemStatus {
  return {
    status: "ok",
    operating_mode: "demo",
    data_kind: "simulated",
    marketplaces: ["demo-market-a", "demo-market-b"],
    notifications: "simulated",
  };
}

export function staticSearch(query: string, limit = 20): SearchResponse {
  const normalized = query.trim().toLowerCase();
  if (normalized.length < 2) return { items: [] };
  return {
    items: DEMO_ITEMS.filter((item) =>
      item.market_hash_name.toLowerCase().includes(normalized),
    ).slice(0, limit),
  };
}

export function staticComparison(marketHashName: string): StoredComparison {
  const prices = DEMO_PRICES[marketHashName];
  const observedAt = "2026-11-05T21:00:00.000Z";
  const quotes = prices
    ? [
        quote("demo-market-a", marketHashName, prices[0], observedAt),
        quote("demo-market-b", marketHashName, prices[1], observedAt),
      ]
    : [];
  const lowest = prices ? prices[0] : null;
  const highest = prices ? prices[1] : null;
  const spread =
    prices === undefined
      ? null
      : (Number(prices[1]) - Number(prices[0])).toFixed(2);

  return {
    market_hash_name: marketHashName,
    currency: "GBP",
    quote_type: "lowest_ask",
    simulated: true,
    quotes,
    lowest_price: lowest,
    highest_price: highest,
    spread,
    excluded_count: 0,
  };
}

export function staticWatchlists(): WatchlistSummary[] {
  return [
    {
      id: "00000000-0000-4000-8000-000000000001",
      name: "Offline demo — simulated prices",
      interval_seconds: 30,
      items: [
        {
          market_hash_name: "AK-47 | Redline (Field-Tested)",
          marketplaces: ["demo-market-a", "demo-market-b"],
        },
      ],
    },
  ];
}

export function staticAlertEvents(): AlertEventSummary[] {
  return [
    {
      id: "00000000-0000-4000-8000-000000000003",
      rule_id: "00000000-0000-4000-8000-000000000002",
      quote: quote(
        "demo-market-a",
        "AK-47 | Redline (Field-Tested)",
        "12.40",
        "2026-11-05T21:00:00.000Z",
      ),
      condition: "below",
      threshold: "13.00",
      triggered_at: "2026-11-05T21:00:00.000Z",
    },
  ];
}

function quote(
  marketplace: string,
  marketHashName: string,
  price: string,
  observedAt: string,
) {
  return {
    marketplace,
    market_hash_name: marketHashName,
    price,
    currency: "GBP",
    observed_at: observedAt,
    quote_type: "lowest_ask",
    external_listing_id: null,
  };
}
