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

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Info } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

/**
 * #679 — a generic first-use explainer: the short "here is what this control
 * actually does" card a user meets once, before the control opens.
 *
 * Deliberately the SAME anatomy as `PreDownloadNotice` (ADR-040): one card,
 * an icon, prose, an optional "don't show this again", a ghost cancel and a
 * primary continue. It is a nudge in front of an optional feature, never a
 * gate — cancel returns the user exactly where they were.
 *
 * The copy is the caller's: this component owns the shape, not the text, so
 * the next explainer (#679 keeps a backlog of candidates) needs no new
 * component. Suppression state lives in `useExplainer(id)`.
 */
export interface FirstUseExplainerProps {
  /** The persisted id, e.g. `fact_pins_intro` — also the test id suffix. */
  explainerId: string;
  title: string;
  paragraphs: string[];
  continueLabel: string;
  /** Offer the "don't show this again" checkbox. */
  canSuppress: boolean;
  onContinue: (dontShowAgain: boolean) => void;
  onCancel: () => void;
}

export function FirstUseExplainer({
  explainerId,
  title,
  paragraphs,
  continueLabel,
  canSuppress,
  onContinue,
  onCancel,
}: FirstUseExplainerProps) {
  const t = useTranslations("explainers");
  const [dontShowAgain, setDontShowAgain] = useState(false);
  const continueRef = useRef<HTMLButtonElement>(null);

  // Focus the primary action: the explainer is a one-key detour — Enter
  // continues, Escape backs out.
  useEffect(() => {
    continueRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onCancel();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onCancel]);

  // Portal to <body> (US289 transform-trap discipline): the explainer opens
  // from controls that sit inside transformed columns, which would otherwise
  // trap a `fixed` overlay to that column's box.
  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      data-testid={`explainer-${explainerId}`}
      className="fixed inset-0 z-[70] flex items-end justify-center bg-black/40 p-0 md:items-center md:p-4"
    >
      <section className="max-h-[85vh] w-full overflow-y-auto rounded-t-2xl border border-outline-variant bg-white p-5 shadow-xl md:max-w-md md:rounded-xl">
        <div className="flex items-start gap-3">
          <Info aria-hidden className="mt-0.5 h-5 w-5 shrink-0 text-teal" />
          <div className="min-w-0">
            <h3 className="text-base font-semibold text-on-surface">{title}</h3>
            {paragraphs.map((paragraph, i) => (
              <p
                key={i}
                data-testid={`explainer-${explainerId}-p${i + 1}`}
                className="mt-2 text-sm text-on-surface-variant"
              >
                {paragraph}
              </p>
            ))}
          </div>
        </div>

        {canSuppress && (
          <label
            data-testid={`explainer-${explainerId}-dontshowagain`}
            className="mt-4 flex cursor-pointer items-center gap-2 text-sm text-on-surface-variant"
          >
            <input
              type="checkbox"
              data-testid={`explainer-${explainerId}-dontshowagain-input`}
              className="h-4 w-4 rounded border-outline-variant"
              checked={dontShowAgain}
              onChange={(e) => setDontShowAgain(e.target.checked)}
            />
            {t("dontShowAgain")}
          </label>
        )}

        <div className="mt-5 flex items-center justify-end gap-2">
          <Button
            type="button"
            variant="ghost"
            data-testid={`explainer-${explainerId}-cancel`}
            onClick={onCancel}
          >
            {t("cancel")}
          </Button>
          <Button
            ref={continueRef}
            type="button"
            variant="primary"
            data-testid={`explainer-${explainerId}-continue`}
            onClick={() => onContinue(canSuppress ? dontShowAgain : false)}
          >
            {continueLabel}
          </Button>
        </div>
      </section>
    </div>,
    document.body,
  );
}
