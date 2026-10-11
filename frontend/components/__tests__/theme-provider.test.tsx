// Copyright (C) 2024-2026 Tobias Rosenbaum
//
// This file is part of Applire.
//
// Applire is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Applire is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with Applire. If not, see <https://www.gnu.org/licenses/>.

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { ThemeProvider } from "../theme-provider";
import { CurrentUserProvider } from "@/lib/auth/current-user";
import { DERIVED_SCHEME_KEYS, deriveScheme } from "@/lib/theme";

afterEach(() => {
  vi.restoreAllMocks();
  // Strip any inline custom properties left on the root between tests.
  for (const key of DERIVED_SCHEME_KEYS) {
    document.documentElement.style.removeProperty(key);
  }
});

function stampPlaceholder() {
  // Simulate the admin editor having stamped its neutral #4a4a4a placeholder
  // palette onto the global document before the user navigated away.
  for (const key of DERIVED_SCHEME_KEYS) {
    document.documentElement.style.setProperty(key, "#4a4a4a");
  }
}

describe("DERIVED_SCHEME_KEYS", () => {
  it("covers exactly the keys deriveScheme emits (so none leak when cleared)", () => {
    const emitted = Object.keys(
      deriveScheme({ primary: "#1b4f72", accent: "#2a8f9d", secondary: "#c9a84c" }, 0.95)
    ).sort();
    expect([...DERIVED_SCHEME_KEYS].sort()).toEqual(emitted);
  });
});

describe("ThemeProvider — restoring the active scheme", () => {
  it("clears leftover inline palette when no active scheme exists (404)", async () => {
    stampPlaceholder();
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: false,
      status: 404,
      json: () => Promise.resolve({ detail: "No active color scheme found" }),
    } as Response);

    render(
      <CurrentUserProvider value={{ status: "unauthenticated" }}>
        <ThemeProvider>
          <div />
        </ThemeProvider>
      </CurrentUserProvider>
    );

    // The neutral placeholder must NOT linger app-wide: each inline override is
    // removed so the static globals.css palette takes over again.
    await waitFor(() => {
      expect(
        document.documentElement.style.getPropertyValue("--color-primary").trim()
      ).toBe("");
    });
    expect(
      document.documentElement.style.getPropertyValue("--color-gold").trim()
    ).toBe("");
  });

  it("applies the active scheme's derived palette on load", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          id: "x",
          name: "EU Blue",
          derived: { "--color-primary": "#1b4f72", "--color-gold": "#c9a84c" },
        }),
    } as Response);

    render(
      <CurrentUserProvider value={{ status: "unauthenticated" }}>
        <ThemeProvider>
          <div />
        </ThemeProvider>
      </CurrentUserProvider>
    );

    await waitFor(() => {
      expect(
        document.documentElement.style.getPropertyValue("--color-primary").trim()
      ).toBe("#1b4f72");
    });
  });
});

// US357 (ADR-023 amended 2026-10-11): which endpoint paints whom.
describe("ThemeProvider — the person's own scheme", () => {
  function okDerived(primary: string) {
    return {
      ok: true,
      status: 200,
      json: () => Promise.resolve({ id: "x", name: "n", derived: { "--color-primary": primary } }),
    } as Response;
  }
  const urls = (spy: ReturnType<typeof vi.spyOn>) =>
    spy.mock.calls.map((c: unknown[]) => String(c[0]));

  it("signed in: paints /api/color-schemes/effective", async () => {
    const spy = vi.spyOn(global, "fetch").mockResolvedValue(okDerived("#2e7d32"));
    render(
      <CurrentUserProvider value={{ status: "authenticated", user: { id: "u", email: "a@b.c", role: "user" } as never }}>
        <ThemeProvider>
          <div />
        </ThemeProvider>
      </CurrentUserProvider>
    );
    await waitFor(() => {
      expect(document.documentElement.style.getPropertyValue("--color-primary").trim()).toBe("#2e7d32");
    });
    expect(urls(spy)[0]).toContain("/api/color-schemes/effective");
    expect(urls(spy).some((u) => u.includes("/api/admin/color-schemes/active"))).toBe(false);
  });

  it("signed in but /effective fails: falls back to the public default", async () => {
    const spy = vi
      .spyOn(global, "fetch")
      .mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) } as Response)
      .mockResolvedValueOnce(okDerived("#1b4f72"));
    render(
      <CurrentUserProvider value={{ status: "authenticated", user: { id: "u", email: "a@b.c", role: "user" } as never }}>
        <ThemeProvider>
          <div />
        </ThemeProvider>
      </CurrentUserProvider>
    );
    await waitFor(() => {
      expect(document.documentElement.style.getPropertyValue("--color-primary").trim()).toBe("#1b4f72");
    });
    expect(urls(spy)[1]).toContain("/api/admin/color-schemes/active");
  });

  it("signed out: never asks the person-scoped endpoint (a 401 would redirect to /login)", async () => {
    const spy = vi.spyOn(global, "fetch").mockResolvedValue(okDerived("#1b4f72"));
    render(
      <CurrentUserProvider value={{ status: "unauthenticated" }}>
        <ThemeProvider>
          <div />
        </ThemeProvider>
      </CurrentUserProvider>
    );
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(urls(spy).every((u) => u.includes("/api/admin/color-schemes/active"))).toBe(true);
  });

  it("while the session is loading: keeps the server-rendered palette, fetches nothing", async () => {
    const spy = vi.spyOn(global, "fetch").mockResolvedValue(okDerived("#000000"));
    document.documentElement.style.setProperty("--color-primary", "#123456");
    render(
      <CurrentUserProvider value={{ status: "loading" }}>
        <ThemeProvider>
          <div />
        </ThemeProvider>
      </CurrentUserProvider>
    );
    await new Promise((r) => setTimeout(r, 20));
    expect(spy).not.toHaveBeenCalled();
    expect(document.documentElement.style.getPropertyValue("--color-primary").trim()).toBe("#123456");
  });
});
