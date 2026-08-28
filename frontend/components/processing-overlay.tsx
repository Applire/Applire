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
import { startCvImport, pollCvImport, CVImportError, type ImportNotAppliedItem } from "@/lib/import-cv";
import { analyzeGapsAsync } from "@/lib/gap-analysis";

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

  // The step list is the run's actual plan (#114 / blind PQ F10): no JD provided →
  // no "Analyzing job description" step and no "Detecting gaps" step (gap analysis
  // only runs against a job). Guided onboarding always analyzes a JD (mandatory).
  // Derived from props, which are fixed for the lifetime of the overlay.
  const jdProvided = jdMode === "url" ? jdUrl.trim().length > 0 : jdText.trim().length > 0;
  const hasJdStep = guided || jdProvided;
  // Index of the first upload step; all step arithmetic below shifts by this.
  const jdOffset = hasJdStep ? 1 : 0;

  const [steps, setSteps] = useState<ProgressStep[]>(() =>
    guided
      ? [
          { label: t("analyzingJD"), status: "pending" as const },
          { label: t("preparingInterview"), status: "pending" as const },
        ]
      : [
          ...(hasJdStep ? [{ label: t("analyzingJD"), status: "pending" as const }] : []),
          ...files.map((_, i) => ({
            label:
              files.length === 1
                ? t("uploadingCV")
                : t("uploadingCVN", { n: i + 1, total: files.length }),
            status: "pending" as const,
          })),
          { label: t("buildingProfile"), status: "pending" as const },
          ...(hasJdStep ? [{ label: t("detectingGaps"), status: "pending" as const }] : []),
        ]
  );

  const [jdNote, setJdNote] = useState<string | null>(null);
  // #151: a blocked/invalid JD URL PAUSES the pipeline instead of silently
  // degrading to a JD-less run. The user either pastes the JD text inline
  // (pipeline resumes with the analyzed job) or explicitly continues without.
  const [jdRecovery, setJdRecovery] = useState<{
    code: "url_invalid" | "fetch_failed";
    text: string;
    error: string | null;
    submitting: boolean;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  // F1/F8 (run3): per-file parse failures surfaced inline with a clean message
  // and a Retry, so one bad CV neither strands the overlay nor loses the batch.
  // Keyed by the file's upload-step index; value is the clean backend detail.
  const [fileErrors, setFileErrors] = useState<Record<number, string>>({});
  const [retrying, setRetrying] = useState<number | null>(null);
  // #615 — entries the merge's own ops did not carry, aggregated across every
  // uploaded file (like undated_positions), rendered as one localised note.
  const [notAppliedItems, setNotAppliedItems] = useState<ImportNotAppliedItem[]>([]);
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
    not_applied?: ImportNotAppliedItem[];
  }

  // Mark a file's step failed with a clean, localized message (FMEA JF-M-2.2).
  // A truncation/timeout gets the reassuring "nothing was changed" copy; other
  // failures the generic one. The raw provider text never shows.
  const markFileFailed = useRef((i: number, e: unknown) => {
    const uploadIdx = jdOffset + i;
    const code = e instanceof CVImportError ? e.errorCode : null;
    const msg =
      code === "llm_truncated" || code === "llm_timeout"
        ? t("uploadTryAgain")
        : t("uploadFailed");
    setFileErrors((prev) => ({ ...prev, [uploadIdx]: msg }));
    setStepStatus(uploadIdx, "error");
  }).current;

  // Phase A of the import queue (PQ F1): POST one file's import job so the SERVER owns
  // it. Returns the import_id, or null on failure (recording the error against the
  // file's step — the batch keeps going and the user can Retry).
  const startOne = useRef(async (i: number): Promise<string | null> => {
    try {
      return await startCvImport(files[i], {
        apiBase: API_BASE,
        signal: abortRef.current?.signal,
      });
    } catch (e) {
      markFileFailed(i, e);
      return null;
    }
  }).current;

  // Phase B: poll a queued import to completion. Returns the parse signals on success,
  // or null on failure. One failure never throws — the batch keeps going (FMEA
  // JF-M-2.2) and the user can Retry.
  const awaitOne = useRef(async (i: number, importId: string): Promise<UploadOk | null> => {
    const uploadIdx = jdOffset + i;
    try {
      const body = await pollCvImport(importId, {
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
        not_applied: body.not_applied,
      };
    } catch (e) {
      markFileFailed(i, e);
      return null;
    }
  }).current;

  // Single-file start + poll — used by the per-file Retry.
  const uploadOne = useRef(async (i: number): Promise<UploadOk | null> => {
    const importId = await startOne(i);
    if (importId === null) return null;
    return awaitOne(i, importId);
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
      const profileIdx = jdOffset + total;
      // Only planned when a JD exists — without a job there is no gap analysis.
      const gapsIdx = profileIdx + 1;
      const cvFailedCount = total - parsedCount;

      setStepStatus(profileIdx, "active");
      await new Promise((r) => setTimeout(r, 400));
      setStepStatus(profileIdx, "done");
      if (hasJdStep) setStepStatus(gapsIdx, "active");

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
        if (hasJdStep) setStepStatus(gapsIdx, "done");
        await new Promise((r) => setTimeout(r, 400));
        router.push(`/flow/${flowId}/gaps${gapsQuery}`);
        return;
      }

      const stateRes = await fetch(`${API_BASE}/api/flow/${flowId}/state`);
      if (!stateRes.ok) throw new Error(await apiErrorMessage(stateRes));
      const flowState = await stateRes.json();
      const linkedJobId: string = flowState.job_id ?? jobId;

      // Kick off the async gap analysis and poll to completion (this step's spinner
      // stays active meanwhile), then advance the flow with the produced analysis id.
      // Resilient: a 504 mid-analysis no longer drops the result and wedges the journey.
      const gapData = await analyzeGapsAsync(linkedJobId, {
        apiBase: API_BASE,
        signal: abortRef.current?.signal,
      });

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

  // Everything AFTER JD analysis: create the flow session (with the job when one
  // was analyzed), run the guided branch or the upload queue, then finish the
  // pipeline. Extracted so the #151 pause-and-paste recovery can resume the run
  // exactly as if the URL had worked (or, on explicit skip, without a job).
  const continueAfterJd = useRef(
    async (jobId: string | null, jdFailReason: "url_invalid" | "fetch_failed" | null) => {
      try {
        // Activate the first upload step then create the flow session
        setStepStatus(jdOffset, "active");

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

        // Steps 1..N: one upload step per file, in two phases (PQ F1 — BLOCKER).
        //
        // Phase A: POST every file's import job up-front (fast — no LLM work in the
        // request). After this phase the SERVER owns the whole queue: a refresh can
        // no longer lose queued files, because the backend finishes all jobs
        // (serialized per user, in creation order) without the client.
        const importIds: (string | null)[] = [];
        for (let i = 0; i < files.length; i++) {
          importIds.push(await startOne(i));
        }

        // Phase B: poll each job to completion, in creation order (matching the
        // backend's per-user processing order, so the step-by-step UI stays honest).
        // A single failed file must NOT abort onboarding (FMEA JF-M-2.2): we
        // continue with the CVs that parsed and surface "N of M" on the summary.
        // Only a total failure (zero parsed) is a hard stop.
        let parsedCount = 0;
        // Upload-time input-plausibility signals (US154/155/157, issue #43)
        let anyNameMismatch = false;
        let anyNotCv = false;
        let totalUndated = 0;
        const allNotApplied: ImportNotAppliedItem[] = [];
        for (let i = 0; i < files.length; i++) {
          const uploadIdx = jdOffset + i;
          const importId = importIds[i];
          if (importId === null) continue; // POST failed — step already marked error
          if (i > 0) setStepStatus(uploadIdx, "active");
          const ok = await awaitOne(i, importId);
          if (ok) {
            if (ok.name_mismatch) anyNameMismatch = true;
            if (ok.looks_like_cv === false) anyNotCv = true;
            if (typeof ok.undated_positions === "number") totalUndated += ok.undated_positions;
            if (ok.not_applied?.length) allNotApplied.push(...ok.not_applied);
            parsedCount += 1;
          }
        }
        if (allNotApplied.length) setNotAppliedItems(allNotApplied);
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
    },
  ).current;

  // #151 pause-and-paste: analyze the pasted JD text and, on success, resume the
  // pipeline exactly as if the URL had worked (flow created with the new job —
  // no jd_status query param, no degraded run).
  async function submitPastedJd() {
    const current = jdRecovery;
    if (!current || !current.text.trim() || current.submitting) return;
    setJdRecovery({ ...current, submitting: true, error: null });
    setStepStatus(0, "active");
    try {
      const res = await fetch(`${API_BASE}/api/job/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: current.text }),
      });
      if (!res.ok) {
        const msg = await apiErrorMessage(res);
        setStepStatus(0, "error");
        setJdRecovery({ ...current, submitting: false, error: msg || t("jdPasteFailed") });
        return;
      }
      const data = await res.json();
      setStepStatus(0, "done");
      setJdRecovery(null);
      await continueAfterJd(data.id, null);
    } catch {
      setStepStatus(0, "error");
      setJdRecovery({ ...current, submitting: false, error: t("jdPasteFailed") });
    }
  }

  // #151: the old silent degradation, now as an EXPLICIT user choice — flow
  // without a job, amber note, jd_status param, gaps-page recovery banner.
  async function continueWithoutJd() {
    const current = jdRecovery;
    if (!current || current.submitting) return;
    setJdRecovery(null);
    setStepStatus(0, "done");
    setJdNote(current.code === "url_invalid" ? t("jdUrlInvalid") : t("jdFetchFailed"));
    await continueAfterJd(null, current.code);
  }

  // Retry just the one failed file/step, in place — never restarts the flow. If
  // the retry succeeds and it was the file blocking onboarding (all had failed),
  // resume the pipeline from where it stalled instead of stranding the user.
  async function retryFile(uploadIdx: number) {
    setRetrying(uploadIdx);
    setStepStatus(uploadIdx, "active");
    const ok = await uploadOne(uploadIdx - jdOffset);
    setRetrying(null);
    if (ok?.not_applied?.length) {
      setNotAppliedItems((prev) => [...prev, ...ok.not_applied!]);
    }
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
    // A fresh controller every mount, so abortRef always points at THIS mount's
    // controller. Under React StrictMode (mount→unmount→remount in dev) the first
    // controller is aborted by its own cleanup, but the surviving second mount
    // installs a live one. runPipeline runs exactly once (the `started` guard) and
    // reads abortRef.current LAZILY at upload time, so it picks up the live signal
    // — never the aborted one. (Regression: guarding the whole effect left abortRef
    // pinned to an aborted controller, so the CV-import fetch rejected before
    // sending — no POST, and a false "couldn't read your CVs". Caught on the real
    // proxied path by blind PQ; JD-analyze carries no signal so it masked the bug.)
    const controller = new AbortController();
    abortRef.current = controller;

    async function runPipeline() {
      try {
        let jobId: string | null = null;

        // Step 0 (only planned when a JD exists): Analyze Job Description
        if (hasJdStep) setStepStatus(0, "active");
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
              if (errorCode === "jd_url_invalid" || errorCode === "jd_fetch_failed") {
                // #151: STOP the pipeline — no flow yet, no silent JD-less run.
                // The user pastes the JD inline (resume with the analyzed job)
                // or explicitly chooses to continue without one.
                setStepStatus(0, "error");
                setJdRecovery({
                  code: errorCode === "jd_url_invalid" ? "url_invalid" : "fetch_failed",
                  text: "",
                  error: null,
                  submitting: false,
                });
                return;
              }
              const msg =
                typeof body?.detail === "string"
                  ? body.detail
                  : detail?.message ?? res.statusText ?? `HTTP ${res.status}`;
              throw new Error(msg);
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
        } else if (hasJdStep) {
          // Guided run without a JD text/url — the step exists but nothing to analyze
          // (the pipeline errors with noCvNeedJd in continueAfterJd).
          setStepStatus(0, "done");
        }

        await continueAfterJd(jobId, null);
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : t("genericError"));
      }
    }

    // Run the pipeline exactly once across StrictMode's double-invoke (it has
    // non-idempotent side effects: creates a flow, uploads CVs). The controller
    // above is still recreated per mount so the abort lifecycle stays correct.
    if (!started.current) {
      started.current = true;
      runPipeline();
    }
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
                  const uploadIdx = jdOffset + i;
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
                  const uploadIdx = jdOffset + i;
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
            {/* #615 — entries the merge's own ops did not carry, named once
                the batch (or a retry) has reported them. Truncated to the
                first 3 labels; the rest fold into a localised "+N more". */}
            {notAppliedItems.length > 0 && (
              <p
                data-testid="processing-not-applied"
                className="text-xs text-on-surface-variant mt-3 text-center"
              >
                {t("notAppliedNote", {
                  count: notAppliedItems.length,
                  labels:
                    notAppliedItems
                      .slice(0, 3)
                      .map((item) => item.label)
                      .join(", ") +
                    (notAppliedItems.length > 3
                      ? " " + t("notAppliedMore", { count: notAppliedItems.length - 3 })
                      : ""),
                })}
              </p>
            )}
            {/* #151 pause-and-paste: the scrape was blocked (or the URL invalid).
                The pipeline is PAUSED — the user pastes the JD text to continue
                with a real job, or explicitly continues without one. */}
            {jdRecovery && (
              <div
                data-testid="jd-paste-recovery"
                className="w-full mt-4 space-y-3 rounded-lg border border-amber-300 bg-amber-50 p-4"
              >
                <p className="text-sm text-amber-800">
                  {jdRecovery.code === "url_invalid"
                    ? t("jdUrlInvalidPaste")
                    : t("jdFetchFailedPaste")}
                </p>
                {jdRecovery.error && (
                  <p data-testid="jd-paste-error" className="text-xs text-critical">
                    {jdRecovery.error}
                  </p>
                )}
                <textarea
                  data-testid="jd-paste-textarea"
                  className={
                    "w-full resize-none text-xs font-body border border-gray-200 rounded px-2 py-1.5 bg-white " +
                    "focus:outline-none focus:ring-1 focus:ring-teal/50 focus:border-teal " +
                    "disabled:opacity-50 min-h-[120px]"
                  }
                  placeholder={t("jdPastePlaceholder")}
                  value={jdRecovery.text}
                  onChange={(e) =>
                    setJdRecovery((r) => (r ? { ...r, text: e.target.value } : r))
                  }
                  disabled={jdRecovery.submitting}
                  rows={6}
                />
                <div className="flex flex-col-reverse sm:flex-row sm:justify-end gap-2">
                  {/* Guided onboarding requires a job — no JD-less escape there. */}
                  {!guided && (
                    <Button
                      size="sm"
                      variant="outline"
                      data-testid="jd-skip-button"
                      disabled={jdRecovery.submitting}
                      onClick={() => void continueWithoutJd()}
                    >
                      {t("continueWithoutJd")}
                    </Button>
                  )}
                  <Button
                    size="sm"
                    data-testid="jd-paste-submit"
                    disabled={!jdRecovery.text.trim() || jdRecovery.submitting}
                    onClick={() => void submitPastedJd()}
                  >
                    {jdRecovery.submitting ? t("analyzingPastedText") : t("analyzePastedText")}
                  </Button>
                </div>
                {guided && (
                  <div className="flex justify-center">
                    <button
                      onClick={onCancel}
                      data-testid="cancel-button"
                      className="px-2 py-1 text-xs font-medium text-on-surface-variant hover:text-neutral-dark transition-colors"
                    >
                      {t("goBack")}
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
