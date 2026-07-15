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


import { useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

const TEMPLATE_IDS = [
  "classic_german",
  "modern_swiss",
  "executive",
  "tech_developer",
  "creative_sidebar",
  "academic",
  "compact_pro",
] as const;

type CVTemplate = (typeof TEMPLATE_IDS)[number];

type TemplateKey =
  | "templateClassic"
  | "templateModern"
  | "templateExecutive"
  | "templateTech"
  | "templateCreative"
  | "templateAcademic"
  | "templateCompact";

type TemplateDescKey =
  | "templateClassicDesc"
  | "templateModernDesc"
  | "templateExecutiveDesc"
  | "templateTechDesc"
  | "templateCreativeDesc"
  | "templateAcademicDesc"
  | "templateCompactDesc";

const TEMPLATE_KEYS: Record<CVTemplate, { name: TemplateKey; desc: TemplateDescKey }> = {
  classic_german:   { name: "templateClassic",   desc: "templateClassicDesc" },
  modern_swiss:     { name: "templateModern",     desc: "templateModernDesc" },
  executive:        { name: "templateExecutive",  desc: "templateExecutiveDesc" },
  tech_developer:   { name: "templateTech",       desc: "templateTechDesc" },
  creative_sidebar: { name: "templateCreative",   desc: "templateCreativeDesc" },
  academic:         { name: "templateAcademic",   desc: "templateAcademicDesc" },
  compact_pro:      { name: "templateCompact",    desc: "templateCompactDesc" },
};

interface TemplateSelectorProps {
  onGenerate: (template: CVTemplate) => void;
  isLoading?: boolean;
  actionLabel?: string;
  // E042/US239 (ADR-051 §1): an optional slot rendered directly above the
  // generate button — the page-target selector lands here so it stays
  // visually tied to "the generate action" without restructuring this component.
  extraControls?: ReactNode;
}

export function TemplateSelector({
  onGenerate,
  isLoading = false,
  actionLabel,
  extraControls,
}: TemplateSelectorProps) {
  const t = useTranslations("cv");
  const [selected, setSelected] = useState<CVTemplate>("classic_german");

  return (
    <div className="max-w-2xl mx-auto animate-fade-in">
      <h1 className="text-2xl font-heading font-bold text-neutral-dark mb-1">
        {t("selectTemplate")}
      </h1>
      <p className="text-sm text-gray-500 mb-8">
        {t("selectTemplateHint")}
      </p>

      <div className="grid grid-cols-2 gap-6 mb-8">
        {TEMPLATE_IDS.map((id) => {
          const keys = TEMPLATE_KEYS[id];
          return (
            <div key={id} data-testid="template-option">
              <button
                type="button"
                data-testid={`template-option-${id}`}
                onClick={() => setSelected(id)}
                className={[
                  "w-full text-left rounded-xl overflow-hidden bg-white shadow-soft border-2 transition-all duration-200",
                  selected === id
                    ? "border-teal ring-2 ring-teal ring-opacity-20"
                    : "border-transparent hover:border-gray-200",
                ].join(" ")}
              >
                <div className="h-1 bg-teal w-full" />
                <div className="bg-neutral-light h-48 overflow-hidden">
                  <img
                    src={`${API_BASE}/static/templates/${id}.png`}
                    alt={t(keys.name)}
                    className="w-full h-full object-cover object-top"
                    onError={(e) => {
                      (e.target as HTMLImageElement).style.display = "none";
                    }}
                  />
                </div>
                <div className="p-4">
                  <div className="flex items-center gap-2 mb-1">
                    {selected === id && (
                      <span className="w-4 h-4 rounded-full bg-teal flex items-center justify-center text-white text-[10px] font-bold" aria-hidden="true">
                        {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                        <span>✓</span>
                      </span>
                    )}
                    <p className="font-semibold text-neutral-dark text-sm">{t(keys.name)}</p>
                  </div>
                  <p className="text-xs text-gray-500">{t(keys.desc)}</p>
                </div>
              </button>
            </div>
          );
        })}
      </div>

      {extraControls && <div className="mb-3 flex justify-end">{extraControls}</div>}

      <button
        type="button"
        data-testid="regenerate-cv-button"
        onClick={() => onGenerate(selected)}
        disabled={isLoading}
        className="w-full bg-teal text-white font-semibold py-3 rounded-lg text-sm hover:bg-teal-dim transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {isLoading ? t("generatingCVButton") : (actionLabel ?? t("generateCVButton"))}
      </button>
    </div>
  );
}
