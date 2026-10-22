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


import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { PhotoManager } from "@/components/profile/PhotoManager";
import { dismissExplainer, getSettings } from "@/lib/api/settings";

/**
 * #789 (Hazelnut build 3): "no photo" is remembered per user, server-side, as
 * the `cv_photo_prompt` explainer id (the #679 dismissal store — no new
 * field). Once chosen, the step is skipped on every later CV; a photo can be
 * added on My Profile at any time.
 */
export const PHOTO_PROMPT_EXPLAINER_ID = "cv_photo_prompt";

// Non-user-facing Material Symbols identifiers — JS consts, not JSX literals.
const UPLOAD_ICON = "add_a_photo";
const SKIP_ICON = "arrow_forward";

interface PhotoPromptStepProps {
  onContinue: () => void;
  currentPhotoUrl?: string | null;
  onPhotoChange?: (photoUrl: string | null) => void;
}

export function PhotoPromptStep({ onContinue, currentPhotoUrl, onPhotoChange }: PhotoPromptStepProps) {
  const t = useTranslations("cv");
  const [showUpload, setShowUpload] = useState(false);
  const [photoAdded, setPhotoAdded] = useState(false);
  // null = still reading the remembered choice (render nothing — no flash of
  // a question the user already answered).
  const [remembered, setRemembered] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    getSettings()
      .then((s) => {
        if (cancelled) return;
        const skip = (s.dismissed_explainers ?? []).includes(PHOTO_PROMPT_EXPLAINER_ID);
        setRemembered(skip);
        if (skip) onContinue();
      })
      .catch(() => {
        // A settings failure asks again — never a gate.
        if (!cancelled) setRemembered(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per mount
  }, []);

  function skipForGood() {
    void dismissExplainer(PHOTO_PROMPT_EXPLAINER_ID);
    onContinue();
  }

  if (remembered !== false) return null;

  if (showUpload) {
    return (
      <div className="max-w-sm mx-auto space-y-4">
        <p className="text-sm text-gray-500 text-center">
          {t("photoStepProgressPhoto")}
        </p>
        <PhotoManager
          currentPhotoUrl={currentPhotoUrl}
          onPhotoChange={(url) => {
            setPhotoAdded(!!url);
            onPhotoChange?.(url);
          }}
        />
        {photoAdded ? (
          <Button className="w-full" onClick={onContinue} data-testid="photo-continue">
            {t("photoUploadedContinue")}
          </Button>
        ) : (
          <Button variant="outline" className="w-full" onClick={skipForGood} data-testid="photo-upload-skip">
            {t("skipAndContinue")}
          </Button>
        )}
      </div>
    );
  }

  return (
    <div className="max-w-sm mx-auto space-y-4">
      <p className="text-sm text-gray-500 text-center">{t("photoStepProgress")}</p>
      <h2 className="text-lg font-semibold text-gray-900 text-center">
        {t("photoStepTitle")}
      </h2>
      <p className="text-sm text-gray-600 text-center">
        {t("photoStepHint")}
      </p>
      <div className="grid grid-cols-2 gap-3">
        <button
          data-testid="photo-prompt-upload"
          className="border-2 border-primary bg-primary text-white rounded-lg p-4 text-center hover:opacity-90 transition-opacity"
          onClick={() => setShowUpload(true)}
        >
          <span className="material-symbols-outlined block mb-1" aria-hidden="true" style={{ fontSize: 24 }}>
            {UPLOAD_ICON}
          </span>
          <p className="text-xs font-semibold">{t("uploadPhoto")}</p>
          <p className="text-xs text-white/80 mt-0.5">{t("uploadPhotoSaved")}</p>
        </button>
        <button
          data-testid="photo-prompt-skip"
          className="border border-outline-variant bg-white rounded-lg p-4 text-center hover:bg-surface-container transition-colors"
          onClick={skipForGood}
        >
          <span className="material-symbols-outlined block mb-1 text-on-surface-variant" aria-hidden="true" style={{ fontSize: 24 }}>
            {SKIP_ICON}
          </span>
          <p className="text-xs font-medium text-on-surface">{t("skipPhotoNow")}</p>
          <p className="text-xs text-on-surface-variant mt-0.5">{t("skipPhotoHint")}</p>
        </button>
      </div>
      <p className="text-xs text-gray-400 text-center">
        {t("photoLaterHint")}
      </p>
    </div>
  );
}
