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

import { useCallback, useEffect, useState } from "react";

import { dismissExplainer, getSettings } from "@/lib/api/settings";

/**
 * #679 — the once-per-user explainer mechanism.
 *
 * `shouldShow` starts TRUE and only ever becomes false when a SUCCESSFUL
 * settings read names this id (D-3, fail-open to showing): a slow or failing
 * `GET /api/settings` must never swallow a first-contact explanation, and it
 * must never make the user wait — the caller opens the explainer synchronously
 * on the click, whatever the read is doing.
 *
 * The reverse failure is cheap by comparison: a user who dismissed it and hits
 * a failing settings endpoint sees the notice once more.
 */
export function useExplainer(explainerId: string): {
  shouldShow: boolean;
  dismiss: () => void;
} {
  const [shouldShow, setShouldShow] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getSettings()
      .then((settings) => {
        if (cancelled) return;
        const dismissed = settings.dismissed_explainers;
        // An absent field is a backend that predates the column — that is
        // "nothing dismissed", not "everything dismissed".
        if (Array.isArray(dismissed) && dismissed.includes(explainerId)) {
          setShouldShow(false);
        }
      })
      .catch(() => {
        // Fail-open: leave shouldShow at its true default.
      });
    return () => {
      cancelled = true;
    };
  }, [explainerId]);

  const dismiss = useCallback(() => {
    setShouldShow(false);
    // Fire-and-forget: `dismissExplainer` already retries once and swallows
    // its own failures (D-3) — the user is on their way to the picker.
    void dismissExplainer(explainerId);
  }, [explainerId]);

  return { shouldShow, dismiss };
}
