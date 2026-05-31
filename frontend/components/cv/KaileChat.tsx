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

// frontend/components/cv/KaileChat.tsx
"use client";

import { useState, type ChangeEvent } from "react";
import { useTranslations } from "next-intl";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

interface GapItem {
  id: string;
  label: string;
}

interface KaileChatProps {
  cvId: string;
  sectionId: string;
  gaps: GapItem[];
  preSelectedGapIds: string[];
  onApply: (suggestion: string) => void;
  onEditFirst: (suggestion: string) => void;
  onCancel: () => void;
}

export function KaileChat({
  cvId,
  sectionId,
  gaps,
  preSelectedGapIds,
  onApply,
  onEditFirst,
  onCancel,
}: KaileChatProps) {
  const t = useTranslations("cv");
  const [directions, setDirections] = useState("");
  const [selectedGaps, setSelectedGaps] = useState<Set<string>>(
    new Set(preSelectedGapIds),
  );
  const [loading, setLoading] = useState(false);
  const [suggestion, setSuggestion] = useState<string | null>(null);

  const toggleGap = (gapId: string) => {
    setSelectedGaps((prev) => {
      const next = new Set(prev);
      if (next.has(gapId)) {
        next.delete(gapId);
      } else {
        next.add(gapId);
      }
      return next;
    });
  };

  const handleSubmit = async () => {
    setLoading(true);
    setSuggestion(null);
    try {
      const res = await fetch(
        `${API_BASE}/api/cv/${cvId}/sections/${sectionId}/rewrite`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            directions,
            gap_ids: Array.from(selectedGaps),
          }),
        },
      );
      const data = await res.json();
      setSuggestion(data.suggestion);
    } finally {
      setLoading(false);
    }
  };

  if (suggestion) {
    return (
      <div className="border border-neutral-medium rounded-lg p-3 bg-white">
        <h4 className="text-sm font-medium text-neutral-dark mb-2">
          {t("kaileSuggestionHeading")}
        </h4>
        <p
          className="text-sm text-neutral-darker bg-neutral-light p-3 rounded whitespace-pre-wrap"
          data-testid="kaile-suggestion"
        >
          {suggestion}
        </p>
        <div className="flex gap-2 mt-3">
          <button
            type="button"
            onClick={() => onApply(suggestion)}
            className="text-xs bg-teal text-white px-3 py-1.5 rounded hover:opacity-90 transition-opacity"
            data-testid="apply-suggestion-btn"
          >
            {t("apply")}
          </button>
          <button
            type="button"
            onClick={() => onEditFirst(suggestion)}
            className="text-xs border border-teal text-teal px-3 py-1.5 rounded hover:bg-teal hover:text-white transition-colors"
            data-testid="edit-first-btn"
          >
            {t("editFirst")}
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="text-xs text-gray-500 underline"
            data-testid="discard-suggestion-btn"
          >
            {t("discardSuggestion")}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="border border-neutral-medium rounded-lg p-3 bg-white">
      <h4 className="text-sm font-medium text-neutral-dark mb-2">
        {t("kaileInstructionsHeading")}
      </h4>
      {gaps.length > 0 && (
        <>
          <p className="text-xs text-neutral-dark mb-2">
            {t("kaileGapsConsider")}
          </p>
          <div className="flex flex-wrap gap-1.5 mb-3">
            {gaps.map((gap) => (
              <button
                key={gap.id}
                type="button"
                onClick={() => toggleGap(gap.id)}
                data-selected={selectedGaps.has(gap.id) ? "true" : "false"}
                data-testid={`gap-chip-${gap.label}`}
                className="text-xs px-2 py-1 rounded border transition-colors data-[selected=true]:bg-teal data-[selected=true]:text-white data-[selected=true]:border-teal data-[selected=false]:border-neutral-medium text-neutral-darker"
              >
                {gap.label}
              </button>
            ))}
          </div>
        </>
      )}
      <textarea
        value={directions}
        onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
          setDirections(e.target.value)
        }
        placeholder={t("directionPlaceholder")}
        className="w-full text-sm border border-neutral-medium rounded p-2 resize-none focus:outline-none focus:ring-2 focus:ring-teal/50"
        rows={3}
        data-testid="kaile-directions-input"
      />
      <div className="flex gap-2 mt-3">
        <button
          type="button"
          onClick={handleSubmit}
          disabled={loading}
          data-testid="kaile-rewrite-btn"
          className="text-xs bg-teal text-white px-3 py-1.5 rounded hover:opacity-90 transition-opacity disabled:opacity-50"
        >
          {loading ? (
            <span data-testid="kaile-loading">{t("rewriting")}</span>
          ) : (
            <span className="flex items-center gap-1">
              {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
              <span aria-hidden="true">↻</span>
              {t("rewriteSection")}
            </span>
          )}
        </button>
      </div>
    </div>
  );
}
