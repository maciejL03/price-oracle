import { formatGbp } from "../lib/format";
import type { SkinportPrice, SteamPrice } from "../types";

interface PriceCardProps {
  marketplace: "steam" | "skinport";
  price: SteamPrice | SkinportPrice;
  isCheaper: boolean;
  itemPage?: string | null;
}

function isSteamPrice(price: SteamPrice | SkinportPrice): price is SteamPrice {
  return "lowest_price" in price;
}

export function PriceCard({ marketplace, price, isCheaper, itemPage }: PriceCardProps) {
  const isSteam = marketplace === "steam";
  const accent = isSteam ? "var(--accent-steam)" : "var(--accent-skinport)";
  const label = isSteam ? "Steam Market" : "Skinport";
  const primaryPrice = isSteamPrice(price) ? price.lowest_price : price.min_price;
  const primaryLabel = isSteam ? "Lowest listing" : "Lowest price";

  return (
    <article
      className={`animate-fade-in-up rounded-2xl border bg-[var(--bg-card)] p-6 transition ${
        isCheaper
          ? "border-[var(--accent-green)]/50 shadow-[0_0_30px_rgba(52,211,153,0.08)]"
          : "border-[var(--border-subtle)]"
      }`}
      style={{ animationDelay: isSteam ? "0ms" : "80ms" }}
    >
      <div className="mb-5 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span
            className="inline-block h-2.5 w-2.5 rounded-full"
            style={{ backgroundColor: accent }}
          />
          <h3 className="text-sm font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
            {label}
          </h3>
        </div>
        {isCheaper && (
          <span className="rounded-full bg-[var(--accent-green)]/15 px-2.5 py-0.5 text-xs font-semibold text-[var(--accent-green)]">
            Cheaper
          </span>
        )}
      </div>

      <p className="font-mono text-3xl font-semibold tracking-tight">
        {formatGbp(primaryPrice)}
      </p>
      <p className="mt-1 text-sm text-[var(--text-muted)]">{primaryLabel}</p>

      <dl className="mt-5 space-y-2 border-t border-[var(--border-subtle)] pt-4 text-sm">
        {isSteamPrice(price) ? (
          <>
            {price.median_price !== null && (
              <div className="flex justify-between gap-4">
                <dt className="text-[var(--text-muted)]">Median</dt>
                <dd className="font-mono">{formatGbp(price.median_price)}</dd>
              </div>
            )}
            {price.volume !== null && (
              <div className="flex justify-between gap-4">
                <dt className="text-[var(--text-muted)]">Volume (24h)</dt>
                <dd className="font-mono">{price.volume.toLocaleString("en-GB")}</dd>
              </div>
            )}
          </>
        ) : (
          <>
            {price.median_price !== null && (
              <div className="flex justify-between gap-4">
                <dt className="text-[var(--text-muted)]">Median</dt>
                <dd className="font-mono">{formatGbp(price.median_price)}</dd>
              </div>
            )}
            {price.mean_price !== null && (
              <div className="flex justify-between gap-4">
                <dt className="text-[var(--text-muted)]">Mean</dt>
                <dd className="font-mono">{formatGbp(price.mean_price)}</dd>
              </div>
            )}
            {price.quantity !== null && (
              <div className="flex justify-between gap-4">
                <dt className="text-[var(--text-muted)]">Listings</dt>
                <dd className="font-mono">{price.quantity.toLocaleString("en-GB")}</dd>
              </div>
            )}
          </>
        )}
      </dl>

      {itemPage && (
        <a
          href={itemPage}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-5 inline-flex text-sm font-medium transition hover:underline"
          style={{ color: accent }}
        >
          View on {label} →
        </a>
      )}
    </article>
  );
}
