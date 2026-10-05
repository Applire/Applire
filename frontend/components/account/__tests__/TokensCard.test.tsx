// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { withIntl } from "@/lib/test-utils/with-intl";
import type { TokenItem } from "../api";
import { TokensCard } from "../TokensCard";

const SECRET_AGENT = "apl_ab12cd34_SECRETAGENTSECRETAGENTSECRET";
const SECRET_API = "apl_ef56gh78_SECRETAPISECRETAPISECRETAPI";

const tok = (over: Partial<TokenItem>): TokenItem => ({
  id: "t-1",
  name: "Claude Desktop",
  scope: "agent",
  prefix: "ab12cd34",
  created_at: "2026-08-12T10:00:00Z",
  last_used_at: null,
  ...over,
});

let calls: { url: string; method: string; body: unknown }[];
let list: TokenItem[];
const realFetch = globalThis.fetch;

function mockApi(extra: (url: string, method: string) => Response | undefined = () => undefined) {
  calls = [];
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = init.method ?? "GET";
    calls.push({ url, method, body: init.body ? JSON.parse(String(init.body)) : undefined });
    const custom = extra(url, method);
    if (custom) return custom;
    if (url === "/api/me/tokens" && method === "GET") return new Response(JSON.stringify({ tokens: list }), { status: 200 });
    return new Response(null, { status: 500 });
  }) as unknown as typeof fetch;
}

beforeEach(() => {
  list = [];
});
afterEach(() => {
  cleanup();
  globalThis.fetch = realFetch;
});

