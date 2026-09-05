"use client";

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

// frontend/components/cv/CriticAdvisoryPanel.tsx

import { useLocale, useTranslations } from "next-intl";
import { Info } from "lucide-react";

// ADR-060 (advisory-only, never gates delivery) / E049 49.6: the outcome
// critic's persisted coherence report, mirrored one-for-one on
// backend/applire/schemas/outcome_critic.py. Two mounts share this exact
// shape — Pass A on the CV (`mount: "cv"`) and Pass B on the cover letter
// (`mount: "letter"`) — so this panel is deliberately the SAME component on
// both document views, never a fork.

export type CriticAdvisoryKind =
  | "letter_only"
  | "letter_richer"
  | "numeric_inconsistency"
  | "internal_inconsistency";

export type CriticAdvisory = {
  concept: string;
  kind: CriticAdvisoryKind;
  // Verbatim, citation-verified spans the finding rests on — quote these
  // exactly, never paraphrase them (SF-CRITIC.2/.6).
  cv_state?: string | null;
  cv_detail?: string | null;
  letter_state?: string | null;
  // Pinned false server-side (SF-CRITIC.5) — this panel never offers a
  // "changed" affordance; it only reads the field for type completeness.
  changed: false;
  // Already localized server-side ({"de": ..., "en": ...}) — render
  // message[locale] directly, never re-translate.
  message: Record<string, string>;
};

export type OutcomeCriticReport = {
  ran: boolean;
  reason?: string | null;
  mount?: "cv" | "letter" | null;
  advisories: CriticAdvisory[];
  dropped_citations: number;
} | null;

// The advisory is an exception surface, not a permanent panel (ADR-060): a
// report that didn't run, or ran and found nothing, renders NOTHING — no
// empty-state box, no "all clear" headline. Never invert this into a
// falsy-look green state; the correct behaviour is to mount nothing at all.
// E058/US300: exported so the document review surface renders group 4's critic
// rows through the SAME server-localised message this panel uses — one
// implementation of "which language does this advisory speak" (ADR-066), never
// a second re-translation on the surface.
export function localizedMessage(message: Record<string, string>, locale: string): string {
  return message[locale] ?? message.de ?? message.en ?? Object.values(message)[0] ?? "";
}

export default function CriticAdvisoryPanel({ report }: { report: OutcomeCriticReport }) {
  const t = useTranslations("criticAdvisory");
  const locale = useLocale();

  if (!report || !report.ran || report.advisories.length === 0) {
    return null;
  }

  return (
    <section
      data-testid="critic-advisory-panel"
      aria-label={t("title")}
      className="rounded-xl border border-outline-variant surface-glass px-4 py-2.5"
    >
      <div className="flex items-start gap-2">
        <Info aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1 space-y-0.5">
          <p data-testid="critic-advisory-title" className="text-sm font-medium text-on-surface">
            {t("title")}
          </p>
          <p data-testid="critic-advisory-subtitle" className="text-xs text-on-surface-variant">
            {t("subtitle")}
          </p>
        </div>
      </div>

      <ul className="mt-2 space-y-3 border-t border-outline-variant pt-2">
        {report.advisories.map((advisory, index) => (
          <li
            key={`${advisory.concept}-${index}`}
            data-testid={`critic-advisory-item-${index}`}
            className="text-sm text-on-surface"
          >
            <p data-testid={`critic-advisory-message-${index}`}>
              {localizedMessage(advisory.message, locale)}
            </p>
            {(advisory.cv_state || advisory.cv_detail || advisory.letter_state) && (
              <div className="mt-1 space-y-1.5">
                {advisory.cv_state && (
                  <div
                    data-testid={`critic-advisory-cv-quote-${index}`}
                    className="ml-1 border-l-2 border-outline-variant pl-2"
                  >
                    <p className="text-[11px] font-medium text-on-surface-variant">
                      {t("cvQuoteLabel")}
                    </p>
                    <p className="text-xs italic text-on-surface-variant">{advisory.cv_state}</p>
                  </div>
                )}
                {advisory.cv_detail && (
                  <div
                    data-testid={`critic-advisory-cv-detail-quote-${index}`}
                    className="ml-1 border-l-2 border-outline-variant pl-2"
                  >
                    <p className="text-[11px] font-medium text-on-surface-variant">
                      {t("cvDetailQuoteLabel")}
                    </p>
                    <p className="text-xs italic text-on-surface-variant">{advisory.cv_detail}</p>
                  </div>
                )}
                {advisory.letter_state && (
                  <div
                    data-testid={`critic-advisory-letter-quote-${index}`}
                    className="ml-1 border-l-2 border-outline-variant pl-2"
                  >
                    <p className="text-[11px] font-medium text-on-surface-variant">
                      {t("letterQuoteLabel")}
                    </p>
                    <p className="text-xs italic text-on-surface-variant">{advisory.letter_state}</p>
                  </div>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
