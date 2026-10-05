// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CurrentUserProvider, type AuthState, type CurrentUser } from "@/lib/auth";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { AdminUser } from "../api";
import { UsersAdmin } from "../UsersAdmin";
import { actionsFor } from "../UsersTable";

const push = vi.fn();
const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace }),
  usePathname: () => "/admin/users",
}));

const ME: CurrentUser = {
  id: "u-1",
  email: "tobias@example.org",
  role: "admin",
  has_password: true,
  oidc_linked: false,
  ui_language: null,
};

const user = (over: Partial<AdminUser> & { id: string; email: string }): AdminUser => ({
  role: "user",
  status: "active",
  created_at: "2026-08-01T10:00:00Z",
  last_login_at: null,
  last_active_at: null,
  invite_expires_at: null,
  metadata: { application_count: 0, document_count: 0, storage_bytes: null, ai_tokens_30d: null },
  ...over,
});

const MB = 1024 * 1024;
const OWN = user({
  id: "u-1",
  email: "tobias@example.org",
  role: "admin",
  last_login_at: "2020-01-01T10:00:00Z",
  metadata: { application_count: 7, document_count: 12, storage_bytes: 212 * MB, ai_tokens_30d: 1_800_000 },
});
const PENDING = user({ id: "u-2", email: "new@example.org", status: "pending", invite_expires_at: "2099-01-01T12:00:00Z" });
const DISABLED = user({ id: "u-3", email: "gone@example.org", status: "disabled" });
const PLAIN = user({ id: "u-4", email: "maria@example.org" });
const OTHER_ADMIN = user({ id: "u-5", email: "second@example.org", role: "admin" });

let users: AdminUser[];
let calls: { url: string; method: string; body: unknown }[];
const realFetch = globalThis.fetch;

const json = (status: number, body?: unknown) => new Response(body === undefined ? null : JSON.stringify(body), { status });
const err = (status: number, code: string) => json(status, { detail: { error_code: code, message: "x" } });

function mockApi(custom: (url: string, method: string) => Response | undefined = () => undefined) {
  calls = [];
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = init.method ?? "GET";
    calls.push({ url, method, body: init.body ? JSON.parse(String(init.body)) : undefined });
    const c = custom(url, method);
    if (c) return c;
    if (url === "/api/admin/users" && method === "GET") return json(200, { users });
    return json(500);
  }) as unknown as typeof fetch;
}
const mutations = () => calls.filter((c) => c.method !== "GET");

function renderAdmin(smtp = false) {
  const state: AuthState = { setup_required: false, oidc_enabled: false, oidc_button_label: "SSO", smtp_enabled: smtp, harness: false };
  return render(withIntl(
    <CurrentUserProvider value={{ user: ME, authState: state }}>
      <UsersAdmin />
    </CurrentUserProvider>,
  ));
}

const row = (email: string) =>
  screen.getAllByTestId("admin-user-row").find((r) => r.getAttribute("data-email") === email)!;

async function openMenu(u: ReturnType<typeof userEvent.setup>, email: string) {
  await u.click(within(row(email)).getByTestId("admin-user-menu"));
  return screen.getByTestId("admin-user-menu-list");
}

beforeEach(() => {
  users = [OWN, PENDING, DISABLED, PLAIN, OTHER_ADMIN];
  push.mockReset();
  replace.mockReset();
  mockApi();
});
afterEach(() => {
  cleanup();
  globalThis.fetch = realFetch;
});

describe("actionsFor", () => {
  it("pending: reinvite + delete", () => {
    expect(actionsFor(PENDING)).toEqual(["reinvite", "delete"]);
  });
  it("disabled: enable, revokeTokens, delete", () => {
    expect(actionsFor(DISABLED)).toEqual(["enable", "revokeTokens", "delete"]);
  });
  it("active admin: offers makeUser, not makeAdmin", () => {
    const a = actionsFor(OWN);
    expect(a).toContain("makeUser");
    expect(a).not.toContain("makeAdmin");
    expect(a[a.length - 1]).toBe("delete");
  });
  it("active user: offers makeAdmin, not makeUser", () => {
    const a = actionsFor(PLAIN);
    expect(a).toEqual(["resetLink", "makeAdmin", "revokeTokens", "disable", "delete"]);
  });
});

