// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { HarnessBanner } from "@/components/shell/HarnessBanner";

/**
 * US330 — the signed-out pages (/login, /setup, /forgot, /invite, /reset):
 * no sidebar, one centred card (W0-B mocks, G-1). Never wrapped by the shell's
 * auth gate.
 */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-surface-dim">
      <HarnessBanner />
      <main className="flex flex-1 items-center justify-center px-4 py-10">{children}</main>
    </div>
  );
}
