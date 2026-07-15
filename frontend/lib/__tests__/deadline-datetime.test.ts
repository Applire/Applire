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

// #164 root cause: loading did `data.deadline.slice(0,16)` (took the UTC wall
// clock digits verbatim into a local <input type="datetime-local">) while
// saving did `new Date(value).toISOString()` (interprets the typed digits as
// LOCAL time). Each round trip sheared the UTC offset off — 09:30 became
// 07:30 became 05:30 in CEST. These converters make BOTH directions go
// through the Date object's local getters/constructor, so the round trip is
// symmetric in any runtime timezone.
//
// TZ note: vitest workers only honor `process.env.TZ` if it is set BEFORE
// the Date/Intl internals initialize (module load order, not per-test), and
// there is no existing project precedent for TZ-pinning vitest runs (grepped
// vitest.config.ts / other lib tests — none). Rather than fork test files
// per TZ, `expectedLocalString` below independently derives the expected
// local wall-clock string from `Date.prototype.getTimezoneOffset()` (offset
// math on UTC getters, NOT reusing the implementation's local getters) so
// every assertion is correct under whatever TZ the runtime actually has —
// CI's UTC, a dev box's Europe/Berlin, anything.

import { describe, it, expect } from "vitest";
import { isoUtcToLocalInput, localInputToIsoUtc } from "../deadline-datetime";

/** Independent oracle: shift the UTC instant by the runtime's own offset for
 * THAT instant (DST-aware) and read it back with UTC getters — deliberately
 * not the implementation's own (local-getter) code path. */
function expectedLocalString(iso: string): string {
  const utcMs = new Date(iso).getTime();
  const offsetMin = new Date(utcMs).getTimezoneOffset(); // UTC minus local, in minutes
  const shifted = new Date(utcMs - offsetMin * 60000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(shifted.getUTCDate())}T${pad(shifted.getUTCHours())}:${pad(shifted.getUTCMinutes())}`;
}

describe("isoUtcToLocalInput", () => {
  it("matches the offset-derived expectation for a summer instant (DST-sensitive)", () => {
    const iso = "2026-08-15T07:30:00Z";
    expect(isoUtcToLocalInput(iso)).toBe(expectedLocalString(iso));
  });

  it("matches the offset-derived expectation for a winter instant", () => {
    const iso = "2026-01-15T07:30:00Z";
    expect(isoUtcToLocalInput(iso)).toBe(expectedLocalString(iso));
  });

  it("returns an empty string for an unparseable input", () => {
    expect(isoUtcToLocalInput("")).toBe("");
    expect(isoUtcToLocalInput("not-a-date")).toBe("");
  });
});

describe("localInputToIsoUtc", () => {
  it("interprets the datetime-local value as local time and returns a UTC ISO string", () => {
    const local = "2026-08-15T09:30";
    const result = localInputToIsoUtc(local);
    expect(result).toBe(new Date(local).toISOString());
  });
});

describe("round trip (the #164 regression guard)", () => {
  it("isoUtcToLocalInput -> localInputToIsoUtc preserves the instant, to the minute", () => {
    const instants = [
      "2026-08-15T07:30:00.000Z",
      "2026-01-15T07:30:00.000Z",
      "2026-03-29T00:30:00.000Z", // around a DST boundary
      "2026-10-25T00:30:00.000Z",
    ];
    for (const iso of instants) {
      const local = isoUtcToLocalInput(iso);
      const roundTripped = localInputToIsoUtc(local);
      // datetime-local has no seconds, so compare to the minute.
      expect(new Date(roundTripped).getTime()).toBe(
        Math.floor(new Date(iso).getTime() / 60000) * 60000
      );
    }
  });

  it("does NOT reproduce the #164 shear: 09:30 typed stays 09:30 after a save->load cycle", () => {
    // Simulate: user types 09:30 local -> saved as UTC ISO -> reloaded from
    // the server -> converted back for the input. Must still read 09:30.
    const typed = "2026-08-15T09:30";
    const savedUtc = localInputToIsoUtc(typed);
    const reloaded = isoUtcToLocalInput(savedUtc);
    expect(reloaded).toBe(typed);
  });
});
