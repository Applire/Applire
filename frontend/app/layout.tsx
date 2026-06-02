// Copyright (C) 2024-2026 Tobias Rosenbaum
//
// This file is part of Applire.
//
// Applire is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Applire is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with Applire. If not, see <https://www.gnu.org/licenses/>.

import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "@/components/providers";
import { loadActiveSchemeVars } from "@/lib/active-theme";

export const metadata: Metadata = {
  title: "Applire — DACH CV Tailoring",
  description: "AI-powered Lebenslauf tailoring for the DACH market",
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Inline the active scheme's CSS variables on <html> during SSR so the first
  // paint already uses the DB palette. Inline custom properties override the
  // globals.css :root defaults, eliminating the flash where the static palette
  // shows briefly before the client ThemeProvider applies the active scheme
  // (GitHub issue #30). null → no inline vars, the globals.css default is used.
  const schemeVars = await loadActiveSchemeVars();

  return (
    <html lang="de" style={(schemeVars ?? undefined) as React.CSSProperties | undefined}>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=block"
        />
      </head>
      <body className="font-body bg-neutral-light text-neutral-dark antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}