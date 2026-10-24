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
            Monitor exact CS2 item variants through the Price Oracle engine.
            Demo mode uses clearly labelled simulated data and no credentials.
          </p>
        </header>
        {children}
        <footer className="mt-16 border-t border-[var(--border-subtle)] pt-6 text-center text-sm text-[var(--text-muted)]">
          Prices are informational observations, not trading recommendations.
        </footer>
      </div>
    </div>
  );
}
