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

import { useState } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";

interface DocumentLanguageSwitchProps {
  applicationId: string;
  /** The CURRENT document's pinned language (status response, clause 3b);
   *  null = legacy row with no pin — neither segment is highlighted. */
  documentLanguage: "de" | "en" | null;
  /** Labels of sections carrying manual overrides on the current document.
   *  Named in the notice (JF-F-G2.1: the notice must name the override loss,
   *  not just "regenerates"). Empty = none known → generic loss sentence. */
  overriddenSectionLabels?: string[];
  /** Called AFTER `language_override` was persisted — the caller triggers its
   *  own existing regeneration path (ADR-038 clause 6: switch = the existing
   *  regeneration path; no in-place language mutation). */
  onSwitched: (lang: "de" | "en") => void;
  apiBase: string;
}

/**
 * E054/US289 (ADR-038 amendment clause 6): post-generation language switch on
 * the document view. Choosing the other language opens an explicit
 * regeneration notice BEFORE anything happens; confirming persists
 * `applications.language_override` (all subsequent documents of the
 * application follow it) and hands off to the page's existing regeneration
 * path. Clicking the already-active language is a no-op — there is nothing to
 * switch (the analysis-step control owns confirm-the-prefill semantics; this
 * control owns switching an existing document).
 */
export function DocumentLanguageSwitch({
  applicationId,
  documentLanguage,
  overriddenSectionLabels = [],
  onSwitched,
  apiBase,
}: DocumentLanguageSwitchProps) {
  const t = useTranslations("document");
  // The language the pending dialog would switch to; null = dialog closed.
  const [target, setTarget] = useState<"de" | "en" | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const langName = (lang: "de" | "en") => (lang === "de" ? t("langNameDe") : t("langNameEn"));

  function openDialog(lang: "de" | "en") {
    if (lang === documentLanguage) return; // nothing to switch
    setFailed(false);
    setTarget(lang);
  }

  async function handleConfirm() {
    if (!target) return;
    setBusy(true);
    setFailed(false);
    try {
      const res = await fetch(`${apiBase}/api/applications/${applicationId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language_override: target }),
      });
      if (!res.ok) {
        setFailed(true);
        return;
      }
      const lang = target;
      setTarget(null);
      onSwitched(lang);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  const buttonBase =
    "px-3 py-1 text-sm font-medium border border-outline-variant first:rounded-l-md last:rounded-r-md focus:outline-none focus-visible:ring-2 focus-visible:ring-primary";

  return (
    <div data-testid="doc-language-switch" className="flex flex-wrap items-center gap-2">
      <span className="text-xs uppercase tracking-wide text-on-surface-variant">
        {t("languageSwitchLabel")}
      </span>
      <div role="group" aria-label={t("languageSwitchLabel")} className="inline-flex">
        {(["de", "en"] as const).map((lang) => (
          <button
            key={lang}
            type="button"
            data-testid={`doc-language-switch-${lang}`}
            aria-pressed={documentLanguage === lang}
            onClick={() => openDialog(lang)}
            className={`${buttonBase} ${
              documentLanguage === lang
                ? "bg-primary text-white"
                : "bg-white text-on-surface hover:bg-surface-container"
            }`}
          >
            {langName(lang)}
          </button>
        ))}
      </div>

      {/* Portal to <body>: the switch lives inside the refinement sidebar,
          whose ancestor stack traps position:fixed (transform containment) —
          rendered in place, the overlay dims only the sidebar column
          (pixel-verified 2026-08-24). */}
      {target && createPortal(
        <div
          role="dialog"
          aria-modal="true"
          aria-label={t("languageSwitchTitle", { lang: langName(target) })}
          className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
          data-testid="doc-language-switch-dialog"
        >
          <div className="bg-white rounded-t-2xl md:rounded-xl p-6 shadow-xl w-full md:max-w-md max-h-[85vh] overflow-y-auto">
            <div aria-hidden="true" className="md:hidden mx-auto mb-3 h-1 w-10 rounded-full bg-gray-300" />
            <h3 className="text-base font-bold text-on-surface mb-2">
              {t("languageSwitchTitle", { lang: langName(target) })}
            </h3>
            <p className="text-sm text-on-surface-variant mb-3 leading-relaxed">
              {t("languageSwitchBody", { lang: langName(target) })}
            </p>
            <p
              className="text-sm font-medium text-on-surface mb-3 leading-relaxed"
              data-testid="doc-language-switch-override-loss"
            >
              {overriddenSectionLabels.length > 0
                ? t("languageSwitchOverridesNamed", {
                    sections: overriddenSectionLabels.join(", "),
                  })
                : t("languageSwitchOverridesGeneric")}
            </p>
            <p className="text-sm text-on-surface-variant mb-5 leading-relaxed">
              {t("languageSwitchScope")}
            </p>
            {failed && (
              <p className="mb-4 text-[13px] font-semibold text-critical">
                {t("languageSwitchError")}
              </p>
            )}
            <div className="flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => setTarget(null)}
                disabled={busy}
                data-testid="doc-language-switch-cancel"
                className="text-[13px] font-bold px-4 py-2 rounded-lg border border-outline-variant text-on-surface hover:bg-surface-container disabled:opacity-50"
              >
                {t("languageSwitchCancel")}
              </button>
              <button
                type="button"
                onClick={() => void handleConfirm()}
                disabled={busy}
                data-testid="doc-language-switch-confirm"
                className="text-[13px] font-bold px-4 py-2 rounded-lg bg-primary text-white hover:opacity-90 disabled:opacity-50"
              >
                {busy ? t("languageSwitchBusy") : t("languageSwitchConfirm")}
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
