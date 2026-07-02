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
import { CoverLetterContentTab } from "@/components/cover-letter/CoverLetterContentTab";
import { CoverLetterDesignTab } from "@/components/cover-letter/CoverLetterDesignTab";
import { CoverLetterActionsTab } from "@/components/cover-letter/CoverLetterActionsTab";
import { DocumentWorkspace } from "@/components/document/DocumentWorkspace";
import { RefinementSidebar, type SidebarTab } from "@/components/document/RefinementSidebar";
import { FileText, Palette, Zap } from "lucide-react";
import { GenerateCoverLetterModal } from "@/components/cover-letter/GenerateCoverLetterModal";
import { ProgressWidget } from "@/components/ui/progress-widget";
import { buildClProgressSteps } from "./cover-letter-utils";
import ATSChecksPanel, { type ATSReport } from "@/components/cv/ATSChecksPanel";
import { PreDownloadNotice } from "@/components/review/PreDownloadNotice";
import { getSettings, setHidePredownloadNotice } from "@/lib/api/settings";

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
  matchScore: number | null;
  expiresAt: string | null;
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
  const tDoc = useTranslations("document");

  const [phase, setPhase] = useState<Phase>("loading");
  const [clState, setClState] = useState<CLState | null>(null);
  const [previewKey, setPreviewKey] = useState(0);
  const [showModal, setShowModal] = useState(false);
  const [downloading, setDownloading] = useState(false);
  // US170 / ADR-040 §3/§4 — pre-download attestation nudge (nudge, not gate).
  // ADR-040 (amended 2026-07-01): pre-download notice. A cover letter is prose, so
  // there are never red flags — only the dismissible AI-content notice. `null` = closed.
  const [downloadNotice, setDownloadNotice] = useState<{ canSuppress: boolean } | null>(null);
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
        gap_summary?: { match_score?: number };
        cv_summary?: { expires_at?: string };
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
        // gap_summary.match_score is a 0–1 fraction; the sidebar expects 0–100.
        matchScore:
          flowData.gap_summary?.match_score != null
            ? flowData.gap_summary.match_score * 100
            : null,
        expiresAt: flowData.cv_summary?.expires_at ?? null,
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

  // ADR-040 amendment: show the AI-content notice unless dismissed-forever. No red
  // flags for prose. A settings failure degrades to "download directly" (never a gate).
  async function requestDownload() {
    const hideNotice = await getSettings()
      .then((s) => s.hide_predownload_notice)
      .catch(() => false);
    if (hideNotice) {
      void handleDownloadPdf();
      return;
    }
    setDownloadNotice({ canSuppress: true });
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

  if (phase === "generating") {
    return (
      <div className="flex items-center justify-center h-[calc(100vh-56px)] p-8">
        <ProgressWidget
          steps={buildClProgressSteps(clState?.status ?? "pending", t)}
          title={t("progressTitle")}
          subtitle={t("progressSubtitle")}
          className="max-w-sm w-full"
        />
      </div>
    );
  }

  const validity =
    clState?.expiresAt != null
      ? {
          label: tDoc("validUntil", { date: new Date(clState.expiresAt).toLocaleDateString() }),
          level: "warning" as const,
        }
      : null;

  const sidebarTabs: SidebarTab[] = [
    {
      id: "content",
      label: t("contentTab"),
      icon: <FileText className="w-4 h-4" aria-hidden="true" />,
      body: (
        <CoverLetterContentTab
          coverLetterId={clState!.coverLetterId}
          letterData={clState!.letterData as Parameters<typeof CoverLetterContentTab>[0]["letterData"]}
          onSectionSaved={handleSectionSaved}
        />
      ),
    },
    {
      id: "design",
      label: t("designTab"),
      icon: <Palette className="w-4 h-4" aria-hidden="true" />,
      body: (
        <CoverLetterDesignTab
          flowId={flowId}
          currentTemplate={clState!.template}
          onTemplateChange={handleTemplateChange}
        />
      ),
    },
    {
      id: "actions",
      label: t("actionsTab"),
      icon: <Zap className="w-4 h-4" aria-hidden="true" />,
      body: (
        <CoverLetterActionsTab
          onRegenerateCoverLetter={() => setShowModal(true)}
        />
      ),
    },
  ];

  return (
    <div data-testid="cover-letter-page">
      <DocumentWorkspace
        flowId={flowId}
        activeDoc="cover-letter"
        onDownloadPdf={requestDownload}
        downloadDisabled={downloading || phase !== "ready"}
        preview={<CoverLetterDocument key={previewKey} coverLetterId={clState!.coverLetterId} />}
        atsPanel={<ATSChecksPanel report={atsReport} />}
        sidebar={
          <RefinementSidebar
            matchScore={clState?.matchScore ?? null}
            validity={validity}
            tabs={sidebarTabs}
            collapsed={!panelOpen}
            onToggleCollapse={() => setPanelOpen((o) => !o)}
          />
        }
      />

      {downloadNotice && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4"
          onClick={() => setDownloadNotice(null)}
          data-testid="cl-download-review-overlay"
        >
          <div className="max-w-md w-full" onClick={(e) => e.stopPropagation()}>
            <PreDownloadNotice
              redFlags={[]}
              canSuppress={downloadNotice.canSuppress}
              onConfirm={(dontShowAgain) => {
                if (dontShowAgain) void setHidePredownloadNotice(true);
                setDownloadNotice(null);
                void handleDownloadPdf();
              }}
              onCancel={() => setDownloadNotice(null)}
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
