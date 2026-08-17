import { PriceCard } from "./PriceCard";
import { formatGbp } from "../lib/format";
import type { CheaperMarket, ComparisonResult } from "../types";

const STEAM_APP_ID = 730;

function steamListingUrl(marketHashName: string): string {
  return `https://steamcommunity.com/market/listings/${STEAM_APP_ID}/${encodeURIComponent(marketHashName)}`;
}

interface ComparisonBannerProps {
  comparison: ComparisonResult;
}

function cheaperLabel(cheaper: CheaperMarket): string {
  switch (cheaper) {
    case "steam":
      return "Steam Market is cheaper";
    case "skinport":
      return "Skinport is cheaper";
    case "same":
      return "Prices are the same";
  }
}

function ComparisonBanner({ comparison }: ComparisonBannerProps) {
  const { cheaper, difference, percentage } = comparison;
  const isSame = cheaper === "same";

  return (
    <section
      className="animate-fade-in-up rounded-2xl border border-[var(--border-accent)] bg-[var(--bg-elevated)] p-6 text-center"
      style={{ animationDelay: "160ms" }}
    >
      <p
        className={`text-lg font-semibold ${
          isSame ? "text-[var(--text-secondary)]" : "text-[var(--accent-green)]"
        }`}
      >
        {cheaperLabel(cheaper)}
      </p>
      {!isSame && (
        <div className="mt-3 flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-sm text-[var(--text-secondary)]">
          <span>
            Difference:{" "}
            <strong className="font-mono text-[var(--text-primary)]">
              {formatGbp(difference)}
            </strong>
          </span>
          <span>
            Spread:{" "}
            <strong className="font-mono text-[var(--accent-amber)]">
              {percentage.toFixed(2)}%
            </strong>
          </span>
        </div>
      )}
    </section>
  );
}

interface ComparisonViewProps {
  comparison: ComparisonResult;
}

function PriceCardWrapper({
  marketplace,
  comparison,
  isCheaper,
}: {
  marketplace: "steam" | "skinport";
  comparison: ComparisonResult;
  isCheaper: boolean;
}) {
  const showCheaperBadge = isCheaper && comparison.cheaper !== "same";

  if (marketplace === "steam") {
    return (
      <PriceCard
        marketplace="steam"
        price={comparison.steam}
        isCheaper={showCheaperBadge}
        itemPage={steamListingUrl(comparison.market_hash_name)}
      />
    );
  }

  return (
    <PriceCard
      marketplace="skinport"
      price={comparison.skinport}
      isCheaper={showCheaperBadge}
      itemPage={comparison.skinport.item_page}
    />
  );
}

export function ComparisonView({ comparison }: ComparisonViewProps) {
  const steamCheaper = comparison.cheaper === "steam";
  const skinportCheaper = comparison.cheaper === "skinport";

  return (
    <div className="space-y-6">
      <div className="animate-fade-in-up flex items-center gap-4 rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-card)] p-4">
        {comparison.item_image && (
          <img
            src={comparison.item_image}
            alt=""
            className="h-16 w-16 shrink-0 rounded-lg object-contain bg-[var(--bg-base)]"
          />
        )}
        <div>
          <h2 className="text-lg font-semibold leading-snug">
            {comparison.item_name}
          </h2>
          <p className="mt-0.5 text-sm text-[var(--text-muted)]">
            {comparison.market_hash_name}
          </p>
        </div>
      </div>

      <ComparisonBanner comparison={comparison} />

      <div className="grid gap-4 sm:grid-cols-2">
        <PriceCardWrapper
          marketplace="steam"
          comparison={comparison}
          isCheaper={steamCheaper}
        />
        <PriceCardWrapper
          marketplace="skinport"
          comparison={comparison}
          isCheaper={skinportCheaper}
        />
      </div>
    </div>
  );
}
