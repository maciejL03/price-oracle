import { useEffect, useRef, useState } from "react";
import type { CatalogItem } from "../types";

interface SearchBoxProps {
  query: string;
  onQueryChange: (value: string) => void;
  results: CatalogItem[];
  isSearching: boolean;
  searchError: string | null;
  onSelect: (item: CatalogItem) => void;
}

export function SearchBox({
  query,
  onQueryChange,
  results,
  isSearching,
  searchError,
  onSelect,
}: SearchBoxProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [highlightIndex, setHighlightIndex] = useState(-1);
  const containerRef = useRef<HTMLDivElement>(null);

  const showDropdown =
    isOpen && query.trim().length >= 2 && (results.length > 0 || isSearching || !!searchError);

  useEffect(() => {
    setHighlightIndex(-1);
  }, [results, query]);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
      }
    }

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  function handleKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (!showDropdown || results.length === 0) return;

    if (event.key === "ArrowDown") {
      event.preventDefault();
      setHighlightIndex((prev) => Math.min(prev + 1, results.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlightIndex((prev) => Math.max(prev - 1, 0));
    } else if (event.key === "Enter" && highlightIndex >= 0) {
      event.preventDefault();
      onSelect(results[highlightIndex]);
      setIsOpen(false);
    } else if (event.key === "Escape") {
      setIsOpen(false);
    }
  }

  return (
    <div ref={containerRef} className="relative">
      <label htmlFor="item-search" className="sr-only">
        Search CS2 items
      </label>
      <div className="relative">
        <svg
          className="pointer-events-none absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-[var(--text-muted)]"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
          aria-hidden
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z"
          />
        </svg>
        <input
          id="item-search"
          type="search"
          autoComplete="off"
          spellCheck={false}
          placeholder="Search items… e.g. AK-47 | Redline (Field-Tested)"
          value={query}
          onChange={(event) => {
            onQueryChange(event.target.value);
            setIsOpen(true);
          }}
          onFocus={() => setIsOpen(true)}
          onKeyDown={handleKeyDown}
          className="w-full rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] py-4 pl-12 pr-4 text-base text-[var(--text-primary)] outline-none transition focus:border-[var(--accent-steam)] focus:ring-2 focus:ring-[var(--accent-steam)]/20"
        />
        {isSearching && (
          <span className="absolute right-4 top-1/2 -translate-y-1/2 text-xs text-[var(--text-muted)] animate-pulse-soft">
            Searching…
          </span>
        )}
      </div>

      {showDropdown && (
        <ul
          role="listbox"
          className="absolute z-20 mt-2 max-h-72 w-full overflow-auto rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-card)] py-2 shadow-2xl"
        >
          {searchError && (
            <li className="px-4 py-3 text-sm text-[var(--accent-red)]">
              {searchError}
            </li>
          )}
          {!searchError && !isSearching && results.length === 0 && (
            <li className="px-4 py-3 text-sm text-[var(--text-muted)]">
              No items found
            </li>
          )}
          {results.map((item, index) => (
            <li key={item.market_hash_name} role="option" aria-selected={index === highlightIndex}>
              <button
                type="button"
                onMouseEnter={() => setHighlightIndex(index)}
                onClick={() => {
                  onSelect(item);
                  setIsOpen(false);
                }}
                className={`flex w-full items-center gap-3 px-4 py-2.5 text-left transition ${
                  index === highlightIndex
                    ? "bg-[var(--border-subtle)]"
                    : "hover:bg-[var(--border-subtle)]/60"
                }`}
              >
                {item.image && (
                  <img
                    src={item.image}
                    alt=""
                    className="h-10 w-10 shrink-0 rounded object-contain bg-[var(--bg-base)]"
                    loading="lazy"
                  />
                )}
                <span className="text-sm leading-snug">{item.market_hash_name}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
