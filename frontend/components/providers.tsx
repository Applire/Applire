"use client";

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


import { ErrorBoundary } from "@/components/error-boundary";
import { OfflineBanner } from "@/components/offline-banner";
import { ThemeProvider } from "@/components/theme-provider";
import { LocaleProvider } from "@/lib/providers/locale-provider";
import { CurrentUserProvider } from "@/lib/auth/current-user";
import { installAuthFetch } from "@/lib/auth/fetch-patch";

// US330 (ADR-091): patch window.fetch at module load — BEFORE any component
// effect fires its first API call — so a 401 `unauthenticated` anywhere in the
// app sends the person to /login?next=… without an edit in the calling files.
installAuthFetch();

interface ProvidersProps {
  children: React.ReactNode;
}

export function Providers({ children }: ProvidersProps) {
  return (
    <ThemeProvider>
      <CurrentUserProvider>
        <LocaleProvider>
          <ErrorBoundary>
            <OfflineBanner />
            {children}
          </ErrorBoundary>
        </LocaleProvider>
      </CurrentUserProvider>
    </ThemeProvider>
  );
}