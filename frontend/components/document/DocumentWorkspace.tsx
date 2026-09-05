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

interface DocumentWorkspaceProps {
  /** Rendered document (iframe preview). Owns the left column's height. */
  preview: ReactNode;
  /** The one document-scope chrome region (ADR-081 cl. 1). Hidden below `md`. */
  sidebar: ReactNode;
  /**
   * Mobile-only floating command bar (E040 / US226). Rendered as a
   * `flex-shrink-0` child at the bottom of the fixed-height column below `md`;
   * the component itself is responsible for its `md:hidden` visibility.
   */
  commandBar?: ReactNode;
}

/**
 * The layout shell both result screens render through (E038 / US206, rebuilt by
 * E058 / US299).
 *
 * **The #625 mechanism, and why the fix is structural.** This component used to
 * put the preview and the findings stack in ONE `overflow-y-auto` column with
 * the preview as `flex-1 min-h-0`. Inside a scroll container `flex-1` does not
 * mean *take the remaining height* — it means *take whatever the tall sibling
 * leaves*, and `min-h-0` permits near-zero. So the document got smallest exactly
 * when the system had found the most: ~45 px of preview against ~600 px of
 * findings on the reporter's screenshot. Shortening the panels would have moved
 * the symptom; ADR-081 clause 1 closes the mechanism instead — **the preview
 * column takes the height unconditionally**, because it has no sibling in that
 * column any more and the column is no longer a scroll container.
 *
 * The findings moved into the workspace panel, which is now the ONE
 * document-scope chrome region: `DocumentTopBar` is dissolved (the document
 * switch and the ADR-038 language badge into the panel header, the PDF and
 * `.docx` exports into its pinned footer — `DocumentIdentityBar` /
 * `DocumentExportFooter`), and `AppSidebar` collapses to a rail on document
 * routes. Six chrome regions became three.
 *
 * Flow-scope chrome (`AppTopbar`, the cancel row, `resolveFlowRedirect`) is
 * untouched: it belongs to the flow layout.
 */
export function DocumentWorkspace({ preview, sidebar, commandBar }: DocumentWorkspaceProps) {
  return (
    <div
      className="flex flex-col h-[calc(100vh-56px)] bg-surface-dim"
      data-testid="document-workspace"
    >
      <div className="flex flex-1 min-h-0">
        {/* Left column: the document, and nothing else. It is NOT an
            `overflow-y-auto` container and the preview is its only child, so no
            sibling's growth can take height from it — that is #625's mechanism
            closed by construction rather than by shortening anything. */}
        <div
          className="flex-1 min-w-0 flex flex-col px-3 py-2 md:px-4 md:py-3"
          data-testid="document-preview-column"
        >
          <div className="flex-1 min-h-0 flex flex-col overflow-hidden">{preview}</div>
        </div>
        {/* Right column: the workspace panel — desktop only (E040 / US226).
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
