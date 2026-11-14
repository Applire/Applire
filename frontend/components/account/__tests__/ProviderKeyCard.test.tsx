// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CurrentUserProvider, type CurrentUser } from "@/lib/auth";
import { withIntl } from "@/lib/test-utils/with-intl";
import en from "@/messages/en.json";
import type { ProviderKeyState } from "../api";
import { ProviderKeyCard, providerKeyErrorKey } from "../ProviderKeyCard";

const T = en.providerKey;
const ME: CurrentUser = { id: "u-1", email: "me@example.org", role: "user", has_password: true, oidc_linked: false, ui_language: null };
const FAMILIES = ["mistral", "openrouter", "requesty", "anthropic", "openai"];
const SECRET = "sk-test-SECRETSECRETSECRET";

const state = (over: Partial<ProviderKeyState> = {}): ProviderKeyState => ({
  key_mode: "user",
  is_set: false,
  family: null,
  model: null,
  updated_at: null,
  unreadable: false,
  instance_key_granted: false,
  payer: "none",
  families: FAMILIES,
  ...over,
});

let current: ProviderKeyState;
let calls: { url: string; method: string; body: unknown }[];
let putResponse: () => Response;
const realFetch = globalThis.fetch;
const json = (status: number, body?: unknown) => new Response(body === undefined ? null : JSON.stringify(body), { status });

function renderCard(signedIn = true) {
  return render(withIntl(<CurrentUserProvider value={{ user: signedIn ? ME : null }}><ProviderKeyCard /></CurrentUserProvider>));
}

beforeEach(() => {
  current = state();
  calls = [];
  putResponse = () => json(200, current);
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = init.method ?? "GET";
    calls.push({ url, method, body: init.body ? JSON.parse(String(init.body)) : undefined });
    if (url === "/api/me/provider-key" && method === "GET") return json(200, current);
    if (url === "/api/me/provider-key" && method === "PUT") return putResponse();
    if (url === "/api/me/provider-key" && method === "DELETE") return json(204);
    return json(500);
  }) as unknown as typeof fetch;
});
afterEach(() => {
  cleanup();
  globalThis.fetch = realFetch;
});