describe("UsersAdmin — table", () => {
  it("renders the columns, without a name column", async () => {
    renderAdmin();
    const table = await screen.findByTestId("admin-users-table");
    const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual([
      "Email address",
      "Role",
      "Status",
      "Last sign-in",
      "Applications",
      "Storage",
      "AI usage (30 days)",
      "Actions",
    ]);
    expect(headers.some((h) => /name/i.test(h ?? ""))).toBe(false);
  });

  it("marks only the own row with (you)", async () => {
    renderAdmin();
    await screen.findByTestId("admin-users-table");
    expect(screen.getAllByTestId("admin-user-you")).toHaveLength(1);
    expect(within(row("tobias@example.org")).getByTestId("admin-user-you")).toHaveTextContent("(you)");
  });

  it("status badges per row", async () => {
    renderAdmin();
    await screen.findByTestId("admin-users-table");
    expect(within(row("tobias@example.org")).getByTestId("user-status")).toHaveTextContent("Active");
    expect(within(row("new@example.org")).getByTestId("user-status")).toHaveTextContent("Invited");
    expect(within(row("gone@example.org")).getByTestId("user-status")).toHaveTextContent("Disabled");
  });

  it("a pending row shows when the invitation expires; others do not", async () => {
    renderAdmin();
    await screen.findByTestId("admin-users-table");
    expect(within(row("new@example.org")).getByTestId("admin-user-invite")).toHaveTextContent(
      "Invitation valid until 01/01/2099",
    );
    expect(within(row("maria@example.org")).queryByTestId("admin-user-invite")).toBeNull();
  });

  it("an expired pending invitation says so", async () => {
    users = [OWN, { ...PENDING, invite_expires_at: "2020-01-01T12:00:00Z" }];
    renderAdmin();
    await screen.findByTestId("admin-users-table");
    expect(within(row("new@example.org")).getByTestId("admin-user-invite")).toHaveTextContent("Invitation expired");
  });

  it("metadata: storage null -> em dash, ai tokens null -> em dash, values formatted", async () => {
    renderAdmin();
    await screen.findByTestId("admin-users-table");
    const own = within(row("tobias@example.org"));
    expect(own.getByText("7")).toBeInTheDocument();
    expect(own.getByText(/^212\s?MB$/)).toBeInTheDocument();
    expect(own.getByText("1.8M tokens")).toBeInTheDocument();
    const plain = within(row("maria@example.org"));
    expect(plain.getAllByText("—")).toHaveLength(2);
  });

  it("never-signed-in people show 'never'", async () => {
    renderAdmin();
    await screen.findByTestId("admin-users-table");
    expect(within(row("maria@example.org")).getByText("never")).toBeInTheDocument();
  });

  it("a failed load shows the error", async () => {
    mockApi((url) => (url === "/api/admin/users" ? json(500) : undefined));
    renderAdmin();
    expect(await screen.findByRole("alert")).toHaveTextContent("The people could not be loaded.");
  });

  it("the row menu offers the actions for the row's status and role", async () => {
    const u = userEvent.setup();
    renderAdmin();
    await screen.findByTestId("admin-users-table");

    let menu = await openMenu(u, "new@example.org");
    expect(within(menu).getAllByRole("menuitem").map((m) => m.textContent)).toEqual([
      "Create new invitation link",
      "Delete",
    ]);
    await u.keyboard("{Escape}");

    menu = await openMenu(u, "gone@example.org");
    expect(within(menu).getAllByRole("menuitem").map((m) => m.textContent)).toEqual([
      "Enable again",
      "Revoke all tokens",
      "Delete",
    ]);
    await u.keyboard("{Escape}");

    menu = await openMenu(u, "second@example.org");
    expect(within(menu).getByText("Remove admin rights")).toBeInTheDocument();
    expect(within(menu).queryByText("Make admin")).toBeNull();
    await u.keyboard("{Escape}");

    menu = await openMenu(u, "maria@example.org");
    expect(within(menu).getByText("Make admin")).toBeInTheDocument();
    expect(within(menu).queryByText("Remove admin rights")).toBeNull();
  });
});

