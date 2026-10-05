// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// US330 (Strawberry 2a) — Playwright auth fixture for OQ specs that stub the API.
//
// The shell now reads `GET /api/auth/me` + `GET /api/auth/state` before it shows
// a page (ADR-091). A spec that stubs its API with `page.route` must stub these
// two as well, or the shell either waits on the real backend or (on a 401)
// redirects to /login. Adoption is one import line:
//
//   import { test, expect } from "../support/auth-fixture";        // signed in as admin
//   test.use({ authUser: REGULAR_USER });                           // … as a plain user
//   test.use({ authUser: null });                                   // … signed out (401)
//   test.use({ authState: { harness: true } });                     // … red test-mode banner
//
// The auto fixture registers its routes BEFORE the spec's own `page.route`
// calls; Playwright matches the most recently registered route first, so a spec
// can still override `/api/auth/*` itself. `stubAuth(page, …)` is the plain
// function for specs that do not use the extended `test`.

import { test as base, expect, type Page } from "@playwright/test";

export interface StubUser {
  id: string;
  email: string;
  role: "admin" | "user";
  has_password: boolean;
  oidc_linked: boolean;
  ui_language: string | null;
}

export interface StubAuthState {
  setup_required: boolean;
  oidc_enabled: boolean;
  oidc_button_label: string;
  smtp_enabled: boolean;
  harness: boolean;
}

export const ADMIN_USER: StubUser = {
  id: "00000000-0000-0000-0000-000000000001",
  email: "anna.bauer@example.org",
  role: "admin",
  has_password: true,
  oidc_linked: false,
  ui_language: "en",
};

export const REGULAR_USER: StubUser = {
  id: "00000000-0000-0000-0000-0000000000a2",
  email: "jonas.keller@example.org",
  role: "user",
  has_password: true,
  oidc_linked: false,
  ui_language: "en",
};

export const DEFAULT_AUTH_STATE: StubAuthState = {
  setup_required: false,
  oidc_enabled: false,
  oidc_button_label: "Single sign-on",
  smtp_enabled: false,
  harness: false,
};

export const UNAUTHENTICATED_BODY = {
  detail: { error_code: "unauthenticated", message: "Sign in to continue." },
};

export interface StubAuthOptions {
  /** The signed-in person; `null` = signed out (`/api/auth/me` → 401). */
  user?: StubUser | null;
  state?: Partial<StubAuthState>;
}

/** Stub `/api/auth/me` and `/api/auth/state` on this page. */
export async function stubAuth(page: Page, opts: StubAuthOptions = {}): Promise<void> {
  const user = opts.user === undefined ? ADMIN_USER : opts.user;
  const state = { ...DEFAULT_AUTH_STATE, ...(opts.state ?? {}) };
  await page.route("**/api/auth/me", (route) =>
    user
      ? route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(user) })
      : route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify(UNAUTHENTICATED_BODY) }),
  );
  await page.route("**/api/auth/state", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(state) }),
  );
}

type AuthFixtures = {
  authUser: StubUser | null;
  authState: Partial<StubAuthState>;
  stubbedAuth: void;
};

export const test = base.extend<AuthFixtures>({
  authUser: [ADMIN_USER, { option: true }],
  authState: [{}, { option: true }],
  stubbedAuth: [
    async ({ page, authUser, authState }, use) => {
      await stubAuth(page, { user: authUser, state: authState });
      await use();
    },
    { auto: true },
  ],
});

export { expect };