describe("ProviderKeyCard (ADR-096)", () => {
  it("renders nothing on a default install (instance mode, no stored key)", async () => {
    current = state({ key_mode: "instance", payer: "instance" });
    renderCard();
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(screen.queryByTestId("provider-key-card")).toBeNull();
  });

  it("does not fetch while signed out", async () => {
    renderCard(false);
    await new Promise((r) => setTimeout(r, 10));
    expect(calls).toHaveLength(0);
    expect(screen.queryByTestId("provider-key-card")).toBeNull();
  });

  it("shows in user mode with 'nobody yet', no remove button, and the fine print + guide link", async () => {
    renderCard();
    expect(await screen.findByTestId("provider-key-card")).toHaveAttribute("id", "provider-key");
    expect(screen.getByTestId("provider-key-status")).toHaveTextContent("nobody yet");
    expect(screen.queryByTestId("provider-key-remove")).toBeNull();
    expect(screen.getByTestId("provider-key-fineprint")).toHaveTextContent(T.finePrint);
    expect(screen.getByTestId("provider-key-fineprint").querySelector("a")).toHaveAttribute("href", expect.stringContaining("llm-models.md"));
    const input = screen.getByTestId("provider-key-key");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("autocomplete", "new-password");
    expect(input).toHaveValue("");
    expect(screen.getByTestId("provider-key-test")).toBeChecked();
  });

  it("shows with a stored key in instance mode, saying it is not used, and never displays the key", async () => {
    current = state({ key_mode: "instance", is_set: true, family: "openai", model: "gpt-x", payer: "instance" });
    renderCard();
    expect(await screen.findByTestId("provider-key-status")).toHaveTextContent("stored but not used");
    expect(screen.getByTestId("provider-key-key")).toHaveValue("");
    expect(screen.getByTestId("provider-key-remove")).toBeInTheDocument();
  });

  it("payer lines: instance grant and own key", async () => {
    current = state({ instance_key_granted: true, payer: "instance" });
    renderCard();
    expect(await screen.findByTestId("provider-key-status")).toHaveTextContent("this instance (granted by your admin)");
    cleanup();
    current = state({ is_set: true, family: "anthropic", model: "claude-x", payer: "user" });
    renderCard();
    await waitFor(() => expect(screen.getByTestId("provider-key-status")).toHaveTextContent("your own key (Anthropic, claude-x)"));
  });

  it("shows the unreadable notice", async () => {
    current = state({ is_set: true, unreadable: true, family: "mistral", model: "m" });
    renderCard();
    expect(await screen.findByTestId("provider-key-unreadable")).toHaveTextContent(T.unreadable);
  });

  it("saves family/model/key/test, clears the key field and never echoes the key", async () => {
    const u = userEvent.setup();
    renderCard();
    await screen.findByTestId("provider-key-card");
    await u.selectOptions(screen.getByTestId("provider-key-family"), "openrouter");
    await u.type(screen.getByTestId("provider-key-model"), "some/model");
    await u.type(screen.getByTestId("provider-key-key"), SECRET);
    await u.click(screen.getByTestId("provider-key-test"));
    current = state({ is_set: true, family: "openrouter", model: "some/model", payer: "user" });
    await u.click(screen.getByTestId("provider-key-save"));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ family: "openrouter", model: "some/model", key: SECRET, test: false });
    await screen.findByTestId("provider-key-notice");
    expect(screen.getByTestId("provider-key-key")).toHaveValue("");
    expect(document.body.innerHTML).not.toContain(SECRET);
    await waitFor(() => expect(screen.getByTestId("provider-key-status")).toHaveTextContent("your own key (OpenRouter, some/model)"));
  });

  it("maps a test-failed reason to a sentence, clears the key, and never shows the server message", async () => {
    putResponse = () => json(422, { detail: { error_code: "provider_key_test_failed", reason: "out_of_credit", message: "RAW SERVER TEXT" } });
    const u = userEvent.setup();
    renderCard();
    await screen.findByTestId("provider-key-card");
    await u.type(screen.getByTestId("provider-key-model"), "m");
    await u.type(screen.getByTestId("provider-key-key"), SECRET);
    await u.click(screen.getByTestId("provider-key-save"));
    const err = await screen.findByTestId("provider-key-error");
    expect(err).toHaveTextContent(T.testOutOfCredit);
    expect(err).not.toHaveTextContent("RAW SERVER TEXT");
    expect(screen.getByTestId("provider-key-key")).toHaveValue("");
  });

  it("remove asks first, then DELETEs", async () => {
    current = state({ is_set: true, family: "mistral", model: "m", payer: "user" });
    const u = userEvent.setup();
    renderCard();
    await u.click(await screen.findByTestId("provider-key-remove"));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await u.click(screen.getByTestId("provider-key-remove-confirm"));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
  });
});

describe("providerKeyErrorKey", () => {
  it.each([
    ["invalid_body", null, "errInvalidBody"],
    ["invalid_provider_family", null, "errInvalidFamily"],
    ["invalid_model", null, "errInvalidModel"],
    ["invalid_provider_key", null, "errInvalidKey"],
    ["provider_key_secret_missing", null, "errSecretMissing"],
    ["provider_key_test_throttled", null, "errTestThrottled"],
    ["provider_key_conflict", null, "errConflict"],
    ["forbidden", null, "errForbidden"],
    ["provider_key_test_failed", "key_rejected", "testKeyRejected"],
    ["provider_key_test_failed", "out_of_credit", "testOutOfCredit"],
    ["provider_key_test_failed", "model_not_found", "testModelNotFound"],
    ["provider_key_test_failed", "rate_limited", "testRateLimited"],
    ["provider_key_test_failed", "timeout", "testTimeout"],
    ["provider_key_test_failed", "unreachable", "testUnreachable"],
    ["provider_key_test_failed", "weird", "testFailedOther"],
    ["something_else", null, "errGeneric"],
    [null, null, "errGeneric"],
  ])("%s / %s -> %s (a real catalog key)", (code, reason, key) => {
    expect(providerKeyErrorKey(code, reason)).toBe(key);
    expect((T as Record<string, string>)[key]).toBeTruthy();
  });
});
