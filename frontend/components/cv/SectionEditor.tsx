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

// frontend/components/cv/SectionEditor.tsx
"use client";

import { useState, useEffect, useRef, forwardRef, useImperativeHandle } from "react";
import { useTranslations } from "next-intl";
import { GapHint } from "./GapHint";
import { SaveScopePrompt } from "./SaveScopePrompt";

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

interface SectionEditorProps {
  cvId: string;
  section: SectionItem;
  onSaved: (updatedHtml: string, savedContent: string, resolvedGaps: string[]) => void;
  onUnsavedChange: (hasUnsaved: boolean) => void;
  onAddressGap?: (gapId: string) => void;
}

/** Imperative handle so a sibling (KaileChat) can push a suggestion into the editor. */
export interface SectionEditorHandle {
  /**
   * Load `text` into the textarea. When `autoSave` is true (the "Apply" action)
   * it saves immediately through the usual scope prompt; when false ("Edit
   * first") it leaves the text unsaved and focuses the field so the user can
   * tweak it before saving. (ADR-040-adjacent; completes the Task 11 TODO.)
   */
  injectSuggestion: (text: string, autoSave: boolean) => void;
}

export const SectionEditor = forwardRef<SectionEditorHandle, SectionEditorProps>(
  function SectionEditor({ cvId, section, onSaved, onUnsavedChange, onAddressGap }, ref) {
  const t = useTranslations("cv");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [content, setContent] = useState(section.content);
  const [savedContent, setSavedContent] = useState(section.content);
  const [visibleGaps, setVisibleGaps] = useState<GapHintItem[]>(section.gaps);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [showPreviewStale, setShowPreviewStale] = useState(false);
  const [showScopePrompt, setShowScopePrompt] = useState(false);

  // Reset when section changes
  useEffect(() => {
    setContent(section.content);
    setSavedContent(section.content);
    setVisibleGaps(section.gaps);
    setSaveError(null);
    setShowPreviewStale(false);
    onUnsavedChange(false);
  }, [section.section_id]);

  function handleContentChange(value: string) {
    setContent(value);
    onUnsavedChange(value !== savedContent);
  }

  function handleCancel() {
    setContent(savedContent);
    setSaveError(null);
    onUnsavedChange(false);
  }

  function handleSaveClick() {
    const remembered = sessionStorage.getItem("finetune_save_scope");
    if (remembered !== null) {
      void executeSave(remembered === "profile");
    } else {
      setShowScopePrompt(true);
    }
  }

  // Push a Kaile suggestion into the editor (US089 handoff / Task 11). Apply →
  // save straight away (honouring any remembered scope, else prompt); Edit first
  // → load unsaved and focus for tweaking. Explicit `contentToSave` avoids the
  // stale-closure trap on the synchronous remembered-scope save.
  function injectSuggestion(text: string, autoSave: boolean) {
    setContent(text);
    onUnsavedChange(text !== savedContent);
    setSaveError(null);
    if (autoSave) {
      const remembered = sessionStorage.getItem("finetune_save_scope");
      if (remembered !== null) {
        void executeSave(remembered === "profile", text);
      } else {
        setShowScopePrompt(true);
      }
    } else {
      textareaRef.current?.focus();
    }
  }

  useImperativeHandle(ref, () => ({ injectSuggestion }));

  async function executeSave(saveToProfile: boolean, contentToSave: string = content) {
    setShowScopePrompt(false);
    setSaving(true);
    setSaveError(null);
    setShowPreviewStale(false);

    try {
      const res = await fetch(
        `${API_BASE}/api/cv/${cvId}/sections/${section.section_id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: contentToSave, save_to_profile: saveToProfile }),
        }
      );

      if (!res.ok) {
        throw new Error(`Save failed: ${res.status}`);
      }

      const data: { html: string; overrides_applied: string[]; resolved_gaps: string[] } =
        await res.json();
      setSavedContent(contentToSave);
      onUnsavedChange(false);
      // Remove resolved gaps from the visible list
      if (data.resolved_gaps?.length) {
        const resolvedSet = new Set(data.resolved_gaps);
        setVisibleGaps((prev) => prev.filter((g) => !resolvedSet.has(g.id)));
      }
      onSaved(data.html, contentToSave, data.resolved_gaps ?? []);
    } catch {
      setSaveError("Speichern fehlgeschlagen. Bitte erneut versuchen.");
      setShowPreviewStale(true);
    } finally {
      setSaving(false);
    }
  }

  function handleDismissGap(gapId: string) {
    setVisibleGaps((prev) => prev.filter((g) => g.id !== gapId));
  }

  const hasUnsaved = content !== savedContent;

  return (
    <div className="p-3 flex flex-col gap-2">
      {showScopePrompt && (
        <SaveScopePrompt
          onConfirm={(saveToProfile) => void executeSave(saveToProfile)}
          onCancel={() => setShowScopePrompt(false)}
        />
      )}

      <p className="text-xs font-semibold text-neutral-dark">{section.label}</p>

      <textarea
        ref={textareaRef}
        value={content}
        onChange={(e) => handleContentChange(e.target.value)}
        data-testid="section-textarea"
        placeholder={t("placeholder")}
        className="w-full min-h-[180px] resize-y text-sm font-mono border border-gray-200 rounded-lg p-2 focus:outline-none focus:ring-2 focus:ring-teal"
      />

      {saveError && (
        <p className="text-xs text-critical">{saveError}</p>
      )}

      {showPreviewStale && (
        <p className="text-xs text-warning">{t("previewStale")}</p>
      )}

      <div className="flex gap-2">
        <button
          type="button"
          onClick={handleSaveClick}
          disabled={saving || !hasUnsaved}
          data-testid="section-save"
          className="flex-1 bg-teal text-white font-semibold py-2 rounded-lg text-sm hover:opacity-90 disabled:opacity-40 transition-opacity"
        >
          {saving ? t("saving") : t("save")}
        </button>
        <button
          type="button"
          onClick={handleCancel}
          disabled={saving || !hasUnsaved}
          data-testid="section-cancel"
          className="flex-1 border border-gray-300 text-gray-600 font-semibold py-2 rounded-lg text-sm hover:opacity-90 disabled:opacity-40 transition-opacity"
        >
          {t("cancel")}
        </button>
      </div>

      {visibleGaps.length > 0 && (
        <div className="mt-1">
          <p className="text-xs text-gray-500 mb-1">{t("gapHints")}</p>
          {visibleGaps.map((gap) => (
            <GapHint
              key={gap.id}
              gap={gap}
              onDismiss={handleDismissGap}
              onAddressGap={onAddressGap ?? (() => {})}
            />
          ))}
        </div>
      )}
    </div>
  );
});
