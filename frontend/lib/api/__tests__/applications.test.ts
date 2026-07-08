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

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { patchApplicationStatus } from "../applications";

const mockFetch = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch);
  mockFetch.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function okResponse(body: unknown) {
  return { ok: true, json: async () => body };
}

describe("patchApplicationStatus", () => {
  it("PATCHes /api/applications/{id} with the new user_status", async () => {
    mockFetch.mockResolvedValue(okResponse({ id: "app-1", user_status: "interviewing" }));

    const result = await patchApplicationStatus("app-1", "interviewing");

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0];
    expect(String(url)).toContain("/api/applications/app-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ user_status: "interviewing" });
    expect(result.user_status).toBe("interviewing");
  });

  it("stamps applied_at when requested (first transition to applied)", async () => {
    mockFetch.mockResolvedValue(okResponse({ id: "app-1", user_status: "applied" }));

    await patchApplicationStatus("app-1", "applied", { stampAppliedAt: true });

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.user_status).toBe("applied");
    // ISO 8601 timestamp, not a Date object
    expect(typeof body.applied_at).toBe("string");
    expect(new Date(body.applied_at).toString()).not.toBe("Invalid Date");
  });

  it("throws on a non-ok response so callers can revert optimistic state", async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 422, json: async () => ({}) });

    await expect(patchApplicationStatus("app-1", "applied")).rejects.toThrow();
  });
});
