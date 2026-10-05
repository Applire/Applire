// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { render, screen, waitFor } from "@testing-library/react";
import { useTranslations } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CurrentUserProvider, type CurrentUserValue } from "@/lib/auth/current-user";
import de from "@/messages/de.json";
import en from "@/messages/en.json";

import { LocaleProvider } from "../locale-provider";

function Probe() {
  const t = useTranslations("auth");
  return <span data-testid="title">{t("loginTitle")}</span>;
}

const realFetch = globalThis.fetch;
const realLanguages = Object.getOwnPropertyDescriptor(Navigator.prototype, "languages");

function browserLanguages(langs: string[]) {
  Object.defineProperty(window.navigator, "languages", { configurable: true, get: () => langs });
}

afterEach(() => {
  globalThis.fetch = realFetch;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  delete (window.navigator as any).languages;
  if (realLanguages) Object.defineProperty(Navigator.prototype, "languages", realLanguages);
});

const USER = { id: "u", email: "a@b.c", role: "user" as const, has_password: true, oidc_linked: false, ui_language: null };

function renderWith(auth: Partial<CurrentUserValue>) {
  return render(
    <CurrentUserProvider value={auth}>
      <LocaleProvider>
        <Probe />
      </LocaleProvider>
    </CurrentUserProvider>,
  );
}

describe("LocaleProvider × sign-in (US330)", () => {
  it("signed out: the browser language decides, /api/settings is never called", async () => {
    browserLanguages(["de-DE", "en"]);
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    renderWith({ status: "unauthenticated" });
    await waitFor(() => expect(screen.getByTestId("title").textContent).toBe(de.auth.loginTitle));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("signed in, explicit choice: the stored language wins over the browser", async () => {
    browserLanguages(["de-DE"]);
    globalThis.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ ui_language: "en", ui_language_explicit: true })),
    ) as unknown as typeof fetch;
    renderWith({ user: { ...USER, ui_language: "en" } });
    await waitFor(() => expect(screen.getByTestId("title").textContent).toBe(en.auth.loginTitle));
  });

  it("signed in, never chosen: keeps the browser language (no flip to the served 'en') and persists it", async () => {
    browserLanguages(["de-AT"]);
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
      init?.method === "PATCH"
        ? new Response("{}")
        : new Response(JSON.stringify({ ui_language: "en", ui_language_explicit: false })),
    );
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    renderWith({ user: USER });
    await waitFor(() => expect(screen.getByTestId("title").textContent).toBe(de.auth.loginTitle));
    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH");
      expect(patch && JSON.parse(String(patch[1]!.body))).toEqual({ ui_language: "de" });
    });
  });
});
