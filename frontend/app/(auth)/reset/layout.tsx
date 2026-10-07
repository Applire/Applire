// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import type { Metadata } from "next";

/**
 * Contract §1 "Link tokens": set-password pages send `Referrer-Policy:
 * no-referrer`, so nothing this page loads learns its URL. The token itself
 * lives only in the fragment (never sent anyway); this closes the remaining
 * referrer channel for the path.
 */
export const metadata: Metadata = { referrer: "no-referrer" };

export default function SetPasswordLayout({ children }: { children: React.ReactNode }) {
  return children;
}
