"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
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
import { Download } from "lucide-react";

interface DocumentExportFooterProps {
  onDownloadPdf: () => void;
  /**
   * US298 (E057 task 1.5, ADR-058 cl.2/ADR-066): the ADR-079 editable Word
   * export. Optional — when omitted, no .docx CTA renders. Both document pages
   * supply it. Gated by the SAME pre-download notice as the PDF button
   * (ADR-040 cl.4) — the caller owns that gate, exactly as it already does for
   * `onDownloadPdf`.
   */
  onDownloadDocx?: () => void;
  downloadDisabled?: boolean;
}

/**
 * The workspace panel's PINNED FOOTER: the exports.
 *
 * E058/US299, ADR-081 clause 1 — the second half of the dissolved
 * `DocumentTopBar`. It sits at the bottom of the panel in every tab and in
 * every review mode, so the export is never behind a tab; the test ids
 * (`document-download-btn`, `document-download-docx-btn`) are carried over
 * unchanged, because the OQ and PQ specs that drive the download are testing
 * the same action in a new place.
 *
 * ADR-040 clause 4 is untouched: this component raises the caller's handler,
 * and the pre-download notice still stands between that handler and every file.
 */
export function DocumentExportFooter({
  onDownloadPdf,
  onDownloadDocx,
  downloadDisabled = false,
}: DocumentExportFooterProps) {
  const t = useTranslations("document");

  return (
    <div className="flex items-center gap-2" data-testid="document-export-footer">
      <button
        type="button"
        onClick={onDownloadPdf}
        disabled={downloadDisabled}
        data-testid="document-download-btn"
        className="btn-pill-primary inline-flex flex-1 items-center justify-center gap-2 hover:shadow-md active:scale-95 transition disabled:opacity-50 disabled:pointer-events-none"
      >
        <Download className="w-4 h-4" aria-hidden="true" />
        {t("downloadPdf")}
      </button>

      {/* US298: the office (.docx) export — secondary style (.btn-glass) so the
          PDF stays the one visually primary action. */}
      {onDownloadDocx && (
        <button
          type="button"
          onClick={onDownloadDocx}
          disabled={downloadDisabled}
          data-testid="document-download-docx-btn"
          className="btn-glass inline-flex items-center justify-center gap-2 disabled:opacity-50 disabled:pointer-events-none"
        >
          <Download className="w-4 h-4" aria-hidden="true" />
          {t("downloadDocx")}
        </button>
      )}
    </div>
  );
}
