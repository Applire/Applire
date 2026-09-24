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

/**
 * ADR-090 — the review actions on a generated document (WORK-PACKAGES
 * Contract 2). Pins each function's URL/method/body and the two error paths:
 * a non-ok response and a network failure, both surfaced as `ReviewActionError`
 * carrying the right status (0 for the network failure).
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import {
  addEvidence,
  takeOut,
  undoDecision,
  markEdited,
  markWalked,
  ReviewActionError,
} from "../api/document-review";

afterEach(() => vi.restoreAllMocks());

function fetchMock(body: object, ok = true, status = 200) {
  return vi.fn().mockResolvedValue({ ok, status, json: () => Promise.resolve(body) });
}

function lastCall(mock: ReturnType<typeof vi.fn>) {
  const [url, init] = mock.mock.calls[0] as [string, RequestInit];
  return { url, init };
}

describe("addEvidence", () => {
  it("POSTs to .../review/add-evidence with the finding key and text", async () => {
    const mock = fetchMock({ testimony: { status: "applied" }, review_state: null });
    global.fetch = mock;
    await addEvidence("cv", "cv-1", "finding-a", "I led that project");
    const { url, init } = lastCall(mock);
    expect(url).toMatch(/\/api\/cv\/cv-1\/review\/add-evidence$/);
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({ "Content-Type": "application/json" });
    expect(JSON.parse(init.body as string)).toEqual({
      finding_key: "finding-a",
      text: "I led that project",
    });
  });

  it("uses the cover-letter URL segment for that kind", async () => {
    const mock = fetchMock({ testimony: { status: "applied" }, review_state: null });
    global.fetch = mock;
    await addEvidence("cover-letter", "cl-1", "finding-b", "text");
    expect(lastCall(mock).url).toMatch(/\/api\/cover-letter\/cl-1\/review\/add-evidence$/);
  });
});

describe("takeOut", () => {
  it("POSTs to .../review/take-out with the finding key", async () => {
    const mock = fetchMock({ changes: [], still_listed: false, review_state: null });
    global.fetch = mock;
    await takeOut("cv", "cv-1", "finding-a");
    const { url, init } = lastCall(mock);
    expect(url).toMatch(/\/api\/cv\/cv-1\/review\/take-out$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ finding_key: "finding-a" });
  });

  it("uses the cover-letter URL segment for that kind", async () => {
    const mock = fetchMock({ changes: [], still_listed: false, review_state: null });
    global.fetch = mock;
    await takeOut("cover-letter", "cl-1", "finding-b");
    expect(lastCall(mock).url).toMatch(/\/api\/cover-letter\/cl-1\/review\/take-out$/);
  });
});

describe("undoDecision", () => {
  it("POSTs to .../review/undo with the finding key", async () => {
    const mock = fetchMock({ review_state: null });
    global.fetch = mock;
    await undoDecision("cv", "cv-1", "finding-a");
    const { url, init } = lastCall(mock);
    expect(url).toMatch(/\/api\/cv\/cv-1\/review\/undo$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ finding_key: "finding-a" });
  });
});

describe("markEdited", () => {
  it("POSTs to .../review/edited with the finding key", async () => {
    const mock = fetchMock({ review_state: null });
    global.fetch = mock;
    await markEdited("cv", "cv-1", "finding-a");
    const { url, init } = lastCall(mock);
    expect(url).toMatch(/\/api\/cv\/cv-1\/review\/edited$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ finding_key: "finding-a" });
  });

  it("uses the cover-letter URL segment for that kind", async () => {
    const mock = fetchMock({ review_state: null });
    global.fetch = mock;
    await markEdited("cover-letter", "cl-1", "finding-b");
    expect(lastCall(mock).url).toMatch(/\/api\/cover-letter\/cl-1\/review\/edited$/);
  });
});

describe("markWalked", () => {
  it("POSTs to .../review/walked with NO body", async () => {
    const mock = fetchMock({ review_state: null });
    global.fetch = mock;
    await markWalked("cv", "cv-1");
    const { url, init } = lastCall(mock);
    expect(url).toMatch(/\/api\/cv\/cv-1\/review\/walked$/);
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
  });

  it("uses the cover-letter URL segment for that kind", async () => {
    const mock = fetchMock({ review_state: null });
    global.fetch = mock;
    await markWalked("cover-letter", "cl-1");
    expect(lastCall(mock).url).toMatch(/\/api\/cover-letter\/cl-1\/review\/walked$/);
  });
});

async function catchError(promise: Promise<unknown>): Promise<ReviewActionError> {
  try {
    await promise;
  } catch (err) {
    return err as ReviewActionError;
  }
  throw new Error("expected promise to reject");
}

describe("ReviewActionError", () => {
  it("a non-ok response throws with the HTTP status", async () => {
    global.fetch = fetchMock({ detail: "nope" }, false, 404);
    const err = await catchError(takeOut("cv", "cv-1", "finding-a"));
    expect(err).toBeInstanceOf(ReviewActionError);
    expect(err.status).toBe(404);
  });

  it("a network error throws ReviewActionError with status 0", async () => {
    global.fetch = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    const err = await catchError(takeOut("cv", "cv-1", "finding-a"));
    expect(err).toBeInstanceOf(ReviewActionError);
    expect(err.status).toBe(0);
  });

  it("every action shares the same error path (spot-check addEvidence)", async () => {
    global.fetch = fetchMock({}, false, 500);
    const err = await catchError(addEvidence("cv", "cv-1", "finding-a", "x"));
    expect(err).toBeInstanceOf(ReviewActionError);
    expect(err.status).toBe(500);
  });
});
