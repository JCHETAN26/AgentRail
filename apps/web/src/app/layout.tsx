import type { Metadata } from 'next';
import type { ReactNode } from 'react';

import { Providers } from './providers';
import './globals.css';

export const metadata: Metadata = {
  title: 'AgentRail',
  description:
    'Evaluate, debug, govern and safely release tool-using AI agents. Phase 0 deterministic slice.',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <div className="shell">
            <header className="shell__header">
              <span className="brand">AgentRail</span>
              <span className="badge badge--mode" title="No model provider is involved">
                Deterministic mode
              </span>
            </header>
            <main className="shell__main">{children}</main>
            <footer className="shell__footer">
              Synthetic data only. The CloudOps sandbox models no real infrastructure.
            </footer>
          </div>
        </Providers>
      </body>
    </html>
  );
}
