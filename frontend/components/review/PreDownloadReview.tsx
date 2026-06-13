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

import { getCvProfileDiff } from "@/lib/api/review";
import { WhatChangedReview, type ReviewChange } from "./WhatChangedReview";

/**
 * US147 / ADR-040 — the pre-download grounding diff + attestation (JF-M-6.1).
 *
 * Fetches the deterministic generated-CV-vs-Master-Profile diff and prompts the user
 * to attest before download. The attestation is a NUDGE, not a gate (ADR-040 §4):
 * `onAttested` proceeds; the user is never blocked.
 */
export interface PreDownloadReviewProps {
  cvId: string;
  onAttested: () => void;
  onFix?: (change: ReviewChange) => void;
}

export function PreDownloadReview({ cvId, onAttested, onFix }: PreDownloadReviewProps) {
  const [changes, setChanges] = useState<ReviewChange[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    getCvProfileDiff(cvId)
      .then((diff) => {
        if (!cancelled) setChanges(diff.items);
      })
      .catch(() => {
        // A diff failure must never block the download (ADR-040 §4) — degrade to
        // an empty review so the attestation prompt still appears.
        if (!cancelled) setChanges([]);
      });
    return () => {
      cancelled = true;
    };
  }, [cvId]);

  if (changes === null) return null;

  return (
    <WhatChangedReview mode="download" changes={changes} onConfirm={onAttested} onFix={onFix} />
  );
}
