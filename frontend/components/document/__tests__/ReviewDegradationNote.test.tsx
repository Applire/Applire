// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { degradationLines, degradedCheckLabel } from "@/lib/review-degradation";
import { ReviewDegradationNote } from "../ReviewDegradationNote";

describe("degradationLines (#786, T-2)", () => {
  it("maps chain ids onto the user's word for the check", () => {
    expect(degradedCheckLabel("cv_language")).toBe("language");
    expect(degradedCheckLabel("cv_language_recheck")).toBe("language");
    expect(degradedCheckLabel("cv_tailoring")).toBe("draft");
    expect(degradedCheckLabel("cover_letter")).toBe("draft");
    expect(degradedCheckLabel("cv_terminal_review")).toBe("final");
    expect(degradedCheckLabel("letter_terminal_review")).toBe("final");
    expect(degradedCheckLabel("something_new")).toBe("other");
  });

  it("folds the language pass and its recheck into one line per stage", () => {
    expect(
      degradationLines([
        { check: "cv_language", stage: "review", reason: "timeout" },
        { check: "cv_language_recheck", stage: "review", reason: "error" },
        { check: "cv_language", stage: "correction", reason: "timeout" },
      ]),
    ).toEqual([
      { label: "language", stage: "review", reason: "timeout" },
      { label: "language", stage: "correction", reason: "timeout" },
    ]);
  });
});

describe("ReviewDegradationNote (#786, T-2)", () => {
  it("renders nothing when not recorded or when every step answered", () => {
    const { container, rerender } = render(withIntl(<ReviewDegradationNote steps={null} />));
    expect(container.textContent).toBe("");
    rerender(withIntl(<ReviewDegradationNote steps={[]} />));
    expect(screen.queryByTestId("review-degradation")).toBeNull();
  });

  it("names the check and what happened in the ruled DE wording, and offers a new version", () => {
    const onRegenerate = vi.fn();
    render(
      withIntl(
        <ReviewDegradationNote
          steps={[
            { check: "cv_language", stage: "review", reason: "timeout" },
            { check: "cv_tailoring", stage: "correction", reason: "malformed" },
          ]}
          onRegenerate={onRegenerate}
        />,
        "de",
      ),
    );
    const note = screen.getByTestId("review-degradation");
    expect(note).toHaveTextContent("Beim Schreiben sind 2 Prüfungen ausgefallen");
    expect(screen.getByTestId("review-degradation-language-review")).toHaveTextContent(
      "Sprachprüfung: lief nicht — der KI-Anbieter hat nicht rechtzeitig geantwortet.",
    );
    expect(screen.getByTestId("review-degradation-draft-correction")).toHaveTextContent(
      "Entwurfsprüfung: hat etwas gefunden, die Korrektur lief aber nicht.",
    );
    expect(note).toHaveTextContent(
      "Das Dokument ist trotzdem fertig. Erstelle eine neue Fassung oder lies diesen Teil selbst gegen, bevor Du sendest.",
    );
    fireEvent.click(screen.getByTestId("review-degradation-regenerate"));
    expect(onRegenerate).toHaveBeenCalledOnce();
  });

  it("offers no button without a regenerate path, and never calls the document wrong (EN)", () => {
    render(withIntl(<ReviewDegradationNote steps={[{ check: "letter_terminal_review", stage: "review", reason: "error" }]} />));
    expect(screen.getByTestId("review-degradation")).toHaveTextContent(
      "Final check: did not run — the AI provider could not be reached.",
    );
    expect(screen.queryByTestId("review-degradation-regenerate")).toBeNull();
    expect(screen.getByTestId("review-degradation").textContent).not.toMatch(/wrong|error in your/i);
  });
});
