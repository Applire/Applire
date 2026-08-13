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

// ADR-074 (#526) — the requirement nobody asked about.
//
// A JD hard requirement the vault holds nothing on, under any name, and that the
// candidate was never asked about has NO truthful expression in a cover letter:
// asserting the term is ungrounded, staying silent breaks the writer's own
// instruction, and denying it invents a limit the candidate never stated. So the
// letter is written as though the requirement had not been named, and the fact is
// brought here instead.
//
// The wording is the decision. This panel says what is true of APPLIRE — "the
// posting asks for this, we hold nothing on it, and you were never asked" — and
// never what might be true of the candidate. "You lack X" is a claim about a
// person that no evidence in this system supports; it is exactly the inversion of
// the truthfulness promise the product exists for. Anyone editing these strings
// should read that sentence again before changing them.

import { HelpCircle } from "lucide-react";
import { useTranslations } from "next-intl";

export interface UnaskedRequirement {
  concept: string;
  surface_forms?: string[];
}

export default function UnaskedRequirementsPanel({
  requirements,
}: {
  requirements: UnaskedRequirement[] | null | undefined;
}) {
  const t = useTranslations("unaskedRequirements");

  // An exception surface, not a permanent panel: with nothing unasked there is
  // nothing to say, and an "all clear" box would be noise on every other run.
  // Note this is NOT the same as the silence ADR-074 removes from the letter —
  // that silence is recorded on the always-on LETTER_UNASKED_REQUIREMENTS log
  // line whether or not anyone opens this page.
  if (!requirements || requirements.length === 0) {
    return null;
  }

  return (
    <section
      data-testid="unasked-requirements-panel"
      aria-label={t("title")}
      className="rounded-xl border border-outline-variant surface-glass px-4 py-2.5"
    >
      <div className="flex items-start gap-2">
        <HelpCircle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1 space-y-0.5">
          <p data-testid="unasked-requirements-title" className="text-sm font-medium text-on-surface">
            {t("title")}
          </p>
          <p data-testid="unasked-requirements-subtitle" className="text-xs text-on-surface-variant">
            {t("subtitle")}
          </p>
        </div>
      </div>

      <ul className="mt-2 space-y-1 border-t border-outline-variant pt-2">
        {requirements.map((requirement, index) => (
          <li
            key={`${requirement.concept}-${index}`}
            data-testid={`unasked-requirement-${index}`}
            className="text-sm text-on-surface"
          >
            {requirement.concept}
          </li>
        ))}
      </ul>

      <p data-testid="unasked-requirements-reassurance" className="mt-2 text-xs text-on-surface-variant">
        {t("reassurance")}
      </p>
    </section>
  );
}
