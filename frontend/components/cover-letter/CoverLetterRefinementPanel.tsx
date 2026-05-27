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
import { useTranslations } from "next-intl";
import { CoverLetterContentTab } from "./CoverLetterContentTab";
import { CoverLetterDesignTab } from "./CoverLetterDesignTab";
import { CoverLetterActionsTab } from "./CoverLetterActionsTab";

type CLTemplate =
  | "classic_german"
  | "modern_swiss"
  | "executive"
  | "tech_developer"
  | "creative_sidebar"
  | "academic"
  | "compact_pro";

type TabId = "content" | "design" | "actions";

interface CoverLetterRefinementPanelProps {
  flowId: string;
  coverLetterId: string;
  letterData: Record<string, unknown> | null;
  currentTemplate: CLTemplate;
  onSectionSaved: () => void;
  onTemplateChange: (template: CLTemplate) => void;
  onRegenerateCoverLetter: () => void;
  onDownloadPdf: () => void;
  downloading: boolean;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

const TABS_META: { id: TabId; icon: string }[] = [
  { id: "content", icon: "✏️" },
  { id: "design", icon: "🎨" },
  { id: "actions", icon: "⚡" },
];

export function CoverLetterRefinementPanel({
  flowId,
  coverLetterId,
  letterData,
  currentTemplate,
  onSectionSaved,
  onTemplateChange,
  onRegenerateCoverLetter,
  onDownloadPdf,
  downloading,
  collapsed,
  onToggleCollapse,
}: CoverLetterRefinementPanelProps) {
  const t = useTranslations("coverLetter");
  const [activeTab, setActiveTab] = useState<TabId>("content");

  const TABS = TABS_META.map((m) => ({
    ...m,
    label: t(`${m.id}Tab` as "contentTab" | "designTab" | "actionsTab"),
  }));

  if (collapsed) {
    return (
      <div className="w-12 flex flex-col items-center bg-white border-l border-neutral-200 py-2 gap-2 flex-shrink-0 transition-[width] duration-200 ease-in-out overflow-hidden">
        <button
          type="button"
          onClick={onToggleCollapse}
          className="w-8 h-8 flex items-center justify-center rounded hover:bg-neutral-100 text-neutral-500 text-sm"
          title={t("contentTab")}
          data-testid="cl-panel-expand-btn"
        >
          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
          <span aria-hidden="true">❮</span>
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
              activeTab === tab.id
                ? "bg-blue-50 text-blue-600"
                : "hover:bg-neutral-100"
            }`}
            title={tab.label}
            data-testid={`cl-tab-icon-${tab.id}`}
          >
            {tab.icon}
          </button>
        ))}
      </div>
    );
  }

  return (
    <div className="w-[380px] flex flex-col h-full bg-white border-l border-neutral-200 flex-shrink-0 transition-[width] duration-200 ease-in-out">
      {/* Tab bar */}
      <div className="flex items-center border-b border-neutral-200 flex-shrink-0">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
              activeTab === tab.id
                ? "border-blue-600 text-blue-600"
                : "border-transparent text-neutral-500 hover:text-neutral-700"
            }`}
            data-testid={`cl-tab-${tab.id}`}
          >
            {tab.label}
          </button>
        ))}
        <button
          type="button"
          onClick={onToggleCollapse}
          className="ml-auto px-2 py-3 text-neutral-400 hover:text-neutral-600 text-sm"
          title={t("designTab")}
          data-testid="cl-panel-collapse-btn"
        >
          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
          <span aria-hidden="true">❯</span>
        </button>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto">
        {activeTab === "content" && (
          <CoverLetterContentTab
            coverLetterId={coverLetterId}
            letterData={letterData as Parameters<typeof CoverLetterContentTab>[0]["letterData"]}
            onSectionSaved={onSectionSaved}
          />
        )}
        {activeTab === "design" && (
          <CoverLetterDesignTab
            flowId={flowId}
            currentTemplate={currentTemplate}
            onTemplateChange={onTemplateChange}
          />
        )}
        {activeTab === "actions" && (
          <CoverLetterActionsTab
            onRegenerateCoverLetter={onRegenerateCoverLetter}
            onDownloadPdf={onDownloadPdf}
            downloading={downloading}
          />
        )}
      </div>
    </div>
  );
}
