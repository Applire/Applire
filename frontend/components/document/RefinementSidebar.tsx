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

import { useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { ChevronLeft, ChevronRight } from "lucide-react";

export interface SidebarTab {
  id: string;
  label: string;
  body: ReactNode;
  /** Shown pinned at the bottom of the sidebar while this tab is active. */
  footer?: ReactNode;
  /** Small icon shown on the collapsed rail. */
  icon?: ReactNode;
  /**
   * E058/US301 (ADR-081 cl. 6): a count badge that stays visible on the tab
   * strip AND on the collapsed rail. The review tab uses it to carry group 1's
   * count, so collapsing the panel cannot hide the one send-blocking number.
   */
  badge?: ReactNode;
}

interface RefinementSidebarProps {
  matchScore: number | null;
  validity?: { label: string; level: "warning" | "critical" } | null;
  tabs: SidebarTab[];
  collapsed: boolean;
  onToggleCollapse: () => void;
  initialTabId?: string;
  /**
   * E058/US299 (ADR-081 cl. 1): the dissolved `DocumentTopBar`'s identity half
   * — the CV ↔ cover-letter switch and the ADR-038 language badge — rendered in
   * this panel's header. The panel is now the ONE document-scope chrome region.
   */
  identityBar?: ReactNode;
  /**
   * ADR-081 cl. 1: the exports, pinned to the panel's bottom in EVERY tab, so
   * the download is never behind a tab. Rendered below the active tab's own
   * contextual footer.
   */
  pinnedFooter?: ReactNode;
}

function ScoreRing({ score }: { score: number | null }) {
  const pct = score == null ? 0 : Math.max(0, Math.min(100, score));
  const r = 20;
  const circumference = 2 * Math.PI * r;
  const offset = circumference * (1 - pct / 100);
  const label = score == null ? "—" : `${Math.round(score)}%`;
  return (
    <div className="relative w-12 h-12 flex items-center justify-center shrink-0">
      <svg className="w-full h-full -rotate-90" viewBox="0 0 48 48" aria-hidden="true">
        <circle cx="24" cy="24" r={r} fill="none" stroke="rgba(255,255,255,0.18)" strokeWidth="4" />
        <circle
          cx="24"
          cy="24"
          r={r}
          fill="none"
          stroke="var(--color-gold)"
          strokeWidth="4"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
        />
      </svg>
      <span className="absolute text-[11px] font-heading font-bold text-white">{label}</span>
    </div>
  );
}

/**
 * The workspace panel — after E058 the ONE document-scope chrome region on both
 * result screens (ADR-081 cl. 1).
 *
 * It owns the navy status header (now also carrying the document switch and the
 * language badge that used to live in `DocumentTopBar`), the tab strip
 * (Prüfung / Bearbeiten / Aktionen), the active tab's body and contextual
 * footer, the pinned export footer, and the collapse rail. The CV and
 * cover-letter pages supply only the per-document tab bodies.
 */
export function RefinementSidebar({
  matchScore,
  validity,
  tabs,
  collapsed,
  onToggleCollapse,
  initialTabId,
  identityBar,
  pinnedFooter,
}: RefinementSidebarProps) {
  const t = useTranslations("document");
  const [activeId, setActiveId] = useState<string>(initialTabId ?? tabs[0]?.id ?? "");
  const active = tabs.find((tab) => tab.id === activeId) ?? tabs[0];

  if (collapsed) {
    return (
      <aside
        className="w-14 flex flex-col items-center h-full surface-glass border-l border-outline-variant py-2 gap-2 flex-shrink-0 transition-[width] duration-200 ease-in-out overflow-hidden"
        data-testid="refinement-sidebar"
      >
        <button
          type="button"
          onClick={onToggleCollapse}
          className="w-8 h-8 flex items-center justify-center rounded hover:bg-surface-container text-on-surface-variant"
          title={t("panelExpand")}
          data-testid="sidebar-expand-btn"
        >
          <ChevronLeft className="w-4 h-4" aria-hidden="true" />
        </button>
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => {
              setActiveId(tab.id);
              onToggleCollapse();
            }}
            className={`relative w-8 h-8 flex items-center justify-center rounded ${
              activeId === tab.id ? "bg-primary-container text-primary" : "hover:bg-surface-container text-on-surface-variant"
            }`}
            title={tab.label}
            data-testid={`sidebar-rail-${tab.id}`}
          >
            {tab.icon ?? tab.label.charAt(0)}
            {/* ADR-081 cl. 6, carried onto the rail: collapsing the panel must
                not hide a non-zero count. */}
            {tab.badge && (
              <span
                data-testid={`sidebar-rail-badge-${tab.id}`}
                className="absolute -top-1 -right-1"
              >
                {tab.badge}
              </span>
            )}
          </button>
        ))}
      </aside>
    );
  }

  return (
    <aside
      className="w-full max-w-[400px] md:w-[400px] h-full flex flex-col flex-shrink-0 surface-glass border-l border-outline-variant transition-[width] duration-200 ease-in-out"
      data-testid="refinement-sidebar"
    >
      {/* Navy status header — the document's identity and its match score.
          ADR-081 cl. 1: this is where the dissolved top bar's switch and
          language badge now live. */}
      <div className="bg-primary text-white px-4 py-3 flex-shrink-0 space-y-2" data-testid="sidebar-status-header">
        {identityBar}
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <ScoreRing score={matchScore} />
            <span className="text-[11px] font-heading font-semibold uppercase tracking-wider text-white/70">
              {t("matchScoreLabel")}
            </span>
          </div>
          {validity && (
            <span
              className="text-[10px] font-semibold uppercase tracking-wide px-2.5 py-1 rounded-full bg-white/10 text-white/80 border border-white/15"
              data-testid="sidebar-validity"
            >
              {validity.label}
            </span>
          )}
        </div>
      </div>

      {/* Tab strip */}
      <div className="flex items-center border-b border-outline-variant shrink-0" role="tablist">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setActiveId(tab.id)}
            role="tab"
            aria-selected={activeId === tab.id}
            className={`flex-1 flex items-center justify-center gap-1.5 text-xs font-heading font-semibold py-3 px-2 transition-colors ${
              activeId === tab.id
                ? "text-primary border-b-2 border-gold"
                : "text-on-surface-variant hover:text-on-surface"
            }`}
            data-testid={`sidebar-tab-${tab.id}`}
          >
            {tab.label}
            {tab.badge}
          </button>
        ))}
        <button
          type="button"
          onClick={onToggleCollapse}
          className="px-2 py-3 text-on-surface-variant hover:text-on-surface shrink-0"
          title={t("panelCollapse")}
          data-testid="sidebar-collapse-btn"
        >
          <ChevronRight className="w-4 h-4" aria-hidden="true" />
        </button>
      </div>

      {/* Active tab body */}
      <div className="flex-1 overflow-y-auto" role="tabpanel" data-testid="sidebar-body">
        {active?.body}
      </div>

      {/* Contextual footer — the active tab's primary action */}
      {active?.footer && (
        <div className="flex-shrink-0 border-t border-outline-variant p-4 bg-surface-bright" data-testid="sidebar-footer">
          {active.footer}
        </div>
      )}

      {/* Pinned footer — the exports, in every tab (ADR-081 cl. 1). ADR-040
          cl. 4's pre-download notice still stands between these and any file. */}
      {pinnedFooter && (
        <div
          className="flex-shrink-0 border-t border-outline-variant px-4 py-3 bg-surface-bright"
          data-testid="sidebar-pinned-footer"
        >
          {pinnedFooter}
        </div>
      )}
    </aside>
  );
}
