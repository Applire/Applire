// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CurrentUserProvider, type CurrentUser } from "@/lib/auth";
import { withIntl } from "@/lib/test-utils/with-intl";
import en from "@/messages/en.json";
import de from "@/messages/de.json";
import { InstanceSignal } from "@/components/dashboard/InstanceSignal";
import { AdminOverview } from "../overview/AdminOverview";
import { UsageView } from "../usage/UsageView";
import { AuditLog } from "../audit/AuditLog";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => "/admin/overview" }));

const mockFetch = vi.fn();
const res = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const ZERO = { calls: 0, failed_calls: 0, estimated_calls: 0, prompt_tokens: 0, completion_tokens: 0, reasoning_tokens: 0, total_tokens: 0 };
const tot = (n: number, calls = 10) => ({ ...ZERO, calls, prompt_tokens: Math.round(n * 0.8), completion_tokens: n - Math.round(n * 0.8), total_tokens: n });

const ADMIN: CurrentUser = { id: "u-1", email: "anna@example.org", role: "admin", has_password: true, oidc_linked: false, ui_language: null };
const USER: CurrentUser = { ...ADMIN, id: "u-2", email: "jonas@example.org", role: "user" };

const DASH = {
  health: {
    status: "degraded",
    version: "0.44.0",
    edition: "community",
    topology: "prod",
    debug_log_on: false,
    llm_provider: "requesty",
    llm_model: "openai/gpt-5.6-luna",
    checked_at: "2026-10-07T12:05:00Z",
    components: [
      { name: "database", status: "ok" },
      { name: "retention", status: "degraded" },
      { name: "backup", status: "degraded" },
    ],
  },
  users: { total: 3, active: 2, pending: 1, disabled: 0, admins: 1 },
  usage_30d: tot(740_000, 350),
  failed_jobs: {
    window_days: 7,
    count: 1,
    items: [{ kind: "import", id: "j1", user_id: "u-2", user_email: "jonas@example.org", failed_at: "2026-10-07T09:41:00Z", error_code: "llm_timeout" }],
  },
  upgrade_notice: null,
  retention: { enabled: false, source: "panel", last_run_at: null, last_run_ok: null, last_run_skipped: true },
  notices: [],
};

const USAGE = {
  window_days: 30,
  since: "2026-09-07T00:00:00Z",
  totals: tot(700_000, 300),
  users: [
    { user_id: "u-1", email: "anna@example.org", role: "admin", status: "active", totals: tot(400_000), last_call_at: "2026-10-07T11:00:00Z" },
    { user_id: "u-2", email: "jonas@example.org", role: "user", status: "active", totals: tot(300_000), last_call_at: null },
    { user_id: "u-3", email: "mira@example.org", role: "user", status: "pending", totals: ZERO, last_call_at: null },
  ],
  unattributed: ZERO,
  by_provider: [{ provider: "requesty", model: "openai/gpt-5.6-luna", totals: tot(700_000) }],
};

beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch);
  mockFetch.mockReset();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const as = (u: CurrentUser, el: React.ReactElement, locale: "en" | "de" = "en") =>
  render(withIntl(<CurrentUserProvider value={{ user: u }}>{el}</CurrentUserProvider>, locale));

describe("dashboard instance signal (#694)", () => {
  it("a non-admin never asks for notices and sees nothing", async () => {
    as(USER, <InstanceSignal />);
    await new Promise((r) => setTimeout(r, 20));
    expect(mockFetch).not.toHaveBeenCalled();
    expect(screen.queryByTestId("instance-signal")).toBeNull();
  });

  it("an admin sees one line with the count, linking to the overview", async () => {
    mockFetch.mockResolvedValue(res(200, { count: 2, items: [{ code: "health_degraded", severity: "warning" }, { code: "failed_jobs", severity: "warning" }] }));
    as(ADMIN, <InstanceSignal />, "de");
    const s = await screen.findByTestId("instance-signal");
    expect(s).toHaveTextContent("Instanz: 2 Hinweise");
    expect(s).toHaveAttribute("data-critical", "false");
    expect(screen.getByTestId("instance-signal-link")).toHaveAttribute("href", "/admin/overview");
    expect(mockFetch.mock.calls[0][0]).toBe("/api/admin/notices");
  });

  it("a critical notice switches to the problem wording; no notices = nothing", async () => {
    mockFetch.mockResolvedValueOnce(res(200, { count: 1, items: [{ code: "health_down", severity: "critical" }] }));
    as(ADMIN, <InstanceSignal />, "de");
    expect(await screen.findByTestId("instance-signal")).toHaveTextContent("Instanz: 1 Problem");
    cleanup();
    mockFetch.mockResolvedValueOnce(res(200, { count: 0, items: [] }));
    as(ADMIN, <InstanceSignal />);
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId("instance-signal")).toBeNull();
  });
});

