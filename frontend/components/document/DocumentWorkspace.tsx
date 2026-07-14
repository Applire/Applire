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

import type { ReactNode } from "react";
import { DocumentTopBar } from "./DocumentTopBar";

interface DocumentWorkspaceProps {
  flowId: string;
  activeDoc: "cv" | "cover-letter";
  onDownloadPdf: () => void;
  downloadDisabled?: boolean;
  /** Rendered document (iframe preview). Fills the left column. */
  preview: ReactNode;
  /** ATS checks panel, shown directly below the document. Optional. */
  atsPanel?: ReactNode;
  /** Right-hand refinement sidebar. Hidden below `md` (E040 / US226). */
  sidebar: ReactNode;
  /**
   * Mobile-only floating command bar (E040 / US226). Rendered as a
   * `flex-shrink-0` child at the bottom of the fixed-height column below `md`;
   * the component itself is responsible for its `md:hidden` visibility.
   */
  commandBar?: ReactNode;
}

/**
 * The single layout shell both result screens render through (E038 / US206).
 * Guarantees structural parity between the CV and cover-letter views: shared top
 * bar, a left preview column with the ATS panel below the document, and a fixed
 * right sidebar. Sits under the global flow AppTopbar (the stepper). Only the
 * slotted content (preview / atsPanel / sidebar) legitimately differs per document.
 */
export function DocumentWorkspace({
  flowId,
  activeDoc,
  onDownloadPdf,
  downloadDisabled = false,
  preview,
  atsPanel,
  sidebar,
  commandBar,
}: DocumentWorkspaceProps) {
  return (
    <div
      className="flex flex-col h-[calc(100vh-56px)] bg-surface-dim"
      data-testid="document-workspace"
    >
      <DocumentTopBar
        flowId={flowId}
        activeDoc={activeDoc}
        onDownloadPdf={onDownloadPdf}
        downloadDisabled={downloadDisabled}
        // E040/US226: a command bar carries its own primary Download action —
        // hide the top-bar one below `md` so mobile shows a single CTA.
        hideDownloadBelowMd={Boolean(commandBar)}
      />
      <div className="flex flex-1 min-h-0">
        {/* Left column: document preview + ATS panel below it.
            Below md the preview goes full-width (the sidebar is hidden) and the
            ATS panel moves into the command bar's bottom sheet, so it's hidden
            here to avoid a duplicate. */}
        <div className="flex-1 min-w-0 flex flex-col overflow-y-auto px-3 py-2 md:px-4 md:py-3 gap-3">
          <div className="flex-1 min-h-0 flex flex-col">{preview}</div>
          {atsPanel && <div className="hidden md:block">{atsPanel}</div>}
        </div>
        {/* Right column: refinement sidebar — desktop only (E040 / US226).
            `hidden md:contents` keeps the aside a direct flex child at md+
            (preserving its width / flex-shrink) while dropping it below md. */}
        {sidebar && <div className="hidden md:contents">{sidebar}</div>}
      </div>
      {/* Mobile-only command bar — a flex-shrink-0 row pinned to the bottom of
          the fixed-height container (no position:fixed, no measured spacer). */}
      {commandBar}
    </div>
  );
}
