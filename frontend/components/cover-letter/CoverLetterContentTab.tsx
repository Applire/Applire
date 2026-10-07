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


import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

interface LetterData {
  header?: { name?: string; address?: string; phone?: string; email?: string };
  recipient?: { name?: string; company?: string };
  body?: { paragraphs?: string[] };
  signature?: { closing?: string; name?: string };
}

interface CoverLetterContentTabProps {
  coverLetterId: string;
  letterData: LetterData | null;
  onSectionSaved: () => void;
  /**
   * ADR-090 cl. 5 — *Let me edit it* on a review finding opens the body editor
   * (the letter's only editable section), nothing pre-filled. A counter, so the
   * same finding can be opened twice.
   */
  openBodyNonce?: number;
  /**
   * D-2: the EFFECTIVE body (the saved override when there is one — see
   * `lib/letter-body.ts`). Without it the editor falls back to the generated
   * paragraphs, which after a take-out or a manual save are stale.
   */
  initialBody?: string;
  /**
   * #737 — whether the body holds text that is not saved, for the page's
   * unsaved-draft dialog (Finetuner Branch B).
   */
  onDirtyChange?: (dirty: boolean) => void;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

export function CoverLetterContentTab({
  coverLetterId,
  letterData,
  onSectionSaved,
  openBodyNonce,
  initialBody,
  onDirtyChange,
}: CoverLetterContentTabProps) {
  const t = useTranslations("coverLetter");
  const tc = useTranslations("common");
  const tEdit = useTranslations("editTab");
  // #737 (D2): the text Abbrechen returns to is the LAST SAVED body — the
  // effective one — never the generated paragraphs. Resetting to the generated
  // text let a later save write back a passage *Nimm es heraus* had removed.
  const [savedBody, setSavedBody] = useState(initialBody ?? letterData?.body?.paragraphs?.join("\n\n") ?? "");
  const [bodyText, setBodyText] = useState(savedBody);
  const [bodyEditing, setBodyEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const seenBodyNonce = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (openBodyNonce === undefined || seenBodyNonce.current === openBodyNonce) return;
    seenBodyNonce.current = openBodyNonce;
    setBodyEditing(true);
  }, [openBodyNonce]);

  const dirty = bodyText !== savedBody;
  const lastDirty = useRef(false);
  useEffect(() => {
    if (lastDirty.current === dirty) return;
    lastDirty.current = dirty;
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  async function handleSaveBody() {
    setSaving(true);
    setSaveError(null);
    try {
      const res = await fetch(`${API_BASE}/api/cover-letter/${coverLetterId}/section`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ section: "body", content: bodyText }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setSavedBody(bodyText);
      setBodyEditing(false);
      onSectionSaved();
    } catch {
      setSaveError(t("saveFailed"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-3 p-3">
      {/* #737: header, recipient and closing are not editable here — one
          line says where they come from instead of three read-only cards. */}
      <p className="rounded-lg bg-surface-container px-3 py-2 text-[12.5px] leading-snug text-on-surface" data-testid="cl-fixed-line">
        {tEdit("letterFixedLine")}
      </p>

      <div
        className={`rounded-xl border p-3 transition-colors ${
          bodyEditing ? "border-primary bg-primary-container" : "border-outline-variant bg-white"
        }`}
      >
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-on-surface-variant">
          {tEdit("letterBodyTitle")}
        </p>
        <textarea
          className="w-full min-h-[200px] resize-y rounded-lg border border-outline-variant bg-white p-2 text-sm leading-relaxed text-on-surface focus:outline-none focus:ring-2 focus:ring-primary"
          value={bodyText}
          onChange={(e) => {
            setBodyText(e.target.value);
            setBodyEditing(true);
          }}
          data-testid="cl-body-textarea"
        />
        {saveError && <p className="mt-1 text-xs text-critical">{saveError}</p>}
        {bodyEditing && (
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              onClick={handleSaveBody}
              disabled={saving}
              className="flex-1 rounded-lg bg-primary py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50"
              data-testid="cl-save-body-btn"
            >
              {saving ? tc("preparing") : tc("save")}
            </button>
            <button
              type="button"
              onClick={() => {
                setBodyText(savedBody);
                setBodyEditing(false);
                setSaveError(null);
              }}
              disabled={saving}
              data-testid="cl-cancel-body-btn"
              className="flex-1 rounded-lg border border-outline-variant py-2 text-sm font-semibold text-on-surface hover:bg-surface-container disabled:opacity-50"
            >
              {tc("cancel")}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
