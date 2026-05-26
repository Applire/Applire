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
import { useRouter } from "next/navigation";
import { use } from "react";
import { useTranslations } from "next-intl";
import { Card } from "@/components/ui/card";
import { ProgressLinear } from "@/components/ui/progress";
import { StepChecklist, StepItem, StepState } from "@/components/ui/step-checklist";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

interface FlowState {
  flow_id: string;
  user_type: "new" | "returning";
  current_step: string;
  available_actions: Record<string, string>;
  job_summary?: { role_title: string } | null;
  profile_completeness?: number | null;
  processing_detail?: string | null;
}

// Simulated step progression timing (in ms)
const STEP_DURATIONS: Record<string, number> = {
  upload: 1500,
  parse: 4000,
  analyze_jd: 3000,
  build_profile: 5000,
  match: 3000,
  detect_gaps: 4000,
};

export default function ProcessingPage({
  params,
}: {
  params: Promise<{ flowId: string }>;
}) {
  const { flowId } = use(params);
  const router = useRouter();
  const t = useTranslations("flow.processing");

  // Build step list inside component so labels are translated
  const PROCESSING_STEPS: StepItem[] = [
    { key: "upload", label: t("stepUploading") },
    { key: "parse", label: t("stepParsing") },
    { key: "analyze_jd", label: t("stepAnalyzingJD") },
    { key: "build_profile", label: t("stepBuildingProfile") },
    { key: "match", label: t("stepMatchingRole") },
    { key: "detect_gaps", label: t("stepDetectingGaps") },
  ];

  const [stepStates, setStepStates] = useState<Record<string, StepState>>({
    upload: "pending",
    parse: "pending",
    analyze_jd: "pending",
    build_profile: "pending",
    match: "pending",
    detect_gaps: "pending",
  });

  const [stepDetails, setStepDetails] = useState<Record<string, string>>({});
  const [currentStepIndex, setCurrentStepIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);

  // Simulate step progression
  useEffect(() => {
    let timeoutId: NodeJS.Timeout;

    const advanceStep = () => {
      const steps = PROCESSING_STEPS;

      if (currentStepIndex >= steps.length) {
        return;
      }

      const stepKey = steps[currentStepIndex].key;

      // Mark current step as in_progress
      setStepStates(prev => ({
        ...prev,
        [stepKey]: "in_progress",
      }));

      // After duration, mark as completed and move to next
      timeoutId = setTimeout(() => {
        setStepStates(prev => ({
          ...prev,
          [stepKey]: "completed",
        }));

        // Add detail text for completed steps
        if (stepKey === "upload") {
          setStepDetails(prev => ({
            ...prev,
            [stepKey]: t("detailFilesReceived"),
          }));
        } else if (stepKey === "analyze_jd") {
          setStepDetails(prev => ({
            ...prev,
            [stepKey]: t("detailRoleIdentified"),
          }));
        } else if (stepKey === "build_profile") {
          setStepDetails(prev => ({
            ...prev,
            [stepKey]: t("detailMergedCVs", { count: 3 }),
          }));
        }

        setCurrentStepIndex(prev => prev + 1);
      }, STEP_DURATIONS[stepKey] || 2000);
    };

    advanceStep();

    return () => {
      if (timeoutId) clearTimeout(timeoutId);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentStepIndex]);

  // Poll backend for actual state
  useEffect(() => {
    const pollInterval = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/flow/${flowId}/state`);
        if (!res.ok) {
          if (res.status === 404) {
            setError(t("errorFlowNotFound"));
          }
          return;
        }

        const state: FlowState = await res.json();

        // If backend indicates we're done processing, redirect
        if (state.current_step === "gap_analysis" || state.current_step === "cv_import") {
          clearInterval(pollInterval);
          // Complete all remaining steps quickly
          setStepStates({
            upload: "completed",
            parse: "completed",
            analyze_jd: "completed",
            build_profile: "completed",
            match: "completed",
            detect_gaps: "completed",
          });

          // Redirect after a brief delay
          setTimeout(() => {
            router.push(`/flow/${flowId}/gaps`);
          }, 500);
        }
      } catch (e) {
        console.error("Polling error:", e);
      }
    }, 2000);

    return () => clearInterval(pollInterval);
  }, [flowId, router, t]);

  // Calculate progress
  const completedCount = Object.values(stepStates).filter(s => s === "completed").length;
  const progress = (completedCount / PROCESSING_STEPS.length) * 100;

  // Update step items with details
  const stepsWithDetails = PROCESSING_STEPS.map(step => ({
    ...step,
    detail: stepDetails[step.key],
  }));

  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-4 py-16">
      <Card className="w-full max-w-[600px] p-8">
        <div className="text-center mb-8">
          <h1 className="font-heading text-2xl font-bold text-neutral-dark mb-2">
            {t("title")}
          </h1>
          <p className="text-sm text-gray-500">
            {t("subtitle")}
          </p>
        </div>

        {error && (
          <div className="mb-6 p-4 rounded-lg bg-critical/10 border border-critical/20">
            <p className="text-sm text-critical">{error}</p>
          </div>
        )}

        <StepChecklist
          steps={stepsWithDetails}
          stepStates={stepStates}
        />

        <div className="mt-8">
          <ProgressLinear value={progress} />
          <p className="text-xs text-gray-500 text-center mt-2">
            {t("percentComplete", { pct: Math.round(progress) })}
          </p>
        </div>

        <p className="text-xs text-gray-500 text-center mt-6">
          {t("usuallyTakes")}
        </p>
      </Card>
    </div>
  );
}
