// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, expect, it } from "vitest";
import { formatEntryPeriod } from "../profile-entries";

// D-14 (Hazelnut build 5): the editors' period line follows the CV rule
// (templates.filters.period) — an ended role with no known end is its start alone.
describe("formatEntryPeriod — is_current decides the missing end (D-14)", () => {
  it("is_current false, no end date: the start date alone, never present", () => {
    expect(formatEntryPeriod("2005-03", null, "present", false, "en")).toBe("Mar 2005");
  });

  it("is_current true, no end date: present", () => {
    expect(formatEntryPeriod("2019-04", null, "present", true, "en")).toBe("Apr 2019 → present");
  });

  it("is_current unstated, no end date: present (legacy reading kept)", () => {
    expect(formatEntryPeriod("2016-02", null, "present", null, "en")).toBe("Feb 2016 → present");
    expect(formatEntryPeriod("2016-02", null, "present", undefined, "en")).toBe("Feb 2016 → present");
  });

  it("an end date renders as itself when the role ended", () => {
    expect(formatEntryPeriod("2010-01", "2015-12", "present", false, "en")).toBe("Jan 2010 → Dec 2015");
  });
});
