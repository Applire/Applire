// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Settings → Konto + Tokens (Strawberry US326/US327/US323 UI; W0-B mocks
 * settings-account.html + tokens.html §1–§6; G-2; MD-7; MD-25 callback states).
 *
 * Mocked API — no backend, no provider. Pins: the cards sit above the existing
 * ones; a token's secret is shown exactly once; the IdP round-trip markers
 * (`?reauth=…`, `?oidc=…`) land in the right state and leave the address bar; the
 * last admin cannot delete themselves; an SSO-only account confirms via the IdP.
 */

import { test, expect, ADMIN_USER } from "../support/auth-fixture";
import type { Page, Route } from "@playwright/test";

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const NOW = new Date().toISOString();

async function stubSettings(page: Page, tokens: unknown[] = []) {
  await page.route("**/api/settings", (route) =>
    json(route, { ui_language: "en", default_accent_hex: "#2b5fa8", target_cv_pages: null }),
  );
  await page.route("**/api/me/tokens", (route) =>
    route.request().method() === "GET" ? json(route, { tokens }) : route.fallback(),
  );
}

const SSO = { oidc_enabled: true, oidc_button_label: "Company login" };

test.describe("Settings → Account", () => {
  test("account and tokens cards come first, above language and data & privacy", async ({ page }) => {
    await stubSettings(page);
    await page.goto("/settings");
    await expect(page.getByTestId("account-email")).toHaveText(ADMIN_USER.email);
    await expect(page.getByTestId("account-card").getByTestId("role-badge")).toHaveText("Admin");
    const account = await page.getByTestId("account-card").boundingBox();
    const tokens = await page.getByTestId("tokens-card").boundingBox();
    const language = await page.getByTestId("lang-switch-de").boundingBox();
    expect(account!.y).toBeLessThan(tokens!.y);
    expect(tokens!.y).toBeLessThan(language!.y);
    // No SSO configured on this instance → no SSO row.
    await expect(page.getByTestId("account-sso-row")).toHaveCount(0);
  });

  test("a wrong current password is named on the field (403 invalid_credentials)", async ({ page }) => {
    await stubSettings(page);
    await page.route("**/api/auth/password", (route) =>
      json(route, { detail: { error_code: "invalid_credentials", message: "x" } }, 403),
    );
    await page.goto("/settings");
    await page.getByTestId("account-password-open").click();
    await page.getByTestId("account-password-current").fill("not-the-password");
    await page.getByTestId("account-password-new").fill("a sentence I can remember");
    await page.getByTestId("account-password-repeat").fill("a sentence I can remember");
    await page.getByTestId("account-password-save").click();
    await expect(page.getByTestId("account-password-error")).toHaveText("The current password is incorrect.");
    // A 403 is not a lost session: still on Settings, not redirected to sign-in.
    await expect(page).toHaveURL(/\/settings/);
  });

  test("the only admin cannot delete their account (409 last_admin)", async ({ page }) => {
    await stubSettings(page);
    await page.route("**/api/me/account", (route) =>
      json(route, { detail: { error_code: "last_admin", message: "x" } }, 409),
    );
    await page.goto("/settings");
    await page.getByTestId("account-delete-open").click();
    await page.getByTestId("account-delete-password").fill("my long password here");
    await page.getByTestId("account-delete-confirm").click();
    await expect(page.getByTestId("account-delete-last-admin")).toContainText("You are the only admin");
  });

});

test.describe("Settings → Account (SSO-only account)", () => {
  test.use({
    authUser: { ...ADMIN_USER, has_password: false, oidc_linked: true },
    authState: SSO,
  });

  test("no password row button, no unlink (it is the only way in)", async ({ page }) => {
    await stubSettings(page);
    await page.goto("/settings");
    await expect(page.getByTestId("account-password-row")).toContainText("You sign in with Company login");
    await expect(page.getByTestId("account-password-open")).toHaveCount(0);
    await expect(page.getByTestId("account-sso-row")).toContainText("Linked");
    await expect(page.getByTestId("account-sso-unlink")).toHaveCount(0);
  });

  test("self-delete confirms through the IdP (MD-7) and returns confirmed (MD-25)", async ({ page }) => {
    await stubSettings(page);
    const starts: unknown[] = [];
    await page.route("**/api/me/reauth/start", (route) => {
      starts.push(route.request().postDataJSON());
      // Stay on the app: the "IdP" sends the browser straight back confirmed.
      return json(route, { authorize_url: "/settings?reauth=account.delete" });
    });
    let deletedWith: unknown = null;
    await page.route("**/api/me/account", (route) => {
      deletedWith = route.request().postDataJSON();
      return route.fulfill({ status: 204 });
    });
    await page.goto("/settings");
    await page.getByTestId("account-delete-open").click();
    await expect(page.getByTestId("account-delete-dialog")).toContainText("sign in with Company login once more");
    await page.getByTestId("account-delete-continue-oidc").click();
    expect(starts).toEqual([{ action: "account.delete", target_id: ADMIN_USER.id }]);

    await expect(page.getByTestId("account-delete-confirmed")).toContainText("Confirmed with Company login");
    await expect(page).toHaveURL(/\/settings$/);
    await page.getByTestId("account-delete-confirm").click();
    await expect(page).toHaveURL(/\/login/);
    expect(deletedWith).toEqual({});
  });
});

