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

/** V-2 = C (founder): the gaps banner names the CVs that were not read. */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { CvParseBanner, readFailedImportNames } from "../CvParseBanner";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams("cv_parsed=1&cv_total=3"),
}));

vi.mock("next-intl", () => ({
  useTranslations:
    (ns: string) =>
    (key: string, vars?: Record<string, string | number>) =>
      key === "failedFileQuoted"
        ? `«${vars?.name}»`
        : `${ns}.${key}${vars ? ":" + JSON.stringify(vars) : ""}`,
}));

const KEY = "applire.failedImports.flow-1";

describe("CvParseBanner", () => {
  beforeEach(() => sessionStorage.clear());

  it("names the files the person continued without", () => {
    sessionStorage.setItem(KEY, JSON.stringify(["cv_alt.pdf", "lebenslauf.docx"]));
    render(<CvParseBanner flowId="flow-1" />);
    const text = screen.getByTestId("cv-parse-banner").textContent ?? "";
    expect(text).toContain("gaps.cvParsedPartialNamed");
    expect(text).toContain('"files":"«cv_alt.pdf», «lebenslauf.docx»"');
    expect(text).toContain('"parsed":1');
    expect(text).toContain('"total":3');
  });

  it("keeps the unnamed line when no names were recorded for this flow", () => {
    sessionStorage.setItem("applire.failedImports.other-flow", JSON.stringify(["x.pdf"]));
    render(<CvParseBanner flowId="flow-1" />);
    const text = screen.getByTestId("cv-parse-banner").textContent ?? "";
    expect(text).toContain("gaps.cvParsedPartial:");
    expect(text).not.toContain("cvParsedPartialNamed");
  });

  it("reads a malformed entry as no names", () => {
    sessionStorage.setItem(KEY, "{not json");
    expect(readFailedImportNames("flow-1")).toEqual([]);
    sessionStorage.setItem(KEY, JSON.stringify({ a: 1 }));
    expect(readFailedImportNames("flow-1")).toEqual([]);
    sessionStorage.setItem(KEY, JSON.stringify(["ok.pdf", 3, ""]));
    expect(readFailedImportNames("flow-1")).toEqual(["ok.pdf"]);
  });
});
