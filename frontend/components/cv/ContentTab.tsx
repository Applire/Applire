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

// frontend/components/cv/ContentTab.tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { SectionEditor } from "./SectionEditor";
import { KaileChat } from "./KaileChat";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

export interface GapHintItem {
  id: string;
  label: string;
}

export interface SectionItem {
  section_id: string;
  label: string;
  content: string;
  has_override: boolean;
  gaps: GapHintItem[];
}

interface FlowStateSummary {
  job_summary: string | null;
  gap_summary: {
    gaps: GapHintItem[];
    sections: SectionItem[];
  } | null;
  cv_summary: { sections: SectionItem[] } | null;
}

interface ContentTabProps {
  cvId: string;
  flowSummary: FlowStateSummary | null;
  onSectionSave: (updatedHtml: string) => void;
  onUnsavedChange: (hasUnsaved: boolean) => void;
}

/**
 * Drop gaps with a duplicate id. The backend maps each gap to a single section,
 * but this guards the aggregate count and the React keys against any duplicate
 * slipping through (bug 4: "16 Lücken gefunden" with repeated chips).
 */
function dedupeById(items: GapHintItem[]): GapHintItem[] {
  const seen = new Set<string>();
  return items.filter((g) => {
    if (seen.has(g.id)) return false;
    seen.add(g.id);
    return true;
  });
}

