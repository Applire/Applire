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

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MergeGateDialog } from "../MergeGateDialog";

vi.mock("next-intl", () => ({
  // Echo the key, appending interpolation values so assertions can see them.
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key} ${Object.values(params).join(" ")}` : key,
}));

function makeFetchMock(response: object, ok = true, status = 200) {
  return vi.fn().mockResolvedValue({
    ok,
    status,
    json: () => Promise.resolve(response),
    statusText: ok ? "OK" : "Error",
  });
}

describe("MergeGateDialog", () => {
  beforeEach(() => {
    global.fetch = makeFetchMock({ action: "merge", profile_id: "p1" });
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders the name-divergence title and both account and CV names", () => {
    render(
      <MergeGateDialog
        gate="name_divergence"
        stagedId="s1"
        accountName="Max Muster"
        cvName="Markus Brandt"
        onResolved={vi.fn()}
        onCancel={vi.fn()}
      />
    );
    expect(screen.getByText("titleDivergence")).toBeInTheDocument();
    expect(screen.getByText(/Max Muster/)).toBeInTheDocument();
    expect(screen.getByText(/Markus Brandt/)).toBeInTheDocument();
  });

  it("renders the not-a-cv title", () => {
    render(
      <MergeGateDialog
        gate="not_a_cv"
        stagedId="s1"
        cvName={null}
        onResolved={vi.fn()}
        onCancel={vi.fn()}
      />
    );
    expect(screen.getByText("titleNotCv")).toBeInTheDocument();
  });

  it("POSTs action=merge to the resolve endpoint and calls onResolved", async () => {
    const onResolved = vi.fn();
    global.fetch = makeFetchMock({ action: "merge", profile_id: "p1", completeness_score: 0.9 });
    render(
      <MergeGateDialog
        gate="name_divergence"
        stagedId="staged-42"
        accountName="Max Muster"
        cvName="Markus Brandt"
        onResolved={onResolved}
        onCancel={vi.fn()}
      />
    );

    fireEvent.click(screen.getByTestId("gate-merge-btn"));

    await waitFor(() => expect(onResolved).toHaveBeenCalledWith("merge", expect.anything()));
    const call = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[0]).toContain("/api/profile/staged/staged-42/resolve");
    expect(JSON.parse((call[1] as RequestInit).body as string)).toEqual({ action: "merge" });
  });

  it("POSTs action=discard and calls onResolved", async () => {
    const onResolved = vi.fn();
    global.fetch = makeFetchMock({ action: "discard" });
    render(
      <MergeGateDialog
        gate="not_a_cv"
        stagedId="staged-99"
        cvName={null}
        onResolved={onResolved}
        onCancel={vi.fn()}
      />
    );

    fireEvent.click(screen.getByTestId("gate-discard-btn"));

    await waitFor(() => expect(onResolved).toHaveBeenCalledWith("discard", expect.anything()));
    const call = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(JSON.parse((call[1] as RequestInit).body as string)).toEqual({ action: "discard" });
  });

  it("shows an error and does not call onResolved when resolve fails", async () => {
    const onResolved = vi.fn();
    global.fetch = makeFetchMock({ detail: "boom" }, false, 409);
    render(
      <MergeGateDialog
        gate="name_divergence"
        stagedId="s1"
        accountName="Max Muster"
        cvName="Markus Brandt"
        onResolved={onResolved}
        onCancel={vi.fn()}
      />
    );

    fireEvent.click(screen.getByTestId("gate-merge-btn"));

    await waitFor(() => expect(screen.getByTestId("gate-error")).toBeInTheDocument());
    expect(onResolved).not.toHaveBeenCalled();
  });

  it("calls onCancel when the cancel button is clicked", () => {
    const onCancel = vi.fn();
    render(
      <MergeGateDialog
        gate="name_divergence"
        stagedId="s1"
        accountName="Max Muster"
        cvName="Markus Brandt"
        onResolved={vi.fn()}
        onCancel={onCancel}
      />
    );
    fireEvent.click(screen.getByTestId("gate-cancel-btn"));
    expect(onCancel).toHaveBeenCalled();
  });
});
