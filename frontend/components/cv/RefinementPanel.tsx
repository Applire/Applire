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

"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { ContentTab } from "./ContentTab";
import { DesignTab } from "./DesignTab";
import { RefinementHeader } from "./RefinementHeader";

type Tab = "content" | "design";

interface RefinementPanelProps {
  cvId: string;
  flowId: string;
  roleTitle: string | null;
  gapSummary: {
    gaps: Array<{ id: string; label: string }>;
    sections: Array<{
      section_id: string;
      label: string;
      content: string;
      has_override: boolean;
      gaps: Array<{ id: string; label: string }>;
    }>;
  } | null;
  cvSummary: {
    sections: Array<{
      section_id: string;
      label: string;
      content: string;
      has_override: boolean;
      gaps: Array<{ id: string; label: string }>;
    }>;
  } | null;
  templateLabel: string | null;
  matchScore: number | null;
  expiryWarning: { level: "none" | "warning" | "critical"; expiresIn: string } | null;
  detectedCompany: { name: string; hex: string } | null;
  currentAccentHex: string;
  onHtmlRefresh: () => void;
  onRegenerateSame: () => void;
  onRegenerateDifferent: () => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

export function RefinementPanel({
  cvId,
  flowId,
  roleTitle,
  gapSummary,
  cvSummary,
  templateLabel,
  matchScore,
  expiryWarning,
  detectedCompany,
  currentAccentHex,
  onHtmlRefresh,
  onRegenerateSame,
  onRegenerateDifferent,
  collapsed,
  onToggleCollapse,
}: RefinementPanelProps) {
  const t = useTranslations("cv");
  const [activeTab, setActiveTab] = useState<Tab>("content");

  const TABS: { id: Tab; label: string; icon: string }[] = [
    { id: "content", label: t("contentTab"), icon: "📝" },
    { id: "design", label: t("designTab"), icon: "🎨" },
  ];

  if (collapsed) {
    return (
      <div
        className="w-12 flex flex-col items-center h-[calc(100vh-56px)] surface-glass border-l border-outline-variant py-2 gap-2 flex-shrink-0 transition-[width] duration-200 ease-in-out overflow-hidden"
        data-testid="refinement-panel"
      >
        <button
          type="button"
          onClick={onToggleCollapse}
          className="w-8 h-8 flex items-center justify-center rounded hover:bg-surface-container text-on-surface-variant text-sm"
          title="Panel öffnen"
          data-testid="cv-panel-expand-btn"
        >
          ❮
        </button>
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => {
              setActiveTab(tab.id);
              onToggleCollapse();
            }}
            className={`w-8 h-8 flex items-center justify-center rounded text-base ${
              activeTab === tab.id ? "bg-primary-container text-primary" : "hover:bg-surface-container"
            }`}
            title={tab.label}
            data-testid={`cv-tab-icon-${tab.id}`}
          >
            {tab.icon}
          </button>
        ))}
      </div>
    );
  }

  const flowSummary = {
    job_summary: roleTitle,
    gap_summary: gapSummary,
    cv_summary: cvSummary,
  };

  return (
    <div
      className="w-[400px] h-[calc(100vh-56px)] overflow-y-auto border-l border-outline-variant surface-glass flex flex-col flex-shrink-0 transition-[width] duration-200 ease-in-out"
      data-testid="refinement-panel"
    >
      <RefinementHeader roleTitle={roleTitle} matchScore={matchScore} expiryWarning={expiryWarning} />

      {/* Tab strip */}
      <div className="flex items-center border-b border-outline-variant shrink-0" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setActiveTab(tab.id)}
            className={`flex-1 text-xs font-heading font-semibold uppercase tracking-wider py-2.5 px-3 transition-colors ${
              activeTab === tab.id
                ? "text-primary border-b-2 border-gold"
                : "text-on-surface-variant hover:text-on-surface"
            }`}
            role="tab"
            aria-selected={activeTab === tab.id}
            id={`tab-${tab.id}`}
            aria-controls={`tabpanel-${tab.id}`}
            data-testid={`tab-${tab.id}`}
          >
            <span className="mr-1">{tab.icon}</span>
            {tab.label}
          </button>
        ))}
        <button
          type="button"
          onClick={onToggleCollapse}
          className="px-2 py-2.5 text-on-surface-variant hover:text-on-surface text-sm shrink-0"
          title="Panel einklappen"
          data-testid="cv-panel-collapse-btn"
        >
          ❯
        </button>
      </div>

      {/* Active tab content */}
      <div
        className="flex-1 overflow-y-auto"
        role="tabpanel"
        aria-labelledby={`tab-${activeTab}`}
        id={`tabpanel-${activeTab}`}
      >
        {activeTab === "content" ? (
          <ContentTab
            cvId={cvId}
            flowSummary={flowSummary}
            onSectionSave={() => onHtmlRefresh()}
            onUnsavedChange={() => {}}
          />
        ) : (
          <DesignTab
            cvId={cvId}
            templateLabel={templateLabel}
            detectedCompany={detectedCompany}
            currentAccentHex={currentAccentHex}
            onColorApplied={onHtmlRefresh}
            onChangeTemplate={onRegenerateDifferent}
            onRegenerateSame={onRegenerateSame}
          />
        )}
      </div>
    </div>
  );
}
