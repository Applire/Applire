// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { render } from "@testing-library/react";
import { vi } from "vitest";

import { CurrentUserProvider, type CurrentUser } from "@/lib/auth";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { Allowance } from "../api";

export const ME: CurrentUser = { id: "u-1", email: "me@example.org", role: "user", has_password: true, oidc_linked: false, ui_language: null };

const CATALOG: NonNullable<Allowance["catalog"]> = {
  currency: "EUR",
  simulated: true,
  plans: [
    { id: "jobseeker", label: "Jobseeker", applications: 5, price_cents: 990 },
    { id: "jobhunter", label: "Jobhunter", applications: 20, price_cents: 2490 },
  ],
  packs: [
    { id: "pack5", applications: 5, price_cents: 990 },
    { id: "pack10", applications: 10, price_cents: 1790 },
  ],
};

/** Jobseeker, 2 of 5 left, on staging. */
export function allowance(over: Partial<Allowance> = {}): Allowance {
  return {
    policy: "allowance",
    purchasing_available: true,
    renews_on: "2026-12-01",
    applications: { limit: 5, used: 3, remaining: 2, purchased_remaining: 0, has_plan: true },
    imports: { limit: 3, used: 1, remaining: 2, purchased_remaining: 0 },
    plan: { id: "jobseeker", label: "Jobseeker", applications: 5, price_cents: 990, currency: "EUR" },
    pending: null,
    catalog: CATALOG,
    ...over,
  };
}

export const hunter = (over: Partial<Allowance> = {}): Allowance =>
  allowance({
    applications: { limit: 20, used: 5, remaining: 15, purchased_remaining: 0, has_plan: true },
    plan: { id: "jobhunter", label: "Jobhunter", applications: 20, price_cents: 2490, currency: "EUR" },
    ...over,
  });

export const zero = (over: Partial<Allowance> = {}): Allowance =>
  allowance({ applications: { limit: 5, used: 5, remaining: 0, purchased_remaining: 0, has_plan: true }, ...over });

/** A Community install with the allowance switched on by its operator. */
export const community = (over: Partial<Allowance> = {}): Allowance =>
  allowance({ purchasing_available: false, plan: null, pending: null, catalog: null, ...over });

export const noPlan = (): Allowance =>
  allowance({ applications: { limit: 0, used: 0, remaining: 0, purchased_remaining: 0, has_plan: false }, plan: null });

export interface Call {
  url: string;
  method: string;
  body: unknown;
}

const json = (status: number, body?: unknown) => new Response(body === undefined ? null : JSON.stringify(body), { status });

/** Mock fetch: GET /api/me/allowance serves `state.current`; PUT/POST are recorded. */
export function mockApi(state: { current: Allowance | null; putResult?: { change: string; allowance: Allowance }; putStatus?: number }) {
  const calls: Call[] = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = init.method ?? "GET";
    calls.push({ url, method, body: init.body ? JSON.parse(String(init.body)) : undefined });
    if (url === "/api/me/allowance" && method === "GET") return state.current ? json(200, state.current) : json(404);
    if (url === "/api/me/plan" && method === "PUT") {
      if (state.putStatus) return json(state.putStatus, { detail: { error_code: "purchasing_unavailable" } });
      return json(200, state.putResult ?? { change: "chosen", allowance: state.current });
    }
    if (url === "/api/me/purchases" && method === "POST") return json(201, { purchase: {}, allowance: state.current });
    return json(500);
  }) as unknown as typeof fetch;
  return { calls, restore: () => (globalThis.fetch = realFetch) };
}

export function renderWithUser(ui: React.ReactElement, locale: "en" | "de" = "en") {
  return render(withIntl(<CurrentUserProvider value={{ user: ME }}>{ui}</CurrentUserProvider>, locale));
}