function routeAdmin() {
  mockFetch.mockImplementation(async (url: string) => {
    if (url === "/api/admin/dashboard") return res(200, DASH);
    if (url.startsWith("/api/admin/usage")) return res(200, USAGE);
    if (url === "/api/admin/settings") return res(200, { items: [], providers: [], dependencies: [] });
    if (url.startsWith("/api/admin/audit"))
      return res(200, {
        items: [{ id: "a1", at: "2026-10-07T11:52:00Z", action: "user.role_changed", actor_user_id: "u-1", actor_email: "anna@example.org", target_user_id: "u-2", target_email: "jonas@example.org", detail: { from_role: "user", to_role: "admin" } }],
        next_cursor: null,
        actions: ["user.role_changed", "settings.changed"],
      });
    if (url === "/api/admin/users") return res(200, { users: [] });
    if (url === "/api/ops/health") return res(403, {});
    return res(404, {});
  });
}

describe("Administration → Übersicht", () => {
  it("renders health, tiles, failed job without content, config and latest audit row", async () => {
    routeAdmin();
    as(ADMIN, <AdminOverview />);
    expect(await screen.findByTestId("admin-health-strip")).toHaveAttribute("data-status", "degraded");
    expect(screen.getByTestId("admin-health-verdict")).toHaveTextContent("2 notices");
    expect(screen.getByTestId("admin-health-chip-retention")).toBeInTheDocument();
    expect(screen.queryByTestId("admin-health-chip-database")).toBeNull();
    expect(screen.getByTestId("admin-overview-tile-people")).toHaveTextContent("3");
    expect(screen.getByTestId("admin-overview-failed-row")).toHaveTextContent(en.adminDashboard.reasonTimeout);
    expect(screen.getByTestId("admin-overview-failed-row")).toHaveTextContent(en.adminDashboard.kindImport);
    expect(screen.getByTestId("admin-overview-retention-off")).toHaveTextContent("2 other people");
    expect(await screen.findByTestId("admin-audit-card")).toHaveTextContent("Role: User → Admin");
    expect(await screen.findAllByTestId("admin-overview-top-user")).toHaveLength(2);
  });
});

describe("Administration → Nutzung", () => {
  it("lists every account incl. zero rows with a share, and switches the window", async () => {
    routeAdmin();
    const u = userEvent.setup();
    as(ADMIN, <UsageView />);
    const rows = await screen.findAllByTestId("admin-usage-row");
    expect(rows.map((r) => r.getAttribute("data-email"))).toEqual(["anna@example.org", "jonas@example.org", "mira@example.org"]);
    expect(screen.getByTestId("admin-usage-provider-row")).toHaveTextContent("openai/gpt-5.6-luna");
    expect(screen.queryByTestId("admin-usage-by-kind")).toBeNull(); // optional field absent → card absent
    await u.click(screen.getByTestId("admin-usage-period-90"));
    await waitFor(() => expect(mockFetch.mock.calls.map((c) => c[0])).toContain("/api/admin/usage?days=90"));
  });
});

describe("Administration → Protokoll", () => {
  it("renders rows and filters a category by the server's own action names", async () => {
    routeAdmin();
    const u = userEvent.setup();
    as(ADMIN, <AuditLog />, "de");
    expect(await screen.findByTestId("admin-audit-row")).toHaveTextContent(de.adminAudit.action.user_role_changed);
    await u.click(screen.getByTestId("admin-audit-filter-settings"));
    await waitFor(() => expect(mockFetch.mock.calls.map((c) => c[0])).toContain("/api/admin/audit?action=settings.changed&limit=50"));
  });
});
