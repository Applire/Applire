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
import { use, useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { CoverLetterDocument } from "@/components/cover-letter/CoverLetterDocument";
import { CoverLetterRefinementPanel } from "@/components/cover-letter/CoverLetterRefinementPanel";
import { GenerateCoverLetterModal } from "@/components/cover-letter/GenerateCoverLetterModal";
import { ProgressWidget } from "@/components/ui/progress-widget";
import { buildClProgressSteps } from "./cover-letter-utils";
import ATSChecksPanel, { type ATSReport } from "@/components/cv/ATSChecksPanel";

type CLTemplate =
  | "classic_german"
  | "modern_swiss"
  | "executive"
  | "tech_developer"
  | "creative_sidebar"
  | "academic"
  | "compact_pro";

type Phase = "loading" | "generating" | "ready" | "not_found";

interface CLState {
  coverLetterId: string;
  status: string;
  template: CLTemplate;
  letterData: Record<string, unknown> | null;
  preGenInputs: Record<string, unknown> | null;
  jobId: string | null;
  roleTitle: string | null;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");
const POLL_INTERVAL_MS = 2000;


export default function CoverLetterPage({
  params,
}: {
  params: Promise<{ flowId: string }>;
}) {
  const { flowId } = use(params);
  const t = useTranslations("coverLetter");
  const tc = useTranslations("common");

  const [phase, setPhase] = useState<Phase>("loading");
  const [clState, setClState] = useState<CLState | null>(null);
  const [previewKey, setPreviewKey] = useState(0);
  const [showModal, setShowModal] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [panelOpen, setPanelOpen] = useState(true);
  const [atsReport, setAtsReport] = useState<ATSReport>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const init = useCallback(async () => {
    try {
      const flowRes = await fetch(`${API_BASE}/api/flow/${flowId}/state`);
      if (!flowRes.ok) { setPhase("not_found"); return; }
      const flowData = await flowRes.json() as {
        cover_letter_summary?: {
          cover_letter_id: string;
          status: string;
          template: string;
        };
        job_id?: string;
        job_summary?: { role_title?: string };
      };

      const clSummary = flowData.cover_letter_summary;
      if (!clSummary) { setPhase("not_found"); return; }

      const clId = clSummary.cover_letter_id;
      const statusRes = await fetch(`${API_BASE}/api/cover-letter/${clId}/status`);
      if (!statusRes.ok) { setPhase("not_found"); return; }
      const statusData = await statusRes.json() as {
        status: string;
        letter_data?: Record<string, unknown> | null;
      };

      setClState({
        coverLetterId: clId,
        status: statusData.status,
        template: clSummary.template as CLTemplate,
        letterData: statusData.letter_data ?? null,
        preGenInputs: null,
        jobId: flowData.job_id ?? null,
        roleTitle: flowData.job_summary?.role_title ?? null,
      });

      if (statusData.status === "ready") {
        setPhase("ready");
      } else if (statusData.status === "failed") {
        setPhase("not_found");
      } else {
        setPhase("generating");
        startPolling(clId);
      }
    } catch {
      setPhase("not_found");
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flowId]);

  useEffect(() => {
    init();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [init]);

  // Fetch ATS report once cover letter is ready
  useEffect(() => {
    if (phase !== "ready" || !clState?.coverLetterId) return;
    async function fetchAtsReport() {
      try {
        const res = await fetch(`${API_BASE}/api/cover-letter/${clState!.coverLetterId}/ats-report`);
        if (!res.ok) return;
        const data: { report: ATSReport } = await res.json();
        setAtsReport(data.report ?? null);
      } catch {
        // Non-fatal — panel shows unavailable state
      }
    }
    void fetchAtsReport();
  }, [phase, clState?.coverLetterId]);

  function startPolling(clId: string) {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/cover-letter/${clId}/status`);
        if (!res.ok) return;
        const data = await res.json() as {
          status: string;
          letter_data?: Record<string, unknown> | null;
        };
        if (data.status === "ready") {
          clearInterval(pollRef.current!);
          setPhase("ready");
          setClState((prev) =>
            prev ? { ...prev, status: "ready", letterData: data.letter_data ?? null } : prev
          );
        } else {
          setClState((prev) => prev ? { ...prev, status: data.status } : prev);
        }
        if (data.status === "failed") {
          clearInterval(pollRef.current!);
          setPhase("not_found");
        }
      } catch { /* ignore poll errors */ }
    }, POLL_INTERVAL_MS);
  }

  async function handleDownloadPdf() {
    if (!clState) return;
    setDownloading(true);
    try {
      const res = await fetch(`${API_BASE}/api/cover-letter/${clState.coverLetterId}/pdf`);
      if (!res.ok) throw new Error(tc("error"));
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "anschreiben.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } finally {
      setDownloading(false);
    }
  }

  function handleTemplateChange(_template: CLTemplate) {
    setShowModal(true);
  }

  function handleSectionSaved() {
    setPreviewKey((k) => k + 1);
  }

  function handleGenerated(newClId: string) {
    setShowModal(false);
    setClState((prev) =>
      prev ? { ...prev, coverLetterId: newClId, status: "pending" } : prev
    );
    setPhase("generating");
    startPolling(newClId);
  }

  if (phase === "loading") {
    return (
      <div className="flex items-center justify-center min-h-screen text-neutral-400 text-sm">
        {tc("loading")}
      </div>
    );
  }

  if (phase === "not_found") {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen gap-4">
        <p className="text-neutral-500 text-sm">{t("generating")}</p>
        <Link href={`/flow/${flowId}/cv`} className="text-blue-600 hover:underline text-sm">
          {t("viewCV")}
        </Link>
      </div>
    );
  }

  const roleTitle = clState?.roleTitle ?? t("generate");

  return (
    <div className="flex flex-col h-screen overflow-hidden">
      {/* Top bar */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-white border-b border-neutral-200 flex-shrink-0">
        <div className="flex items-center gap-2 text-sm">
          <span className="text-neutral-400">{roleTitle}</span>
          <span className="text-neutral-300" aria-hidden="true">{t("breadcrumbSeparator")}</span>
          <span className="font-semibold">{t("coverLetterTitle")}</span>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href={`/flow/${flowId}/cv`}
            className="px-3 py-1.5 text-sm border border-blue-500 text-blue-600 rounded hover:bg-blue-50 transition-colors"
            data-testid="cl-view-cv-btn"
          >
            {t("viewCV")}
          </Link>
          <button
            type="button"
            onClick={() => void handleDownloadPdf()}
            disabled={downloading || phase !== "ready"}
            className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors disabled:opacity-50"
            data-testid="cl-topbar-download-btn"
          >
            {downloading ? tc("loading") : t("download")}
          </button>
        </div>
      </div>

      {/* Body */}
      {phase === "generating" ? (
        <div className="flex items-center justify-center flex-1 p-8">
          <ProgressWidget
            steps={buildClProgressSteps(clState?.status ?? "pending", t)}
            title={t("progressTitle")}
            subtitle={t("progressSubtitle")}
            className="max-w-sm w-full"
          />
        </div>
      ) : (
        <div className="flex flex-1 min-h-0">
          {/* LEFT: preview */}
          <div className="flex-1 min-w-0 flex flex-col border-r border-neutral-200 bg-neutral-50 p-3 gap-3">
            <CoverLetterDocument
              key={previewKey}
              coverLetterId={clState!.coverLetterId}
            />
            <ATSChecksPanel report={atsReport} />
          </div>

          {/* RIGHT: controls */}
          <div className="flex-shrink-0 flex flex-col overflow-hidden">
            <CoverLetterRefinementPanel
              flowId={flowId}
              coverLetterId={clState!.coverLetterId}
              letterData={clState!.letterData}
              currentTemplate={clState!.template}
              onSectionSaved={handleSectionSaved}
              onTemplateChange={handleTemplateChange}
              onRegenerateCoverLetter={() => setShowModal(true)}
              onDownloadPdf={() => void handleDownloadPdf()}
              downloading={downloading}
              collapsed={!panelOpen}
              onToggleCollapse={() => setPanelOpen((o) => !o)}
            />
          </div>
        </div>
      )}

      {showModal && clState?.jobId && (
        <GenerateCoverLetterModal
          jobId={clState.jobId}
          existingInputs={clState.preGenInputs as GenerateCoverLetterModalProps["existingInputs"]}
          onClose={() => setShowModal(false)}
          onGenerated={handleGenerated}
        />
      )}
    </div>
  );
}

type GenerateCoverLetterModalProps = Parameters<typeof GenerateCoverLetterModal>[0];
