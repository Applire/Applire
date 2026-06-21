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

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { EnrichmentDrawer } from "../EnrichmentDrawer";
import { withIntl } from "@/lib/test-utils/with-intl";

vi.mock("@/lib/api/enrich", () => ({
  startEnrichSession: vi.fn(),
  respondToEnrich: vi.fn(),
  skipGap: vi.fn(),
  markGapNA: vi.fn(),
  // real guard logic — the drawer branches on this
  isEnrichNoGaps: (r: unknown) =>
    typeof r === "object" && r !== null && "noGaps" in r,
}));

import { startEnrichSession } from "@/lib/api/enrich";

const startMock = vi.mocked(startEnrichSession);

describe("EnrichmentDrawer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // US166 — the health hub offers Improve based on section-level gaps, but Mode C
  // keys off work-entry gaps. When none remain the backend 404s (-> noGaps); the
  // drawer must show a friendly "complete" message, never a raw error.
  it("shows a friendly complete state (not an error) when there are no gaps", async () => {
    startMock.mockResolvedValueOnce({ noGaps: true });
    render(withIntl(<EnrichmentDrawer open onClose={vi.fn()} />, "en"));

    await waitFor(() =>
      expect(
        screen.getByText(/Nothing to enrich here right now/i),
      ).toBeInTheDocument(),
    );
    // No raw error, and no chat input offered.
    expect(
      screen.queryByText(/resource was not found/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("renders the first question when gaps exist", async () => {
    startMock.mockResolvedValueOnce({
      session_id: "sess-1",
      first_question: "Tell us about a concrete win.",
      gaps: [{ id: "g1", label: "achievements", status: "active" }],
      estimated_questions: 3,
    });
    render(withIntl(<EnrichmentDrawer open onClose={vi.fn()} />, "en"));

    await waitFor(() =>
      expect(
        screen.getByText("Tell us about a concrete win."),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });

  it("shows the no-gaps message in German under the de locale", async () => {
    startMock.mockResolvedValueOnce({ noGaps: true });
    render(withIntl(<EnrichmentDrawer open onClose={vi.fn()} />, "de"));

    await waitFor(() =>
      expect(
        screen.getByText(/nichts zu ergänzen/i),
      ).toBeInTheDocument(),
    );
  });
});
