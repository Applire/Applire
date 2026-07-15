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

// TZ pinning: the exact deadline wall-clock assertions below ("…T09:30" for
// a 07:30Z instant) assume CEST — pin Europe/Berlin so they hold on UTC CI
// too. Same mechanism + rationale as lib/__tests__/deadline-datetime.test.ts
// (which also carries the sanity test proving the pin observes a non-zero
// offset). Restored in afterAll to avoid leaking into other worker files.
const ORIGINAL_TZ = process.env.TZ;
process.env.TZ = "Europe/Berlin";

import { describe, it, expect, vi, beforeEach, afterEach, afterAll } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { DossierTrackingSidebar, type TrackingSavedPatch } from "../DossierTrackingSidebar";
import { withIntl } from "@/lib/test-utils/with-intl";
import { patchApplication } from "@/lib/api/applications";
import type { ApplicationDetail } from "@/app/(shell)/applications/[appId]/page";

vi.mock("@/lib/api/applications", () => ({
  patchApplication: vi.fn(),
}));

const mockPatch = vi.mocked(patchApplication);

beforeEach(() => {
  mockPatch.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

afterAll(() => {
  if (ORIGINAL_TZ === undefined) {
    delete process.env.TZ;
  } else {
    process.env.TZ = ORIGINAL_TZ;
  }
});

function baseApplication(o: Partial<ApplicationDetail> = {}): ApplicationDetail {
  return {
    id: "app-1",
    job_analysis_id: "job-1",
    role_title: "Senior Software Engineer",
    company_name: "TechVision GmbH",
    workflow_status: "tracking",
    user_status: "tracking",
    notes: null,
    applied_at: null,
    deadline: null,
    source_url: null,
    submitted_cv_id: null,
    submitted_cv_created_at: null,
    submitted_cover_letter_id: null,
    stale_cv: null,
    flow_session_id: null,
    flow_current_step: null,
    created_at: "2026-07-01T09:00:00Z",
    updated_at: "2026-07-10T09:00:00Z",
    expires_at: null,
    ...o,
  };
}

function patchResponse(o: Partial<{
  notes: string | null;
  deadline: string | null;
  source_url: string | null;
  updated_at: string;
}> = {}) {
  return {
    id: "app-1",
    notes: o.notes ?? null,
    deadline: o.deadline ?? null,
    source_url: o.source_url ?? null,
    updated_at: o.updated_at ?? "2026-07-14T10:00:00Z",
    user_status: "tracking",
    applied_at: null,
  };
}

function renderSidebar(opts: {
  application: ApplicationDetail;
  onSaved?: (patch: TrackingSavedPatch) => void;
  onError?: (message: string) => void;
}) {
  const onSaved = opts.onSaved ?? vi.fn();
  const onError = opts.onError ?? vi.fn();
  render(
    withIntl(
      <DossierTrackingSidebar application={opts.application} onSaved={onSaved} onError={onError} />
    )
  );
  return { onSaved, onError };
}

describe("DossierTrackingSidebar (US234, closes #164)", () => {
  it("renders the zone root testid", () => {
    renderSidebar({ application: baseApplication() });
    expect(screen.getByTestId("dossier-tracking-sidebar")).toBeInTheDocument();
  });

  it("#164 regression guard: a UTC deadline renders as the correct local wall-clock value, not shifted", () => {
    // 07:30 UTC on this date is 09:30 CEST (UTC+2) — the exact #164 shear case.
    renderSidebar({ application: baseApplication({ deadline: "2026-08-15T07:30:00Z" }) });
    const input = screen.getByTestId("dossier-tracking-deadline") as HTMLInputElement;
    expect(input.value).toBe("2026-08-15T09:30");
  });

  it("blurring the deadline field with no change does not PATCH", () => {
    renderSidebar({ application: baseApplication({ deadline: "2026-08-15T07:30:00Z" }) });
    const input = screen.getByTestId("dossier-tracking-deadline");
    fireEvent.focus(input);
    fireEvent.blur(input);
    expect(mockPatch).not.toHaveBeenCalled();
  });

  it("editing the deadline and blurring PATCHes ONLY the deadline field, and reports only that key", async () => {
    mockPatch.mockResolvedValue(patchResponse({ deadline: "2026-08-15T07:30:00.000Z" }));
    const { onSaved } = renderSidebar({ application: baseApplication() });
    const input = screen.getByTestId("dossier-tracking-deadline");
    fireEvent.change(input, { target: { value: "2026-08-15T09:30" } });
    fireEvent.blur(input);

    await waitFor(() => expect(mockPatch).toHaveBeenCalledTimes(1));
    expect(mockPatch).toHaveBeenCalledWith("app-1", { deadline: "2026-08-15T07:30:00.000Z" });
    // onSaved carries ONLY the saved key + updated_at — never other fields
    // from the PATCH response (out-of-order response protection, page-side).
    await waitFor(() =>
      expect(onSaved).toHaveBeenCalledWith({
        deadline: "2026-08-15T07:30:00.000Z",
        updated_at: "2026-07-14T10:00:00Z",
      })
    );
  });

  it("clearing the deadline field sends an explicit null", async () => {
    mockPatch.mockResolvedValue(patchResponse({ deadline: null }));
    renderSidebar({ application: baseApplication({ deadline: "2026-08-15T07:30:00Z" }) });
    const input = screen.getByTestId("dossier-tracking-deadline");
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);

    await waitFor(() => expect(mockPatch).toHaveBeenCalledWith("app-1", { deadline: null }));
  });

  it("a failed deadline save reverts the input and shows an inline error", async () => {
    mockPatch.mockRejectedValue(new Error("application patch 500"));
    renderSidebar({ application: baseApplication({ deadline: "2026-08-15T07:30:00Z" }) });
    const input = screen.getByTestId("dossier-tracking-deadline") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "2026-08-20T10:00" } });
    fireEvent.blur(input);

    await waitFor(() => expect(mockPatch).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(input.value).toBe("2026-08-15T09:30")); // reverted
    expect(screen.getByTestId("dossier-tracking-deadline-error")).toBeInTheDocument();
  });

  it("a failed save does NOT clobber a newer in-flight edit (edit-generation guard)", async () => {
    let rejectPatch!: (e: Error) => void;
    mockPatch.mockImplementation(
      () => new Promise((_, reject) => { rejectPatch = reject; }) as never
    );
    renderSidebar({ application: baseApplication({ deadline: "2026-08-15T07:30:00Z" }) });
    const input = screen.getByTestId("dossier-tracking-deadline") as HTMLInputElement;

    // Edit + blur fires the save; the request stays pending.
    fireEvent.change(input, { target: { value: "2026-08-20T10:00" } });
    fireEvent.blur(input);
    await waitFor(() => expect(mockPatch).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("dossier-tracking-deadline-saving")).toBeInTheDocument();

    // The user keeps editing while the request is in flight — the "saving"
    // indicator must stay visible (not reset to idle by the keystroke) …
    fireEvent.change(input, { target: { value: "2026-08-21T11:00" } });
    expect(screen.getByTestId("dossier-tracking-deadline-saving")).toBeInTheDocument();

    // … and when the stale request then fails, the NEWER text survives
    // (no revert to the pre-save baseline) while the inline error shows.
    rejectPatch(new Error("application patch 500"));
    await waitFor(() =>
      expect(screen.getByTestId("dossier-tracking-deadline-error")).toBeInTheDocument()
    );
    expect(input.value).toBe("2026-08-21T11:00");
  });

  it("editing and blurring the source link PATCHes only source_url, trimmed, and reports only that key", async () => {
    mockPatch.mockResolvedValue(patchResponse({ source_url: "https://example.com/job" }));
    const { onSaved } = renderSidebar({ application: baseApplication() });
    const input = screen.getByTestId("dossier-tracking-source");
    fireEvent.change(input, { target: { value: "  https://example.com/job  " } });
    fireEvent.blur(input);

    await waitFor(() =>
      expect(mockPatch).toHaveBeenCalledWith("app-1", { source_url: "https://example.com/job" })
    );
    await waitFor(() =>
      expect(onSaved).toHaveBeenCalledWith({
        source_url: "https://example.com/job",
        updated_at: "2026-07-14T10:00:00Z",
      })
    );
  });

  it("notes autosave is debounced ~800ms and PATCHes only notes", async () => {
    vi.useFakeTimers();
    mockPatch.mockResolvedValue(patchResponse({ notes: "Great culture fit" }));
    renderSidebar({ application: baseApplication() });
    const textarea = screen.getByTestId("dossier-tracking-notes");
    fireEvent.change(textarea, { target: { value: "Great culture fit" } });

    // Not yet — debounce hasn't elapsed.
    expect(mockPatch).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(800);

    expect(mockPatch).toHaveBeenCalledWith("app-1", { notes: "Great culture fit" });
    vi.useRealTimers();
  });

  it("shows a saved indicator after a successful save", async () => {
    mockPatch.mockResolvedValue(patchResponse({ deadline: "2026-08-15T07:30:00.000Z" }));
    renderSidebar({ application: baseApplication() });
    const input = screen.getByTestId("dossier-tracking-deadline");
    fireEvent.change(input, { target: { value: "2026-08-15T09:30" } });
    fireEvent.blur(input);

    await waitFor(() =>
      expect(screen.getByTestId("dossier-tracking-deadline-status")).toHaveTextContent("Saved")
    );
  });

  it("renders the created/applied/updated footer as one muted line", () => {
    renderSidebar({
      application: baseApplication({
        applied_at: "2026-07-05T09:00:00Z",
        created_at: "2026-07-01T09:00:00Z",
        updated_at: "2026-07-10T09:00:00Z",
      }),
    });
    const footer = screen.getByTestId("dossier-tracking-footer");
    expect(footer).toBeInTheDocument();
    expect(footer.textContent).toMatch(/created/i);
    expect(footer.textContent).toMatch(/applied/i);
    expect(footer.textContent).toMatch(/updated/i);
  });

  it("omits the applied segment when applied_at is absent", () => {
    renderSidebar({ application: baseApplication({ applied_at: null }) });
    const footer = screen.getByTestId("dossier-tracking-footer");
    expect(footer.textContent).not.toMatch(/applied/i);
  });
});
