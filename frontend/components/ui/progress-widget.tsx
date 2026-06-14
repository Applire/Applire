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


import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

export type ProgressStepStatus = "done" | "active" | "pending" | "error";

export interface ProgressStep {
  label: string;
  status: ProgressStepStatus;
}

interface ProgressWidgetProps {
  steps: ProgressStep[];
  title: string;
  subtitle?: string;
  className?: string;
}

const RING_R = 30;
const CIRCUMFERENCE = 2 * Math.PI * RING_R; // ≈ 188.5

function StepIcon({ status }: { status: ProgressStepStatus }) {
  if (status === "done") {
    return (
      <div className="flex-shrink-0 w-4 h-4 rounded-full bg-primary flex items-center justify-center">
        <svg className="w-2.5 h-2.5 stroke-white" fill="none" viewBox="0 0 24 24" aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
        </svg>
      </div>
    );
  }
  if (status === "active") {
    return (
      <div className="flex-shrink-0 w-4 h-4 rounded-full border-2 border-gold-dim flex items-center justify-center">
        <div className="w-1.5 h-1.5 rounded-full bg-gold-dim animate-pulse" />
      </div>
    );
  }
  if (status === "error") {
    return (
      <div className="flex-shrink-0 w-4 h-4 rounded-full bg-critical flex items-center justify-center">
        <svg className="w-2.5 h-2.5 stroke-white" fill="none" viewBox="0 0 24 24" aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M6 6l12 12M18 6L6 18" />
        </svg>
      </div>
    );
  }
  return (
    <div className="flex-shrink-0 w-4 h-4 rounded-full border-2 border-outline-variant" />
  );
}

export function ProgressWidget({ steps, title, subtitle, className }: ProgressWidgetProps) {
  const t = useTranslations("progressWidget");
  const doneCount = steps.filter((s) => s.status === "done").length;
  const pct = steps.length === 0 ? 0 : Math.round((doneCount / steps.length) * 100);
  const offset = CIRCUMFERENCE - (pct / 100) * CIRCUMFERENCE;

  return (
    <div className={cn("flex flex-col items-center", className)}>
      {/* Ring */}
      <div className="relative w-20 h-20 mb-4">
        <svg className="w-full h-full -rotate-90" viewBox="0 0 72 72" aria-hidden="true">
          <circle
            cx="36" cy="36" r={RING_R}
            fill="none"
            className="stroke-surface-container-high"
            strokeWidth="5"
          />
          <circle
            cx="36" cy="36" r={RING_R}
            fill="none"
            className="stroke-primary"
            strokeWidth="5.5"
            strokeDasharray={CIRCUMFERENCE}
            strokeDashoffset={offset}
            strokeLinecap="round"
            style={{ transition: "stroke-dashoffset 0.6s ease" }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
          <span className="text-lg font-extrabold text-primary leading-none">{pct}%</span>
          <span className="text-[8px] font-semibold tracking-widest uppercase text-on-surface-variant mt-0.5">
            {t("done")}
          </span>
        </div>
      </div>

      {/* Title + subtitle */}
      <div className="text-center mb-4">
        <p className="font-semibold text-sm text-primary">{title}</p>
        {subtitle && (
          <p className="text-xs text-on-surface-variant mt-0.5">{subtitle}</p>
        )}
      </div>

      {/* Steps */}
      <div className="w-full space-y-1.5">
        {steps.map((step, i) => (
          <div
            key={i}
            data-step-status={step.status}
            className={cn(
              "flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs transition-all duration-200",
              step.status === "done" && "bg-primary-container/20",
              step.status === "active" && "animate-shimmer border border-gold/40",
              step.status === "error" && "bg-critical/10 border border-critical/20",
              step.status === "pending" && "opacity-40"
            )}
          >
            <StepIcon status={step.status} />
            <span
              className={cn(
                "font-medium",
                step.status === "active" && "font-bold text-gold-dim",
                step.status !== "active" && "text-on-surface"
              )}
            >
              {step.label}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
