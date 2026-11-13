// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CurrentUserProvider, type CurrentUser } from "@/lib/auth";
import { PROVIDER_KEY_REQUIRED_EVENT } from "@/lib/provider-key-events";
import { withIntl } from "@/lib/test-utils/with-intl";
import { ProviderKeyBanner, ProviderKeyDialog } from "../ProviderKeyNotices";
import { ShellUserProvider } from "../ShellUserContext";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, replace: vi.fn() }), usePathname: () => "/" }));

const ME: CurrentUser = { id: "u-1", email: "me@example.org", role: "user", has_password: true, oidc_linked: false, ui_language: null };
const realFetch = globalThis.fetch;
let fetchMock: ReturnType<typeof vi.fn>;

function mockState(over: Record<string, unknown>) {
  fetchMock = vi.fn(
    async () =>
      new Response(
        JSON.stringify({ key_mode: "user", is_set: false, family: null, model: null, updated_at: null, unreadable: false, instance_key_granted: false, payer: "none", families: [], ...over }),
        { status: 200 },
      ),
  );
  globalThis.fetch = fetchMock as unknown as typeof fetch;
}

function renderBanner(user: CurrentUser | null) {
  return render(
    withIntl(
      <CurrentUserProvider value={{ user }}>
        <ShellUserProvider userName={null}>
          <ProviderKeyBanner />
        </ShellUserProvider>
      </CurrentUserProvider>,
    ),
  );
}

beforeEach(() => push.mockReset());
afterEach(() => {
  cleanup();
  globalThis.fetch = realFetch;
});

describe("ProviderKeyBanner (ADR-096)", () => {
  it("shows for user mode + payer none, and the button goes to the card", async () => {
    mockState({});
    const u = userEvent.setup();
    renderBanner(ME);
    expect(await screen.findByTestId("provider-key-banner")).toHaveTextContent("To generate documents, add your own AI provider key");
    await u.click(screen.getByTestId("provider-key-banner-add"));
    expect(push).toHaveBeenCalledWith("/settings#provider-key");
  });

  it.each([
    ["instance mode", { key_mode: "instance", payer: "instance" }],
    ["own key", { is_set: true, payer: "user" }],
    ["granted", { instance_key_granted: true, payer: "instance" }],
  ])("stays hidden for %s", async (_n, over) => {
    mockState(over);
    renderBanner(ME);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("provider-key-banner")).toBeNull();
  });

  it("does not fetch while signed out", async () => {
    mockState({});
    renderBanner(null);
    await new Promise((r) => setTimeout(r, 10));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId("provider-key-banner")).toBeNull();
  });
});

describe("ProviderKeyDialog (ADR-096)", () => {
  const fire = (code: string) => act(() => void window.dispatchEvent(new CustomEvent(PROVIDER_KEY_REQUIRED_EVENT, { detail: { code } })));

  it("opens on provider_key_required with the banner text and links to the card", async () => {
    const u = userEvent.setup();
    render(withIntl(<ProviderKeyDialog />));
    expect(screen.queryByTestId("provider-key-dialog")).toBeNull();
    fire("provider_key_required");
    expect(await screen.findByTestId("provider-key-dialog")).toHaveTextContent("To generate documents, add your own AI provider key");
    await u.click(screen.getByTestId("provider-key-dialog-add"));
    expect(push).toHaveBeenCalledWith("/settings#provider-key");
    expect(screen.queryByTestId("provider-key-dialog")).toBeNull();
  });

  it("uses the unreadable variant and can be closed", async () => {
    const u = userEvent.setup();
    render(withIntl(<ProviderKeyDialog />));
    fire("provider_key_unreadable");
    expect(await screen.findByTestId("provider-key-dialog")).toHaveTextContent("Ask your admin");
    await u.click(screen.getByTestId("provider-key-dialog-close"));
    expect(screen.queryByTestId("provider-key-dialog")).toBeNull();
  });
});
