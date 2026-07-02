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

/**
 * ImportInProgressBanner (PQ F1 AC3 — truthful dashboard).
 *
 * With all import jobs queued server-side up-front, a refresh mid-import lands the
 * user on the dashboard while the backend is still merging CVs. The dashboard must
 * say so — never "all good" over a half-imported profile — and the indicator must
 * disappear on its own once the imports finish.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ImportInProgressBanner } from "../ImportInProgressBanner";

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

function res(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response;
}

const ACTIVE_JOB = {
  import_id: "imp-1",
  status: "processing",
  filename: "cv.pdf",
  created_at: "2026-07-02T10:00:00Z",
};

afterEach(() => vi.restoreAllMocks());

describe("ImportInProgressBanner", () => {
  it("shows the banner while imports are active and hides it when they finish", async () => {
    let call = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
      call++;
      return res(call < 3 ? [ACTIVE_JOB] : []);
    });

    render(<ImportInProgressBanner pollMs={20} />);

    await waitFor(() =>
      expect(screen.getByTestId("import-in-progress-banner")).toBeInTheDocument(),
    );
    expect(screen.getByText("importInProgress")).toBeInTheDocument();

    // Keeps polling; once the queue drains the banner disappears on its own.
    await waitFor(
      () => expect(screen.queryByTestId("import-in-progress-banner")).toBeNull(),
      { timeout: 5000 },
    );
  });

  it("renders nothing when there are no active imports (and does not keep polling)", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(res([]));

    render(<ImportInProgressBanner pollMs={10} />);

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("import-in-progress-banner")).toBeNull();
    // Give a couple of poll intervals a chance — no further polling once idle.
    await new Promise((r) => setTimeout(r, 50));
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("queries the user-scoped active listing", async () => {
    let url = "";
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      url = typeof input === "string" ? input : input.toString();
      return res([]);
    });

    render(<ImportInProgressBanner pollMs={10} />);

    await waitFor(() => expect(url).toContain("/api/profile/import-jobs?active=true"));
  });

  it("fires onAllDone exactly once when the active imports drain", async () => {
    let call = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
      call++;
      return res(call < 2 ? [ACTIVE_JOB] : []);
    });
    const onAllDone = vi.fn();

    render(<ImportInProgressBanner pollMs={10} onAllDone={onAllDone} />);

    await waitFor(() => expect(onAllDone).toHaveBeenCalledTimes(1), { timeout: 5000 });
    // Stays at one — no repeat fires after the queue is empty.
    await new Promise((r) => setTimeout(r, 50));
    expect(onAllDone).toHaveBeenCalledTimes(1);
  });
});
