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


import Link from "next/link";
import { useTranslations } from "next-intl";

type CLTemplate =
  | "classic_german"
  | "modern_swiss"
  | "executive"
  | "tech_developer"
  | "creative_sidebar"
  | "academic"
  | "compact_pro";

interface TemplateOption {
  value: CLTemplate;
  label: string;
  description: string;
}

interface CoverLetterDesignTabProps {
  flowId: string;
  currentTemplate: CLTemplate;
  onTemplateChange: (template: CLTemplate) => void;
}

export function CoverLetterDesignTab({
  flowId,
  currentTemplate,
  onTemplateChange,
}: CoverLetterDesignTabProps) {
  const t = useTranslations("coverLetter");
  const tc = useTranslations("cv");

  const TEMPLATES: TemplateOption[] = [
    { value: "classic_german", label: tc("templateClassic"), description: tc("templateClassicDesc") },
    { value: "modern_swiss", label: tc("templateModern"), description: tc("templateModernDesc") },
    { value: "executive", label: tc("templateExecutive"), description: tc("templateExecutiveDesc") },
    { value: "tech_developer", label: tc("templateTech"), description: tc("templateTechDesc") },
    { value: "creative_sidebar", label: tc("templateCreative"), description: tc("templateCreativeDesc") },
    { value: "academic", label: tc("templateAcademic"), description: tc("templateAcademicDesc") },
    { value: "compact_pro", label: tc("templateCompact"), description: tc("templateCompactDesc") },
  ];

  return (
    <div className="flex flex-col gap-3 p-3">
      <p className="text-xs font-semibold text-neutral-500 uppercase tracking-wide">
        {t("templateSection")}
      </p>
      <div className="flex flex-col gap-2">
        {TEMPLATES.map((tmpl) => (
          <button
            key={tmpl.value}
            type="button"
            onClick={() => onTemplateChange(tmpl.value)}
            className={`flex items-center justify-between rounded-lg border px-3 py-2.5 text-left transition-colors ${
              currentTemplate === tmpl.value
                ? "border-blue-500 bg-blue-50"
                : "border-neutral-200 hover:border-neutral-400 bg-neutral-50"
            }`}
            data-testid={`cl-template-${tmpl.value}`}
          >
            <div>
              <div className="text-sm font-medium">{tmpl.label}</div>
              <div className="text-xs text-neutral-500">{tmpl.description}</div>
            </div>
            {currentTemplate === tmpl.value && (
              <span className="text-blue-600 text-xs font-semibold">{t("activeLabel")}</span>
            )}
          </button>
        ))}
      </div>

      <div className="border-t border-neutral-200 pt-3 mt-1">
        <p className="text-xs text-neutral-500 mb-1">{t("colorSchemeSection")}</p>
        <p className="text-xs text-neutral-400">
          {t("colorSchemeHint")}<Link
            href={`/flow/${flowId}/cv`}
            className="text-blue-500 hover:underline"
            data-testid="cl-design-change-color-link"
          >{t("colorSchemeChangeLink")}</Link>
        </p>
      </div>
    </div>
  );
}