describe("TokensCard", () => {
  it("splits the one list into agent and api tables", async () => {
    list = [
      tok({ id: "a1", name: "Agent one", scope: "agent" }),
      tok({ id: "p1", name: "Script one", scope: "api", prefix: "ef56gh78" }),
      tok({ id: "a2", name: "Agent two", scope: "agent", prefix: "zz99yy88" }),
    ];
    mockApi();
    render(withIntl(<TokensCard />));
    const agent = await screen.findByTestId("tokens-agent-table");
    const api = screen.getByTestId("tokens-api-table");
    expect(within(agent).getAllByTestId("tokens-agent-table-row")).toHaveLength(2);
    expect(within(agent).getByText("Agent one")).toBeInTheDocument();
    expect(within(agent).queryByText("Script one")).toBeNull();
    expect(within(api).getAllByTestId("tokens-api-table-row")).toHaveLength(1);
    expect(within(api).getByText("Script one")).toBeInTheDocument();
    expect(calls[0]).toMatchObject({ url: "/api/me/tokens", method: "GET" });
  });

  it("empty state per section", async () => {
    mockApi();
    render(withIntl(<TokensCard />));
    expect(await screen.findByTestId("tokens-agent-table-empty")).toHaveTextContent("No tokens yet.");
    expect(screen.getByTestId("tokens-api-table-empty")).toHaveTextContent("No tokens yet.");
  });

  it("the list shows at most apl_<prefix>_…, never the secret; 'never' for unused", async () => {
    list = [tok({ prefix: "ab12cd34" })];
    mockApi();
    render(withIntl(<TokensCard />));
    const row = await screen.findByTestId("tokens-agent-table-row");
    expect(row).toHaveTextContent("apl_ab12cd34_…");
    expect(row).toHaveTextContent("never");
    expect(row).toHaveTextContent("08/12/2026");
  });

  it("a failed load shows the load error", async () => {
    globalThis.fetch = vi.fn(async () => new Response(null, { status: 500 })) as unknown as typeof fetch;
    render(withIntl(<TokensCard />));
    expect(await screen.findByRole("alert")).toHaveTextContent("The tokens could not be loaded.");
  });

  it("create agent token: POST {name, scope:'agent'}, shown once with the APPLIRE_AGENT_TOKEN snippet, gone after Done", async () => {
    const created = { ...tok({ id: "new", name: "Laptop", prefix: "ab12cd34" }), token: SECRET_AGENT };
    mockApi((url, method) =>
      url === "/api/me/tokens" && method === "POST" ? new Response(JSON.stringify(created), { status: 201 }) : undefined,
    );
    const user = userEvent.setup();
    render(withIntl(<TokensCard />));
    await screen.findByTestId("tokens-agent-table-empty");

    await user.click(screen.getByTestId("tokens-agent-create"));
    const submit = screen.getByTestId("tokens-create-dialog-submit");
    expect(submit).toBeDisabled();
    await user.type(screen.getByTestId("tokens-create-dialog-name"), "  Laptop  ");
    await user.click(submit);

    const secret = await screen.findByTestId("tokens-shown-once-secret");
    expect(secret).toHaveTextContent(SECRET_AGENT);
    const post = calls.find((c) => c.method === "POST");
    expect(post).toEqual({ url: "/api/me/tokens", method: "POST", body: { name: "Laptop", scope: "agent" } });

    const dialog = screen.getByTestId("tokens-shown-once");
    expect(dialog).toHaveTextContent("Copy it now — it is shown only this once.");
    expect(dialog).toHaveTextContent("APPLIRE_AGENT_TOKEN");
    expect(dialog.textContent).toContain(`"APPLIRE_AGENT_TOKEN": "${SECRET_AGENT}"`);
    expect(dialog).toHaveTextContent("Restart the client afterwards.");

    await user.click(screen.getByTestId("tokens-shown-once-done"));
    expect(screen.queryByTestId("tokens-shown-once")).toBeNull();
    expect(document.body.textContent).not.toContain(SECRET_AGENT);
  });

  it("create API token: scope 'api', curl snippet, no restart note", async () => {
    const created = { ...tok({ id: "new", name: "CI", scope: "api", prefix: "ef56gh78" }), token: SECRET_API };
    mockApi((url, method) =>
      url === "/api/me/tokens" && method === "POST" ? new Response(JSON.stringify(created), { status: 201 }) : undefined,
    );
    const user = userEvent.setup();
    render(withIntl(<TokensCard />));
    await screen.findByTestId("tokens-api-table-empty");

    await user.click(screen.getByTestId("tokens-api-create"));
    await user.type(screen.getByTestId("tokens-create-dialog-name"), "CI");
    await user.click(screen.getByTestId("tokens-create-dialog-submit"));

    await screen.findByTestId("tokens-shown-once-secret");
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ name: "CI", scope: "api" });
    const dialog = screen.getByTestId("tokens-shown-once");
    expect(dialog.textContent).toContain(`curl -H "Authorization: Bearer ${SECRET_API}"`);
    expect(dialog.textContent).toContain("/api/applications");
    expect(dialog.textContent).not.toContain("APPLIRE_AGENT_TOKEN");
    expect(dialog.textContent).not.toContain("Restart the client");
  });

  it("a failed create shows the generic error and no secret dialog", async () => {
    mockApi((url, method) => (url === "/api/me/tokens" && method === "POST" ? new Response(null, { status: 500 }) : undefined));
    const user = userEvent.setup();
    render(withIntl(<TokensCard />));
    await screen.findByTestId("tokens-agent-table-empty");
    await user.click(screen.getByTestId("tokens-agent-create"));
    await user.type(screen.getByTestId("tokens-create-dialog-name"), "X");
    await user.click(screen.getByTestId("tokens-create-dialog-submit"));
    expect(await screen.findByRole("alert")).toHaveTextContent("That did not work. Try again.");
    expect(screen.queryByTestId("tokens-shown-once")).toBeNull();
  });

  it("revoke -> confirm: DELETE /api/me/tokens/<id>, then the list is re-read", async () => {
    list = [tok({ id: "a/1", name: "Agent one" })];
    mockApi((url, method) => {
      if (method === "DELETE") {
        list = [];
        return new Response(null, { status: 204 });
      }
      return undefined;
    });
    const user = userEvent.setup();
    render(withIntl(<TokensCard />));
    await user.click(await screen.findByTestId("tokens-agent-table-revoke"));
    const dialog = screen.getByTestId("tokens-revoke-dialog");
    expect(dialog).toHaveTextContent("Revoke token “Agent one”?");
    expect(dialog).toHaveTextContent("An agent using this token stops at its next step.");
    await user.click(screen.getByTestId("tokens-revoke-dialog-confirm"));

    await waitFor(() => expect(screen.getByTestId("tokens-agent-table-empty")).toBeInTheDocument());
    const del = calls.find((c) => c.method === "DELETE");
    expect(del?.url).toBe("/api/me/tokens/a%2F1");
    expect(calls.filter((c) => c.method === "GET")).toHaveLength(2);
    expect(screen.queryByTestId("tokens-revoke-dialog")).toBeNull();
  });

  it("revoke failing (500) keeps the dialog open with an error", async () => {
    list = [tok({})];
    mockApi((_u, method) => (method === "DELETE" ? new Response(null, { status: 500 }) : undefined));
    const user = userEvent.setup();
    render(withIntl(<TokensCard />));
    await user.click(await screen.findByTestId("tokens-agent-table-revoke"));
    await user.click(screen.getByTestId("tokens-revoke-dialog-confirm"));
    expect(await within(screen.getByTestId("tokens-revoke-dialog")).findByRole("alert")).toHaveTextContent(
      "That did not work. Try again.",
    );
  });
});
