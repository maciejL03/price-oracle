import type { ReactNode } from "react";

interface LayoutProps {
  children: ReactNode;
}

export function Layout({ children }: LayoutProps) {
  return (
    <div className="min-h-screen px-4 py-10 sm:px-6 lg:px-8">
      <div className="mx-auto max-w-4xl">
        <header className="mb-10 text-center">
          <p className="mb-2 text-sm font-medium uppercase tracking-[0.2em] text-[var(--text-muted)]">
            Counter-Strike 2
          </p>
          <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
            Price Oracle
          </h1>
          <p className="mx-auto mt-3 max-w-xl text-[var(--text-secondary)]">
            Compare Steam Market and Skinport prices in GBP. Search any CS2 item
            and see which marketplace is cheaper.
          </p>
        </header>
        {children}
        <footer className="mt-16 border-t border-[var(--border-subtle)] pt-6 text-center text-sm text-[var(--text-muted)]">
          Prices from Steam Community Market (GB) and Skinport API. For
          informational use only.
        </footer>
      </div>
    </div>
  );
}
