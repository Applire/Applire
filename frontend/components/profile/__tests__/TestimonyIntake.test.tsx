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

import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TestimonyIntake } from "../TestimonyIntake";

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}:${JSON.stringify(params)}` : key,
}));

function res(body: unknown, ok = true): Response {
  return { ok, status: ok ? 200 : 500, json: async () => body } as Response;
}

afterEach(() => vi.restoreAllMocks());

describe("TestimonyIntake", () => {
  it("submits the pasted text and shows the applied-change count", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      res({
        submission_id: "s1",
        status: "applied",
        changes: [
          { section: "skills", field: "name", action: "added" },
          { section: "work_experience", field: "role", action: "updated" },
        ],
        confirmations: [],
        conflicts: [],
      }),
    );
    const onSubmitted = vi.fn();

    render(<TestimonyIntake onSubmitted={onSubmitted} />);
    fireEvent.change(screen.getByTestId("testimony-textarea"), {
      target: { value: "I ran Kafka in production for three years." },
    });
    fireEvent.click(screen.getByText("testimony.submit"));

    await waitFor(() => expect(screen.getByTestId("testimony-result")).toBeInTheDocument());

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/profile/testimony"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ text: "I ran Kafka in production for three years." }),
      }),
    );
    expect(screen.getByTestId("testimony-result")).toHaveAttribute("data-status", "applied");
    expect(screen.getByTestId("testimony-result").textContent).toContain(
      "testimony.statusApplied",
    );
    expect(screen.getByTestId("testimony-result").textContent).toContain('"count":2');
    expect(onSubmitted).toHaveBeenCalledTimes(1);
    // the textarea clears after a successful submission
    expect((screen.getByTestId("testimony-textarea") as HTMLTextAreaElement).value).toBe("");
  });

  it("reports a partial application distinctly, never as an unqualified applied (#370)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      res({
        submission_id: "s3",
        status: "partial",
        changes: [{ section: "work_experience", field: "team_size", action: "updated" }],
        confirmations: [],
        conflicts: [],
        not_applied: [
          { span: "1350000", kind: "figure", reason: "figure_not_in_any_op" },
        ],
      }),
    );

    render(<TestimonyIntake />);
    fireEvent.change(screen.getByTestId("testimony-textarea"), {
      target: { value: "Team of 12, budget 1350000 EUR." },
    });
    fireEvent.click(screen.getByText("testimony.submit"));

    await waitFor(() =>
      expect(screen.getByTestId("testimony-result")).toHaveAttribute("data-status", "partial"),
    );
    expect(screen.getByTestId("testimony-result").textContent).toContain(
      "testimony.statusPartial",
    );
    expect(screen.getByTestId("testimony-result").textContent).toContain('"count":1');
    // The witness's spans are not persisted anywhere, so the result itself
    // must show them — one list item per not_applied entry, span included.
    const list = screen.getByTestId("testimony-not-applied");
    expect(list.querySelectorAll("li")).toHaveLength(1);
    expect(list.textContent).toContain("testimony.notAppliedFigure");
    expect(list.textContent).toContain("1350000");
  });

  it("reports a denial as denial_recorded, not a silent no-op", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      res({
        submission_id: "s2",
        status: "denial_recorded",
        changes: [{ section: "denied_concepts", field: "concept", action: "added" }],
        confirmations: [],
        conflicts: [],
      }),
    );

    render(<TestimonyIntake />);
    fireEvent.change(screen.getByTestId("testimony-textarea"), {
      target: { value: "I have no blockchain experience — an honest gap." },
    });
    fireEvent.click(screen.getByText("testimony.submit"));

    await waitFor(() =>
      expect(screen.getByTestId("testimony-result")).toHaveAttribute(
        "data-status",
        "denial_recorded",
      ),
    );
    expect(screen.getByTestId("testimony-result").textContent).toBe(
      "testimony.statusDenialRecorded",
    );
  });

  it("blocks submission of empty text with a validation message, no fetch call", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");

    render(<TestimonyIntake />);
    fireEvent.click(screen.getByText("testimony.submit"));

    expect(await screen.findByTestId("testimony-error")).toHaveTextContent(
      "testimony.emptyError",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows a failure message when the request errors, and keeps the text for retry", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(res({ detail: "boom" }, false));

    render(<TestimonyIntake />);
    fireEvent.change(screen.getByTestId("testimony-textarea"), {
      target: { value: "Some testimony." },
    });
    fireEvent.click(screen.getByText("testimony.submit"));

    expect(await screen.findByTestId("testimony-error")).toHaveTextContent(
      "testimony.submitFailed",
    );
    expect((screen.getByTestId("testimony-textarea") as HTMLTextAreaElement).value).toBe(
      "Some testimony.",
    );
  });
});
