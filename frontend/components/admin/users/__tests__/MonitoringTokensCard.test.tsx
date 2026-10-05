// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TokenItem } from "@/components/account/api";
import { withIntl } from "@/lib/test-utils/with-intl";
import { MonitoringTokensCard } from "../MonitoringTokensCard";

const SECRET = "apl_pq12rs34_PROBESECRETPROBESECRETPROBE";

const probe = (over: Partial<TokenItem> = {}): TokenItem => ({
  id: "p-1",
  name: "Uptime Kuma",
  scope: "probe",
  prefix: "pq12rs34",
  created_at: "2026-09-20T08:00:00Z",
  last_used_at: null,
  ...over,
});

let list: TokenItem[];
let calls: { url: string; method: string; body: unknown }[];
const realFetch = globalThis.fetch;

function mockApi(custom: (url: string, method: string) => Response | undefined = () => undefined) {
  calls = [];
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = init.method ?? "GET";
    calls.push({ url, method, body: init.body ? JSON.parse(String(init.body)) : undefined });
    const c = custom(url, method);
    if (c) return c;
    if (url === "/api/admin/probe-tokens" && method === "GET") return new Response(JSON.stringify({ tokens: list }), { status: 200 });
    return new Response(null, { status: 500 });
  }) as unknown as typeof fetch;
}

beforeEach(() => {
  list = [];
  mockApi();
});
afterEach(() => {
  cleanup();
  globalThis.fetch = realFetch;
});

describe("MonitoringTokensCard", () => {
  it("lists the probe tokens from GET /api/admin/probe-tokens (masked) with a setup hint", async () => {
    list = [probe()];
    mockApi();
    render(withIntl(<MonitoringTokensCard />));
    const row = await screen.findByTestId("monitoring-table-row");
    expect(row).toHaveTextContent("Uptime Kuma");
    expect(row).toHaveTextContent("apl_pq12rs34_…");
    expect(calls[0]).toMatchObject({ url: "/api/admin/probe-tokens", method: "GET" });
    // The hint shows the masked token against /api/ops/health, never a secret.
    expect(screen.getByText("This is how your monitoring queries the instance:")).toBeInTheDocument();
    expect(screen.getByTestId("monitoring-tokens").textContent).toContain("/api/ops/health");
  });

  it("empty state", async () => {
    render(withIntl(<MonitoringTokensCard />));
    expect(await screen.findByTestId("monitoring-table-empty")).toHaveTextContent("No tokens yet.");
  });

  it("create: POST {name}, shown once with the /api/ops/health curl, secret gone after Done", async () => {
    mockApi((url, method) =>
      url === "/api/admin/probe-tokens" && method === "POST"
        ? new Response(JSON.stringify({ ...probe({ id: "p-new", name: "Kuma" }), token: SECRET }), { status: 201 })
        : undefined,
    );
    const u = userEvent.setup();
    render(withIntl(<MonitoringTokensCard />));
    await screen.findByTestId("monitoring-table-empty");
    await u.click(screen.getByTestId("monitoring-create"));
    await u.type(screen.getByTestId("monitoring-create-dialog-name"), "Kuma");
    await u.click(screen.getByTestId("monitoring-create-dialog-submit"));

    expect(await screen.findByTestId("monitoring-shown-once-secret")).toHaveTextContent(SECRET);
    expect(calls.find((c) => c.method === "POST")).toEqual({
      url: "/api/admin/probe-tokens",
      method: "POST",
      body: { name: "Kuma" },
    });
    const dialog = screen.getByTestId("monitoring-shown-once");
    expect(dialog.textContent).toContain(`curl -H "Authorization: Bearer ${SECRET}"`);
    expect(dialog.textContent).toContain("/api/ops/health");

    await u.click(screen.getByTestId("monitoring-shown-once-done"));
    expect(document.body.textContent).not.toContain(SECRET);
  });

  it("revoke -> confirm: DELETE /api/admin/probe-tokens/<id>, list re-read", async () => {
    list = [probe()];
    mockApi((_u, method) => {
      if (method === "DELETE") {
        list = [];
        return new Response(null, { status: 204 });
      }
      return undefined;
    });
    const u = userEvent.setup();
    render(withIntl(<MonitoringTokensCard />));
    await u.click(await screen.findByTestId("monitoring-table-revoke"));
    const dialog = screen.getByTestId("monitoring-revoke-dialog");
    expect(dialog).toHaveTextContent("Checks using this token lose access immediately.");
    await u.click(within(dialog).getByTestId("monitoring-revoke-dialog-confirm"));
    await waitFor(() => expect(screen.getByTestId("monitoring-table-empty")).toBeInTheDocument());
    expect(calls.find((c) => c.method === "DELETE")?.url).toBe("/api/admin/probe-tokens/p-1");
    expect(calls.filter((c) => c.method === "GET")).toHaveLength(2);
  });

  it("a failed load shows the load error", async () => {
    globalThis.fetch = vi.fn(async () => new Response(null, { status: 403 })) as unknown as typeof fetch;
    render(withIntl(<MonitoringTokensCard />));
    expect(await screen.findByRole("alert")).toHaveTextContent("The tokens could not be loaded.");
  });
});
