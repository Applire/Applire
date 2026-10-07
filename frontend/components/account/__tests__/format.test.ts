// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, expect, it } from "vitest";

import { formatCompact, formatDate, formatMegabytes, formatWhen } from "../format";

const t = (key: string, values?: Record<string, string | number>) =>
  values ? `${key}|${Object.entries(values).map(([k, v]) => `${k}=${v}`).join(",")}` : key;

// Local-time constructors: formatWhen compares calendar days in the local zone.
const NOW = new Date(2026, 9, 5, 15, 0, 0);
const iso = (d: Date) => d.toISOString();

describe("formatWhen", () => {
  it("under a minute -> justNow", () => {
    expect(formatWhen(iso(new Date(NOW.getTime() - 30_000)), "en", t, NOW)).toBe("justNow");
  });

  it("under an hour -> minutesAgo with the floored count", () => {
    expect(formatWhen(iso(new Date(NOW.getTime() - 4 * 60_000 - 20_000)), "en", t, NOW)).toBe("minutesAgo|count=4");
  });

  it("earlier the same day -> todayAt with a formatted time", () => {
    const out = formatWhen(iso(new Date(2026, 9, 5, 9, 12)), "en", t, NOW);
    expect(out).toMatch(/^todayAt\|time=09:12/);
  });

  it("the day before -> yesterdayAt", () => {
    const out = formatWhen(iso(new Date(2026, 9, 4, 21, 40)), "en", t, NOW);
    expect(out).toMatch(/^yesterdayAt\|time=09:40 PM/);
  });

  it("older -> a plain date in the UI locale", () => {
    const when = iso(new Date(2026, 7, 12, 10, 0));
    expect(formatWhen(when, "de", t, NOW)).toBe("12.08.2026");
    expect(formatWhen(when, "en", t, NOW)).toBe("08/12/2026");
  });

  it("an unparseable timestamp -> empty string", () => {
    expect(formatWhen("not a date", "en", t, NOW)).toBe("");
  });

  it("a future timestamp is not 'just now'", () => {
    const out = formatWhen(iso(new Date(NOW.getTime() + 30_000)), "en", t, NOW);
    expect(out).not.toBe("justNow");
  });
});

describe("formatDate", () => {
  it("formats by the UI locale and tolerates garbage", () => {
    const when = iso(new Date(2026, 9, 3, 12, 0));
    expect(formatDate(when, "de")).toBe("03.10.2026");
    expect(formatDate(when, "en")).toBe("10/03/2026");
    expect(formatDate("x", "en")).toBe("");
  });
});

describe("formatMegabytes", () => {
  it("rounds to whole megabytes", () => {
    expect(formatMegabytes(212 * 1024 * 1024, "en")).toMatch(/^212\s?MB$/);
    expect(formatMegabytes(212 * 1024 * 1024 + 400_000, "en")).toMatch(/^212\s?MB$/);
    expect(formatMegabytes(0, "en")).toMatch(/^0\s?MB$/);
  });

  it("uses the locale's number grouping", () => {
    expect(formatMegabytes(1500 * 1024 * 1024, "en")).toMatch(/^1,500\s?MB$/);
    expect(formatMegabytes(1500 * 1024 * 1024, "de")).toMatch(/^1\.500\s?MB$/);
  });
});

describe("formatCompact", () => {
  it("en: 1.8M", () => {
    expect(formatCompact(1_800_000, "en")).toBe("1.8M");
    expect(formatCompact(420_000, "en")).toBe("420K");
  });

  it("de: decimal comma and Mio.", () => {
    expect(formatCompact(1_800_000, "de")).toMatch(/^1,8\s?Mio\.$/);
  });

  it("small numbers stay as they are", () => {
    expect(formatCompact(950, "en")).toBe("950");
  });
});
