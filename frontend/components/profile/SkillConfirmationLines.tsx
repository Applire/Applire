"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Sorbet WP-Q10 (ADR-046 am. 2026-11-17) — where a skill confirmed from a job
 * posting came from: "Von dir bestätigt am 17.11.2026 · Wortlaut aus der
 * Stellenanzeige NovaPay". Read-only; one line per confirmation.
 */

import { useLocale, useTranslations } from "next-intl";
import { formatUiDate } from "@/lib/format-date";
import { confirmationKey } from "@/lib/posting-terms";
import type { Skill } from "@/lib/profile-entries";

const SEPARATOR = " · ";

export function SkillConfirmationLines({ skill }: { skill: Skill }) {
  const t = useTranslations("profile.skillConfirmation");
  const locale = useLocale();
  const confirmations = Array.isArray(skill.confirmations) ? skill.confirmations : [];
  if (confirmations.length === 0) return null;
  return (
    <div data-testid="skill-confirmations" className="mt-1.5 flex flex-col gap-0.5">
      {confirmations.map((c, i) => (
        <p key={`${c.name}-${c.on}-${i}`} className="text-[12.5px] text-on-surface-variant">
          <span className="font-medium text-on-surface">{skill.name}</span>
          {SEPARATOR}
          {t(confirmationKey(skill.name, c), {
            date: formatUiDate(c.on, locale),
            posting: c.posting ?? "",
            name: c.name,
          })}
        </p>
      ))}
    </div>
  );
}
