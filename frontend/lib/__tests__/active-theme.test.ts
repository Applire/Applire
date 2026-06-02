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
import { loadActiveSchemeVars } from "../active-theme";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("loadActiveSchemeVars — server-side pre-paint fetch", () => {
  it("returns the active scheme's derived vars on success", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          id: "x",
          name: "EU Blue",
          derived: { "--color-primary": "#003399", "--color-gold": "#fecb00" },
        }),
    } as Response);

    const vars = await loadActiveSchemeVars();

    expect(vars).toEqual({ "--color-primary": "#003399", "--color-gold": "#fecb00" });
  });

  it("returns null when no scheme is active (404) so globals.css is used", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: false,
      status: 404,
      json: () => Promise.resolve({ detail: "No active color scheme found" }),
    } as Response);

    expect(await loadActiveSchemeVars()).toBeNull();
  });

  it("returns null when the backend is unreachable (network error)", async () => {
    vi.spyOn(global, "fetch").mockRejectedValue(new Error("ECONNREFUSED"));

    expect(await loadActiveSchemeVars()).toBeNull();
  });
});
