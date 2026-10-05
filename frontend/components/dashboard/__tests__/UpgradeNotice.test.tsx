// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CurrentUserProvider, type CurrentUser } from "@/lib/auth";
import { withIntl } from "@/lib/test-utils/with-intl";
import { UpgradeNotice, crossesMultiUser } from "../UpgradeNotice";

const ADMIN: CurrentUser = {
  id: "u-1",
  email: "a@example.org",
  role: "admin",
  has_password: true,
  oidc_linked: false,
  ui_language: null,
};

type Item = { env_var: string; introduced_in?: string; semantics_changed_in?: string; default: string; description: string };
const notice = (from: string, to: string, over: { unset?: Item[]; re_meant?: Item[] } = {}) => ({
  from,
  to,
  unset: over.unset ?? [],
  re_meant: over.re_meant ?? [],
});

let calls: { url: string; method: string }[];
const realFetch = globalThis.fetch;

function mockOps(status: number, body: unknown) {
  calls = [];
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    calls.push({ url: String(input), method: init.method ?? "GET" });
    return new Response(JSON.stringify(body), { status });
  }) as unknown as typeof fetch;
}

function renderNotice(user: CurrentUser) {
  return render(withIntl(
    <CurrentUserProvider value={{ user }}>
      <UpgradeNotice />
    </CurrentUserProvider>,
  ));
}

beforeEach(() => mockOps(200, {}));
afterEach(() => {
  cleanup();
  globalThis.fetch = realFetch;
});

describe("UpgradeNotice — who asks", () => {
  it("a non-admin never requests the ops report and renders nothing", async () => {
    mockOps(200, { upgrade_notice: notice("0.42.0-beta", "0.43.0-beta"), debug_log_on: true });
    const { container } = renderNotice({ ...ADMIN, role: "user" });
    await new Promise((r) => setTimeout(r, 20));
    expect(calls).toHaveLength(0);
    expect(container.querySelector("[data-testid='upgrade-notice']")).toBeNull();
  });

  it("an admin reads GET /api/ops/health (not /health)", async () => {
    mockOps(200, { upgrade_notice: notice("0.42.0-beta", "0.43.0-beta") });
    renderNotice(ADMIN);
    await screen.findByTestId("upgrade-notice");
    expect(calls).toHaveLength(1);
    expect(calls[0]).toEqual({ url: "/api/ops/health", method: "GET" });
  });

  it("503 (the 'down' verdict) still carries the report and renders", async () => {
    mockOps(503, { status: "down", upgrade_notice: notice("0.42.0-beta", "0.43.0-beta") });
    renderNotice(ADMIN);
    expect(await screen.findByTestId("upgrade-notice")).toHaveTextContent("Last started: 0.42.0-beta · now running: 0.43.0-beta");
  });

  it.each([401, 403, 500])("%i renders nothing", async (status) => {
    mockOps(status, { upgrade_notice: notice("0.42.0-beta", "0.43.0-beta") });
    renderNotice(ADMIN);
    await waitFor(() => expect(calls).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("upgrade-notice")).toBeNull();
  });

  it("nothing to say (no notice, no debug log): renders nothing", async () => {
    mockOps(200, { upgrade_notice: null, debug_log_on: false });
    renderNotice(ADMIN);
    await waitFor(() => expect(calls).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("upgrade-notice")).toBeNull();
  });
});

describe("UpgradeNotice — AUTH_PROVIDER", () => {
  const authItem: Item = { env_var: "AUTH_PROVIDER", semantics_changed_in: "0.43.0", default: "local", description: "" };
  const otherItem: Item = { env_var: "LLM_TIMEOUT", semantics_changed_in: "0.43.0", default: "60", description: "" };

  it("re-meant AUTH_PROVIDER shows the authReMeant paragraph and no generic re-meant line for it", async () => {
    mockOps(200, { upgrade_notice: notice("0.42.0-beta", "0.43.0-beta", { re_meant: [authItem] }) });
    renderNotice(ADMIN);
    expect(await screen.findByTestId("upgrade-notice-auth")).toHaveTextContent(
      "Applire now has accounts. You can delete the line AUTH_PROVIDER=none from your .env",
    );
    expect(screen.queryByText("AUTH_PROVIDER")).toBeNull();
    // With AUTH_PROVIDER the only re-meant item, the generic heading goes too.
    expect(screen.queryByText("You set these, and their meaning has changed:")).toBeNull();
  });

  it("other re-meant settings keep their generic line next to the AUTH_PROVIDER paragraph", async () => {
    mockOps(200, { upgrade_notice: notice("0.42.0-beta", "0.43.0-beta", { re_meant: [authItem, otherItem] }) });
    renderNotice(ADMIN);
    await screen.findByTestId("upgrade-notice-auth");
    expect(screen.getByText("LLM_TIMEOUT")).toBeInTheDocument();
    expect(screen.getByText("You set these, and their meaning has changed:")).toBeInTheDocument();
    expect(screen.queryByText("AUTH_PROVIDER")).toBeNull();
  });

  it("without AUTH_PROVIDER there is no authReMeant paragraph", async () => {
    mockOps(200, { upgrade_notice: notice("0.42.0-beta", "0.43.0-beta", { re_meant: [otherItem] }) });
    renderNotice(ADMIN);
    await screen.findByTestId("upgrade-notice");
    expect(screen.queryByTestId("upgrade-notice-auth")).toBeNull();
  });
});

