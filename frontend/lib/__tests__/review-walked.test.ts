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

import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import {
  isDocumentWalked,
  markDocumentWalked,
  clearDocumentWalked,
  resolveReviewMode,
  walkedStorageKey,
  isReviewModePreference,
} from "../review-walked";

describe("walkedStorageKey — ADR-081 clause 5a: keyed on the GENERATED-DOCUMENT id", () => {
  it("puts the document id in the key", () => {
    expect(walkedStorageKey("cv", "doc-abc")).toBe("applire.review.walked.cv.doc-abc");
  });

  it("gives two generations of the SAME application two different keys", () => {
    // JF-F-K.3: keyed on the application these would collide and a regenerated
    // document would inherit `walked = true`.
    expect(walkedStorageKey("cv", "gen-1")).not.toBe(walkedStorageKey("cv", "gen-2"));
  });

  it("does not collide across document kinds", () => {
    expect(walkedStorageKey("cv", "x")).not.toBe(walkedStorageKey("cover-letter", "x"));
  });
});

describe("the walked bit is per document and per browser", () => {
  beforeEach(() => window.localStorage.clear());

  it("is false before anything is written", () => {
    expect(isDocumentWalked("cv", "doc-1")).toBe(false);
  });

  it("round-trips through localStorage", () => {
    markDocumentWalked("cv", "doc-1");
    expect(isDocumentWalked("cv", "doc-1")).toBe(true);
    clearDocumentWalked("cv", "doc-1");
    expect(isDocumentWalked("cv", "doc-1")).toBe(false);
  });

  it("never claims a document is walked when the id is missing", () => {
    markDocumentWalked("cv", null);
    expect(isDocumentWalked("cv", null)).toBe(false);
  });

  it("degrades to 'not walked' when storage throws (private mode) — the benign direction", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(isDocumentWalked("cv", "doc-1")).toBe(false);
    spy.mockRestore();
  });

  it("does not throw when writing fails", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(() => markDocumentWalked("cv", "doc-1")).not.toThrow();
    spy.mockRestore();
  });
});

describe("resolveReviewMode — ADR-081 clause 5", () => {
  afterEach(() => window.localStorage.clear());

  it("honours an explicit `overview` preference regardless of the document", () => {
    expect(
      resolveReviewMode({
        preference: "overview",
        kind: "cv",
        documentId: "doc-1",
        hasGroup1Findings: true,
      }),
    ).toBe("overview");
  });

  it("honours an explicit `guided` preference even on a walked, clean document", () => {
    expect(
      resolveReviewMode({
        preference: "guided",
        kind: "cv",
        documentId: "doc-1",
        hasGroup1Findings: false,
      }),
    ).toBe("guided");
  });

  it("under `auto`, resolves to guided while group-1 findings are unwalked", () => {
    expect(
      resolveReviewMode(
        { preference: "auto", kind: "cv", documentId: "doc-1", hasGroup1Findings: true },
        () => false,
      ),
    ).toBe("guided");
  });

  it("under `auto`, resolves to overview once this document has been walked", () => {
    expect(
      resolveReviewMode(
        { preference: "auto", kind: "cv", documentId: "doc-1", hasGroup1Findings: true },
        () => true,
      ),
    ).toBe("overview");
  });

  it("under `auto`, resolves to overview when there is nothing send-blocking to walk", () => {
    expect(
      resolveReviewMode(
        { preference: "auto", kind: "cv", documentId: "doc-1", hasGroup1Findings: false },
        () => false,
      ),
    ).toBe("overview");
  });

  it("under `auto`, resolves to overview while the document id is not yet known", () => {
    expect(
      resolveReviewMode({
        preference: "auto",
        kind: "cv",
        documentId: null,
        hasGroup1Findings: true,
      }),
    ).toBe("overview");
  });

  // JF-F-K.3 — the regenerate-then-assert test the row demanded.
  it("REGENERATION returns to guided instead of inheriting walked=true", () => {
    const first = "generated-cv-1";
    const second = "generated-cv-2"; // a regeneration: same application, new document

    markDocumentWalked("cv", first);
    expect(
      resolveReviewMode({
        preference: "auto",
        kind: "cv",
        documentId: first,
        hasGroup1Findings: true,
      }),
    ).toBe("overview");

    expect(
      resolveReviewMode({
        preference: "auto",
        kind: "cv",
        documentId: second,
        hasGroup1Findings: true,
      }),
    ).toBe("guided");
  });
});

describe("isReviewModePreference", () => {
  it("accepts the three stored values and rejects anything else", () => {
    expect(["auto", "overview", "guided"].every(isReviewModePreference)).toBe(true);
    expect(isReviewModePreference("sideways")).toBe(false);
    expect(isReviewModePreference(undefined)).toBe(false);
    expect(isReviewModePreference(null)).toBe(false);
  });
});
