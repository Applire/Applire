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

import { describe, it, expect } from "vitest";
import {
  USER_STATUS_OPTIONS,
  STALE_STATUS_DAYS,
  isStaleStatus,
  staleNextStatuses,
  countByUserStatus,
} from "../user-status";

const DAY = 24 * 36e5;
const NOW = new Date("2026-07-08T12:00:00Z");
const daysAgo = (d: number) => new Date(NOW.getTime() - d * DAY).toISOString();

describe("USER_STATUS_OPTIONS", () => {
  // The frontend seam of the E039/US218 enum ripple: this list must mirror
  // backend UserStatus exactly, in pipeline order.
  it("mirrors the backend UserStatus enum in pipeline order", () => {
    expect(USER_STATUS_OPTIONS.map((o) => o.value)).toEqual([
      "tracking",
      "applied",
      "interviewing",
      "offer",
      "rejected",
      "hired",
    ]);
  });

  it("every option carries a labelKey and a badge className", () => {
    for (const opt of USER_STATUS_OPTIONS) {
      expect(opt.labelKey).toMatch(/^status/);
      expect(opt.className.length).toBeGreaterThan(0);
    }
  });
});

describe("isStaleStatus (JF-E-P2.1 cheap detection)", () => {
  it("is stale for an active pipeline status older than the threshold", () => {
    for (const status of ["applied", "interviewing", "offer"]) {
      expect(isStaleStatus(status, daysAgo(STALE_STATUS_DAYS + 1), NOW)).toBe(true);
    }
  });

  it("is not stale within the threshold", () => {
    expect(isStaleStatus("applied", daysAgo(STALE_STATUS_DAYS - 1), NOW)).toBe(false);
  });

  it("terminal and pre-pipeline statuses are never stale", () => {
    for (const status of ["tracking", "rejected", "hired"]) {
      expect(isStaleStatus(status, daysAgo(90), NOW)).toBe(false);
    }
  });

  it("an undefined status is never stale", () => {
    expect(isStaleStatus(undefined, daysAgo(90), NOW)).toBe(false);
  });
});

describe("staleNextStatuses", () => {
  it("offers the pipeline successors for each active status", () => {
    expect(staleNextStatuses("applied")).toEqual(["interviewing", "rejected"]);
    expect(staleNextStatuses("interviewing")).toEqual(["offer", "rejected"]);
    expect(staleNextStatuses("offer")).toEqual(["hired", "rejected"]);
  });

  it("offers nothing for terminal or pre-pipeline statuses", () => {
    expect(staleNextStatuses("tracking")).toEqual([]);
    expect(staleNextStatuses("rejected")).toEqual([]);
    expect(staleNextStatuses("hired")).toEqual([]);
  });
});

describe("countByUserStatus", () => {
  it("counts applications per status, defaulting a missing status to tracking", () => {
    const apps = [
      { user_status: "applied" },
      { user_status: "applied" },
      { user_status: "interviewing" },
      {},
    ];
    expect(countByUserStatus(apps)).toEqual({
      tracking: 1,
      applied: 2,
      interviewing: 1,
    });
  });

  it("returns an empty record for no applications", () => {
    expect(countByUserStatus([])).toEqual({});
  });
});
