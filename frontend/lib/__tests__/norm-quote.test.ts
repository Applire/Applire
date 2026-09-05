// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
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

import fs from "fs";
import path from "path";
import { describe, it, expect } from "vitest";
import { normQuote } from "../norm-quote";

// SF-REVIEW.5 — the divergence detector. The SAME file is read by
// backend/tests/unit/test_norm_quote_vectors.py, which asserts the Python
// `_norm_quote` produces these outputs. If the two folds drift apart, one of
// the two suites reddens. Reading the repo file (rather than copying the pairs
// in here) is the whole point: a copy would drift silently.
const VECTORS_PATH = path.resolve(__dirname, "../../../tests/files/norm_quote_vectors.json");

type Vector = { in: string; out: string };

describe("normQuote — parity with the backend _norm_quote fold (ADR-070 cl. 1 / SF-REVIEW.5)", () => {
  const doc = JSON.parse(fs.readFileSync(VECTORS_PATH, "utf-8")) as {
    vectors: Vector[];
  };

  it("reads a non-empty shared vector file (a missing/empty file must not pass silently)", () => {
    expect(Array.isArray(doc.vectors)).toBe(true);
    expect(doc.vectors.length).toBeGreaterThanOrEqual(20);
  });

  for (const v of doc.vectors) {
    it(`folds ${JSON.stringify(v.in)} to ${JSON.stringify(v.out)}`, () => {
      expect(normQuote(v.in)).toBe(v.out);
    });
  }

  it("treats null and undefined as the empty string", () => {
    expect(normQuote(null)).toBe("");
    expect(normQuote(undefined)).toBe("");
  });

  it("does NOT fold two merely-similar terms together (SF-REVIEW.3's negative direction)", () => {
    expect(normQuote("SAP PP")).not.toBe(normQuote("SAP PP/DS"));
    expect(normQuote("ISO 45001")).not.toBe(normQuote("ISO 9001"));
  });
});
