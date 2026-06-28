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
import { ProgressWidget, ProgressStep } from "@/components/ui/progress-widget";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");
const POLL_INTERVAL_MS = 3000;
const STALENESS_MS = 60_000;

type CVStatus = "pending" | "generating" | "ready" | "failed" | "expired";

interface CVStatusResponse {
  cv_id: string;
  status: CVStatus;
  // Stable machine code for a failed generation (ADR-047 §4 / PQ F6). The raw LLM
  // exception text is never sent; we localize this code below.
  error_code: string | null;
  expires_at: string;
}

interface GenerationProgressProps {
  cvId: string;
  flowId: string;
  onReady: (cvId: string) => void;
  onRetry: () => void;
}

function activeStepIndex(status: CVStatus): number {
  if (status === "generating") return 1;
  if (status === "ready") return 2;
  return 0;
}

export function GenerationProgress({ cvId, flowId, onReady, onRetry }: GenerationProgressProps) {
  const t = useTranslations("cv");
  const [status, setStatus] = useState<CVStatus>("pending");
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const startedAt = useRef(Date.now());
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    async function poll() {
      if (Date.now() - startedAt.current > STALENESS_MS) {
        setStale(true);
      }

      try {
        const res = await fetch(`${API_BASE}/api/cv/${cvId}/status`);
        if (!res.ok) return;
        const data: CVStatusResponse = await res.json();
        setStatus(data.status);

        if (data.status === "ready") {
          if (intervalRef.current) clearInterval(intervalRef.current);
          try {
            await fetch(`${API_BASE}/api/flow/${flowId}/advance`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ step: "cv_generation", artifact_id: cvId }),
            });
          } catch {
            // non-fatal
          }
          onReady(cvId);
        } else if (data.status === "failed") {
          if (intervalRef.current) clearInterval(intervalRef.current);
          const code = data.error_code;
          const message =
            code === "llm_truncated"
              ? t("generationFailedTruncated")
              : code === "llm_timeout"
              ? t("generationFailedTimeout")
              : t("generationFailed");
          setError(message);
        }
      } catch {
        // Continue polling on network errors
      }
    }

    void poll();
    intervalRef.current = setInterval(() => void poll(), POLL_INTERVAL_MS);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [cvId, flowId, onReady, t]);

  const activeIndex = activeStepIndex(status);
  const labels = [t("stepQueued"), t("stepRendering"), t("stepDone")];
  const progressSteps: ProgressStep[] = labels.map((label, i) => ({
    label,
    status:
      i < activeIndex
        ? "done"
        : i === activeIndex && status !== "failed"
        ? "active"
        : "pending",
  }));

  return (
    <div className="max-w-md mx-auto animate-fade-in">
      <ProgressWidget
        steps={progressSteps}
        title={t("generationTitle")}
        subtitle={t("progressSubtitle")}
        className="mb-6"
      />

      {stale && !error && (
        <div className="border-l-4 border-warning bg-warning-container rounded-r-lg p-3 text-sm text-neutral-dark">
          {t("generationStale")}
        </div>
      )}

      {error && (
        <div className="border-l-4 border-critical bg-critical-container rounded-r-lg p-4">
          <p className="text-sm text-neutral-dark mb-2">{error}</p>
          <button
            type="button"
            onClick={onRetry}
            className="text-sm font-semibold text-critical hover:underline"
          >
            {t("retryGeneration")}
          </button>
        </div>
      )}
    </div>
  );
}