describe("UsersAdmin — add person", () => {
  async function openAdd(u: ReturnType<typeof userEvent.setup>) {
    await screen.findByTestId("admin-users-table");
    await u.click(screen.getByTestId("admin-add-open"));
  }

  const created = (mailed: boolean, mailFailed = false) =>
    json(201, {
      user: user({ id: "u-9", email: "kim@example.org", status: "pending" }),
      link: {
        purpose: "invite",
        url: "https://applire.test/invite?token=abc",
        expires_at: "2026-10-12T10:00:00Z",
        mailed,
        mail_failed: mailFailed,
      },
    });

  it("smtp enabled + box checked (default): send_mail true", async () => {
    mockApi((url, m) => (url === "/api/admin/users" && m === "POST" ? created(true) : undefined));
    const u = userEvent.setup();
    renderAdmin(true);
    await openAdd(u);
    const box = screen.getByTestId("admin-add-send-mail");
    expect(box).toBeChecked();
    expect(box).toBeEnabled();
    await u.type(screen.getByTestId("admin-add-email"), "kim@example.org");
    await u.click(screen.getByTestId("admin-add-submit"));
    await screen.findByTestId("admin-link-dialog");
    expect(mutations()[0]).toEqual({
      url: "/api/admin/users",
      method: "POST",
      body: { email: "kim@example.org", role: "user", send_mail: true },
    });
    expect(screen.getByTestId("admin-link-text")).toHaveTextContent("We sent an email with the link.");
  });

  it("smtp enabled + box unchecked: send_mail false (sent explicitly), role admin carried", async () => {
    mockApi((url, m) => (url === "/api/admin/users" && m === "POST" ? created(false) : undefined));
    const u = userEvent.setup();
    renderAdmin(true);
    await openAdd(u);
    await u.click(screen.getByTestId("admin-add-send-mail"));
    await u.click(screen.getByTestId("admin-add-role-admin"));
    await u.type(screen.getByTestId("admin-add-email"), "kim@example.org");
    await u.click(screen.getByTestId("admin-add-submit"));
    await screen.findByTestId("admin-link-dialog");
    const body = mutations()[0].body as Record<string, unknown>;
    expect(body).toEqual({ email: "kim@example.org", role: "admin", send_mail: false });
    expect("send_mail" in body).toBe(true);
  });

  it("smtp disabled: checkbox disabled and unchecked with the hint, send_mail false", async () => {
    mockApi((url, m) => (url === "/api/admin/users" && m === "POST" ? created(false) : undefined));
    const u = userEvent.setup();
    renderAdmin(false);
    await openAdd(u);
    const box = screen.getByTestId("admin-add-send-mail");
    expect(box).toBeDisabled();
    expect(box).not.toBeChecked();
    expect(screen.getByText(/^Email is not set up \(SMTP_HOST\)/)).toBeInTheDocument();
    await u.type(screen.getByTestId("admin-add-email"), "kim@example.org");
    await u.click(screen.getByTestId("admin-add-submit"));
    await screen.findByTestId("admin-link-dialog");
    expect(mutations()[0].body).toMatchObject({ send_mail: false });
  });

  it("names the controller role (RD-5)", async () => {
    const u = userEvent.setup();
    renderAdmin();
    await openAdd(u);
    expect(screen.getByTestId("admin-add-controller-note")).toHaveTextContent("GDPR: controller");
  });

  it("409 email_taken: the 'already an account' copy; dialog stays open", async () => {
    mockApi((url, m) => (url === "/api/admin/users" && m === "POST" ? err(409, "email_taken") : undefined));
    const u = userEvent.setup();
    renderAdmin();
    await openAdd(u);
    await u.type(screen.getByTestId("admin-add-email"), "maria@example.org");
    await u.click(screen.getByTestId("admin-add-submit"));
    expect(await screen.findByTestId("admin-add-error")).toHaveTextContent(
      "There is already an account for this email address.",
    );
    expect(screen.getByTestId("admin-add-dialog")).toBeInTheDocument();
  });

  it("created, not mailed: 'Send this link to <email>' and the url", async () => {
    mockApi((url, m) => (url === "/api/admin/users" && m === "POST" ? created(false) : undefined));
    const u = userEvent.setup();
    renderAdmin();
    await openAdd(u);
    await u.type(screen.getByTestId("admin-add-email"), "kim@example.org");
    await u.click(screen.getByTestId("admin-add-submit"));
    await screen.findByTestId("admin-link-dialog");
    expect(screen.getByTestId("admin-link-text")).toHaveTextContent(
      "Send this link to kim@example.org — it is valid for 7 days and works only once:",
    );
    expect(screen.getByTestId("admin-link-url")).toHaveTextContent("https://applire.test/invite?token=abc");
    expect(screen.queryByTestId("admin-link-mail-failed")).toBeNull();
  });

  it("mail_failed: shows the warning above the link", async () => {
    mockApi((url, m) => (url === "/api/admin/users" && m === "POST" ? created(false, true) : undefined));
    const u = userEvent.setup();
    renderAdmin(true);
    await openAdd(u);
    await u.type(screen.getByTestId("admin-add-email"), "kim@example.org");
    await u.click(screen.getByTestId("admin-add-submit"));
    expect(await screen.findByTestId("admin-link-mail-failed")).toHaveTextContent(
      "The email could not be sent. Pass the link on yourself instead.",
    );
    expect(screen.getByTestId("admin-link-url")).toHaveTextContent("https://applire.test/invite?token=abc");
  });
});

