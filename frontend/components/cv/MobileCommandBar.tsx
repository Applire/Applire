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

import { useEffect, useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { ClipboardCheck, SlidersHorizontal, Download, X } from "lucide-react";
import type { ATSReport } from "./ATSChecksPanel";

interface MobileCommandBarProps {
  /** ATS report — drives the pass-count badge. `null` while unavailable. */
  atsReport: ATSReport;
  /** The existing ATSChecksPanel, re-presented inside the ATS bottom sheet. */
  atsPanel: ReactNode;
  /**
   * The existing CV editing surface (ContentTab). Mounted only while the
   * Fine-tune sheet is open (read-degraded on mobile — US228 does the polish).
   */
  fineTuneSurface: ReactNode;
  /** Wired to the CV page's requestDownload() / PreDownloadNotice path. */
  onDownloadPdf: () => void;
}

type ActiveSheet = "ats" | "fineTune" | null;

/**
 * E040 / US226 — the mobile CV-review command bar (ADR-050 §2/§5).
 *
 * Below `md` the desktop RefinementSidebar is hidden; this floating bar carries
 * exactly three actions (Versions was cut — ADR-050 amendment 2026-07-14):
 * ATS Checks (bottom sheet, pass-count badge), Fine-tune (bottom sheet hosting
 * the existing ContentTab, read-degraded), and Download PDF (primary). It opens
 * existing, reachable components — never the orphaned CVPreview/FineTunePanel.
 *
 * Presentation-only: no new panel content, no API surface. Rendered as a
 * `flex-shrink-0` child at the bottom of the fixed-height DocumentWorkspace
 * column, so it needs neither `position: fixed` nor a measured spacer — the
 * preview simply fills the remaining height and the iframe scrolls internally.
 */
export function MobileCommandBar({
  atsReport,
  atsPanel,
  fineTuneSurface,
  onDownloadPdf,
}: MobileCommandBarProps) {
  const t = useTranslations("commandBar");
  const tCommon = useTranslations("common");
  const [sheet, setSheet] = useState<ActiveSheet>(null);

  // Close the open sheet on Escape (parity with the other overlays).
  useEffect(() => {
    if (!sheet) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSheet(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sheet]);

  const passCount =
    atsReport != null
      ? atsReport.checks.filter((c) => c.status === "pass").length
      : null;

  const sheetTitle =
    sheet === "ats" ? t("atsSheetTitle") : sheet === "fineTune" ? t("fineTuneSheetTitle") : "";

  return (
    <>
      <div
        data-testid="mobile-command-bar"
        className="md:hidden flex-shrink-0 flex items-stretch gap-2 border-t border-outline-variant bg-surface-bright px-3 pt-2.5 pb-[max(0.75rem,env(safe-area-inset-bottom))]"
      >
        <button
          type="button"
          data-testid="command-ats"
          onClick={() => setSheet("ats")}
          className="relative flex flex-1 flex-col items-center justify-center gap-1 rounded-xl border border-outline-variant bg-surface-bright py-2 text-on-surface hover:bg-surface-container active:scale-95 transition"
        >
          <ClipboardCheck className="w-5 h-5" aria-hidden="true" />
          <span className="text-xs font-heading font-semibold">{t("atsChecks")}</span>
          {passCount !== null && (
            <span
              data-testid="command-ats-badge"
              aria-label={t("passBadgeLabel", { count: passCount })}
              className="absolute -top-1.5 -right-1.5 min-w-5 h-5 px-1 flex items-center justify-center rounded-full bg-success-container text-success text-[11px] font-bold border border-surface-bright"
            >
              {passCount}
            </span>
          )}
        </button>

        <button
          type="button"
          data-testid="command-finetune"
          onClick={() => setSheet("fineTune")}
          className="flex flex-1 flex-col items-center justify-center gap-1 rounded-xl border border-outline-variant bg-surface-bright py-2 text-on-surface hover:bg-surface-container active:scale-95 transition"
        >
          <SlidersHorizontal className="w-5 h-5" aria-hidden="true" />
          <span className="text-xs font-heading font-semibold">{t("fineTune")}</span>
        </button>

        <button
          type="button"
          data-testid="command-download"
          onClick={onDownloadPdf}
          className="flex flex-[1.4] flex-col items-center justify-center gap-1 rounded-xl bg-primary py-2 text-white shadow-sm hover:shadow-md active:scale-95 transition"
        >
          <Download className="w-5 h-5" aria-hidden="true" />
          <span className="text-xs font-heading font-semibold">{t("downloadPdf")}</span>
        </button>
      </div>

      {sheet && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={sheetTitle}
          data-testid="command-sheet"
          className="md:hidden fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0"
          onClick={() => setSheet(null)}
        >
          <div
            className="bg-white rounded-t-2xl w-full max-h-[85vh] flex flex-col overflow-hidden shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div aria-hidden="true" className="mx-auto mt-3 mb-1 h-1 w-10 rounded-full bg-gray-300" />
            <div className="flex items-center justify-between px-4 py-2 border-b border-outline-variant">
              <h3 className="text-sm font-heading font-semibold text-on-surface">{sheetTitle}</h3>
              <button
                type="button"
                data-testid="command-sheet-close"
                aria-label={tCommon("close")}
                onClick={() => setSheet(null)}
                className="text-on-surface-variant hover:text-on-surface p-1"
              >
                <X className="w-5 h-5" aria-hidden="true" />
              </button>
            </div>

            {/* Extra bottom padding keeps content clear of the sheet edge (mockup nit). */}
            <div className="flex-1 overflow-y-auto px-4 py-4 pb-8">
              {sheet === "ats" && atsPanel}
              {sheet === "fineTune" && (
                <>
                  <p
                    data-testid="command-finetune-degraded"
                    className="mb-4 rounded-lg border border-outline-variant bg-surface-container px-3 py-2 text-xs text-on-surface-variant"
                  >
                    {t("fineTuneDegradedNotice")}
                  </p>
                  {fineTuneSurface}
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
