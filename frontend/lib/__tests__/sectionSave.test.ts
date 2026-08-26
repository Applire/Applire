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

import { describe, expect, it, vi } from "vitest";
import {
  saveProfileSection,
  saveProfileObjectSection,
  type ProfileSectionsResponseLike,
} from "../sectionSave";

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

const PROFILE: ProfileSectionsResponseLike = {
  updated_at: "2026-08-25T10:00:00Z",
  profile: {
    work_experience: [{ id: "e1", company: "Acme", role: "Engineer" }],
  },
};

describe("saveProfileSection", () => {
  // H1.6 — basis_updated_at is sent, url-encoded, as a query param.
  it("PATCHes with the section body and an encoded basis_updated_at", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(200, PROFILE));
    await saveProfileSection({
      apiBase: "http://api",
      section: "work_experience",
      entries: [{ id: "e1", company: "Acme", role: "Engineer" }],
      basisUpdatedAt: "2026-08-25T09:00:00+00:00",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0];
    expect(String(url)).toBe(
      "http://api/api/profile/work_experience?basis_updated_at=2026-08-25T09%3A00%3A00%2B00%3A00",
    );
    expect((init as RequestInit).method).toBe("PATCH");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual([
      { id: "e1", company: "Acme", role: "Engineer" },
    ]);
  });

  it("returns ok + mismatch:false when the saved entry round-trips byte-identical", async () => {
    const saved = { id: "e1", company: "Acme", role: "Engineer", achievements: ["Shipped X"] };
    const fetchImpl = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { work_experience: [saved] } }),
    );
    const result = await saveProfileSection({
      apiBase: "http://api",
      section: "work_experience",
      entries: [saved],
      basisUpdatedAt: "t1",
      savedEntryId: "e1",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(result.status).toBe("ok");
    if (result.status === "ok") expect(result.mismatch).toBe(false);
  });

  // H0.4 — a committer gate that returns 200 without persisting the change
  // must be caught, not read as a silent success.
  it("flags mismatch:true when the response's entry differs from what was sent", async () => {
    const sent = { id: "e1", company: "Acme", role: "Engineer", achievements: ["Shipped X"] };
    const unchanged = { id: "e1", company: "Acme", role: "Engineer", achievements: [] };
    const fetchImpl = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { work_experience: [unchanged] } }),
    );
    const result = await saveProfileSection({
      apiBase: "http://api",
      section: "work_experience",
      entries: [sent],
      basisUpdatedAt: "t1",
      savedEntryId: "e1",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(result.status).toBe("ok");
    if (result.status === "ok") expect(result.mismatch).toBe(true);
  });

  it("flags mismatch:true when the saved entry is missing from the response entirely", async () => {
    const sent = { id: "e1", company: "Acme", role: "Engineer" };
    const fetchImpl = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { work_experience: [] } }),
    );
    const result = await saveProfileSection({
      apiBase: "http://api",
      section: "work_experience",
      entries: [sent],
      basisUpdatedAt: "t1",
      savedEntryId: "e1",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(result.status).toBe("ok");
    if (result.status === "ok") expect(result.mismatch).toBe(true);
  });

  it("skips the mismatch check for a brand-new entry with no id yet", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { work_experience: [{ id: "minted", company: "Acme", role: "Engineer" }] } }),
    );
    const result = await saveProfileSection({
      apiBase: "http://api",
      section: "work_experience",
      entries: [{ company: "Acme", role: "Engineer" }],
      basisUpdatedAt: "t1",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(result.status).toBe("ok");
    if (result.status === "ok") expect(result.mismatch).toBe(false);
  });

  // Contract correction: FastAPI wraps HTTPException.detail, so the 409 body
  // is {"detail": {"error": "stale_edit", "current": <profile>}}.
  it("classifies a 409 as stale and surfaces `current` for reload", async () => {
    const current = { updated_at: "t3", profile: { work_experience: [] } };
    const fetchImpl = vi.fn(async () =>
      jsonResponse(409, { detail: { error: "stale_edit", current } }),
    );
    const result = await saveProfileSection({
      apiBase: "http://api",
      section: "work_experience",
      entries: [],
      basisUpdatedAt: "t1",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(result.status).toBe("stale");
    if (result.status === "stale") expect(result.current).toEqual(current);
  });

  it("does not retry on 409 — exactly one fetch call", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(409, { detail: { error: "stale_edit", current: PROFILE } }),
    );
    await saveProfileSection({
      apiBase: "http://api",
      section: "work_experience",
      entries: [],
      basisUpdatedAt: "t1",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("classifies a 422 with the string detail message", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(422, { detail: "company must not be blank" }));
    const result = await saveProfileSection({
      apiBase: "http://api",
      section: "work_experience",
      entries: [],
      basisUpdatedAt: "t1",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(result.status).toBe("invalid");
    if (result.status === "invalid") expect(result.message).toBe("company must not be blank");
  });

  it("classifies any other non-ok status as a generic error", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(500, {}));
    const result = await saveProfileSection({
      apiBase: "http://api",
      section: "work_experience",
      entries: [],
      basisUpdatedAt: "t1",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(result.status).toBe("error");
  });

  it("classifies a network failure as a generic error", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new Error("network down");
    });
    const result = await saveProfileSection({
      apiBase: "http://api",
      section: "work_experience",
      entries: [],
      basisUpdatedAt: "t1",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(result.status).toBe("error");
  });
});

// US292/#178 — the object-section (merge-patch) driver behind SummaryEditor
// and PersonalInfoEditor.
describe("saveProfileObjectSection", () => {
  it("PATCHes with only the supplied keys as the body, url-encoding basis_updated_at", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { personal_info: { email: "anna@example.com" } } }),
    );
    await saveProfileObjectSection({
      apiBase: "http://api",
      section: "personal_info",
      patch: { email: "anna@example.com" },
      basisUpdatedAt: "2026-08-25T09:00:00+00:00",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0];
    expect(String(url)).toBe(
      "http://api/api/profile/personal_info?basis_updated_at=2026-08-25T09%3A00%3A00%2B00%3A00",
    );
    expect((init as RequestInit).method).toBe("PATCH");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ email: "anna@example.com" });
  });

  it("returns ok + mismatch:false when every patched key round-trips", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(200, {
        updated_at: "t2",
        profile: { personal_info: { name: "Anna Bauer", email: "anna@example.com" } },
      }),
    );
    const result = await saveProfileObjectSection({
      apiBase: "http://api",
      section: "personal_info",
      patch: { email: "anna@example.com" },
      basisUpdatedAt: "t1",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(result.status).toBe("ok");
    if (result.status === "ok") expect(result.mismatch).toBe(false);
  });

  // H0.4 — a cleared field legitimately comes back `null`, not absent;
  // undefined (key omitted from the response section) must count as equal.
  it("treats a returned undefined as equal to a sent null (cleared field)", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { personal_info: { name: "Anna Bauer" } } }),
    );
    const result = await saveProfileObjectSection({
      apiBase: "http://api",
      section: "personal_info",
      patch: { phone: null },
      basisUpdatedAt: "t1",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(result.status).toBe("ok");
    if (result.status === "ok") expect(result.mismatch).toBe(false);
  });

  it("flags mismatch:true when a supplied key does not round-trip", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { personal_info: { email: "old@example.com" } } }),
    );
    const result = await saveProfileObjectSection({
      apiBase: "http://api",
      section: "personal_info",
      patch: { email: "new@example.com" },
      basisUpdatedAt: "t1",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(result.status).toBe("ok");
    if (result.status === "ok") expect(result.mismatch).toBe(true);
  });

  it("classifies a 409 as stale and surfaces `current` for reload", async () => {
    const current = { updated_at: "t3", profile: { personal_info: {} } };
    const fetchImpl = vi.fn(async () => jsonResponse(409, { detail: { error: "stale_edit", current } }));
    const result = await saveProfileObjectSection({
      apiBase: "http://api",
      section: "personal_info",
      patch: { name: "Anna Bauer" },
      basisUpdatedAt: "t1",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(result.status).toBe("stale");
    if (result.status === "stale") expect(result.current).toEqual(current);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("classifies a 422 with the string detail message", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(422, { detail: "date_of_birth is invalid" }));
    const result = await saveProfileObjectSection({
      apiBase: "http://api",
      section: "personal_info",
      patch: { date_of_birth: "not-a-date" },
      basisUpdatedAt: "t1",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(result.status).toBe("invalid");
    if (result.status === "invalid") expect(result.message).toBe("date_of_birth is invalid");
  });

  it("classifies a network failure as a generic error", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new Error("network down");
    });
    const result = await saveProfileObjectSection({
      apiBase: "http://api",
      section: "personal_info",
      patch: { name: "Anna Bauer" },
      basisUpdatedAt: "t1",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(result.status).toBe("error");
  });
});
