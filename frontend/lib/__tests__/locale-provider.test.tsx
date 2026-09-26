// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire. See <https://www.gnu.org/licenses/> for the
// GNU Affero General Public License this file is distributed under.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { hydrateRoot } from "react-dom/client";
import { act } from "react";
import { useTimeZone } from "next-intl";
import {
  LocaleProvider,
  DEFAULT_TIME_ZONE,
  browserTimeZone,
} from "@/lib/providers/locale-provider";

// #677 / #605: next-intl needs a global time zone, or every server render logs
// ENVIRONMENT_FALLBACK and formats dates in the process zone (UTC in the
// container) while the browser uses its own.

function ZoneProbe() {
  return <span data-testid="zone">{useTimeZone() ?? "NONE"}</span>;
}

describe("LocaleProvider time zone (#677)", () => {
  const realDTF = Intl.DateTimeFormat;

  beforeEach(() => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ ui_language: "en", ui_language_explicit: true }),
    }) as unknown as typeof fetch;
  });

  afterEach(() => {
    Intl.DateTimeFormat = realDTF;
    vi.restoreAllMocks();
  });

  function fakeBrowserZone(zone: string) {
    const Fake = function (...args: ConstructorParameters<typeof Intl.DateTimeFormat>) {
      const inst = new realDTF(...args);
      const resolved = inst.resolvedOptions();
      inst.resolvedOptions = () => ({ ...resolved, timeZone: zone });
      return inst;
    } as unknown as typeof Intl.DateTimeFormat;
    Intl.DateTimeFormat = Fake;
  }

  it("server render carries the DACH default zone, never an unset one", () => {
    // renderToString runs no effects — this is exactly the markup the server
    // produces and the first client render must reproduce for hydration. The
    // faked browser zone proves the zone is NOT looked up during render (a
    // render-time lookup would put the browser's zone into this markup).
    fakeBrowserZone("Asia/Tokyo");
    const html = renderToString(
      <LocaleProvider>
        <ZoneProbe />
      </LocaleProvider>,
    );
    expect(DEFAULT_TIME_ZONE).toBe("Europe/Berlin");
    expect(html).toContain(">Europe/Berlin<");
    expect(html).not.toContain("NONE");
  });

  it("switches to the browser's own zone after mount", async () => {
    fakeBrowserZone("America/New_York");
    render(
      <LocaleProvider>
        <ZoneProbe />
      </LocaleProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("zone").textContent).toBe("America/New_York"));
  });

  it("hydrates server markup rendered in another zone without a mismatch", async () => {
    // Server: its process zone. Browser: a different one. A render-time zone
    // lookup would make the first client render differ from this markup.
    fakeBrowserZone("UTC");
    const html = renderToString(
      <LocaleProvider>
        <ZoneProbe />
      </LocaleProvider>,
    );
    fakeBrowserZone("Asia/Tokyo");
    const container = document.createElement("div");
    container.innerHTML = html;
    document.body.appendChild(container);
    const recoverable = vi.fn();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    await act(async () => {
      hydrateRoot(
        container,
        <LocaleProvider>
          <ZoneProbe />
        </LocaleProvider>,
        { onRecoverableError: recoverable },
      );
    });
    expect(recoverable).not.toHaveBeenCalled();
    expect(
      consoleError.mock.calls.filter((c) => /hydrat/i.test(String(c[0]))),
    ).toEqual([]);
    expect(container.querySelector('[data-testid="zone"]')?.textContent).toBe("Asia/Tokyo");
    container.remove();
  });

  it("falls back to the default when the runtime names no zone", () => {
    fakeBrowserZone("");
    expect(browserTimeZone()).toBe(DEFAULT_TIME_ZONE);
  });
});