export function ContentTab({ cvId, flowSummary, onSectionSave, onUnsavedChange }: ContentTabProps) {
  const t = useTranslations("cv");
  const tUnsaved = useTranslations("unsavedChanges");
  const [mode, setMode] = useState<"browse" | "edit">("browse");
  const [activeSectionId, setActiveSectionId] = useState<string | null>(null);
  const [preSelectedGapIds, setPreSelectedGapIds] = useState<string[]>([]);
  const [hasUnsaved, setHasUnsaved] = useState(false);
  const [sections, setSections] = useState<SectionItem[]>([]);
  const [generalGaps, setGeneralGaps] = useState<GapHintItem[]>([]);
  const [sectionsLoading, setSectionsLoading] = useState(true);
  const [sectionsError, setSectionsError] = useState(false);

  // Fetch sections from the CV sections endpoint
  useEffect(() => {
    if (!cvId) return;
    setSectionsLoading(true);
    setSectionsError(false);
    fetch(`${API_BASE}/api/cv/${cvId}/sections`)
      .then((res) => {
        if (!res.ok) throw new Error("Failed");
        return res.json() as Promise<{ sections: SectionItem[]; general_gaps: GapHintItem[] }>;
      })
      .then((data) => {
        setSections(data.sections);
        setGeneralGaps(data.general_gaps);
      })
      .catch(() => setSectionsError(true))
      .finally(() => setSectionsLoading(false));
  }, [cvId]);

  // Warn before page unload when in edit mode with unsaved changes
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (hasUnsaved && mode === "edit") {
        e.preventDefault();
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [hasUnsaved, mode]);

  // All gaps = section gaps flattened + general gaps, deduped by id
  const sectionGaps: GapHintItem[] = dedupeById(sections.flatMap((s) => s.gaps));
  const allGaps: GapHintItem[] = dedupeById([...sectionGaps, ...generalGaps]);

  const handleBrowseToEdit = useCallback(
    (sectionId: string, preselectedGaps: string[] = []) => {
      if (hasUnsaved) {
        if (!confirm(tUnsaved("title"))) {
          return;
        }
      }
      setActiveSectionId(sectionId);
      setPreSelectedGapIds(preselectedGaps);
      setMode("edit");
      setHasUnsaved(false);
    },
    [hasUnsaved],
  );

  const handleBackToBrowse = useCallback(() => {
    if (hasUnsaved) {
      if (!confirm(tUnsaved("title"))) {
        return;
      }
    }
    setMode("browse");
    setActiveSectionId(null);
    setPreSelectedGapIds([]);
    setHasUnsaved(false);
  }, [hasUnsaved]);

  const handleAddressGap = useCallback(
    (gapId: string) => {
      const owner = sections.find((s) => s.gaps.some((g) => g.id === gapId));
      if (owner) {
        handleBrowseToEdit(owner.section_id, [gapId]);
      }
    },
    [sections, handleBrowseToEdit],
  );

  const handleSectionEdit = useCallback(
    (sectionId: string) => {
      handleBrowseToEdit(sectionId, []);
    },
    [handleBrowseToEdit],
  );

  const activeSection = sections.find((s) => s.section_id === activeSectionId) ?? null;

  if (mode === "edit" && activeSection) {
    return (
      <div className="flex flex-col gap-4 p-3">
        <button
          type="button"
          onClick={handleBackToBrowse}
          className="text-xs text-teal underline hover:opacity-80 self-start"
          data-testid="back-to-browse"
        >
          {t("backToOverview")}
        </button>

        <h3 className="text-sm font-medium text-neutral-dark">
          {activeSection.label}
        </h3>

        <SectionEditor
          cvId={cvId}
          section={activeSection}
          onSaved={(html, savedContent, resolvedGaps) => {
            onSectionSave(html);
            setHasUnsaved(false);
            setSections((prev) =>
              prev.map((s) => {
                if (s.section_id !== activeSectionId) return s;
                const updated = { ...s, content: savedContent, has_override: true };
                if (resolvedGaps.length > 0) {
                  const resolvedSet = new Set(resolvedGaps);
                  return { ...updated, gaps: updated.gaps.filter((g) => !resolvedSet.has(g.id)) };
                }
                return updated;
              })
            );
          }}
          onUnsavedChange={(unsaved) => {
            setHasUnsaved(unsaved);
            onUnsavedChange(unsaved);
          }}
          onAddressGap={handleAddressGap}
        />

        <KaileChat
          cvId={cvId}
          sectionId={activeSection.section_id}
          gaps={activeSection.gaps}
          preSelectedGapIds={preSelectedGapIds}
          onApply={() => {
            // TODO (Task 11): wire suggestion into SectionEditor textarea via shared state
          }}
          onEditFirst={() => {
            // TODO (Task 11): wire suggestion into SectionEditor textarea and focus
          }}
          onCancel={() => {}}
        />
      </div>
    );
  }

  // Browse mode
  const pluralGaps = allGaps.length !== 1;

  if (sectionsLoading) {
    return (
      <div className="flex flex-col gap-2 p-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-10 rounded-lg bg-gray-200 animate-pulse" />
        ))}
      </div>
    );
  }

  if (sectionsError) {
    return (
      <div className="p-4 text-center">
        <p className="text-sm text-gray-500 mb-2">{t("sectionsLoadError")}</p>
        <button
          type="button"
          onClick={() => {
            setSectionsError(false);
            setSectionsLoading(true);
            fetch(`${API_BASE}/api/cv/${cvId}/sections`)
              .then((res) => {
                if (!res.ok) throw new Error("Failed");
                return res.json() as Promise<{ sections: SectionItem[]; general_gaps: GapHintItem[] }>;
              })
              .then((data) => {
                setSections(data.sections);
                setGeneralGaps(data.general_gaps);
              })
              .catch(() => setSectionsError(true))
              .finally(() => setSectionsLoading(false));
          }}
          className="text-sm text-teal underline hover:opacity-80"
        >
          {t("retryLoad")}
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 p-3">
      {allGaps.length > 0 && (
        <>
          <div className="flex items-center gap-2">
            {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
            <span className="text-lg" aria-hidden="true">🤖</span>
            <p className="text-sm text-neutral-dark">
              {t("gapsFoundFor", {
                count: allGaps.length,
                label: pluralGaps ? t("gapsFoundPlural") : t("gapsFoundSingular"),
                role: flowSummary?.job_summary ?? "",
              })}
            </p>
          </div>
          <div className="flex flex-col gap-2">
            {sectionGaps.map((gap) => (
              <button
                key={gap.id}
                type="button"
                onClick={() => handleAddressGap(gap.id)}
                className="ai-card text-left text-sm cursor-pointer hover:shadow-card transition-shadow"
                data-testid="gap-card"
              >
                <span className="text-xs text-neutral-medium font-medium">{gap.label}</span>
              </button>
            ))}
            {generalGaps.length > 0 && (
              <>
                <p className="text-xs font-semibold text-neutral-dark mt-1">{t("generalGaps")}</p>
                {generalGaps.map((gap) => (
                  <button
                    key={gap.id}
                    type="button"
                    onClick={() => handleAddressGap(gap.id)}
                    className="ai-card text-left text-sm cursor-pointer hover:shadow-card transition-shadow"
                    data-testid="gap-card"
                  >
                    <span className="text-xs text-neutral-medium font-medium">{gap.label}</span>
                  </button>
                ))}
              </>
            )}
          </div>
          <hr className="border-neutral-medium" />
        </>
      )}

      <h4 className="text-xs font-semibold text-neutral-dark uppercase tracking-wide">
        {t("sectionsEdit")}
      </h4>
      {sections.length === 0 && (
        <p className="text-xs text-gray-500">
          {t("noSections")}
        </p>
      )}
      <div className="flex flex-col gap-1.5">
        {sections.map((section) => (
          <button
            key={section.section_id}
            type="button"
            onClick={() => handleSectionEdit(section.section_id)}
            className="text-left text-sm flex items-center justify-between border-l-2 border-transparent rounded-r-lg px-3 py-2 hover:border-gold hover:bg-surface-container transition-colors"
          >
            <span className="text-neutral-darker">{section.label}</span>
            {section.gaps.length > 0 ? (
              <span className="text-xs bg-gold-container text-gold-dim px-1.5 py-0.5 rounded-full">
                {section.gaps.length}
              </span>
            ) : (
              /* eslint-disable-next-line formatjs/no-literal-string-in-jsx */
              <span className="text-xs text-success" aria-hidden="true">✓</span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
