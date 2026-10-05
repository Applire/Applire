// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Administration → Personen / Monitoring (Strawberry US324/US326/US329 UI; W0-B
 * mocks admin-users.html + tokens.html §7; rulings W0B-1, G-2, G-3, MD-28).
 *
 * Mocked API — no backend, no provider. Pins what an admin sees per person
 * (metadata only, no name column), the add-person dialog's mail checkbox reaching
 * the request (MD-28), the last-admin refusal, delete-by-typing-the-email, and
 * that a non-admin never sees the area.
 */

import { test, expect, ADMIN_USER, REGULAR_USER } from "../support/auth-fixture";
import type { Page, Route } from "@playwright/test";

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const now = new Date();
const inDays = (d: number) => new Date(now.getTime() + d * 86_400_000).toISOString();

const USERS = [
  {
    id: ADMIN_USER.id,
    email: ADMIN_USER.email,
    role: "admin",
    status: "active",
    created_at: inDays(-90),
    last_login_at: inDays(0),
    last_active_at: inDays(0),
    invite_expires_at: null,
    metadata: { application_count: 14, document_count: 30, storage_bytes: 222_298_112, ai_tokens_30d: 1_800_000 },
  },
  {
    id: "00000000-0000-0000-0000-0000000000a2",
    email: "jonas.keller@example.org",
    role: "user",
    status: "active",
    created_at: inDays(-40),
    last_login_at: inDays(-1),
    last_active_at: inDays(-1),
    invite_expires_at: null,
    metadata: { application_count: 6, document_count: 9, storage_bytes: 50_331_648, ai_tokens_30d: 420_000 },
  },
  {
    id: "00000000-0000-0000-0000-0000000000a3",
    email: "mira.santos@example.org",
    role: "user",
    status: "pending",
    created_at: inDays(-2),
    last_login_at: null,
    last_active_at: null,
    invite_expires_at: inDays(5),
    metadata: { application_count: 0, document_count: 0, storage_bytes: 0, ai_tokens_30d: 0 },
  },
  {
    id: "00000000-0000-0000-0000-0000000000a4",
    email: "lukas.brandt@example.org",
    role: "user",
    status: "disabled",
    created_at: inDays(-200),
    last_login_at: inDays(-54),
    last_active_at: inDays(-54),
    invite_expires_at: null,
    metadata: { application_count: 3, document_count: 4, storage_bytes: null, ai_tokens_30d: null },
  },
];

async function stubUsers(page: Page, users: unknown[] = USERS) {
  await page.route("**/api/admin/users", (route) =>
    route.request().method() === "GET" ? json(route, { users }) : route.fallback(),
  );
}

function row(page: Page, email: string) {
  return page.locator(`[data-testid="admin-user-row"][data-email="${email}"]`);
}

