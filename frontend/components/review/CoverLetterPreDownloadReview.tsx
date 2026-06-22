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

import { WhatChangedReview } from "./WhatChangedReview";

/**
 * US170 / ADR-040 §3 — the cover-letter pre-download attestation (JF-M-8.1).
 *
 * The CV twin (`PreDownloadReview`) fetches a deterministic generated-CV-vs-profile
 * diff. A cover letter is free prose, so there is no field-level diff to compute —
 * the prevention tier is the server-side grounding reviewer (US170). This surface is
 * the ADR-040 §3/§4 *detection* nudge: an active, dismissible attestation, never a
 * gate (`onAttested` proceeds; `onDismiss` skips — the user is never blocked).
 */
export interface CoverLetterPreDownloadReviewProps {
  onAttested: () => void;
  onDismiss?: () => void;
}

export function CoverLetterPreDownloadReview({
  onAttested,
  onDismiss,
}: CoverLetterPreDownloadReviewProps) {
  return (
    <WhatChangedReview
      mode="download"
      changes={[]}
      onConfirm={onAttested}
      onDismiss={onDismiss}
    />
  );
}
