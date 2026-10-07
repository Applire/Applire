"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

// #737 (WP-E) — the cover letter's "Aussehen", stated honestly. The letter
// takes its template and colour from the application's CV at generation
// (services/cover_letter.py, "Resolve the active CV (for template +
// color_profile_id)"); the generate request carries no template. The seven
// template buttons this replaces opened the regenerate modal and discarded the
// choice — a control that did nothing it said.
import Link from "next/link";
import { useTranslations } from "next-intl";

const KNOWN = new Set([
  "classic_german",
  "modern_swiss",
  "executive",
  "tech_developer",
  "creative_sidebar",
  "academic",
  "compact_pro",
]);

export function LetterLookLine({ template, cvEditHref }: { template: string | null; cvEditHref: string | null }) {
  const t = useTranslations("editTab");
  const name = template && KNOWN.has(template) ? t(`templateName_${template}`) : t("templateName_classic_german");
  return (
    <div className="flex flex-col gap-1.5 px-3 pb-3" data-testid="letter-look">
      <p className="text-xs font-semibold uppercase tracking-wide text-on-surface-variant">{t("lookTitle")}</p>
      <p className="rounded-lg bg-surface-container px-3 py-2 text-[12.5px] leading-snug text-on-surface">
        {t("letterLookLine", { template: name })}
        {cvEditHref && (
          <Link href={cvEditHref} className="ml-1 font-semibold text-primary underline" data-testid="letter-look-cv-link">
            {t("letterLookLink")}
          </Link>
        )}
      </p>
    </div>
  );
}