describe("UsersAdmin — row actions", () => {
  it("disable -> confirm: PATCH {disabled:true}, then the list is re-read", async () => {
    mockApi((url, m) => (m === "PATCH" ? json(200, PLAIN) : undefined));
    const u = userEvent.setup();
    renderAdmin();
    await screen.findByTestId("admin-users-table");
    const menu = await openMenu(u, "maria@example.org");
    await u.click(within(menu).getByText("Disable"));
    const dialog = screen.getByTestId("admin-confirm-dialog");
    expect(dialog).toHaveTextContent("Disable maria@example.org?");
    await u.click(screen.getByTestId("admin-confirm-dialog-confirm"));
    await waitFor(() => expect(screen.queryByTestId("admin-confirm-dialog")).toBeNull());
    expect(mutations()).toEqual([{ url: "/api/admin/users/u-4", method: "PATCH", body: { disabled: true } }]);
    expect(calls.filter((c) => c.method === "GET")).toHaveLength(2);
  });

  it("disable 409 last_admin: 'Not possible: <email> is the only active admin…'", async () => {
    mockApi((_u, m) => (m === "PATCH" ? err(409, "last_admin") : undefined));
    const u = userEvent.setup();
    renderAdmin();
    await screen.findByTestId("admin-users-table");
    const menu = await openMenu(u, "second@example.org");
    await u.click(within(menu).getByText("Disable"));
    await u.click(screen.getByTestId("admin-confirm-dialog-confirm"));
    expect(await screen.findByTestId("admin-confirm-dialog-error")).toHaveTextContent(
      "Not possible: second@example.org is the only active admin. Make someone else an admin first.",
    );
    // The confirm button is replaced by a close button.
    expect(screen.queryByTestId("admin-confirm-dialog-confirm")).toBeNull();
    await u.click(screen.getByTestId("admin-confirm-dialog-close"));
    expect(screen.queryByTestId("admin-confirm-dialog")).toBeNull();
  });

  it("make admin -> confirm: PATCH {role:'admin'}", async () => {
    mockApi((_u, m) => (m === "PATCH" ? json(200, PLAIN) : undefined));
    const u = userEvent.setup();
    renderAdmin();
    await screen.findByTestId("admin-users-table");
    const menu = await openMenu(u, "maria@example.org");
    await u.click(within(menu).getByText("Make admin"));
    await u.click(screen.getByTestId("admin-confirm-dialog-confirm"));
    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toEqual({ url: "/api/admin/users/u-4", method: "PATCH", body: { role: "admin" } });
  });

  it("enable again: PATCH {disabled:false} straight away", async () => {
    mockApi((_u, m) => (m === "PATCH" ? json(200, DISABLED) : undefined));
    const u = userEvent.setup();
    renderAdmin();
    await screen.findByTestId("admin-users-table");
    const menu = await openMenu(u, "gone@example.org");
    await u.click(within(menu).getByText("Enable again"));
    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toEqual({ url: "/api/admin/users/u-3", method: "PATCH", body: { disabled: false } });
  });

  it("remove admin rights: PATCH {role:'user'} straight away; last_admin refusal shown", async () => {
    mockApi((_u, m) => (m === "PATCH" ? err(409, "last_admin") : undefined));
    const u = userEvent.setup();
    renderAdmin();
    await screen.findByTestId("admin-users-table");
    const menu = await openMenu(u, "tobias@example.org");
    await u.click(within(menu).getByText("Remove admin rights"));
    expect(await screen.findByTestId("admin-confirm-dialog-error")).toHaveTextContent(
      "Not possible: tobias@example.org is the only active admin.",
    );
    expect(mutations()[0]).toEqual({ url: "/api/admin/users/u-1", method: "PATCH", body: { role: "user" } });
  });

  it("revoke all tokens -> confirm: POST …/revoke-tokens", async () => {
    mockApi((url, m) => (m === "POST" && url.endsWith("/revoke-tokens") ? json(204) : undefined));
    const u = userEvent.setup();
    renderAdmin();
    await screen.findByTestId("admin-users-table");
    const menu = await openMenu(u, "maria@example.org");
    await u.click(within(menu).getByText("Revoke all tokens"));
    expect(screen.getByTestId("admin-confirm-dialog")).toHaveTextContent("Revoke all tokens of maria@example.org?");
    await u.click(screen.getByTestId("admin-confirm-dialog-confirm"));
    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toMatchObject({ url: "/api/admin/users/u-4/revoke-tokens", method: "POST" });
  });

  it("reset link: POST …/reset-link, then the reset dialog with the url", async () => {
    mockApi((url, m) =>
      m === "POST" && url.endsWith("/reset-link")
        ? json(200, {
            purpose: "reset",
            url: "https://applire.test/reset?token=xyz",
            expires_at: "2026-10-05T16:00:00Z",
            mailed: false,
            mail_failed: false,
          })
        : undefined,
    );
    const u = userEvent.setup();
    renderAdmin();
    await screen.findByTestId("admin-users-table");
    const menu = await openMenu(u, "maria@example.org");
    await u.click(within(menu).getByText("Create reset link"));
    const dialog = await screen.findByTestId("admin-link-dialog");
    expect(mutations()[0]).toMatchObject({ url: "/api/admin/users/u-4/reset-link", method: "POST" });
    expect(dialog).toHaveTextContent("Reset link for maria@example.org");
    expect(screen.getByTestId("admin-link-url")).toHaveTextContent("https://applire.test/reset?token=xyz");
  });

  it("reinvite: POST …/reinvite shows the invitation dialog", async () => {
    mockApi((url, m) =>
      m === "POST" && url.endsWith("/reinvite")
        ? json(200, { purpose: "invite", url: "https://applire.test/invite?token=new", expires_at: "x", mailed: false, mail_failed: false })
        : undefined,
    );
    const u = userEvent.setup();
    renderAdmin();
    await screen.findByTestId("admin-users-table");
    const menu = await openMenu(u, "new@example.org");
    await u.click(within(menu).getByText("Create new invitation link"));
    await screen.findByTestId("admin-link-dialog");
    expect(mutations()[0]).toMatchObject({ url: "/api/admin/users/u-2/reinvite", method: "POST" });
    expect(screen.getByTestId("admin-link-url")).toHaveTextContent("invite?token=new");
  });
});

