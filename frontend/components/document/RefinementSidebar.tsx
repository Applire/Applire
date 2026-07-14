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
}

interface RefinementSidebarProps {
  matchScore: number | null;
  validity?: { label: string; level: "warning" | "critical" } | null;
  tabs: SidebarTab[];
  collapsed: boolean;
  onToggleCollapse: () => void;
  initialTabId?: string;
}

function ScoreRing({ score }: { score: number | null }) {
  const pct = score == null ? 0 : Math.max(0, Math.min(100, score));
  const r = 20;
  const circumference = 2 * Math.PI * r;
  const offset = circumference * (1 - pct / 100);
  const label = score == null ? "—" : `${Math.round(score)}%`;
  return (
    <div className="relative w-14 h-14 flex items-center justify-center shrink-0">
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
      <span className="absolute text-sm font-heading font-bold text-white">{label}</span>
    </div>
  );
}

/**
 * Shared refinement sidebar shell for both result screens (E038 / US207).
 * Owns the navy status header (match-score ring + validity), the tab strip,
 * the active tab's body + pinned footer, and the collapse rail. The CV and
 * cover-letter panels supply only the per-document tab bodies. No premium /
 * pro-tier framing — dropped by E038 decision.
 */
export function RefinementSidebar({
  matchScore,
  validity,
  tabs,
  collapsed,
  onToggleCollapse,
  initialTabId,
}: RefinementSidebarProps) {
  const t = useTranslations("document");
  const [activeId, setActiveId] = useState<string>(initialTabId ?? tabs[0]?.id ?? "");
  const active = tabs.find((tab) => tab.id === activeId) ?? tabs[0];

  if (collapsed) {
    return (
      <aside
        className="w-12 flex flex-col items-center h-full surface-glass border-l border-outline-variant py-2 gap-2 flex-shrink-0 transition-[width] duration-200 ease-in-out overflow-hidden"
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
            className={`w-8 h-8 flex items-center justify-center rounded ${
              activeId === tab.id ? "bg-primary-container text-primary" : "hover:bg-surface-container text-on-surface-variant"
            }`}
            title={tab.label}
            data-testid={`sidebar-rail-${tab.id}`}
          >
            {tab.icon ?? tab.label.charAt(0)}
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
      {/* Navy status header */}
      <div className="bg-primary text-white px-5 py-4 flex-shrink-0" data-testid="sidebar-status-header">
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
            className={`flex-1 text-xs font-heading font-semibold py-3 px-2 transition-colors ${
              activeId === tab.id
                ? "text-primary border-b-2 border-gold"
                : "text-on-surface-variant hover:text-on-surface"
            }`}
            data-testid={`sidebar-tab-${tab.id}`}
          >
            {tab.label}
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
    </aside>
  );
}
