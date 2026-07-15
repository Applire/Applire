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

// TZ pinning: this file runs under Europe/Berlin regardless of the runner's
// timezone, so the #164 regression guard observes a NON-ZERO UTC offset even
// on UTC CI (where an offset-derived oracle would degenerate to a no-op —
// slice(0,16) and the correct conversion are indistinguishable at offset 0).
// Node ≥13 re-reads an assigned `process.env.TZ` on the next Date operation
// (the env setter resets the cached ICU timezone), and this project's vitest
// honors it — the "TZ pinning sanity" test below FAILS loudly with the real
// observed offset if that mechanism ever stops working, so the pin can never
// silently degrade back into a no-op. Restored in afterAll to avoid leaking
// Berlin time into other test files sharing this worker process.
const ORIGINAL_TZ = process.env.TZ;
process.env.TZ = "Europe/Berlin";

import { describe, it, expect, afterAll } from "vitest";
import { isoUtcToLocalInput, localInputToIsoUtc } from "../deadline-datetime";

afterAll(() => {
  if (ORIGINAL_TZ === undefined) {
    delete process.env.TZ;
  } else {
    process.env.TZ = ORIGINAL_TZ;
  }
});

// #164 root cause: loading did `data.deadline.slice(0,16)` (UTC wall-clock
// digits verbatim into a local datetime-local input) while saving did
// `new Date(value).toISOString()` (interprets the digits as LOCAL time) —
// each round trip sheared the UTC offset off (09:30 → 07:30 → 05:30 in
// CEST). The converters make both directions go through the Date object's
// local getters/constructor, so the round trip is symmetric.

describe("TZ pinning sanity (proof the regression guard observes a non-zero offset)", () => {
  it("this file runs under Europe/Berlin: CEST in summer, CET in winter", () => {
    expect(new Date("2026-08-15T07:30:00Z").getTimezoneOffset()).toBe(-120); // CEST = UTC+2
    expect(new Date("2026-01-15T07:30:00Z").getTimezoneOffset()).toBe(-60); // CET = UTC+1
  });
});

describe("isoUtcToLocalInput", () => {
  it("converts a summer UTC instant to CEST wall clock (the #164 case)", () => {
    expect(isoUtcToLocalInput("2026-08-15T07:30:00Z")).toBe("2026-08-15T09:30");
  });

  it("converts a winter UTC instant to CET wall clock (DST-sensitive)", () => {
    expect(isoUtcToLocalInput("2026-01-15T07:30:00Z")).toBe("2026-01-15T08:30");
  });

  it("crosses a date boundary when the offset pushes past midnight", () => {
    expect(isoUtcToLocalInput("2026-08-14T22:30:00Z")).toBe("2026-08-15T00:30");
  });

  it("returns an empty string for empty/unparseable input", () => {
    expect(isoUtcToLocalInput("")).toBe("");
    expect(isoUtcToLocalInput("not-a-date")).toBe("");
  });
});

describe("localInputToIsoUtc", () => {
  it("interprets a summer datetime-local value as CEST and returns the UTC instant", () => {
    expect(localInputToIsoUtc("2026-08-15T09:30")).toBe("2026-08-15T07:30:00.000Z");
  });

  it("interprets a winter datetime-local value as CET and returns the UTC instant", () => {
    expect(localInputToIsoUtc("2026-01-15T08:30")).toBe("2026-01-15T07:30:00.000Z");
  });

  it("returns an empty string for empty/unparseable input instead of throwing", () => {
    expect(localInputToIsoUtc("")).toBe("");
    expect(localInputToIsoUtc("not-a-date")).toBe("");
  });
});

describe("round trip (the #164 regression guard)", () => {
  it("isoUtcToLocalInput -> localInputToIsoUtc preserves the instant, to the minute", () => {
    // Note the inherent limit of ANY datetime-local round trip: during the
    // fall-back hour (2026-10-25 02:00–03:00 Berlin wall clock occurs twice)
    // the input value carries no offset, so the second occurrence (01:30Z,
    // CET) is unavoidably re-parsed as the first (00:30Z, CEST). That's a
    // property of the input type, not a converter defect — the invariant is
    // asserted for all UNambiguous wall times, including instants adjacent
    // to both DST boundaries.
    const instants = [
      "2026-08-15T07:30:00.000Z",
      "2026-01-15T07:30:00.000Z",
      "2026-03-29T00:30:00.000Z", // 01:30 CET, just before the spring-forward jump (01:00Z)
      "2026-03-29T01:30:00.000Z", // 03:30 CEST, just after it
      "2026-10-25T00:30:00.000Z", // 02:30 CEST — first occurrence of the doubled hour
      "2026-10-25T02:30:00.000Z", // 03:30 CET — past the fall-back, unambiguous again
    ];
    for (const iso of instants) {
      const roundTripped = localInputToIsoUtc(isoUtcToLocalInput(iso));
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
    expect(savedUtc).toBe("2026-08-15T07:30:00.000Z");
    expect(isoUtcToLocalInput(savedUtc)).toBe(typed);
  });
});
