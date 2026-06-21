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

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  startEnrichSession,
  isEnrichNoGaps,
  respondToEnrich,
  skipGap,
  markGapNA,
} from "../api/enrich";

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

function mockOk(data: unknown) {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    json: async () => data,
  });
}

function mockError(detail: string, status = 404) {
  mockFetch.mockResolvedValueOnce({
    ok: false,
    status,
    statusText: "Error",
    json: async () => ({ detail }),
  });
}

beforeEach(() => mockFetch.mockClear());

describe("startEnrichSession", () => {
  it("calls /api/profile/enrich/start with null scope when no scope provided", async () => {
    mockOk({
      session_id: "abc",
      first_question: "Q?",
      gaps: [],
      estimated_questions: 3,
    });
    await startEnrichSession();
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/profile/enrich/start"),
      expect.objectContaining({ body: JSON.stringify({ scope: null }) })
    );
  });

  it("passes scope when provided", async () => {
    mockOk({
      session_id: "abc",
      first_question: "Q?",
      gaps: [],
      estimated_questions: 1,
    });
    await startEnrichSession("work_experience:Beta GmbH:Product Lead");
    expect(mockFetch).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        body: JSON.stringify({
          scope: "work_experience:Beta GmbH:Product Lead",
        }),
      })
    );
  });

  it("throws with backend detail on a non-404 error", async () => {
    mockError("Session in an invalid state", 422);
    await expect(startEnrichSession()).rejects.toThrow(
      "Session in an invalid state"
    );
  });

  // US166 — the completeness panel surfaces section-level gaps, but Mode C keys
  // off work-entry gaps; when none exist the backend 404s. Clicking Improve must
  // land on a friendly "nothing to enrich" state, never a raw error.
  it("returns a noGaps sentinel (not a throw) on a 404 no-gaps response", async () => {
    mockError("No completeness gaps detected in profile", 404);
    const result = await startEnrichSession();
    expect(isEnrichNoGaps(result)).toBe(true);
  });

  it("returns EnrichSession with all fields", async () => {
    const session = {
      session_id: "sess-1",
      first_question: "What is your primary skill?",
      gaps: [
        { id: "gap-1", label: "Python", status: "pending" as const },
      ],
      estimated_questions: 5,
    };
    mockOk(session);
    const result = await startEnrichSession();
    if (isEnrichNoGaps(result)) throw new Error("expected a real session");
    expect(result).toEqual(session);
    expect(result.session_id).toBe("sess-1");
    expect(result.estimated_questions).toBe(5);
  });
});

describe("respondToEnrich", () => {
  it("posts answer and returns result with profile_updated", async () => {
    const result = {
      next_question: "Next?",
      gaps: [{ id: "gap-1", label: "Python", status: "done" as const }],
      done: false,
      profile_updated: true,
    };
    mockOk(result);
    const res = await respondToEnrich("session-1", "My answer");
    expect(res.profile_updated).toBe(true);
    expect(res.next_question).toBe("Next?");
  });

  it("sends session_id and answer in request body", async () => {
    mockOk({
      next_question: null,
      gaps: [],
      done: true,
      profile_updated: true,
    });
    await respondToEnrich("sess-123", "Some technical skill");
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/sess-123/respond"),
      expect.objectContaining({
        body: JSON.stringify({ answer: "Some technical skill" }),
      })
    );
  });

  it("throws on error response", async () => {
    mockError("Invalid session", 422);
    await expect(respondToEnrich("bad-id", "answer")).rejects.toThrow(
      "Invalid session"
    );
  });
});

describe("skipGap", () => {
  it("posts to /skip endpoint", async () => {
    mockOk({
      next_question: "Next?",
      gaps: [],
      done: false,
    });
    await skipGap("session-1");
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/session-1/skip"),
      expect.objectContaining({ method: "POST" })
    );
  });

  it("returns EnrichActionResult with next_question", async () => {
    const result = {
      next_question: "What about databases?",
      gaps: [{ id: "gap-2", label: "Database", status: "active" as const }],
      done: false,
    };
    mockOk(result);
    const res = await skipGap("sess-1");
    expect(res.next_question).toBe("What about databases?");
    expect(res.done).toBe(false);
  });

  it("throws on error response", async () => {
    mockError("Session expired", 422);
    await expect(skipGap("bad-id")).rejects.toThrow("Session expired");
  });
});

describe("markGapNA", () => {
  it("posts to /na endpoint", async () => {
    mockOk({
      next_question: null,
      gaps: [],
      done: true,
    });
    await markGapNA("session-1");
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/session-1/na"),
      expect.objectContaining({ method: "POST" })
    );
  });

  it("returns EnrichActionResult with done flag", async () => {
    const result = {
      next_question: null,
      gaps: [],
      done: true,
    };
    mockOk(result);
    const res = await markGapNA("sess-2");
    expect(res.done).toBe(true);
    expect(res.next_question).toBeNull();
  });

  it("throws on error response", async () => {
    mockError("Conflict", 422);
    await expect(markGapNA("sess-bad")).rejects.toThrow("Conflict");
  });
});
