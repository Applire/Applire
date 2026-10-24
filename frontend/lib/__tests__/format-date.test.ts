// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve as resolvePath } from "node:path";
import { formatUiDate, uiDateLocale } from "../format-date";

describe("formatUiDate (#789: dates follow the UI language, not the browser)", () => {
  const iso = "2026-12-22T10:00:00Z";

  it("German UI → day.month.year", () => {
    expect(formatUiDate(iso, "de")).toBe("22.12.2026");
  });

  it("English UI → day/month/year (en-GB), never the US month-first form", () => {
    expect(formatUiDate(iso, "en")).toBe("22/12/2026");
    expect(formatUiDate(iso, "en")).not.toBe("12/22/2026");
  });

  it("passes options through", () => {
    expect(formatUiDate(iso, "de", { day: "numeric", month: "long", year: "numeric" })).toBe("22. Dezember 2026");
    expect(formatUiDate(iso, "en", { day: "numeric", month: "short", year: "numeric" })).toBe("22 Dec 2026");
  });

  it("an unparseable value renders empty, not 'Invalid Date'", () => {
    expect(formatUiDate("not a date", "de")).toBe("");
  });

  it("maps the UI language to an Intl tag", () => {
    expect(uiDateLocale("de")).toBe("de-DE");
    expect(uiDateLocale("en")).toBe("en-GB");
  });
});

// The 12 sites of 789:174 were each a bare `toLocaleDateString()` — formatted by
// the BROWSER locale. Guard the whole UI tree so a new one cannot come back.
function sources(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    if (name === "__tests__" || name === "node_modules") continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...sources(p));
    else if (/\.(tsx?|jsx?)$/.test(name)) out.push(p);
  }
  return out;
}

describe("no locale-less date/number formatting in the UI", () => {
  it("app/ and components/ never call toLocale(Date|Time)String() without a locale", () => {
    const root = resolvePath(__dirname, "../..");
    const offenders: string[] = [];
    for (const dir of ["app", "components"]) {
      for (const file of sources(join(root, dir))) {
        const src = readFileSync(file, "utf8");
        src.split("\n").forEach((line, i) => {
          if (/\.toLocale(Date|Time)?String\(\s*\)/.test(line)) offenders.push(`${file.slice(root.length + 1)}:${i + 1}`);
        });
      }
    }
    expect(offenders).toEqual([]);
  });
});