describe("UpgradeNotice — next steps", () => {
  it("0.42.0-beta -> 0.43.0-beta crosses into accounts: the to-do list", async () => {
    mockOps(200, { upgrade_notice: notice("0.42.0-beta", "0.43.0-beta") });
    renderNotice(ADMIN);
    const steps = await screen.findByTestId("upgrade-notice-next-steps");
    expect(steps).toHaveTextContent("Still to do after the upgrade, if you use them:");
    expect(steps.querySelectorAll("li")).toHaveLength(4);
    expect(steps).toHaveTextContent("create an agent token under Settings → Tokens");
    expect(steps).toHaveTextContent("/api/ops/health now needs a monitoring token");
  });

  it("0.43.0 -> 0.43.1 does not", async () => {
    mockOps(200, { upgrade_notice: notice("0.43.0", "0.43.1") });
    renderNotice(ADMIN);
    await screen.findByTestId("upgrade-notice");
    expect(screen.queryByTestId("upgrade-notice-next-steps")).toBeNull();
  });

  it("an unparseable version does not claim a crossing", () => {
    expect(crossesMultiUser("unknown", "0.43.0")).toBe(false);
    expect(crossesMultiUser("0.42.0", "")).toBe(false);
    expect(crossesMultiUser("v0.41.9", "v0.44.0")).toBe(true);
    expect(crossesMultiUser("0.43.0", "0.44.0")).toBe(false);
  });
});

describe("UpgradeNotice — retired profiles", () => {
  it("2 -> '2 older ones were set aside…'", async () => {
    mockOps(200, { upgrade_notice: notice("0.42.0-beta", "0.43.0-beta"), retired_profiles: 2 });
    renderNotice(ADMIN);
    expect(await screen.findByTestId("upgrade-notice-retired")).toHaveTextContent(/2 older ones were set aside/);
  });

  it("1 -> singular wording", async () => {
    mockOps(200, { upgrade_notice: notice("0.42.0-beta", "0.43.0-beta"), retired_profiles: 1 });
    renderNotice(ADMIN);
    expect(await screen.findByTestId("upgrade-notice-retired")).toHaveTextContent(/an older one was set aside/);
  });

  it.each([[0], [undefined]])("%s hides the line", async (n) => {
    mockOps(200, { upgrade_notice: notice("0.42.0-beta", "0.43.0-beta"), retired_profiles: n });
    renderNotice(ADMIN);
    await screen.findByTestId("upgrade-notice");
    expect(screen.queryByTestId("upgrade-notice-retired")).toBeNull();
  });
});

describe("UpgradeNotice — dismiss and debug log", () => {
  it("dismiss POSTs /api/settings/upgrade-notice/dismiss and removes the notice", async () => {
    mockOps(200, { upgrade_notice: notice("0.42.0-beta", "0.43.0-beta") });
    const u = userEvent.setup();
    renderNotice(ADMIN);
    await u.click(await screen.findByTestId("upgrade-notice-dismiss"));
    await waitFor(() => expect(screen.queryByTestId("upgrade-notice")).toBeNull());
    expect(calls).toContainEqual({ url: "/api/settings/upgrade-notice/dismiss", method: "POST" });
  });

  it("debug_log_on alone shows only the debug line, with no dismiss control", async () => {
    mockOps(200, { upgrade_notice: null, debug_log_on: true });
    renderNotice(ADMIN);
    expect(await screen.findByTestId("upgrade-notice-debug-log")).toHaveTextContent("The LLM debug log is on");
    expect(screen.queryByText("Something about your settings changed since your last start")).toBeNull();
    expect(screen.queryByTestId("upgrade-notice-dismiss")).toBeNull();
    expect(screen.queryByTestId("upgrade-notice-next-steps")).toBeNull();
    expect(screen.queryByTestId("upgrade-notice-retired")).toBeNull();
  });

  it("dismissing the notice keeps the debug line", async () => {
    mockOps(200, { upgrade_notice: notice("0.42.0-beta", "0.43.0-beta"), debug_log_on: true });
    const u = userEvent.setup();
    renderNotice(ADMIN);
    await u.click(await screen.findByTestId("upgrade-notice-dismiss"));
    await waitFor(() => expect(screen.queryByText("Something about your settings changed since your last start")).toBeNull());
    expect(screen.getByTestId("upgrade-notice-debug-log")).toBeInTheDocument();
  });
});
