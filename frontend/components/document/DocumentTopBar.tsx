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

import Link from "next/link";
import { useTranslations } from "next-intl";
import { Download } from "lucide-react";
import { DocumentLanguageBadge } from "@/components/document/DocumentLanguageBadge";

interface DocumentTopBarProps {
  flowId: string;
  activeDoc: "cv" | "cover-letter";
  onDownloadPdf: () => void;
  /**
   * US298 (E057 task 1.5, ADR-058 cl.2/ADR-066): the ADR-079 editable Word
   * export. Optional — when omitted, no .docx CTA renders (keeps this bar
   * usable by a caller that hasn't wired an office-export handler). Both
   * document pages supply it. Gated by the SAME pre-download notice as the
   * PDF button (ADR-040 cl.4) — the caller is responsible for that gate,
   * exactly as it already is for `onDownloadPdf`.
   */
  onDownloadDocx?: () => void;
  downloadDisabled?: boolean;
  /**
   * E040 / US226: hide this Download button below `md` when the page also
   * renders a MobileCommandBar with its own primary Download action, so
   * mobile never shows two Download CTAs at once. Desktop is unaffected.
   * US298: the same rule applies to the .docx CTA — mobile keeps exactly
   * one primary CTA (ADR-050), the office export stays a desktop-only
   * top-bar affordance.
   */
  hideDownloadBelowMd?: boolean;
  /**
   * E054/US289 (FMEA JF-F-G2.2): the active document's PINNED language.
   * null/undefined (legacy rows, still generating) renders no badge — the
   * bar must not claim a language the generation run never stamped.
   */
  documentLanguage?: "de" | "en" | null;
}

/**
 * Shared per-page bar for the CV and cover-letter result screens (E038 / US206).
 * Sits directly under the global flow AppTopbar (which owns the stepper). Carries
 * the document toggle (Lebenslauf ↔ Anschreiben) and the persistent primary
 * "PDF herunterladen" CTA — identical on both documents. Replaces the CV's
 * CVPageActionBar and the cover letter's bespoke breadcrumb bar.
 */
export function DocumentTopBar({
  flowId,
  activeDoc,
  onDownloadPdf,
  onDownloadDocx,
  downloadDisabled = false,
  hideDownloadBelowMd = false,
  documentLanguage = null,
}: DocumentTopBarProps) {
  const t = useTranslations("document");

  const items = [
    { id: "cv", href: `/flow/${flowId}/cv`, label: t("navCv"), testid: "document-nav-cv" },
    {
      id: "cover-letter",
      href: `/flow/${flowId}/cover-letter`,
      label: t("navCoverLetter"),
      testid: "document-nav-cover-letter",
    },
  ] as const;

  return (
    <div
      className="flex items-center justify-between gap-3 px-5 py-2.5 bg-surface-bright border-b border-outline-variant flex-shrink-0"
      data-testid="document-topbar"
    >
      {/* Document toggle — segmented pill (+ the active document's pinned
          language, E054/US289: after a switch, older documents keep their
          language by design; the badge is what keeps that from reading as
          inconsistency) */}
      <div className="flex items-center gap-3 min-w-0">
      <div
        className="flex items-center gap-1 rounded-full bg-surface-container p-1"
        role="tablist"
        aria-label={t("navAriaLabel")}
      >
        {items.map((it) => {
          const active = it.id === activeDoc;
          return (
            <Link
              key={it.id}
              href={it.href}
              role="tab"
              aria-current={active ? "page" : undefined}
              aria-selected={active}
              data-testid={it.testid}
              className={`px-5 py-1.5 rounded-full text-sm font-heading font-semibold transition-colors ${
                active
                  ? "bg-primary text-white shadow-sm"
                  : "text-on-surface-variant hover:text-on-surface"
              }`}
            >
              {it.label}
            </Link>
          );
        })}
      </div>
      {documentLanguage && <DocumentLanguageBadge lang={documentLanguage} />}
      </div>

      {/* Primary CTA — always visible on both documents */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onDownloadPdf}
          disabled={downloadDisabled}
          data-testid="document-download-btn"
          className={`btn-pill-primary items-center gap-2 hover:shadow-md active:scale-95 transition disabled:opacity-50 disabled:pointer-events-none ${
            hideDownloadBelowMd ? "hidden md:inline-flex" : "inline-flex"
          }`}
        >
          <Download className="w-4 h-4" aria-hidden="true" />
          {t("downloadPdf")}
        </button>

        {/* US298 (E057 task 1.5): the office (.docx) export — same top bar,
            same mobile-visibility rule as the primary CTA, secondary style
            (.btn-glass) so the PDF stays the one visually primary action. */}
        {onDownloadDocx && (
          <button
            type="button"
            onClick={onDownloadDocx}
            disabled={downloadDisabled}
            data-testid="document-download-docx-btn"
            className={`btn-glass items-center gap-2 disabled:opacity-50 disabled:pointer-events-none ${
              hideDownloadBelowMd ? "hidden md:inline-flex" : "inline-flex"
            }`}
          >
            <Download className="w-4 h-4" aria-hidden="true" />
            {t("downloadDocx")}
          </button>
        )}
      </div>
    </div>
  );
}
