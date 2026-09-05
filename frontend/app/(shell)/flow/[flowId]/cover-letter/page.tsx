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
import { DocumentLanguageSwitch } from "@/components/document/DocumentLanguageSwitch";
import { DocumentIdentityBar } from "@/components/document/DocumentIdentityBar";
import { DocumentExportFooter } from "@/components/document/DocumentExportFooter";
import { ReviewSurface } from "@/components/document/ReviewSurface";
import { RefinementSidebar, type SidebarTab } from "@/components/document/RefinementSidebar";
import { ClipboardCheck, Palette, Zap } from "lucide-react";
import { GenerateCoverLetterModal } from "@/components/cover-letter/GenerateCoverLetterModal";
import { ProgressWidget } from "@/components/ui/progress-widget";
import { buildClProgressSteps } from "./cover-letter-utils";
import ATSChecksPanel, { type ATSReport } from "@/components/cv/ATSChecksPanel";
import { type TruthfulnessReport } from "@/components/cv/TruthfulnessPanel";
import { type OutcomeCriticReport } from "@/components/cv/CriticAdvisoryPanel";
import UnaskedRequirementsPanel, {
  type UnaskedRequirement,
} from "@/components/gaps/UnaskedRequirementsPanel";
import { PreDownloadNotice } from "@/components/review/PreDownloadNotice";
import { getSettings, setHidePredownloadNotice } from "@/lib/api/settings";
import { buildReviewGroups } from "@/lib/review-groups";
import type { ReviewModePreference } from "@/lib/review-walked";
import { extractFilenameFromContentDisposition } from "@/lib/download-filename";

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
  applicationId: string | null;
  roleTitle: string | null;
  matchScore: number | null;
  expiresAt: string | null;
  /** E054/US289: the letter's PINNED language (clause 3b); null = legacy row. */
  documentLanguage: "de" | "en" | null;
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
  // US298 (E057 task 1.5): carries WHICH format the notice gates — the same
  // notice, extended (ADR-079 cl.6), not a second surface for the .docx export.
  const [downloadNotice, setDownloadNotice] = useState<{
    canSuppress: boolean;
    format: "pdf" | "docx";
  } | null>(null);
  const [panelOpen, setPanelOpen] = useState(true);
  const [atsReport, setAtsReport] = useState<ATSReport>(null);
  // E043/US247: truthfulness self-audit report, fetched alongside the ATS report.
  const [truthReport, setTruthReport] = useState<TruthfulnessReport>(null);
  // ADR-060/E049 49.6: outcome critic advisory report (Pass B, cross-document mount).
  const [criticReport, setCriticReport] = useState<OutcomeCriticReport>(null);
  // ADR-074 (#526): JD hard requirements Applire holds nothing on and never
  // asked about. Derived per APPLICATION from the persisted keyword ledger, so
  // it rides the gap-analysis response the gaps page already reads rather than a
  // report column on this letter — it cannot drift past a post-interview
  // recompute, and it clears itself once the candidate is asked and answers.
  const [unasked, setUnasked] = useState<UnaskedRequirement[]>([]);
  // E058/US301 (ADR-081 cl. 5): the stored review-mode preference. `auto` is
  // both the default and the degraded value — a settings failure must never
  // decide the mode for the user.
  const [reviewMode, setReviewMode] = useState<ReviewModePreference>("auto");

  useEffect(() => {
    let cancelled = false;
    getSettings()
      .then((s) => {
        if (!cancelled) setReviewMode(s.review_mode ?? "auto");
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);
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
        application_id?: string | null;
        job_summary?: { role_title?: string };
        gap_summary?: { match_score?: number };
        cv_summary?: { expires_at?: string };
      };

      const clSummary = flowData.cover_letter_summary;
      if (!clSummary) {
        // F2 (blind PQ blocker): no letter has been generated yet — this is a
        // legitimate empty state, not an error. Keep jobId so the not_found view can
        // offer a "Generate" CTA via the same modal the sidebar Actions tab uses.
        setClState({
          coverLetterId: "",
          status: "none",
          template: "classic_german",
          letterData: null,
          preGenInputs: null,
          jobId: flowData.job_id ?? null,
          applicationId: flowData.application_id ?? null,
          roleTitle: flowData.job_summary?.role_title ?? null,
          matchScore: null,
          expiresAt: null,
          documentLanguage: null,
        });
        setPhase("not_found");
        return;
      }

      const clId = clSummary.cover_letter_id;
      const statusRes = await fetch(`${API_BASE}/api/cover-letter/${clId}/status`);
      if (!statusRes.ok) { setPhase("not_found"); return; }
      const statusData = await statusRes.json() as {
        status: string;
        letter_data?: Record<string, unknown> | null;
        document_language?: "de" | "en" | null;
      };

      setClState({
        coverLetterId: clId,
        status: statusData.status,
        template: clSummary.template as CLTemplate,
        letterData: statusData.letter_data ?? null,
        preGenInputs: null,
        jobId: flowData.job_id ?? null,
        applicationId: flowData.application_id ?? null,
        roleTitle: flowData.job_summary?.role_title ?? null,
        // gap_summary.match_score is a 0–1 fraction; the sidebar expects 0–100.
        matchScore:
          flowData.gap_summary?.match_score != null
            ? flowData.gap_summary.match_score * 100
            : null,
        expiresAt: flowData.cv_summary?.expires_at ?? null,
        documentLanguage: statusData.document_language ?? null,
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
    async function fetchTruthReport() {
      try {
        const res = await fetch(
          `${API_BASE}/api/cover-letter/${clState!.coverLetterId}/truthfulness-report`,
        );
        if (!res.ok) return;
        const data: { report: TruthfulnessReport } = await res.json();
        setTruthReport(data.report ?? null);
      } catch {
        // Non-fatal — panel shows unavailable state
      }
    }
    async function fetchCriticReport() {
      try {
        const res = await fetch(
          `${API_BASE}/api/cover-letter/${clState!.coverLetterId}/critic-report`,
        );
        if (!res.ok) return;
        const data: { report: OutcomeCriticReport } = await res.json();
        setCriticReport(data.report ?? null);
      } catch {
        // Non-fatal — advisory panel simply doesn't render
      }
    }
    async function fetchUnaskedRequirements() {
      if (!clState?.jobId) return;
      try {
        const res = await fetch(`${API_BASE}/api/job/${clState.jobId}/gaps`);
        if (!res.ok) return;
        const data: { unasked_requirements?: UnaskedRequirement[] } = await res.json();
        setUnasked(data.unasked_requirements ?? []);
      } catch {
        // Non-fatal — the panel simply doesn't render. The omission itself is
        // recorded server-side on the always-on LETTER_UNASKED_REQUIREMENTS line
        // regardless of whether this fetch succeeds.
      }
    }
    void fetchAtsReport();
    void fetchTruthReport();
    void fetchCriticReport();
    void fetchUnaskedRequirements();
  }, [phase, clState?.coverLetterId, clState?.jobId]);

  function startPolling(clId: string) {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/cover-letter/${clId}/status`);
        if (!res.ok) return;
        const data = await res.json() as {
          status: string;
          letter_data?: Record<string, unknown> | null;
          document_language?: "de" | "en" | null;
        };
        if (data.status === "ready") {
          clearInterval(pollRef.current!);
          setPhase("ready");
          setClState((prev) =>
            prev
              ? {
                  ...prev,
                  status: "ready",
                  letterData: data.letter_data ?? null,
                  documentLanguage: data.document_language ?? null,
                }
              : prev
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
      // issue #246 (NEW-5) — use the server's own Content-Disposition filename
      // (jd_language-aware, PR #242) instead of a hardcoded German default that
      // silently overrode it for every letter, English ones included.
      const filename =
        extractFilenameFromContentDisposition(res.headers.get("Content-Disposition")) ??
        "anschreiben.pdf";
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } finally {
      setDownloading(false);
    }
  }

  // US298 (E057 task 1.5, ADR-079): the editable Word export. Mirrors
  // handleDownloadPdf exactly — same on-demand REST door
  // (GET /api/cover-letter/{id}/docx, already live), only the extension and
  // fallback filename differ.
  async function handleDownloadDocx() {
    if (!clState) return;
    setDownloading(true);
    try {
      const res = await fetch(`${API_BASE}/api/cover-letter/${clState.coverLetterId}/docx`);
      if (!res.ok) throw new Error(tc("error"));
      const filename =
        extractFilenameFromContentDisposition(res.headers.get("Content-Disposition")) ??
        "anschreiben.docx";
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
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
  // US298: the SAME gate now also covers the office (.docx) export — `format`
  // says which download the notice (and a subsequent confirm) is for.
  async function requestDownload(format: "pdf" | "docx" = "pdf") {
    const hideNotice = await getSettings()
      .then((s) => s.hide_predownload_notice)
      .catch(() => false);
    if (hideNotice) {
      if (format === "docx") void handleDownloadDocx();
      else void handleDownloadPdf();
      return;
    }
    setDownloadNotice({ canSuppress: true, format });
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
      <div
        className="flex flex-col items-center justify-center min-h-screen gap-4 text-center px-4"
        data-testid="cl-not-found"
      >
        <p className="text-on-surface text-base font-medium">{t("notFoundTitle")}</p>
        <p className="text-on-surface-variant text-sm max-w-sm">{t("notFoundHint")}</p>
        {clState?.jobId && (
          <button
            type="button"
            onClick={() => setShowModal(true)}
            className="btn-pill-primary mt-2"
            data-testid="cl-not-found-generate"
          >
            {t("generateArrow")}
          </button>
        )}
        <Link href={`/flow/${flowId}/cv`} className="text-blue-600 hover:underline text-sm">
          {t("viewCV")}
        </Link>
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

  // ADR-081 cl. 2 (E058/US300): the findings, grouped by the user's question.
  // The letter has no gap-analysis cluster producer — §5.3.26's clusters are
  // computed against the CV — so that producer is declared absent rather than
  // reported as empty (which would claim there are none) or unknown (which
  // would claim it failed).
  const reviewSurface = (
    <ReviewSurface
      documentKind="cover-letter"
      documentId={clState?.coverLetterId ?? null}
      atsReport={atsReport}
      truthReport={truthReport}
      criticReport={criticReport}
      gapClusters={[]}
      hasClusterProducer={false}
      modePreference={reviewMode}
    >
      {/* Not one of ADR-081's four producers — rendered after the groups so it
          can never be mistaken for one of them. */}
      <UnaskedRequirementsPanel requirements={unasked} />
    </ReviewSurface>
  );

  const group1Count = buildReviewGroups({
    atsReport,
    truthReport,
    criticReport,
    gapClusters: [],
    hasClusterProducer: false,
  }).find((g) => g.id === 1)!.items.length;

  const sidebarTabs: SidebarTab[] = [
    {
      id: "review",
      label: tDoc("tabReview"),
      icon: <ClipboardCheck className="w-4 h-4" aria-hidden="true" />,
      badge:
        group1Count > 0 ? (
          <span
            data-testid="tab-badge-review"
            className="inline-flex min-w-4 items-center justify-center rounded-full bg-critical-container px-1 text-[10px] font-bold text-critical"
          >
            {group1Count}
          </span>
        ) : undefined,
      body: reviewSurface,
    },
    {
      id: "edit",
      label: tDoc("tabEdit"),
      icon: <Palette className="w-4 h-4" aria-hidden="true" />,
      body: (
        <div className="flex flex-col gap-3">
          <CoverLetterContentTab
            coverLetterId={clState!.coverLetterId}
            letterData={clState!.letterData as Parameters<typeof CoverLetterContentTab>[0]["letterData"]}
            onSectionSaved={handleSectionSaved}
          />
          {/* ADR-081 cl. 3: fact pins live on the editing tab, outside the
              finding groups, application-scoped (ADR-077 cl. 1). */}
          <ATSChecksPanel report={atsReport} variant="pins" />
          <CoverLetterDesignTab
            flowId={flowId}
            currentTemplate={clState!.template}
            onTemplateChange={handleTemplateChange}
          />
        </div>
      ),
    },
    {
      id: "actions",
      label: t("actionsTab"),
      icon: <Zap className="w-4 h-4" aria-hidden="true" />,
      body: (
        <CoverLetterActionsTab
          onRegenerateCoverLetter={() => setShowModal(true)}
          languageSwitch={
            clState?.applicationId ? (
              <DocumentLanguageSwitch
                applicationId={clState.applicationId}
                documentLanguage={clState.documentLanguage}
                // The page does not know WHICH letter sections carry manual
                // overrides — the switch falls back to the generic loss notice.
                // ADR-038 clause 6: after persisting the override, hand off to
                // the letter's existing regeneration path (the modal).
                onSwitched={() => setShowModal(true)}
                apiBase={API_BASE}
              />
            ) : undefined
          }
        />
      ),
    },
  ];

  return (
    <div data-testid="cover-letter-page">
      <DocumentWorkspace
        preview={<CoverLetterDocument key={previewKey} coverLetterId={clState!.coverLetterId} />}
        sidebar={
          <RefinementSidebar
            matchScore={clState?.matchScore ?? null}
            validity={validity}
            tabs={sidebarTabs}
            collapsed={!panelOpen}
            onToggleCollapse={() => setPanelOpen((o) => !o)}
            initialTabId="review"
            identityBar={
              <DocumentIdentityBar
                flowId={flowId}
                activeDoc="cover-letter"
                documentLanguage={clState?.documentLanguage ?? null}
              />
            }
            pinnedFooter={
              <DocumentExportFooter
                onDownloadPdf={() => void requestDownload("pdf")}
                onDownloadDocx={() => void requestDownload("docx")}
                downloadDisabled={downloading || phase !== "ready"}
              />
            }
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
              canSuppress={downloadNotice.canSuppress}
              format={downloadNotice.format}
              onConfirm={(dontShowAgain) => {
                if (dontShowAgain) void setHidePredownloadNotice(true);
                const format = downloadNotice.format;
                setDownloadNotice(null);
                if (format === "docx") void handleDownloadDocx();
                else void handleDownloadPdf();
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
