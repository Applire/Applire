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

// frontend/components/cv/FineTunePanel.tsx
"use client";

import { useState, useEffect } from "react";
import { useTranslations } from "next-intl";
import { SectionEditor } from "./SectionEditor";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

interface GapHintItem {
  id: string;
  label: string;
}

interface SectionItem {
  section_id: string;
  label: string;
  content: string;
  has_override: boolean;
  gaps: GapHintItem[];
}

interface CVSectionsResponse {
  sections: SectionItem[];
  general_gaps: GapHintItem[];
}

interface FineTunePanelProps {
  cvId: string;
  initialHtml: string | null;
  onClose: () => void;
  onUnsavedChange?: (hasUnsaved: boolean) => void;
}

export function FineTunePanel({ cvId, initialHtml, onClose, onUnsavedChange }: FineTunePanelProps) {
  const t = useTranslations("cv");
  const tUnsaved = useTranslations("unsavedChanges");
  const [sections, setSections] = useState<SectionItem[]>([]);
  const [generalGaps, setGeneralGaps] = useState<GapHintItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [activeSection, setActiveSection] = useState<SectionItem | null>(null);
  const [htmlContent, setHtmlContent] = useState<string | null>(initialHtml);
  const [pendingSection, setPendingSection] = useState<SectionItem | null>(null);
  const [showDiscardDialog, setShowDiscardDialog] = useState(false);
  const [hasUnsaved, setHasUnsaved] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [openAccordionId, setOpenAccordionId] = useState<string | null>(null);

  useEffect(() => {
    void loadSections();
  }, [cvId]);

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 768px)");
    setIsMobile(mq.matches);
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  function updateHasUnsaved(value: boolean) {
    setHasUnsaved(value);
    onUnsavedChange?.(value);
  }

  async function loadSections() {
    setLoading(true);
    setError(false);
    try {
      const res = await fetch(`${API_BASE}/api/cv/${cvId}/sections`);
      if (!res.ok) throw new Error("Failed");
      const data: CVSectionsResponse = await res.json();
      setSections(data.sections);
      setGeneralGaps(data.general_gaps);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }

  function requestSectionSwitch(section: SectionItem) {
    if (hasUnsaved) {
      setPendingSection(section);
      setShowDiscardDialog(true);
    } else {
      setActiveSection(section);
    }
  }

  function handleDiscard() {
    setShowDiscardDialog(false);
    updateHasUnsaved(false);
    if (pendingSection === null) {
      onClose();
    } else {
      setActiveSection(pendingSection);
      setPendingSection(null);
    }
  }

  function handleKeepEditing() {
    setShowDiscardDialog(false);
    setPendingSection(null);
  }

  function handleSaved(updatedHtml: string, savedContent: string, resolvedGaps: string[]) {
    setHtmlContent(updatedHtml);
    updateHasUnsaved(false);
    setSections((prev) =>
      prev.map((s) => {
        const updated =
          s.section_id === activeSection?.section_id
            ? { ...s, content: savedContent, has_override: true }
            : s;
        if (resolvedGaps.length > 0) {
          const resolvedSet = new Set(resolvedGaps);
          return { ...updated, gaps: updated.gaps.filter((g) => !resolvedSet.has(g.id)) };
        }
        return updated;
      })
    );
  }

  function handleCloseRequest() {
    if (hasUnsaved) {
      setPendingSection(null);
      setShowDiscardDialog(true);
    } else {
      onClose();
    }
  }

  const allGapsClosed =
    sections.length > 0 && sections.every((s) => s.gaps.length === 0);

  return (
    <div className="flex-1 flex h-[75vh]">
      {/* Unsaved changes dialog (shared) */}
      {showDiscardDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-white rounded-xl p-6 shadow-xl max-w-sm w-full mx-4">
            <p className="text-sm font-semibold text-neutral-dark mb-4">
              {tUnsaved("title")}
            </p>
            <div className="flex gap-3">
              <button
                type="button"
                onClick={handleDiscard}
                className="flex-1 bg-critical text-white font-semibold py-2 rounded-lg text-sm hover:opacity-90"
                data-testid="discard-confirm"
              >
                {tUnsaved("discard")}
              </button>
              <button
                type="button"
                onClick={handleKeepEditing}
                className="flex-1 border border-teal text-teal font-semibold py-2 rounded-lg text-sm hover:opacity-90"
                data-testid="keep-editing"
              >
                {tUnsaved("keepEditing")}
              </button>
            </div>
          </div>
        </div>
      )}

      {isMobile ? (
        /* Mobile accordion layout */
        <div className="w-full flex flex-col gap-3 overflow-y-auto" data-testid="mobile-accordion">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 bg-neutral-light rounded-xl">
            <span className="text-sm font-bold text-neutral-dark">
              {allGapsClosed ? (
                <span className="text-success" data-testid="all-gaps-closed">
                  {t("allGapsClosedLabel")}
                </span>
              ) : (
                t("editSections")
              )}
            </span>
            <button
              type="button"
              onClick={handleCloseRequest}
              className="text-xs text-gray-500 hover:text-gray-700"
            >
              {t("closeSections")}
            </button>
          </div>

          {/* Mini CV preview */}
          <div className="max-h-[35vh] overflow-y-auto rounded-xl bg-white shadow-soft">
            {htmlContent ? (
              <iframe
                srcDoc={htmlContent}
                sandbox="allow-same-origin"
                title={t("iframeTitle")}
                className="w-full h-[35vh] border-0"
              />
            ) : (
              <div className="w-full h-[35vh] animate-pulse bg-gray-100 rounded-xl" />
            )}
          </div>

          {/* Accordion sections */}
          {loading && (
            <div className="space-y-2">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-12 rounded-lg bg-gray-200 animate-pulse" data-testid="section-skeleton" />
              ))}
            </div>
          )}

          {error && !loading && (
            <div className="p-4 text-center">
              <p className="text-sm text-gray-500 mb-2">{t("sectionsLoadFailed")}</p>
              <button type="button" onClick={() => void loadSections()} className="text-sm text-teal underline">
                {t("retryLoadSections")}
              </button>
            </div>
          )}

          {!loading && !error && sections.map((section) => {
            const isOpen = openAccordionId === section.section_id;
            return (
              <div
                key={section.section_id}
                data-testid="accordion-section"
                className="bg-neutral-light rounded-xl overflow-hidden"
              >
                <button
                  type="button"
                  onClick={() => {
                    if (hasUnsaved && !isOpen) {
                      setPendingSection(section);
                      setShowDiscardDialog(true);
                    } else {
                      const nextId = isOpen ? null : section.section_id;
                      setOpenAccordionId(nextId);
                      if (nextId) setActiveSection(section);
                    }
                  }}
                  className="w-full flex items-center justify-between px-3 py-3 text-left"
                  data-testid="section-list-item"
                >
                  <span className="text-sm font-medium text-neutral-dark truncate">
                    {section.label}
                  </span>
                  <div className="flex items-center gap-2 ml-2 shrink-0">
                    {section.gaps.length > 0 ? (
                      <span
                        data-testid="gap-badge"
                        className="bg-warning text-white text-xs font-bold px-1.5 py-0.5 rounded-full"
                      >
                        {section.gaps.length}
                      </span>
                    ) : (
                      /* eslint-disable-next-line formatjs/no-literal-string-in-jsx */
                      <span className="text-success text-xs" aria-hidden="true">✓</span>
                    )}
                    {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- triangle symbols for accordion toggle */}
                    <span className="text-gray-400 text-xs">{isOpen ? "▲" : "▼"}</span>
                  </div>
                </button>

                {isOpen && (
                  <div className="border-t border-gray-200">
                    <SectionEditor
                      cvId={cvId}
                      section={section}
                      onSaved={handleSaved}
                      onUnsavedChange={updateHasUnsaved}
                    />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        /* Desktop split-screen layout */
        <div className="flex flex-row gap-4 w-full">
          {/* Left: editor panel (42%) */}
          <div className="w-[42%] flex flex-col bg-neutral-light rounded-xl overflow-hidden">
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
              <span className="text-sm font-bold text-neutral-dark">
                {allGapsClosed ? (
                  <span className="text-success" data-testid="all-gaps-closed">
                    {t("allGapsClosedLabel")}
                  </span>
                ) : (
                  t("editSections")
                )}
              </span>
              <button
                type="button"
                onClick={handleCloseRequest}
                className="text-xs text-gray-500 hover:text-gray-700"
              >
                {t("closeSections")}
              </button>
            </div>

            {/* Section list */}
            <div className="overflow-y-auto flex-1 p-2">
              {loading && (
                <div className="space-y-2 p-2">
                  {[1, 2, 3].map((i) => (
                    <div
                      key={i}
                      className="h-12 rounded-lg bg-gray-200 animate-pulse"
                      data-testid="section-skeleton"
                    />
                  ))}
                </div>
              )}

              {error && !loading && (
                <div className="p-4 text-center">
                  <p className="text-sm text-gray-500 mb-2">
                    {t("sectionsLoadFailed")}
                  </p>
                  <button
                    type="button"
                    onClick={() => void loadSections()}
                    className="text-sm text-teal underline hover:opacity-80"
                  >
                    {t("retryLoadSections")}
                  </button>
                </div>
              )}

              {!loading &&
                !error &&
                sections.map((section) => (
                  <button
                    key={section.section_id}
                    type="button"
                    onClick={() => requestSectionSwitch(section)}
                    data-testid="section-list-item"
                    className={`w-full flex items-center justify-between px-3 py-2.5 rounded-lg mb-1 text-left transition-colors ${
                      activeSection?.section_id === section.section_id
                        ? "bg-teal text-white"
                        : "hover:bg-gray-100 text-neutral-dark"
                    }`}
                  >
                    <span className="text-sm font-medium truncate">{section.label}</span>
                    <span className="ml-2 shrink-0">
                      {section.gaps.length > 0 ? (
                        <span
                          data-testid="gap-badge"
                          className="bg-warning text-white text-xs font-bold px-1.5 py-0.5 rounded-full"
                        >
                          {section.gaps.length}
                        </span>
                      ) : (
                        /* eslint-disable-next-line formatjs/no-literal-string-in-jsx */
                        <span className="text-success text-xs" aria-hidden="true">✓</span>
                      )}
                    </span>
                  </button>
                ))}
            </div>

            {/* Section editor */}
            {activeSection && !loading && (
              <div className="border-t border-gray-200">
                <SectionEditor
                  cvId={cvId}
                  section={activeSection}
                  onSaved={handleSaved}
                  onUnsavedChange={updateHasUnsaved}
                />
              </div>
            )}
          </div>

          {/* Right: CV preview iframe (58%) */}
          <div className="flex-1 bg-white rounded-xl shadow-soft overflow-hidden relative">
            {htmlContent ? (
              <iframe
                srcDoc={htmlContent}
                sandbox="allow-same-origin"
                title={t("iframeTitle")}
                className="w-full h-full border-0"
                data-testid="finetune-preview-iframe"
              />
            ) : (
              <div className="w-full h-full animate-pulse bg-gray-100 rounded" />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
