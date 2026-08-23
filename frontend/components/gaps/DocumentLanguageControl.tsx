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

import { useRef, useState } from "react";
import { useTranslations } from "next-intl";

interface DocumentLanguageControlProps {
  applicationId: string;
  /** The detector's verdict (JobAnalysisResponse.jd_language); null for legacy rows. */
  detectedLanguage: "de" | "en" | null;
  /** The persisted override (ApplicationResponse.language_override); null = automatic. */
  initialOverride: "de" | "en" | null;
  apiBase: string;
}

/**
 * E054/US288 (ADR-038 amendment 2026-08-23): the user's choice of the leading
 * document language, shown with the parsed-JD echo after JD analysis.
 *
 * Detection is the default, never the law: the control is prefilled with the
 * detected language and marked "automatisch erkannt" until the user chooses.
 * Clause 6: ANY interaction writes `language_override` — including clicking
 * the already-active detected value. A confirmation must be distinguishable
 * from never-having-looked (the 2026-08-01 `ui_language` default-vs-choice
 * lesson); an onChange-diff trigger would reopen that trap, which is why this
 * is a segmented control (a native select never fires on same-value confirm).
 */
export function DocumentLanguageControl({
  applicationId,
  detectedLanguage,
  initialOverride,
  apiBase,
}: DocumentLanguageControlProps) {
  const t = useTranslations("gaps");
  const [override, setOverride] = useState<"de" | "en" | null>(initialOverride);
  const [saveError, setSaveError] = useState(false);
  // Rapid clicks issue concurrent PATCHes; responses can arrive out of
  // network order. Only the LATEST click may write state (adversarial
  // finding, 2026-08-23) — the server sees last-write-wins on the same
  // ordering the user clicked in, so the seq guard keeps UI and DB aligned.
  const clickSeq = useRef(0);

  // No override and no detection (legacy row, jd_language NULL): highlight
  // nothing rather than claim a hardcoded German detection that never ran.
  const active = override ?? detectedLanguage;

  async function choose(lang: "de" | "en") {
    const seq = ++clickSeq.current;
    setSaveError(false);
    try {
      const res = await fetch(`${apiBase}/api/applications/${applicationId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language_override: lang }),
      });
      if (seq !== clickSeq.current) return; // stale response — a newer click won
      if (!res.ok) {
        setSaveError(true);
        return;
      }
      setOverride(lang);
    } catch {
      if (seq === clickSeq.current) setSaveError(true);
    }
  }

  const buttonBase =
    "px-3 py-1 text-sm font-medium border border-outline-variant first:rounded-l-md last:rounded-r-md focus:outline-none focus-visible:ring-2 focus-visible:ring-primary";

  return (
    <div data-testid="doc-language-control" className="mt-4 flex flex-wrap items-center gap-2">
      <span className="text-xs uppercase tracking-wide text-on-surface-variant">
        {t("docLanguageLabel")}
      </span>
      <div role="group" aria-label={t("docLanguageLabel")} className="inline-flex">
        {(["de", "en"] as const).map((lang) => (
          <button
            key={lang}
            type="button"
            data-testid={`doc-language-${lang}`}
            aria-pressed={active === lang}
            onClick={() => choose(lang)}
            className={`${buttonBase} ${
              active === lang
                ? "bg-primary text-white"
                : "bg-white text-on-surface hover:bg-surface-container"
            }`}
          >
            {lang === "de" ? t("docLanguageGerman") : t("docLanguageEnglish")}
          </button>
        ))}
      </div>
      {override === null && (
        <span
          data-testid="doc-language-auto-badge"
          className="rounded-md bg-surface-container px-2 py-0.5 text-xs text-on-surface-variant"
        >
          {t("docLanguageAutoDetected")}
        </span>
      )}
      {saveError && (
        <span className="text-xs text-red-600">{t("docLanguageSaveError")}</span>
      )}
    </div>
  );
}