test.describe("Administration → People", () => {
  test("lists people with the W0B-1 columns — no name column — and marks the own row", async ({ page }) => {
    await stubUsers(page);
    await page.goto("/admin/users");

    const table = page.getByTestId("admin-users-table");
    await expect(table).toBeVisible();
    const headers = await table.locator("thead th").allInnerTexts();
    expect(headers.map((h) => h.trim().toLowerCase()).filter(Boolean)).toEqual([
      "email address",
      "role",
      "status",
      "last sign-in",
      "applications",
      "storage",
      "ai usage (30 days)",
      "actions",
    ]);
    await expect(row(page, ADMIN_USER.email).getByTestId("admin-user-you")).toBeVisible();
    await expect(row(page, "mira.santos@example.org")).toContainText("Invited");
    await expect(row(page, "mira.santos@example.org").getByTestId("admin-user-invite")).toContainText(
      "Invitation valid until",
    );
    await expect(row(page, "mira.santos@example.org")).toContainText("never");
    await expect(row(page, "lukas.brandt@example.org")).toContainText("Disabled");
    await expect(row(page, ADMIN_USER.email)).toContainText("212 MB");
    await expect(row(page, ADMIN_USER.email)).toContainText("1.8M tokens");
    // Admin sub-nav from the section registry (G-3), "People" active.
    await expect(page.getByTestId("admin-nav-users")).toHaveAttribute("aria-current", "page");
    await expect(page.getByTestId("admin-nav-appearance")).toBeVisible();
    await expect(page.getByTestId("admin-nav-monitoring")).toBeVisible();
  });

  test("the row menu offers the actions of the person's status (mock §2)", async ({ page }) => {
    await stubUsers(page);
    await page.goto("/admin/users");

    await row(page, "mira.santos@example.org").getByTestId("admin-user-menu").click();
    const menu = page.getByTestId("admin-user-menu-list");
    await expect(menu.getByRole("menuitem")).toHaveText(["Create new invitation link", "Delete"]);
    await page.keyboard.press("Escape");

    await row(page, "lukas.brandt@example.org").getByTestId("admin-user-menu").click();
    await expect(menu.getByRole("menuitem")).toHaveText(["Enable again", "Revoke all tokens", "Delete"]);
    await page.keyboard.press("Escape");

    await row(page, "jonas.keller@example.org").getByTestId("admin-user-menu").click();
    await expect(menu.getByRole("menuitem")).toHaveText([
      "Create reset link",
      "Make admin",
      "Revoke all tokens",
      "Disable",
      "Delete",
    ]);
  });

  test("add person: the mail checkbox reaches the request as send_mail (MD-28), the link is shown", async ({
    page,
  }) => {
    await stubUsers(page);
    const bodies: Record<string, unknown>[] = [];
    await page.route("**/api/admin/users", (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      bodies.push(route.request().postDataJSON());
      return json(
        route,
        {
          user: { ...USERS[2], email: "neu@example.org", id: "00000000-0000-0000-0000-0000000000b1" },
          link: {
            purpose: "invite",
            url: "http://applire.test/invite#tok-123",
            expires_at: inDays(7),
            mailed: false,
            mail_failed: false,
          },
        },
        201,
      );
    });
    await page.goto("/admin/users");
    await page.getByTestId("admin-add-open").click();
    const dialog = page.getByTestId("admin-add-dialog");
    await expect(dialog).toBeVisible();
    // No SMTP on this instance (fixture default): checkbox off and disabled, hint shown.
    await expect(page.getByTestId("admin-add-send-mail")).toBeDisabled();
    await expect(dialog).toContainText("Email is not set up (SMTP_HOST)");
    await expect(page.getByTestId("admin-add-controller-note")).toContainText("controller");
    await page.getByTestId("admin-add-email").fill("neu@example.org");
    await page.getByTestId("admin-add-submit").click();

    const link = page.getByTestId("admin-link-dialog");
    await expect(link).toContainText("Invitation for neu@example.org created");
    await expect(page.getByTestId("admin-link-text")).toContainText("Send this link to neu@example.org");
    await expect(page.getByTestId("admin-link-url")).toHaveText("http://applire.test/invite#tok-123");
    expect(bodies).toEqual([{ email: "neu@example.org", role: "user", send_mail: false }]);
  });

  test("an existing e-mail is refused in the dialog (409 email_taken)", async ({ page }) => {
    await stubUsers(page);
    await page.route("**/api/admin/users", (route) =>
      route.request().method() === "POST"
        ? json(route, { detail: { error_code: "email_taken", message: "x" } }, 409)
        : route.fallback(),
    );
    await page.goto("/admin/users");
    await page.getByTestId("admin-add-open").click();
    await page.getByTestId("admin-add-email").fill("jonas.keller@example.org");
    await page.getByTestId("admin-add-submit").click();
    await expect(page.getByTestId("admin-add-error")).toHaveText(
      "There is already an account for this email address.",
    );
  });

  test("disabling the last admin is refused in the action's own dialog (409 last_admin)", async ({ page }) => {
    await stubUsers(page);
    await page.route("**/api/admin/users/*", (route) =>
      route.request().method() === "PATCH"
        ? json(route, { detail: { error_code: "last_admin", message: "x" } }, 409)
        : route.fallback(),
    );
    await page.goto("/admin/users");
    await row(page, ADMIN_USER.email).getByTestId("admin-user-menu").click();
    await page.getByTestId("admin-user-action-disable").click();
    await page.getByTestId("admin-confirm-dialog-confirm").click();
    await expect(page.getByTestId("admin-confirm-dialog-error")).toContainText(
      `Not possible: ${ADMIN_USER.email} is the only active admin.`,
    );
  });

  test("delete needs the e-mail typed; the own row goes to Settings instead", async ({ page }) => {
    await stubUsers(page);
    const deleted: string[] = [];
    await page.route("**/api/admin/users/*", (route) => {
      if (route.request().method() !== "DELETE") return route.fallback();
      deleted.push(route.request().url());
      return route.fulfill({ status: 204 });
    });
    await page.goto("/admin/users");
    await row(page, "lukas.brandt@example.org").getByTestId("admin-user-menu").click();
    await page.getByTestId("admin-user-action-delete").click();
    const confirm = page.getByTestId("admin-delete-confirm");
    await expect(confirm).toBeDisabled();
    await page.getByTestId("admin-delete-email").fill("Lukas.Brandt@example.org");
    await expect(confirm).toBeEnabled();
    await confirm.click();
    await expect(page.getByTestId("admin-delete-dialog")).toHaveCount(0);
    expect(deleted).toHaveLength(1);
    expect(deleted[0]).toContain("/api/admin/users/00000000-0000-0000-0000-0000000000a4");

    await row(page, ADMIN_USER.email).getByTestId("admin-user-menu").click();
    await page.getByTestId("admin-user-action-delete").click();
    await expect(page).toHaveURL(/\/settings$/);
    expect(deleted).toHaveLength(1);
  });

  test("a reset link is shown once, with its one-hour wording", async ({ page }) => {
    await stubUsers(page);
    await page.route("**/api/admin/users/*/reset-link", (route) =>
      json(route, {
        purpose: "reset",
        url: "http://applire.test/reset#rst-9",
        expires_at: inDays(0.04),
        mailed: false,
        mail_failed: false,
      }),
    );
    await page.goto("/admin/users");
    await row(page, "jonas.keller@example.org").getByTestId("admin-user-menu").click();
    await page.getByTestId("admin-user-action-resetLink").click();
    await expect(page.getByTestId("admin-link-dialog")).toContainText("Reset link for jonas.keller@example.org");
    await expect(page.getByTestId("admin-link-text")).toContainText("valid for one hour");
    await expect(page.getByTestId("admin-link-url")).toHaveText("http://applire.test/reset#rst-9");
  });
});

