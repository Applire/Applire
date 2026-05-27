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
import { Button } from "@/components/ui/button";
import { PhotoManager } from "@/components/profile/PhotoManager";

interface PhotoPromptStepProps {
  onContinue: () => void;
  currentPhotoUrl?: string | null;
  onPhotoChange?: (photoUrl: string | null) => void;
}

export function PhotoPromptStep({ onContinue, currentPhotoUrl, onPhotoChange }: PhotoPromptStepProps) {
  const t = useTranslations("cv");
  const [showUpload, setShowUpload] = useState(false);
  const [photoAdded, setPhotoAdded] = useState(false);

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
        <Button className="w-full" onClick={onContinue}>
          {photoAdded ? t("photoUploadedContinue") : t("skipAndContinue")}
        </Button>
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
          className="border-2 border-blue-600 bg-blue-50 rounded-lg p-4 text-center hover:bg-blue-100 transition-colors"
          onClick={() => setShowUpload(true)}
        >
          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
          <div className="text-2xl mb-1" aria-hidden="true">📷</div>
          <p className="text-xs font-semibold text-blue-700">{t("uploadPhoto")}</p>
          <p className="text-xs text-blue-500 mt-0.5">{t("uploadPhotoSaved")}</p>
        </button>
        <button
          data-testid="photo-prompt-skip"
          className="border border-gray-200 bg-gray-50 rounded-lg p-4 text-center hover:bg-gray-100 transition-colors"
          onClick={onContinue}
        >
          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
          <div className="text-2xl mb-1" aria-hidden="true">⏭</div>
          <p className="text-xs font-medium text-gray-700">{t("skipPhotoNow")}</p>
          <p className="text-xs text-gray-400 mt-0.5">{t("skipPhotoHint")}</p>
        </button>
      </div>
      <p className="text-xs text-gray-400 text-center">
        {t("photoLaterHint")}
      </p>
    </div>
  );
}