describe("UsersAdmin — delete", () => {
  it("requires typing the email (case-insensitive); then DELETE", async () => {
    mockApi((_u, m) => (m === "DELETE" ? json(204) : undefined));
    const u = userEvent.setup();
    renderAdmin();
    await screen.findByTestId("admin-users-table");
    const menu = await openMenu(u, "maria@example.org");
    await u.click(within(menu).getByText("Delete"));
    const confirm = screen.getByTestId("admin-delete-confirm");
    expect(screen.getByTestId("admin-delete-dialog")).toHaveTextContent("Delete maria@example.org for good?");
    expect(confirm).toBeDisabled();
    const input = screen.getByTestId("admin-delete-email");
    await u.type(input, "maria@example.or");
    expect(confirm).toBeDisabled();
    await u.type(input, "G");
    expect(confirm).toBeEnabled();
    expect(mutations()).toHaveLength(0);
    await u.click(confirm);
    await waitFor(() => expect(screen.queryByTestId("admin-delete-dialog")).toBeNull());
    expect(mutations()).toEqual([{ url: "/api/admin/users/u-4", method: "DELETE", body: undefined }]);
  });

  it("another person's email does not enable the button", async () => {
    const u = userEvent.setup();
    renderAdmin();
    await screen.findByTestId("admin-users-table");
    const menu = await openMenu(u, "maria@example.org");
    await u.click(within(menu).getByText("Delete"));
    await u.type(screen.getByTestId("admin-delete-email"), "second@example.org");
    expect(screen.getByTestId("admin-delete-confirm")).toBeDisabled();
  });

  it("delete 409 last_admin shows the refusal in the delete dialog", async () => {
    mockApi((_u, m) => (m === "DELETE" ? err(409, "last_admin") : undefined));
    const u = userEvent.setup();
    renderAdmin();
    await screen.findByTestId("admin-users-table");
    const menu = await openMenu(u, "second@example.org");
    await u.click(within(menu).getByText("Delete"));
    await u.type(screen.getByTestId("admin-delete-email"), "second@example.org");
    await u.click(screen.getByTestId("admin-delete-confirm"));
    expect(await screen.findByTestId("admin-delete-error")).toHaveTextContent(
      "Not possible: second@example.org is the only active admin.",
    );
  });

  it("own row: goes to /settings and sends nothing", async () => {
    const u = userEvent.setup();
    renderAdmin();
    await screen.findByTestId("admin-users-table");
    const before = calls.length;
    const menu = await openMenu(u, "tobias@example.org");
    await u.click(within(menu).getByText("Delete"));
    expect(push).toHaveBeenCalledWith("/settings");
    expect(screen.queryByTestId("admin-delete-dialog")).toBeNull();
    expect(calls.length).toBe(before);
    expect(mutations()).toHaveLength(0);
  });
});
