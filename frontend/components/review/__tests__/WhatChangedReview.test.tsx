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

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { WhatChangedReview, type ReviewChange } from "../WhatChangedReview";

// Key-aware mock: t() echoes the key; t.has() is false for keys containing
// "unknown" so the missing-key fallback branch is exercisable.
vi.mock("next-intl", () => ({
  useTranslations: () =>
    Object.assign((key: string) => key, { has: (key: string) => !key.includes("unknown") }),
}));

const CHANGES: ReviewChange[] = [
  {
    section: "work_experience",
    field: "work_experience",
    action: "merged",
    newValue: "Senior Dev @ Acme",
    rationale: "Treated as the same position at Acme.",
  },
  {
    section: "skills",
    field: "skills",
    action: "added",
    newValue: "Kubernetes",
    rationale: "New skill from this source.",
  },
];

describe("WhatChangedReview", () => {
  it("localizes rationale via rationaleKey when present (#2/ADR-038)", () => {
    render(
      <WhatChangedReview
        mode="merge"
        changes={[{ section: "skills", field: "skills", action: "added", newValue: "Rust", rationaleKey: "new_skill", rationale: "English fallback prose." }]}
      />,
    );
    // mocked t() returns the key path → proves the localized lookup is used, not the literal
    expect(screen.getByText("rationale.new_skill")).toBeInTheDocument();
    expect(screen.queryByText("English fallback prose.")).toBeNull();
  });

  it("falls back to the stored rationale when no rationaleKey (legacy records)", () => {
    render(
      <WhatChangedReview
        mode="merge"
        changes={[{ section: "skills", field: "skills", action: "added", newValue: "Rust", rationale: "legacy prose" }]}
      />,
    );
    expect(screen.getByText("legacy prose")).toBeInTheDocument();
  });

  // Blind-PQ regression: the backend passes `section` and `rationale_key` from
  // arbitrary profile keys/reconcile ops; an unmapped one must NOT leak a raw
  // key (e.g. "section.projects" / "rationale.reconcile_merged") into the UI.
  it("falls back to the generic section label and the prose rationale for unmapped keys", () => {
    render(
      <WhatChangedReview
        mode="merge"
        changes={[
          {
            section: "unknown_section",
            field: "x",
            action: "added",
            newValue: "X",
            rationaleKey: "unknown_key",
            rationale: "human prose fallback",
          },
        ]}
      />,
    );
    // section → "*" fallback, never the raw "section.unknown_section"
    expect(screen.getByText("section.*")).toBeInTheDocument();
    expect(screen.queryByText("section.unknown_section")).toBeNull();
    // rationaleKey missing → prose, never the raw "rationale.unknown_key"
    expect(screen.getByText("human prose fallback")).toBeInTheDocument();
    expect(screen.queryByText("rationale.unknown_key")).toBeNull();
  });

  it("renders the per-mode title", () => {
    render(<WhatChangedReview mode="extraction" changes={CHANGES} />);
    expect(screen.getByTestId("what-changed-title")).toHaveTextContent("titleExtraction");
  });

  it("renders one row per change with its rationale", () => {
    render(<WhatChangedReview mode="merge" changes={CHANGES} />);
    const rows = screen.getAllByTestId("what-changed-row");
    expect(rows).toHaveLength(2);
    expect(screen.getByText("Treated as the same position at Acme.")).toBeInTheDocument();
    expect(screen.getByText("New skill from this source.")).toBeInTheDocument();
  });

  it("calls onConfirm when the confirm button is clicked", () => {
    const onConfirm = vi.fn();
    render(<WhatChangedReview mode="extraction" changes={CHANGES} onConfirm={onConfirm} />);
    fireEvent.click(screen.getByTestId("what-changed-confirm"));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("renders a skip button only when onDismiss is provided", () => {
    const { rerender } = render(<WhatChangedReview mode="extraction" changes={CHANGES} />);
    expect(screen.queryByTestId("what-changed-skip")).toBeNull();
    rerender(<WhatChangedReview mode="extraction" changes={CHANGES} onDismiss={vi.fn()} />);
    expect(screen.getByTestId("what-changed-skip")).toBeInTheDocument();
  });

  it("calls onFix with the specific change (Branch G)", () => {
    const onFix = vi.fn();
    render(<WhatChangedReview mode="merge" changes={CHANGES} onFix={onFix} />);
    const fixButtons = screen.getAllByTestId("what-changed-fix");
    fireEvent.click(fixButtons[0]);
    expect(onFix).toHaveBeenCalledWith(CHANGES[0]);
  });

  it("uses the attestation confirm label in download mode", () => {
    render(<WhatChangedReview mode="download" changes={CHANGES} onConfirm={vi.fn()} />);
    expect(screen.getByTestId("what-changed-confirm")).toHaveTextContent("confirmDownload");
  });

  it("states the vault-join fact guarantee in download mode (ADR-067 clause 9)", () => {
    // SF-WRITE.17 / JF-M-6.1: employer/date detection was retired because the
    // fields are vault-joined and cannot diverge — the surface must STATE the
    // guarantee so its disappearance never reads as a missing check.
    render(<WhatChangedReview mode="download" changes={[]} onConfirm={vi.fn()} />);
    expect(screen.getByTestId("what-changed-fact-guarantee")).toBeInTheDocument();
  });

  it("does not show the fact guarantee outside download mode", () => {
    render(<WhatChangedReview mode="merge" changes={CHANGES} />);
    expect(screen.queryByTestId("what-changed-fact-guarantee")).toBeNull();
  });

  it("shows an empty state when there are no changes", () => {
    render(<WhatChangedReview mode="merge" changes={[]} />);
    expect(screen.getByTestId("what-changed-empty")).toBeInTheDocument();
  });

  it("renders before→after when oldValue is present (merge rows)", () => {
    render(
      <WhatChangedReview
        mode="merge"
        changes={[
          {
            section: "work_experience",
            field: "role",
            action: "merged",
            oldValue: "Team Lead @ AcmeCo",
            newValue: "Engineering Team Lead @ AcmeCo",
            rationaleKey: "merged_same_position",
          },
        ]}
      />,
    );
    const row = screen.getByTestId("what-changed-row");
    expect(row).toHaveTextContent("Team Lead @ AcmeCo");
    expect(row).toHaveTextContent("Engineering Team Lead @ AcmeCo");
    expect(screen.getByTestId("what-changed-oldvalue")).toBeInTheDocument();
  });

  // issue #241 item 2 — a signature_stories change carries the FULL story blob
  // in newValue (ADR-055 / apply.py, Oracle receipts by blob containment). The
  // generic displayValue() dump ("id: …, title: …, experience_refs: …") reads
  // as a raw key-value dump, not a story; this section must render it as a
  // story card instead, with id/experience_refs hidden.
  describe("signature story rows (#241 item 2)", () => {
    const STORY = {
      id: "story-123",
      title: "Cut deploy time from 45 to 8 minutes",
      challenge: "Deploys took 45 minutes and blocked releases.",
      mechanism: "Parallelized the CI pipeline and cached the Docker layers.",
      outcome: "Deploy time dropped to 8 minutes; releases went daily.",
      benchmark: "82% faster",
      experience_refs: ["exp-1", "exp-2"],
      source: "interview",
    };

    it("renders title + challenge/mechanism/outcome instead of a raw key-value dump", () => {
      render(
        <WhatChangedReview
          mode="interview"
          changes={[
            { section: "signature_stories", field: STORY.title, action: "added", newValue: STORY },
          ]}
        />,
      );
      const row = screen.getByTestId("what-changed-story");
      expect(row).toHaveTextContent(STORY.title);
      expect(row).toHaveTextContent(STORY.challenge);
      expect(row).toHaveTextContent(STORY.mechanism);
      expect(row).toHaveTextContent(STORY.outcome);
      expect(row).toHaveTextContent(STORY.benchmark);
    });

    it("hides id and experience_refs — no raw key-value dump leaks through", () => {
      render(
        <WhatChangedReview
          mode="interview"
          changes={[
            { section: "signature_stories", field: STORY.title, action: "added", newValue: STORY },
          ]}
        />,
      );
      const row = screen.getByTestId("what-changed-row");
      expect(row).not.toHaveTextContent("story-123");
      expect(row).not.toHaveTextContent("exp-1");
      expect(row).not.toHaveTextContent("id:");
      expect(row).not.toHaveTextContent("experience_refs:");
    });

    it("uses the signature-story section label, not the generic fallback", () => {
      render(
        <WhatChangedReview
          mode="interview"
          changes={[
            { section: "signature_stories", field: STORY.title, action: "added", newValue: STORY },
          ]}
        />,
      );
      expect(screen.getByText("section.signature_stories")).toBeInTheDocument();
    });

    it("falls back to the generic row for a non-story-shaped signature_stories change", () => {
      // Defensive: an unexpected shape (e.g. a bare string) must not crash or
      // silently drop the value — it degrades to the existing generic render.
      render(
        <WhatChangedReview
          mode="interview"
          changes={[
            { section: "signature_stories", field: "x", action: "added", newValue: "not a story object" },
          ]}
        />,
      );
      expect(screen.queryByTestId("what-changed-story")).toBeNull();
      expect(screen.getByText("not a story object")).toBeInTheDocument();
    });
  });
});
