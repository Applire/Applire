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

// frontend/app/flow/[flowId]/cv/page.tsx
"use client";

import { useRef } from "react";
import { use, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { TemplateSelector } from "@/components/cv/TemplateSelector";
import { TargetPagesSelect } from "@/components/cv/TargetPagesSelect";
import { GenerationProgress } from "@/components/cv/GenerationProgress";
import { CVDocument, type CVDocumentHandle } from "@/components/cv/CVDocument";
import { DocumentWorkspace } from "@/components/document/DocumentWorkspace";
import { DocumentLanguageSwitch } from "@/components/document/DocumentLanguageSwitch";
import { RefinementSidebar, type SidebarTab } from "@/components/document/RefinementSidebar";
import { ContentTab } from "@/components/cv/ContentTab";
import { DesignTab } from "@/components/cv/DesignTab";
import { CVActionsTab } from "@/components/cv/CVActionsTab";
import { FileText, Palette, Zap } from "lucide-react";
import { WhatNext } from "@/components/cv/WhatNext";
import { PhotoPromptStep } from "@/components/cv/PhotoPromptStep";
import { GenerateCoverLetterModal } from "@/components/cover-letter/GenerateCoverLetterModal";
import { PreDownloadNotice } from "@/components/review/PreDownloadNotice";
import { MarkAppliedPrompt } from "@/components/applications/MarkAppliedPrompt";
import { getSettings, setHidePredownloadNotice } from "@/lib/api/settings";
import { getApplication } from "@/lib/api/applications";
import ATSChecksPanel, { type ATSReport } from "@/components/cv/ATSChecksPanel";
import TruthfulnessPanel, { type TruthfulnessReport } from "@/components/cv/TruthfulnessPanel";
import CriticAdvisoryPanel, { type OutcomeCriticReport } from "@/components/cv/CriticAdvisoryPanel";
import { MobileCommandBar } from "@/components/cv/MobileCommandBar";
import { decodeGained, formatGained, type StaleCVGained } from "@/lib/stale-cv";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

// Non-user-facing Material Symbols identifiers — JS consts to avoid the JSX literal rule
const GROWTH_ICON = "trending_up";
const CLOSE_ICON = "close";

type Phase = "photo_prompt" | "template_select" | "generating" | "preview" | "complete";
type CVTemplate = "classic_german" | "modern_swiss" | "executive" | "tech_developer" | "creative_sidebar" | "academic" | "compact_pro";
const CV_TEMPLATES: readonly CVTemplate[] = ["classic_german", "modern_swiss", "executive", "tech_developer", "creative_sidebar", "academic", "compact_pro"];

interface FlowState {
  job_id: string;
  application_id?: string | null;
  job_summary?: { role_title: string } | null;
  gap_summary?: {
    match_score: number;
    gaps?: Array<{ id: string; label: string }>;
    sections?: Array<{ section_id: string; label: string; content: string; has_override: boolean; gaps: Array<{ id: string; label: string }> }>;
    detected_company?: { name: string; hex: string } | null;
    current_accent_hex?: string | null;
  } | null;
  cv_summary?: { cv_id: string; pdf_url: string; expires_at: string; sections?: Array<{ section_id: string; label: string; content: string; has_override: boolean; gaps: Array<{ id: string; label: string }> }> } | null;
  cover_letter_summary?: { cover_letter_id: string } | null;
}

export default function CVPage({
  params,
}: {
  params: Promise<{ flowId: string }>;
}) {
  const { flowId } = use(params);
  const router = useRouter();
  const t = useTranslations("cv");
  const tDoc = useTranslations("document");
  const tProfile = useTranslations("profile");

  const [phase, setPhase] = useState<Phase | null>(null); // null = initializing
  const [cvId, setCvId] = useState<string | null>(null);
  const [template, setTemplate] = useState<CVTemplate>("classic_german");
  const [flowState, setFlowState] = useState<FlowState | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [profilePhotoUrl, setProfilePhotoUrl] = useState<string | null>(null);
  const [showCoverLetterModal, setShowCoverLetterModal] = useState(false);
  // ADR-040 (amended 2026-07-04): the pre-download AI-content notice (nudge, not gate).
  // `null` = closed.
  const [downloadNotice, setDownloadNotice] = useState<{ canSuppress: boolean } | null>(null);
  // E039/US218 natural-moment prompt: after a download, offer to mark the
  // application as applied (only when it's still in `tracking`). `null` = closed.
  // Carries the downloaded CV id so confirming also pins the sent version (US219).
  const [markAppliedPrompt, setMarkAppliedPrompt] = useState<{
    applicationId: string;
    stampAppliedAt: boolean;
    submittedCvId?: string;
  } | null>(null);
  // E039/US221: arrived via one-click re-tailor — the new version explains
  // itself ("changed because your profile gained X"). null = normal visit.
  // Read from window.location on mount (useSearchParams would force a
  // Suspense boundary on this page for no gain).
  const [retailoredGained, setRetailoredGained] = useState<StaleCVGained[] | null>(null);
  const [panelOpen, setPanelOpen] = useState(true);
  const [atsReport, setAtsReport] = useState<ATSReport>(null);
  // E043/US247: truthfulness self-audit report, fetched alongside the ATS report.
  const [truthReport, setTruthReport] = useState<TruthfulnessReport>(null);
  // ADR-060/E049 49.6: outcome critic advisory report (Pass A, this mount).
  const [criticReport, setCriticReport] = useState<OutcomeCriticReport>(null);
  // E042/US239 (ADR-051 §1): per-generation page-target override, sent as
  // `target_pages` in the generate POST body. Defaults to the user's
  // `target_cv_pages` setting when set, else the DACH standard (2).
  const [targetPages, setTargetPages] = useState<number>(2);
  // Bumping this counter re-fetches the ATS report after a section save (backend re-audits asynchronously)
  const [atsRefresh, setAtsRefresh] = useState(0);
  // E054/US289: the previewed CV's PINNED language (ADR-038 clause 3b) — badge
  // + language-switch state. null = legacy row without a pin (no badge).
  const [docLanguage, setDocLanguage] = useState<"de" | "en" | null>(null);

  const cvDocRef = useRef<CVDocumentHandle>(null);

  useEffect(() => {
    const param = new URLSearchParams(window.location.search).get("retailored");
    if (param) setRetailoredGained(decodeGained(param));
  }, []);

  // E042/US239: seed the page-target selector from the user's saved default.
  // A settings failure just keeps the DACH-standard (2) default — never a gate.
  useEffect(() => {
    let cancelled = false;
    getSettings()
      .then((s) => {
        if (!cancelled) setTargetPages(s.target_cv_pages ?? 2);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  // Restore state from server on mount — determine correct phase before rendering
  useEffect(() => {
    async function advanceFlow(cvId: string) {
      // Advance through cv_generation → complete, ignoring errors if already past that step
      try {
        await fetch(`${API_BASE}/api/flow/${flowId}/advance`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ step: "cv_generation", artifact_id: cvId }),
        });
      } catch {}
      try {
        await fetch(`${API_BASE}/api/flow/${flowId}/advance`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ step: "complete", artifact_id: cvId }),
        });
      } catch {}
    }

    async function init() {
      try {
        const res = await fetch(`${API_BASE}/api/flow/${flowId}/state`);
        if (!res.ok) {
          setPhase("template_select");
          return;
        }
        const fs: FlowState = await res.json();
        setFlowState(fs);
        if (fs.cv_summary?.cv_id) {
          setCvId(fs.cv_summary.cv_id);
          setPhase("preview");
          return;
        }

        // No CV in flow state — check if there's a ready or in-progress CV for this job
        // (happens when the user navigates away before the flow advance completes)
        try {
          const cvListRes = await fetch(`${API_BASE}/api/cv?job_id=${fs.job_id}`);
          if (cvListRes.ok) {
            const cvList: Array<{ cv_id: string; status: string }> = await cvListRes.json();
            const mostRecent = cvList[0];
            if (mostRecent?.status === "ready") {
              await advanceFlow(mostRecent.cv_id);
              setCvId(mostRecent.cv_id);
              setPhase("preview");
              return;
            }
            if (mostRecent?.status === "pending" || mostRecent?.status === "generating") {
              setCvId(mostRecent.cv_id);
              setPhase("generating");
              return;
            }
          }
        } catch {
          // Non-fatal — fall through
        }

        // No existing CV — check if user has a profile photo
        try {
          const profileRes = await fetch(`${API_BASE}/api/profile`);
          if (profileRes.ok) {
            const profileData = await profileRes.json();
            const photoUrl: string | null =
              profileData?.profile?.personal_info?.photo_url ?? null;
            setProfilePhotoUrl(photoUrl);
            setPhase(photoUrl ? "template_select" : "photo_prompt");
            return;
          }
        } catch {
          // Non-fatal — fall through to template_select
        }
        setPhase("template_select");
      } catch {
        // Non-fatal — user sees template picker
        setPhase("template_select");
      }
    }
    void init();
  }, [flowId]);

  // Fetch ATS report once a CV is ready; re-fetch when atsRefresh is bumped (e.g. after section save).
  // Gate on phase === "preview": cvId is set while the CV is still "generating" (the report isn't
  // persisted yet → null), and handleReady reuses the SAME cvId, so a cvId-only dependency would
  // never re-fire after readiness and the panel would stay "unavailable". Re-running on the phase
  // transition (mirrors the cover-letter page's `phase === "ready"` guard) fetches a present report.
  useEffect(() => {
    if (!cvId || phase !== "preview") return;
    async function fetchAtsReport() {
      try {
        const res = await fetch(`${API_BASE}/api/cv/${cvId}/ats-report`);
        if (!res.ok) return;
        const data: { report: ATSReport } = await res.json();
        setAtsReport(data.report ?? null);
      } catch {
        // Non-fatal — panel shows unavailable state
      }
    }
    async function fetchTruthReport() {
      try {
        const res = await fetch(`${API_BASE}/api/cv/${cvId}/truthfulness-report`);
        if (!res.ok) return;
        const data: { report: TruthfulnessReport } = await res.json();
        setTruthReport(data.report ?? null);
      } catch {
        // Non-fatal — panel shows unavailable state
      }
    }
    async function fetchCriticReport() {
      try {
        const res = await fetch(`${API_BASE}/api/cv/${cvId}/critic-report`);
        if (!res.ok) return;
        const data: { report: OutcomeCriticReport } = await res.json();
        setCriticReport(data.report ?? null);
      } catch {
        // Non-fatal — advisory panel simply doesn't render
      }
    }
    void fetchAtsReport();
    void fetchTruthReport();
    void fetchCriticReport();
  }, [cvId, phase, atsRefresh]);

  // E054/US289: read the previewed CV's pinned document_language (badge +
  // switch) and seed the template from the status response — after a reload
  // the local template state would otherwise default to classic_german and
  // "regenerate in the same template" (incl. the language switch) would
  // silently change the template.
  useEffect(() => {
    if (!cvId || phase !== "preview") return;
    let cancelled = false;
    fetch(`${API_BASE}/api/cv/${cvId}/status`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data: { document_language?: "de" | "en" | null; template?: string | null } | null) => {
        if (cancelled || !data) return;
        setDocLanguage(data.document_language ?? null);
        if (data.template && (CV_TEMPLATES as readonly string[]).includes(data.template)) {
          setTemplate(data.template as CVTemplate);
        }
      })
      .catch(() => {
        // Non-fatal — no badge, template keeps its current value
      });
    return () => {
      cancelled = true;
    };
  }, [cvId, phase]);

  async function handleGenerate(tpl: CVTemplate) {
    if (!flowState) return;
    setTemplate(tpl);
    setIsGenerating(true);
    try {
      const res = await fetch(`${API_BASE}/api/cv/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: flowState.job_id, template: tpl, target_pages: targetPages }),
      });
      if (!res.ok) return;
      const data: { cv_id: string; status: string; expires_at: string } = await res.json();
      setCvId(data.cv_id);
      setPhase("generating");
    } finally {
      setIsGenerating(false);
    }
  }

  function handleReady(readyCvId: string) {
    setCvId(readyCvId);
    fetch(`${API_BASE}/api/flow/${flowId}/advance`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ step: "complete", artifact_id: readyCvId }),
    })
      .then(() => fetch(`${API_BASE}/api/flow/${flowId}/state`))
      .then((r) => r.json())
      .then((fs: FlowState) => setFlowState(fs))
      .catch(() => {});
    setPhase("preview");
  }

  async function handleDownloadPdf() {
    try {
      const res = await fetch(`${API_BASE}/api/cv/${cvId}/pdf`);
      if (!res.ok) return;
      const blob = await res.blob();
      const disposition = res.headers.get("Content-Disposition") ?? "";
      const match = disposition.match(/filename="([^"]+)"/);
      const filename = match?.[1] ?? `lebenslauf-${cvId!.slice(0, 8)}.pdf`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
      void offerMarkApplied();
    } catch {
      // silently fail
    }
  }

  // The download is the cheapest truthful moment to update the pipeline status
  // (E039/US218, FMEA JF-E-P2.1). Only nudge while the application is still
  // `tracking` — anything later means the user already maintains the status.
  async function offerMarkApplied() {
    const applicationId = flowState?.application_id;
    if (!applicationId) return;
    try {
      const app = await getApplication(applicationId);
      if (app.user_status === "tracking") {
        setMarkAppliedPrompt({
          applicationId,
          stampAppliedAt: app.applied_at == null,
          submittedCvId: cvId ?? undefined,
        });
      }
    } catch {
      // Best-effort — no prompt is fine.
    }
  }

  // ADR-040 amendment: show the AI-content notice unless dismissed-forever. A
  // settings failure degrades to "show the notice" — never a gate (ADR-040 §4).
  async function requestDownload() {
    if (!cvId) return;
    const hideNotice = await getSettings()
      .then((s) => s.hide_predownload_notice)
      .catch(() => false);
    if (hideNotice) {
      void handleDownloadPdf();
      return;
    }
    setDownloadNotice({ canSuppress: true });
  }

  // --- Preview phase: 70/30 split ---
  if (phase === "preview" && cvId) {
    const isExpired = flowState?.cv_summary
      ? new Date(flowState.cv_summary.expires_at) < new Date()
      : false;

    const validity = flowState?.cv_summary
      ? {
          label: isExpired
            ? t("expired")
            : tDoc("validUntil", { date: new Date(flowState.cv_summary.expires_at).toLocaleDateString() }),
          level: (isExpired ? "critical" : "warning") as "critical" | "warning",
        }
      : null;

    const refreshPreviewAndAts = () => {
      cvDocRef.current?.refresh();
      // Re-fetch ATS report after a short delay so the backend re-audit (BackgroundTask ~1s) has landed
      setTimeout(() => setAtsRefresh((n) => n + 1), 2500);
    };

    const flowSummary = {
      job_summary: flowState?.job_summary?.role_title ?? null,
      gap_summary: {
        gaps: flowState?.gap_summary?.gaps ?? [],
        sections: flowState?.gap_summary?.sections ?? [],
      },
      cv_summary: { sections: flowState?.cv_summary?.sections ?? [] },
    };

    const sidebarTabs: SidebarTab[] = [
      {
        id: "content",
        label: t("contentTab"),
        icon: <FileText className="w-4 h-4" aria-hidden="true" />,
        body: (
          <ContentTab
            cvId={cvId}
            flowSummary={flowSummary}
            onSectionSave={refreshPreviewAndAts}
            onUnsavedChange={() => {}}
          />
        ),
      },
      {
        id: "design",
        label: t("designTab"),
        icon: <Palette className="w-4 h-4" aria-hidden="true" />,
        body: (
          <DesignTab
            cvId={cvId}
            templateLabel={template === "classic_german" ? t("templateClassic") : t("templateModern")}
            detectedCompany={flowState?.gap_summary?.detected_company ?? null}
            currentAccentHex={flowState?.gap_summary?.current_accent_hex ?? "#003399"}
            onColorApplied={refreshPreviewAndAts}
            onChangeTemplate={() => setPhase("template_select")}
            onRegenerateSame={() => void handleGenerate(template)}
          />
        ),
      },
      {
        id: "actions",
        label: t("actionsTab"),
        icon: <Zap className="w-4 h-4" aria-hidden="true" />,
        body: (
          <CVActionsTab
            flowId={flowId}
            applicationId={flowState?.application_id ?? null}
            coverLetterId={flowState?.cover_letter_summary?.cover_letter_id ?? null}
            cvId={cvId}
            onGenerateCoverLetter={() => setShowCoverLetterModal(true)}
            onRegenerateSame={() => void handleGenerate(template)}
            onNext={() => setPhase("complete")}
            languageSwitch={
              flowState?.application_id ? (
                <DocumentLanguageSwitch
                  applicationId={flowState.application_id}
                  documentLanguage={docLanguage}
                  overriddenSectionLabels={
                    flowState?.cv_summary?.sections
                      ?.filter((s) => s.has_override)
                      .map((s) => s.label) ?? []
                  }
                  // ADR-038 clause 6: the switch IS the existing regeneration
                  // path — same template, new GeneratedCV, overrides fall.
                  onSwitched={() => void handleGenerate(template)}
                  apiBase={API_BASE}
                />
              ) : undefined
            }
          />
        ),
      },
    ];

    return (
      <div data-testid="cv-page">
        {/* E039/US221: the freshly re-tailored version explains itself —
            "changed because your profile gained X" (delta from the nudge). */}
        {retailoredGained !== null && (
          <div
            className="fixed top-16 left-1/2 -translate-x-1/2 z-[55] max-w-xl w-[calc(100%-2rem)] flex items-start gap-2 p-3 rounded-lg bg-primary-container border border-primary/30 shadow-md"
            data-testid="retailored-note"
          >
            <span className="material-symbols-outlined text-primary" aria-hidden="true" style={{ fontSize: 18 }}>
              {GROWTH_ICON}
            </span>
            <p className="flex-1 text-sm text-on-surface">
              {retailoredGained.length > 0
                ? t("retailoredNote", { gained: formatGained(retailoredGained, tProfile) })
                : t("retailoredNotePlain")}
            </p>
            <button
              type="button"
              onClick={() => setRetailoredGained(null)}
              aria-label={t("retailoredNoteClose")}
              className="text-on-surface-variant hover:text-on-surface"
            >
              <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 18 }}>
                {CLOSE_ICON}
              </span>
            </button>
          </div>
        )}
        <DocumentWorkspace
          flowId={flowId}
          activeDoc="cv"
          documentLanguage={docLanguage}
          onDownloadPdf={() => void requestDownload()}
          preview={<CVDocument cvId={cvId} ref={cvDocRef} className="flex-1" />}
          atsPanel={
            <div className="space-y-2">
              <ATSChecksPanel report={atsReport} />
              <TruthfulnessPanel report={truthReport} atsReport={atsReport} />
              <CriticAdvisoryPanel report={criticReport} />
            </div>
          }
          sidebar={
            <RefinementSidebar
              matchScore={
                flowState?.gap_summary?.match_score != null
                  ? flowState.gap_summary.match_score * 100
                  : null
              }
              validity={validity}
              tabs={sidebarTabs}
              collapsed={!panelOpen}
              onToggleCollapse={() => setPanelOpen((o) => !o)}
            />
          }
          commandBar={
            <MobileCommandBar
              atsReport={atsReport}
              atsPanel={
                <div className="space-y-2">
                  <ATSChecksPanel report={atsReport} />
                  <TruthfulnessPanel report={truthReport} atsReport={atsReport} />
                  <CriticAdvisoryPanel report={criticReport} />
                </div>
              }
              fineTuneSurface={
                <ContentTab
                  cvId={cvId}
                  flowSummary={flowSummary}
                  onSectionSave={refreshPreviewAndAts}
                  onUnsavedChange={() => {}}
                />
              }
              onDownloadPdf={() => void requestDownload()}
            />
          }
        />
        {downloadNotice && (
          <div
            className="fixed inset-0 z-[60] flex items-start justify-center overflow-y-auto bg-black/40 p-4 py-8"
            onClick={() => setDownloadNotice(null)}
            data-testid="download-review-overlay"
          >
            <div className="max-w-md w-full" onClick={(e) => e.stopPropagation()}>
              <PreDownloadNotice
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
        {markAppliedPrompt && (
          <MarkAppliedPrompt
            applicationId={markAppliedPrompt.applicationId}
            stampAppliedAt={markAppliedPrompt.stampAppliedAt}
            submittedCvId={markAppliedPrompt.submittedCvId}
            onClose={() => setMarkAppliedPrompt(null)}
          />
        )}
        {showCoverLetterModal && flowState?.job_id && (
          <GenerateCoverLetterModal
            jobId={flowState.job_id.toString()}
            onClose={() => setShowCoverLetterModal(false)}
            onGenerated={(_clId) => {
              setShowCoverLetterModal(false);
              router.push(`/flow/${flowId}/cover-letter`);
            }}
          />
        )}
      </div>
    );
  }


  if (phase === null) {
    return (
      <div className="p-6 min-h-screen bg-neutral-light flex items-center justify-center" data-testid="cv-page">
        <div className="w-8 h-8 border-4 border-teal border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="p-6 min-h-screen bg-neutral-light" data-testid="cv-page">
      {phase === "photo_prompt" && (
        <PhotoPromptStep
          currentPhotoUrl={profilePhotoUrl}
          onContinue={() => setPhase("template_select")}
          onPhotoChange={(url) => setProfilePhotoUrl(url)}
        />
      )}

      {phase === "template_select" && (
        <TemplateSelector
          onGenerate={handleGenerate}
          isLoading={isGenerating}
          extraControls={
            <TargetPagesSelect value={targetPages} onChange={(v) => setTargetPages(v ?? 2)} />
          }
        />
      )}

      {phase === "generating" && cvId && (
        <GenerationProgress
          cvId={cvId}
          flowId={flowId}
          onReady={handleReady}
          onRetry={() => setPhase("template_select")}
        />
      )}

      {phase === "complete" && (
        <WhatNext flowId={flowId} roleTitle={flowState?.job_summary?.role_title} />
      )}
    </div>
  );
}
