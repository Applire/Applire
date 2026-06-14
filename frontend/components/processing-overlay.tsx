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
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Card } from "@/components/ui/card";
import { ProgressWidget, ProgressStep } from "@/components/ui/progress-widget";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

async function apiErrorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail))
      return detail.map((e: { msg?: string }) => e.msg ?? JSON.stringify(e)).join("; ");
    return res.statusText || `HTTP ${res.status}`;
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

interface Props {
  files: File[];
  jdMode: "url" | "text";
  jdUrl: string;
  jdText: string;
  onCancel: () => void;
  // No-CV guided onboarding (US156): skip uploads and go straight to the guided
  // interview that builds the profile from scratch against the job ad.
  guided?: boolean;
}

export function ProcessingOverlay({ files, jdMode, jdUrl, jdText, onCancel, guided = false }: Props) {
  const router = useRouter();
  const t = useTranslations("processing");

  const [steps, setSteps] = useState<ProgressStep[]>(() =>
    guided
      ? [
          { label: t("analyzingJD"), status: "pending" as const },
          { label: t("preparingInterview"), status: "pending" as const },
        ]
      : [
          { label: t("analyzingJD"), status: "pending" as const },
          ...files.map((_, i) => ({
            label:
              files.length === 1
                ? t("uploadingCV")
                : t("uploadingCVN", { n: i + 1, total: files.length }),
            status: "pending" as const,
          })),
          { label: t("buildingProfile"), status: "pending" as const },
          { label: t("detectingGaps"), status: "pending" as const },
        ]
  );

  const [jdNote, setJdNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  function setStepStatus(index: number, status: ProgressStep["status"]) {
    setSteps((prev) => prev.map((s, i) => (i === index ? { ...s, status } : s)));
  }

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    async function runPipeline() {
      const profileIdx = 1 + files.length;
      const gapsIdx = 2 + files.length;
      try {
        let jobId: string | null = null;
        let jdFailReason: "url_invalid" | "fetch_failed" | null = null;

        // Step 0: Analyze Job Description
        setStepStatus(0, "active");
        if (jdMode === "url" && jdUrl.trim()) {
          const res = await fetch(`${API_BASE}/api/job/analyze`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url: jdUrl.trim() }),
          });
          if (!res.ok) {
            if (res.status === 422) {
              let body: { detail?: { error_code?: string; message?: string } | string | unknown[] } | null = null;
              try {
                body = await res.json();
              } catch {
                // body stays null
              }
              const detail =
                body?.detail && typeof body.detail === "object" && !Array.isArray(body.detail)
                  ? body.detail
                  : null;
              const errorCode = detail?.error_code;
              if (errorCode === "jd_url_invalid") {
                setStepStatus(0, "done");
                setJdNote(t("jdUrlInvalid"));
                jdFailReason = "url_invalid";
              } else if (errorCode === "jd_fetch_failed") {
                setStepStatus(0, "done");
                setJdNote(t("jdFetchFailed"));
                jdFailReason = "fetch_failed";
              } else {
                const msg =
                  typeof body?.detail === "string"
                    ? body.detail
                    : detail?.message ?? res.statusText ?? `HTTP ${res.status}`;
                throw new Error(msg);
              }
            } else {
              throw new Error(await apiErrorMessage(res));
            }
          } else {
            const data = await res.json();
            jobId = data.id;
            setStepStatus(0, "done");
          }
        } else if (jdMode === "text" && jdText.trim()) {
          const res = await fetch(`${API_BASE}/api/job/analyze`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: jdText }),
          });
          if (!res.ok) throw new Error(await apiErrorMessage(res));
          const data = await res.json();
          jobId = data.id;
          setStepStatus(0, "done");
        } else {
          setStepStatus(0, "done");
        }

        // Activate the first upload step then create the flow session
        setStepStatus(1, "active");

        let flowId: string;
        if (jobId !== null) {
          const appRes = await fetch(`${API_BASE}/api/applications`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ job_analysis_id: jobId, start_workflow: true }),
          });
          if (!appRes.ok) throw new Error(await apiErrorMessage(appRes));
          const appData = await appRes.json();
          flowId = appData.flow_session_id;
        } else {
          const flowRes = await fetch(`${API_BASE}/api/flow`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ job_id: null }),
          });
          if (!flowRes.ok) throw new Error(await apiErrorMessage(flowRes));
          const flow = await flowRes.json();
          flowId = flow.flow_id;
        }

        // No-CV guided onboarding (US156, FMEA 2.6): no uploads — the guided
        // interview builds the profile. It needs a job (create_session requires
        // one), so a JD is mandatory here. We create the guided session and
        // advance the flow jd_analysis → interview (ADR-016 amended) BEFORE
        // routing — otherwise the step-order guard bounces /interview to /import.
        if (guided) {
          if (!jobId) throw new Error(t("noCvNeedJd"));
          const sessRes = await fetch(`${API_BASE}/api/session`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ job_id: jobId, mode: "guided" }),
          });
          if (!sessRes.ok) throw new Error(await apiErrorMessage(sessRes));
          const sess = await sessRes.json();
          await fetch(`${API_BASE}/api/flow/${flowId}/advance`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ step: "interview", artifact_id: sess.session_id }),
          });
          setSteps((prev) => prev.map((s) => ({ ...s, status: "done" })));
          await new Promise((r) => setTimeout(r, 400));
          router.push(`/flow/${flowId}/interview`);
          return;
        }

        // Steps 1..N: one upload step per file.
        // A single failed file must NOT abort onboarding (FMEA JF-M-2.2): we
        // continue with the CVs that parsed and surface "N of M" on the summary.
        // Only a total failure (zero parsed) is a hard stop.
        let parsedCount = 0;
        // Upload-time input-plausibility signals (US154/155/157, issue #43)
        let anyNameMismatch = false;
        let anyNotCv = false;
        let totalUndated = 0;
        for (let i = 0; i < files.length; i++) {
          const uploadIdx = 1 + i;
          if (i > 0) setStepStatus(uploadIdx, "active");
          const formData = new FormData();
          formData.append("file", files[i]);
          try {
            const uploadRes = await fetch(`${API_BASE}/api/profile/upload`, {
              method: "POST",
              body: formData,
            });
            if (!uploadRes.ok) {
              setStepStatus(uploadIdx, "error");
              continue;
            }
            const body = await uploadRes.json().catch(() => null);
            if (body) {
              if (body.name_mismatch) anyNameMismatch = true;
              if (body.looks_like_cv === false) anyNotCv = true;
              if (typeof body.undated_positions === "number") totalUndated += body.undated_positions;
            }
            setStepStatus(uploadIdx, "done");
            parsedCount += 1;
          } catch {
            setStepStatus(uploadIdx, "error");
          }
        }
        if (parsedCount === 0) {
          throw new Error(t("allCvsFailed"));
        }
        const cvFailedCount = files.length - parsedCount;

        // Build profile (instant — upload already did the work)
        setStepStatus(profileIdx, "active");
        await new Promise((r) => setTimeout(r, 400));
        setStepStatus(profileIdx, "done");

        // Detect gaps
        setStepStatus(gapsIdx, "active");

        // Carry JD-recovery + per-file CV parse status to the gaps summary.
        const gapsQuery = (() => {
          const p = new URLSearchParams();
          if (jdFailReason) p.set("jd_status", jdFailReason);
          if (cvFailedCount > 0) {
            p.set("cv_parsed", String(parsedCount));
            p.set("cv_total", String(files.length));
          }
          if (anyNameMismatch) p.set("name_warning", "1");
          if (anyNotCv) p.set("doc_warning", "1");
          if (totalUndated > 0) p.set("undated", String(totalUndated));
          const qs = p.toString();
          return qs ? `?${qs}` : "";
        })();

        if (!jobId) {
          setStepStatus(gapsIdx, "done");
          await new Promise((r) => setTimeout(r, 400));
          router.push(`/flow/${flowId}/gaps${gapsQuery}`);
          return;
        }

        const stateRes = await fetch(`${API_BASE}/api/flow/${flowId}/state`);
        if (!stateRes.ok) throw new Error("Could not retrieve flow state");
        const flowState = await stateRes.json();
        const linkedJobId: string = flowState.job_id ?? jobId;

        const gapRes = await fetch(`${API_BASE}/api/job/${linkedJobId}/gaps`, {
          method: "POST",
        });
        if (!gapRes.ok) throw new Error(await apiErrorMessage(gapRes));
        const gapData = await gapRes.json();

        await fetch(`${API_BASE}/api/flow/${flowId}/advance`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ step: "gap_analysis", artifact_id: gapData.id ?? null }),
        });

        setStepStatus(gapsIdx, "done");

        await new Promise((r) => setTimeout(r, 400));
        router.push(`/flow/${flowId}/gaps${gapsQuery}`);
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "An error occurred. Please try again.");
      }
    }

    runPipeline();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div
      data-testid="processing-indicator"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm px-4"
    >
      <Card className="w-full max-w-[560px] p-8">
        {error ? (
          <div className="space-y-4">
            <div
              data-testid="processing-error"
              className="p-4 rounded-lg bg-critical/10 border border-critical/20"
            >
              <p className="text-sm text-critical">{error}</p>
            </div>
            <div className="flex justify-center">
              <button
                onClick={onCancel}
                data-testid="cancel-button"
                className="px-4 py-2 text-sm font-medium text-on-surface-variant hover:text-neutral-dark transition-colors"
              >
                {t("goBack")}
              </button>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center">
            <ProgressWidget steps={steps} title={t("title")} subtitle={t("subtitle")} />
            {jdNote && (
              <p className="text-xs text-on-surface-variant mt-3 text-center">{jdNote}</p>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