test.describe("Settings → Account (password + SSO not linked)", () => {
  test.use({ authState: SSO });

  test("an OIDC link round-trip lands as a confirmation and leaves the address bar clean", async ({ page }) => {
    await stubSettings(page);
    await page.goto("/settings?oidc=linked");
    await expect(page.getByTestId("account-sso-notice")).toContainText("Your account is now linked with Company login.");
    await expect(page).toHaveURL(/\/settings$/);
  });

  test("a failed link round-trip says so (?oidc=failed)", async ({ page }) => {
    await stubSettings(page);
    await page.route("**/api/me/oidc/link", (route) => json(route, { authorize_url: "/settings?oidc=failed" }));
    await page.goto("/settings");
    await page.getByTestId("account-sso-link").click();
    await expect(page.getByTestId("account-sso-notice")).toContainText("Linking with Company login did not work");
    await expect(page).toHaveURL(/\/settings$/);
  });
});

test.describe("Settings → Tokens", () => {
  test("create an agent token: shown once with the MCP snippet, then only its prefix", async ({ page }) => {
    let tokens: unknown[] = [];
    await stubSettings(page);
    await page.route("**/api/me/tokens", (route) => {
      if (route.request().method() === "GET") return json(route, { tokens });
      const req = route.request().postDataJSON();
      const created = {
        id: "00000000-0000-0000-0000-0000000000d1",
        name: req.name,
        scope: req.scope,
        prefix: "3kq8x2mz",
        created_at: NOW,
        last_used_at: null,
        token: "apl_3kq8x2mz_Vb2nQ8xR4tYk1LmZ0aPc7dEf9gHj3KsWuT6oNiBv5yX",
      };
      tokens = [{ ...created, token: undefined }];
      return json(route, created, 201);
    });
    await page.goto("/settings");
    await expect(page.getByTestId("tokens-agent-table-empty")).toHaveText("No tokens yet.");
    await page.getByTestId("tokens-agent-create").click();
    await page.getByTestId("tokens-create-dialog-name").fill("Claude Desktop (laptop)");
    await page.getByTestId("tokens-create-dialog-submit").click();

    const shown = page.getByTestId("tokens-shown-once");
    await expect(page.getByTestId("tokens-shown-once-secret")).toHaveText(
      "apl_3kq8x2mz_Vb2nQ8xR4tYk1LmZ0aPc7dEf9gHj3KsWuT6oNiBv5yX",
    );
    await expect(shown).toContainText("APPLIRE_AGENT_TOKEN");
    await expect(shown).toContainText('"mcpServers"');
    await page.getByTestId("tokens-shown-once-done").click();

    await expect(page.getByText("Vb2nQ8xR4tYk1LmZ0aPc7dEf9gHj3KsWuT6oNiBv5yX")).toHaveCount(0);
    await expect(page.getByTestId("tokens-agent-table")).toContainText("Claude Desktop (laptop)");
    await expect(page.getByTestId("tokens-agent-table")).toContainText("apl_3kq8x2mz_…");
  });

  test("revoking asks first, then DELETEs that token", async ({ page }) => {
    const revoked: string[] = [];
    await stubSettings(page, [
      { id: "00000000-0000-0000-0000-0000000000e1", name: "Backup script", scope: "api", prefix: "7tz4c0hb", created_at: NOW, last_used_at: NOW },
    ]);
    await page.route("**/api/me/tokens/*", (route) => {
      revoked.push(route.request().url());
      return route.fulfill({ status: 204 });
    });
    await page.goto("/settings");
    await page.getByTestId("tokens-api-table-revoke").click();
    const dialog = page.getByTestId("tokens-revoke-dialog");
    await expect(dialog).toContainText("Revoke token “Backup script”?");
    await expect(dialog).toContainText("Scripts using this token lose access immediately.");
    await page.getByTestId("tokens-revoke-dialog-confirm").click();
    await expect(dialog).toHaveCount(0);
    expect(revoked).toEqual([expect.stringContaining("/api/me/tokens/00000000-0000-0000-0000-0000000000e1")]);
  });
});
