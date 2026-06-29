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
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ProgressWidget, ProgressStep } from "@/components/ui/progress-widget";
import { extractApiError, translateApiError } from "@/lib/api/errors";
import { uploadCvAsync, CVImportError } from "@/lib/import-cv";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

// F1/F7/F9 (run3): never surface raw provider/Pydantic text. extractApiError
// strips leaky validation noise (returning "" for it); when the backend gives a
// clean, deliberately-worded detail (e.g. the 502 "Nothing was changed — please
// try uploading it again.") we show THAT, otherwise translateApiError falls back
// to a friendly status-based message. Keeps the overlay + per-file retry human.
async function apiErrorMessage(res: Response): Promise<string> {
  const detail = await extractApiError(res);
  // A clean, non-leaky backend detail is deliberately user-facing — prefer it
  // over the generic status copy (e.g. the 502 truncation message).
  if (detail && detail.trim()) return detail;
  return translateApiError(res.status, undefined);
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
  // F1/F8 (run3): per-file parse failures surfaced inline with a clean message
  // and a Retry, so one bad CV neither strands the overlay nor loses the batch.
  // Keyed by the file's upload-step index; value is the clean backend detail.
  const [fileErrors, setFileErrors] = useState<Record<number, string>>({});
  const [retrying, setRetrying] = useState<number | null>(null);
  // F8 (run3): real-LLM steps take minutes; an elapsed/"still working" heartbeat
  // keeps an active step from ever reading as a silent hang.
  const [elapsed, setElapsed] = useState(0);
  const working = !error && steps.some((s) => s.status === "active");
  const started = useRef(false);
  // Aborts in-flight async imports if the overlay unmounts mid-poll, so a long import
  // poll can't outlive the component (no orphaned fetches / state updates after unmount).
  const abortRef = useRef<AbortController | null>(null);
  // Pipeline context captured during the run, so a recovery Retry can pick the
  // flow back up from where the uploads failed instead of restarting onboarding.
  const pipelineCtx = useRef<{
    flowId: string | null;
    jobId: string | null;
    jdFailReason: "url_invalid" | "fetch_failed" | null;
  }>({ flowId: null, jobId: null, jdFailReason: null });

  function setStepStatus(index: number, status: ProgressStep["status"]) {
    setSteps((prev) => prev.map((s, i) => (i === index ? { ...s, status } : s)));
  }

  // Tick a "still working" elapsed counter whenever a step is active; reset when
  // nothing is running so a fresh step starts the heartbeat from zero.
  useEffect(() => {
    if (!working) {
      setElapsed(0);
      return;
    }
    const id = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(id);
  }, [working]);

  interface UploadOk {
    name_mismatch?: boolean;
    looks_like_cv?: boolean;
    undated_positions?: number;
  }

  // Upload a single CV. Returns the parse signals on success, or null on failure
  // (recording the clean backend message against the file's step). One failure
  // never throws — the batch keeps going (FMEA JF-M-2.2) and the user can Retry.
  const uploadOne = useRef(async (i: number): Promise<UploadOk | null> => {
    const uploadIdx = 1 + i;
    try {
      // Async import (E036): returns immediately and the heavy segmented work runs in a
      // background task, polled here — so a slow/output-capped model can no longer 504
      // the request and drop this CV. Sequential per file (caller awaits each in turn),
      // so the merge order is preserved.
      const body = await uploadCvAsync(files[i], {
        apiBase: API_BASE,
        signal: abortRef.current?.signal,
      });
      setFileErrors((prev) => {
        if (!(uploadIdx in prev)) return prev;
        const next = { ...prev };
        delete next[uploadIdx];
        return next;
      });
      setStepStatus(uploadIdx, "done");
      return {
        name_mismatch: body.name_mismatch,
        looks_like_cv: body.looks_like_cv,
        undated_positions: body.undated_positions,
      };
    } catch (e) {
      // A failed import marks just this file (FMEA JF-M-2.2) — the batch keeps going and
      // the user can Retry. A truncation/timeout gets the reassuring "nothing was changed"
      // copy; other failures the generic one. The raw provider text never shows.
      const code = e instanceof CVImportError ? e.errorCode : null;
      const msg =
        code === "llm_truncated" || code === "llm_timeout"
          ? t("uploadTryAgain")
          : t("uploadFailed");
      setFileErrors((prev) => ({ ...prev, [uploadIdx]: msg }));
      setStepStatus(uploadIdx, "error");
      return null;
    }
  }).current;

  // Build profile → detect gaps → advance flow → navigate. Shared by the main
  // run and a recovery Retry, so retrying a stranded file resumes the flow from
  // where the uploads failed (never a full restart, never a freeze).
  const finishPipeline = useRef(
    async (parsedCount: number, total: number, signals?: {
      nameMismatch?: boolean;
      notCv?: boolean;
      undated?: number;
    }) => {
      const { flowId, jobId, jdFailReason } = pipelineCtx.current;
      if (!flowId) return;
      const profileIdx = 1 + total;
      const gapsIdx = 2 + total;
      const cvFailedCount = total - parsedCount;

      setStepStatus(profileIdx, "active");
      await new Promise((r) => setTimeout(r, 400));
      setStepStatus(profileIdx, "done");
      setStepStatus(gapsIdx, "active");

      const gapsQuery = (() => {
        const p = new URLSearchParams();
        if (jdFailReason) p.set("jd_status", jdFailReason);
        if (cvFailedCount > 0) {
          p.set("cv_parsed", String(parsedCount));
          p.set("cv_total", String(total));
        }
        if (signals?.nameMismatch) p.set("name_warning", "1");
        if (signals?.notCv) p.set("doc_warning", "1");
        if (signals?.undated && signals.undated > 0) p.set("undated", String(signals.undated));
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
      if (!stateRes.ok) throw new Error(await apiErrorMessage(stateRes));
      const flowState = await stateRes.json();
      const linkedJobId: string = flowState.job_id ?? jobId;

      const gapRes = await fetch(`${API_BASE}/api/job/${linkedJobId}/gaps`, { method: "POST" });
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
    },
  ).current;

  // Retry just the one failed file/step, in place — never restarts the flow. If
  // the retry succeeds and it was the file blocking onboarding (all had failed),
  // resume the pipeline from where it stalled instead of stranding the user.
  async function retryFile(uploadIdx: number) {
    setRetrying(uploadIdx);
    setStepStatus(uploadIdx, "active");
    const ok = await uploadOne(uploadIdx - 1);
    setRetrying(null);
    if (ok && error && pipelineCtx.current.flowId) {
      // Recovery: at least one CV now parsed. Clear the hard error and resume.
      setError(null);
      const remainingErrors = Object.keys(fileErrors).filter(
        (k) => Number(k) !== uploadIdx,
      ).length;
      const parsedCount = files.length - remainingErrors;
      try {
        await finishPipeline(parsedCount, files.length, {
          nameMismatch: ok.name_mismatch,
          notCv: ok.looks_like_cv === false,
          undated: ok.undated_positions,
        });
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : t("uploadFailed"));
      }
    }
  }

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const controller = new AbortController();
    abortRef.current = controller;

    async function runPipeline() {
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
        // Capture context so a recovery Retry can resume from here.
        pipelineCtx.current = { flowId, jobId, jdFailReason };

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
          const ok = await uploadOne(i);
          if (ok) {
            if (ok.name_mismatch) anyNameMismatch = true;
            if (ok.looks_like_cv === false) anyNotCv = true;
            if (typeof ok.undated_positions === "number") totalUndated += ok.undated_positions;
            parsedCount += 1;
          }
        }
        if (parsedCount === 0) {
          // Hard stop, but recoverable: the failed-file Retry can resume the
          // pipeline (FMEA JF-M-2.2) — pipelineCtx is already captured.
          throw new Error(t("allCvsFailed"));
        }

        await finishPipeline(parsedCount, files.length, {
          nameMismatch: anyNameMismatch,
          notCv: anyNotCv,
          undated: totalUndated,
        });
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : t("genericError"));
      }
    }

    runPipeline();
    return () => controller.abort();
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
            {/* All-CVs-failed: offer a per-file Retry so the user can recover
                without re-uploading everything from the landing page. */}
            {Object.keys(fileErrors).length > 0 && (
              <div className="space-y-2" data-testid="processing-file-errors">
                {files.map((file, i) => {
                  const uploadIdx = 1 + i;
                  const msg = fileErrors[uploadIdx];
                  if (!msg) return null;
                  return (
                    <div
                      key={uploadIdx}
                      className="flex items-center justify-between gap-2 p-2 rounded-lg bg-critical/5 border border-critical/20"
                    >
                      <div className="min-w-0">
                        <p className="text-xs font-medium text-neutral-dark truncate">{file.name}</p>
                        <p className="text-xs text-on-surface-variant">{msg}</p>
                      </div>
                      <Button
                        size="sm"
                        variant="outline"
                        className="shrink-0"
                        data-testid={`retry-file-${i}`}
                        disabled={retrying !== null}
                        onClick={() => retryFile(uploadIdx)}
                      >
                        {retrying === uploadIdx ? t("retrying") : t("retry")}
                      </Button>
                    </div>
                  );
                })}
              </div>
            )}
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
            {/* F8: heartbeat — an active step never reads as a silent hang. */}
            {working && (
              <p
                data-testid="processing-heartbeat"
                className="text-xs text-on-surface-variant mt-3 text-center"
              >
                {elapsed >= 20 ? t("stillWorkingElapsed", { seconds: elapsed }) : t("stillWorking")}
              </p>
            )}
            {/* Per-file failure surfaced inline while the rest of the batch
                continues (partial success); Retry re-runs just this file. */}
            {Object.keys(fileErrors).length > 0 && (
              <div className="w-full space-y-2 mt-3" data-testid="processing-file-errors">
                {files.map((file, i) => {
                  const uploadIdx = 1 + i;
                  const msg = fileErrors[uploadIdx];
                  if (!msg) return null;
                  return (
                    <div
                      key={uploadIdx}
                      className="flex items-center justify-between gap-2 p-2 rounded-lg bg-critical/5 border border-critical/20"
                    >
                      <div className="min-w-0">
                        <p className="text-xs font-medium text-neutral-dark truncate">{file.name}</p>
                        <p className="text-xs text-on-surface-variant">{msg}</p>
                      </div>
                      <Button
                        size="sm"
                        variant="outline"
                        className="shrink-0"
                        data-testid={`retry-file-${i}`}
                        disabled={retrying !== null}
                        onClick={() => retryFile(uploadIdx)}
                      >
                        {retrying === uploadIdx ? t("retrying") : t("retry")}
                      </Button>
                    </div>
                  );
                })}
              </div>
            )}
            {jdNote && (
              <p className="text-xs text-on-surface-variant mt-3 text-center">{jdNote}</p>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
