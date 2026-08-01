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


import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { NextIntlClientProvider } from "next-intl";
import enMessages from "../../messages/en.json";
import deMessages from "../../messages/de.json";

type Locale = "de" | "en";

const messages: Record<Locale, typeof enMessages> = {
  en: enMessages,
  de: deMessages,
};

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

interface LocaleContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => Promise<void>;
}

const LocaleContext = createContext<LocaleContextValue>({
  locale: "en",
  setLocale: async () => {},
});

export function useLocale() {
  return useContext(LocaleContext);
}

export function LocaleProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>("en");

  useEffect(() => {
    fetch(`${API_BASE}/api/settings`)
      .then((r) => r.json())
      .then((data) => {
        const lang = data.ui_language as Locale;
        if (lang === "de" || lang === "en") {
          setLocaleState(lang);
          document.documentElement.lang = lang;
          // ADR-038 (amended 2026-08-01, #400): the UI is an explicit language
          // context — persist the active locale once so "no explicit choice"
          // reliably means a headless/agent-channel journey.
          if (data.ui_language_explicit === false) {
            fetch(`${API_BASE}/api/settings`, {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ ui_language: lang }),
            }).catch(() => {
              // Best-effort — the next load retries.
            });
          }
        }
      })
      .catch(() => {
        // Network error — stay with "en" default
      });
  }, []);

  const setLocale = useCallback(async (newLocale: Locale) => {
    await fetch(`${API_BASE}/api/settings`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ui_language: newLocale }),
    });
    setLocaleState(newLocale);
    document.documentElement.lang = newLocale;
  }, []);

  return (
    <LocaleContext.Provider value={{ locale, setLocale }}>
      <NextIntlClientProvider locale={locale} messages={messages[locale]}>
        {children}
      </NextIntlClientProvider>
    </LocaleContext.Provider>
  );
}
