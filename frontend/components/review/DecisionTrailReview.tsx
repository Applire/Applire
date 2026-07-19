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

"use client";

import { useEffect, useState } from "react";

import { getProfileChanges } from "@/lib/api/review";
import { WhatChangedReview, type ReviewChange } from "./WhatChangedReview";

/**
 * US148 / ADR-040 — the post-merge (resolved-assumptions, JF-M-3.2/3.4) and
 * post-interview (change-summary, JF-M-5.2) surfaces. Reads the durable decision
 * trail (`GET /api/profile/changes`) and filters it by mode:
 *   - merge: every non-interview change + pending conflicts (the auto-merge assumptions)
 *   - interview: the changes captured from the user's interview answers
 */
export interface DecisionTrailReviewProps {
  /** merge = resolved-assumptions (3.2/3.4); interview = answer summary (5.2);
   *  extraction = "here's what we read" confirm at upload (3.1). */
  mode: "merge" | "interview" | "extraction";
  onConfirm?: () => void;
  onDismiss?: () => void;
  onFix?: (change: ReviewChange) => void;
}

export function DecisionTrailReview({ mode, onConfirm, onDismiss, onFix }: DecisionTrailReviewProps) {
  const [changes, setChanges] = useState<ReviewChange[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    getProfileChanges()
      .then((trail) => {
        if (cancelled) return;
        const records = trail.enrichmentHistory ?? [];
        // agent_interview = vault changes submitted by an external agent via the
        // submit_claims MCP tool (US256) — testimony, so it belongs to the
        // interview surface, not the import-merge one.
        const fromInterview = (src: string) => src === "interview" || src === "agent_interview";
        const selected = records
          .filter((r) => {
            if (mode === "interview") return fromInterview(r.source);
            if (mode === "extraction") return r.source === "cv_upload" || r.source === "cv_paste";
            return !fromInterview(r.source); // merge
          })
          .flatMap((r) => r.changes)
          // extraction confirm only shows what was *read in* (added), not later assumptions
          .filter((c) => (mode === "extraction" ? c.action === "added" : true));
        // The pending conflicts are auto-merge assumptions — only relevant to the merge surface.
        const conflicts = mode === "merge" ? trail.pendingConflicts ?? [] : [];
        setChanges([...selected, ...conflicts]);
      })
      .catch(() => {
        if (!cancelled) setChanges([]);
      });
    return () => {
      cancelled = true;
    };
  }, [mode]);

  if (changes === null) return null;

  return (
    <WhatChangedReview mode={mode} changes={changes} onConfirm={onConfirm} onDismiss={onDismiss} onFix={onFix} />
  );
}
