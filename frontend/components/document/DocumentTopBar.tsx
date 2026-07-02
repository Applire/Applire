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

interface DocumentTopBarProps {
  flowId: string;
  activeDoc: "cv" | "cover-letter";
  onDownloadPdf: () => void;
  downloadDisabled?: boolean;
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
  downloadDisabled = false,
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
      {/* Document toggle — segmented pill */}
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

      {/* Primary CTA — always visible on both documents */}
      <button
        type="button"
        onClick={onDownloadPdf}
        disabled={downloadDisabled}
        data-testid="document-download-btn"
        className="btn-pill-primary inline-flex items-center gap-2 hover:shadow-md active:scale-95 transition disabled:opacity-50 disabled:pointer-events-none"
      >
        <Download className="w-4 h-4" aria-hidden="true" />
        {t("downloadPdf")}
      </button>
    </div>
  );
}
