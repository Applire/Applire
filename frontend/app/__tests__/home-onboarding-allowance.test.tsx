// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * DH item 1 (Sorbet invite-gate delivery run, 2026-11-25): a signed-in person without
 * a profile onboards on "/" — outside the (shell) layout. Its first CV import is a
 * metered door, so an allowance refusal there must open the same dialog as inside
 * the shell (it said "Wir konnten diese Datei nicht lesen" with no dialog).
 */
import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CurrentUserProvider } from "@/lib/auth";
import { withIntl } from "@/lib/test-utils/with-intl";
import { ALLOWANCE_REFUSED_EVENT } from "@/components/plan/events";
import { ME, community, mockApi } from "@/components/plan/__tests__/fixtures";
import Home from "../page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/",
}));
vi.mock("@/components/onboarding/FirstRunScreen", () => ({ FirstRunScreen: () => <div data-testid="first-run" /> }));
vi.mock("@/components/shell/AppSidebar", () => ({ AppSidebar: () => null }));
vi.mock("@/components/shell/AppTopbar", () => ({ AppTopbar: () => null }));

let api: ReturnType<typeof mockApi> | null = null;
afterEach(() => {
  api?.restore();
  api = null;
});

describe("onboarding on / (in the shell frame)", () => {
  it("shows the imports dialog when the first import is refused by the allowance", async () => {
    api = mockApi({ current: community({ imports: { limit: 3, used: 3, remaining: 0, purchased_remaining: 0 } }) });
    const inner = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/api/profile/exists")) return new Response(JSON.stringify({ exists: false }), { status: 200 });
      return inner(input, init);
    }) as typeof fetch;
    render(
      withIntl(
        <CurrentUserProvider value={{ user: ME, status: "authenticated", authState: { harness: false } as never }}>
          <Home />
        </CurrentUserProvider>,
        "de",
      ),
    );
    await screen.findByTestId("onboarding-in-shell");
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent(ALLOWANCE_REFUSED_EVENT, {
          detail: { code: "allowance_exhausted", action: "profile_import", renews_on: "2026-12-01" },
        }),
      );
    });
    const dlg = await screen.findByTestId("imports-zero-dialog");
    expect(dlg.textContent).toContain("Deine Lebenslauf-Importe für November sind aufgebraucht");
  });
});
