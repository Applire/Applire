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

import { useTranslations } from "next-intl";

interface DocumentLanguageBadgeProps {
  /** The document's PINNED language (ADR-038 clause 3b). Callers hide the
   *  badge for legacy NULL rows instead of passing a guess. */
  lang: "de" | "en";
  testid?: string;
}

/**
 * E054/US289 (FMEA JF-F-G2.2): labels a generated document with its pinned
 * language. Without the label, correct pinning READS as inconsistency — after
 * a language switch, older documents keep their language by design (no
 * repaint), and a DE-CV next to an EN-letter looks like a bug unless each
 * document says which language it is in.
 */
export function DocumentLanguageBadge({ lang, testid }: DocumentLanguageBadgeProps) {
  const t = useTranslations("document");
  return (
    <span
      data-testid={testid ?? "document-language-badge"}
      title={t("languageBadgeTitle")}
      className="inline-flex items-center shrink-0 text-[11px] font-semibold px-2 py-0.5 rounded-md bg-surface-container text-on-surface-variant border border-outline-variant"
    >
      {lang === "de" ? t("langNameDe") : t("langNameEn")}
    </span>
  );
}
