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
import { GenerationProgress } from "@/components/cv/GenerationProgress";
import { CVDocument, type CVDocumentHandle } from "@/components/cv/CVDocument";
import { RefinementPanel } from "@/components/cv/RefinementPanel";
import { WhatNext } from "@/components/cv/WhatNext";
import { PhotoPromptStep } from "@/components/cv/PhotoPromptStep";
import { GenerateCoverLetterModal } from "@/components/cover-letter/GenerateCoverLetterModal";
import { CVPageActionBar } from "@/components/cv/CVPageActionBar";
import { PreDownloadReview } from "@/components/review/PreDownloadReview";
import ATSChecksPanel, { type ATSReport } from "@/components/cv/ATSChecksPanel";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

type Phase = "photo_prompt" | "template_select" | "generating" | "preview" | "complete";
type CVTemplate = "classic_german" | "modern_swiss" | "executive" | "tech_developer" | "creative_sidebar" | "academic" | "compact_pro";

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

  const [phase, setPhase] = useState<Phase | null>(null); // null = initializing
  const [cvId, setCvId] = useState<string | null>(null);
  const [template, setTemplate] = useState<CVTemplate>("classic_german");
  const [flowState, setFlowState] = useState<FlowState | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [profilePhotoUrl, setProfilePhotoUrl] = useState<string | null>(null);
  const [showCoverLetterModal, setShowCoverLetterModal] = useState(false);
  // US147 / ADR-040 — pre-download grounding review + attestation (nudge, not gate).
  const [showDownloadReview, setShowDownloadReview] = useState(false);
  const [attested, setAttested] = useState(false);
  const [panelOpen, setPanelOpen] = useState(true);
  const [atsReport, setAtsReport] = useState<ATSReport>(null);
  // Bumping this counter re-fetches the ATS report after a section save (backend re-audits asynchronously)
  const [atsRefresh, setAtsRefresh] = useState(0);

  const cvDocRef = useRef<CVDocumentHandle>(null);

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
    void fetchAtsReport();
  }, [cvId, phase, atsRefresh]);

  async function handleGenerate(tpl: CVTemplate) {
    if (!flowState) return;
    setTemplate(tpl);
    setIsGenerating(true);
    try {
      const res = await fetch(`${API_BASE}/api/cv/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: flowState.job_id, template: tpl }),
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
    } catch {
      // silently fail
    }
  }

  // --- Preview phase: 70/30 split ---
  if (phase === "preview" && cvId) {
    const isExpired = flowState?.cv_summary
      ? new Date(flowState.cv_summary.expires_at) < new Date()
      : false;

    const expiryWarning = isExpired
      ? { level: "critical" as const, expiresIn: t("expired") }
      : flowState?.cv_summary
        ? {
            level: "warning" as const,
            expiresIn: `${t("availableUntil")} ${new Date(flowState.cv_summary.expires_at).toLocaleDateString()}`,
          }
        : null;

    return (
      <div className="min-h-screen bg-surface-dim" data-testid="cv-page">
        <div className="flex w-full h-[calc(100vh-56px)] gap-0">
          <div className="flex-1 min-w-0 flex flex-col px-4 py-3 gap-3 overflow-hidden">
            {flowState?.job_summary && (
              <h2 className="text-lg font-heading font-bold text-on-surface leading-snug">
                {flowState.job_summary.role_title}
              </h2>
            )}
            <CVPageActionBar
              flowId={flowId}
              applicationId={flowState?.application_id ?? null}
              coverLetterId={flowState?.cover_letter_summary?.cover_letter_id ?? null}
              onDownloadPdf={() => {
                if (attested) void handleDownloadPdf();
                else setShowDownloadReview(true);
              }}
              onGenerateCoverLetter={() => setShowCoverLetterModal(true)}
              onNext={() => setPhase("complete")}
            />
            {showDownloadReview && cvId && (
              <div
                className="fixed inset-0 z-[60] flex items-start justify-center overflow-y-auto bg-black/40 p-4 py-8"
                onClick={() => setShowDownloadReview(false)}
                data-testid="download-review-overlay"
              >
                <div
                  className="max-w-lg w-full max-h-[90vh] overflow-y-auto"
                  onClick={(e) => e.stopPropagation()}
                >
                  <PreDownloadReview
                    cvId={cvId}
                    onAttested={() => {
                      setAttested(true);
                      setShowDownloadReview(false);
                      void handleDownloadPdf();
                    }}
                    onFix={() => {
                      setShowDownloadReview(false);
                      router.push("/profile");
                    }}
                  />
                </div>
              </div>
            )}
            <ATSChecksPanel report={atsReport} />
            <CVDocument cvId={cvId} ref={cvDocRef} className="flex-1" />
          </div>
          <RefinementPanel
            cvId={cvId}
            flowId={flowId}
            roleTitle={flowState?.job_summary?.role_title ?? null}
            gapSummary={{
              gaps: flowState?.gap_summary?.gaps ?? [],
              sections: flowState?.gap_summary?.sections ?? [],
            }}
            cvSummary={{
              sections: flowState?.cv_summary?.sections ?? [],
            }}
            templateLabel={template === "classic_german" ? t("templateClassic") : t("templateModern")}
            matchScore={flowState?.gap_summary?.match_score ?? null}
            expiryWarning={expiryWarning}
            detectedCompany={flowState?.gap_summary?.detected_company ?? null}
            currentAccentHex={flowState?.gap_summary?.current_accent_hex ?? "#003399"}
            onHtmlRefresh={() => {
              cvDocRef.current?.refresh();
              // Re-fetch ATS report after a short delay so the backend re-audit (BackgroundTask ~1s) has landed
              setTimeout(() => setAtsRefresh((n) => n + 1), 2500);
            }}
            onRegenerateSame={() => void handleGenerate(template)}
            onRegenerateDifferent={() => setPhase("template_select")}
            collapsed={!panelOpen}
            onToggleCollapse={() => setPanelOpen((o) => !o)}
          />
        </div>
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
        <TemplateSelector onGenerate={handleGenerate} isLoading={isGenerating} />
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