test.describe("Administration → Monitoring", () => {
  test("instance panel above the monitoring tokens; create shows the token once with the curl line", async ({
    page,
  }) => {
    let tokens: unknown[] = [];
    await page.route("**/api/ops/health", (route) =>
      json(route, {
        status: "ok",
        edition: "community",
        version: "0.43.0",
        llm_provider: "mock",
        checked_at: now.toISOString(),
        components: {},
        usage: {},
      }),
    );
    await page.route("**/api/admin/probe-tokens", (route) => {
      if (route.request().method() === "GET") return json(route, { tokens });
      const created = {
        id: "00000000-0000-0000-0000-0000000000c1",
        name: route.request().postDataJSON().name,
        scope: "probe",
        prefix: "m4r8s1qa",
        created_at: now.toISOString(),
        last_used_at: null,
        token: "apl_m4r8s1qa_SECRETSECRETSECRETSECRETSECRETSECRETSECR",
      };
      tokens = [{ ...created, token: undefined }];
      return json(route, created, 201);
    });
    await page.goto("/admin/monitoring");
    await expect(page.getByTestId("operator-panel")).toBeVisible();
    await expect(page.getByTestId("monitoring-tokens")).toBeVisible();
    const panelBox = await page.getByTestId("operator-panel").boundingBox();
    const tokensBox = await page.getByTestId("monitoring-tokens").boundingBox();
    expect(panelBox!.y).toBeLessThan(tokensBox!.y);

    await page.getByTestId("monitoring-create").click();
    await page.getByTestId("monitoring-create-dialog-name").fill("Uptime Kuma");
    await page.getByTestId("monitoring-create-dialog-submit").click();
    const shown = page.getByTestId("monitoring-shown-once");
    await expect(page.getByTestId("monitoring-shown-once-secret")).toHaveText(
      "apl_m4r8s1qa_SECRETSECRETSECRETSECRETSECRETSECRETSECR",
    );
    await expect(shown).toContainText("/api/ops/health");
    await page.getByTestId("monitoring-shown-once-done").click();
    await expect(page.getByText("SECRETSECRET")).toHaveCount(0);
    await expect(page.getByTestId("monitoring-table")).toContainText("apl_m4r8s1qa_…");
  });
});

test.describe("Administration — not an admin", () => {
  test.use({ authUser: REGULAR_USER });

  test("a signed-in non-admin is sent to the dashboard and never sees the tabs", async ({ page }) => {
    let listed = 0;
    await page.route("**/api/admin/users", (route) => {
      listed += 1;
      return json(route, { detail: { error_code: "forbidden", message: "x" } }, 403);
    });
    await page.goto("/admin/users");
    await expect(page).toHaveURL(/\/dashboard/);
    await expect(page.getByTestId("admin-nav-users")).toHaveCount(0);
    expect(listed).toBe(0);
  });
});
