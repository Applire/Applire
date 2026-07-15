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
import Link from "next/link";
import { useTranslations } from "next-intl";
import { CheckCircle2, FileText, Loader2, AlertTriangle, Send, X } from "lucide-react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { patchSubmittedCv, patchSubmittedCoverLetter } from "@/lib/api/applications";
import type { ApplicationDetail, CoverLetterSummary } from "@/app/(shell)/applications/[appId]/page";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

/** Shape of a row in GET /api/cv?job_id (CVStatusResponse, US232 fields). */
interface CvListItem {
  cv_id: string;
  status: "pending" | "generating" | "ready" | "failed" | "expired";
  template?: string | null;
  created_at?: string | null;
  pdf_url?: string | null;
  error_code?: string | null;
}

// Template id → next-intl key in the `cv` namespace — the same mapping
// TemplateSelector.tsx uses, so labels stay localized (de/en) and consistent.
// (DocumentsTable.tsx's hardcoded-English Record is pre-existing debt, not
// the precedent.)
const TEMPLATE_NAME_KEYS: Record<string, string> = {
  classic_german: "templateClassic",
  modern_swiss: "templateModern",
  executive: "templateExecutive",
  tech_developer: "templateTech",
  creative_sidebar: "templateCreative",
  academic: "templateAcademic",
  compact_pro: "templateCompact",
};

interface DossierDocumentsZoneProps {
  application: ApplicationDetail;
  coverLetter: CoverLetterSummary | null;
  onError: (message: string) => void;
  onPinChange: () => void;
}

/**
 * Documents zone (E041/US232) — "what exactly do THEY have?" for the FULL
 * version history, not just the last one. Every generated CV version is
 * listed (expired ones hidden); the pin (`submitted_cv_id`) is an explicit
 * user act, never guessed (US219 never-guess boundary). The cover letter
 * mirrors the same pin symmetry via `submitted_cover_letter_id`.
 *
 * Pin identity lives entirely in the `application` prop — this component
 * holds no local pin state, so it can never drift from what the page (and
 * therefore the backend) actually knows. `onPinChange` asks the page to
 * refetch the application after a successful PATCH.
 */
export function DossierDocumentsZone({
  application,
  coverLetter,
  onError,
  onPinChange,
}: DossierDocumentsZoneProps) {
  const t = useTranslations("applications");
  const tCv = useTranslations("cv");

  const [cvs, setCvs] = useState<CvListItem[]>([]);
  const [savingId, setSavingId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function loadCvs() {
      try {
        const res = await fetch(`${API_BASE}/api/cv?job_id=${application.job_analysis_id}`);
        if (!res.ok) {
          if (!cancelled) onError(t("documentsLoadFailed"));
          return;
        }
        const list: CvListItem[] = await res.json();
        if (!cancelled) setCvs(Array.isArray(list) ? list : []);
      } catch {
        if (!cancelled) onError(t("documentsLoadFailed"));
      }
    }
    void loadCvs();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [application.job_analysis_id]);

  function failedCvMessage(errorCode: string | null | undefined): string {
    if (errorCode === "llm_truncated") return tCv("generationFailedTruncated");
    if (errorCode === "llm_timeout") return tCv("generationFailedTimeout");
    return tCv("generationFailed");
  }

  function openPdf(url: string) {
    window.open(url, "_blank", "noopener,noreferrer");
  }

  async function handleMarkCvSent(cvId: string) {
    setSavingId(cvId);
    try {
      await patchSubmittedCv(application.id, cvId);
      onPinChange();
    } catch {
      onError(t("pinActionFailed"));
    } finally {
      setSavingId(null);
    }
  }

  async function handleUnpinCv() {
    setSavingId("unpin-cv");
    try {
      await patchSubmittedCv(application.id, null);
      onPinChange();
    } catch {
      onError(t("pinActionFailed"));
    } finally {
      setSavingId(null);
    }
  }

  async function handleMarkClSent(clId: string) {
    setSavingId(clId);
    try {
      await patchSubmittedCoverLetter(application.id, clId);
      onPinChange();
    } catch {
      onError(t("pinActionFailed"));
    } finally {
      setSavingId(null);
    }
  }

  async function handleUnpinCl() {
    setSavingId("unpin-cl");
    try {
      await patchSubmittedCoverLetter(application.id, null);
      onPinChange();
    } catch {
      onError(t("pinActionFailed"));
    } finally {
      setSavingId(null);
    }
  }

  const visibleCvs = cvs
    .filter((cv) => cv.status !== "expired")
    .slice()
    .sort((a, b) => new Date(b.created_at ?? 0).getTime() - new Date(a.created_at ?? 0).getTime());

  // Row rendering rules mirror the CV list: expired → hidden.
  const showCoverLetter = !!coverLetter && coverLetter.status !== "expired";
  const isEmpty = visibleCvs.length === 0 && !showCoverLetter;

  return (
    <Card className="p-6" data-testid="dossier-documents-zone">
      <h2 className="font-heading text-xl font-bold text-neutral-dark mb-4">
        {t("documentsZoneTitle")}
      </h2>

      {isEmpty ? (
        <p className="text-sm text-on-surface-variant">{t("noDocumentsYet")}</p>
      ) : (
        <div className="space-y-3">
          {visibleCvs.map((cv) => {
            const isPinned = application.submitted_cv_id === cv.cv_id;
            const isReady = cv.status === "ready";
            const isGenerating = cv.status === "pending" || cv.status === "generating";
            const isFailed = cv.status === "failed";
            const dateLabel = cv.created_at
              ? t("versionFrom", { date: new Date(cv.created_at).toLocaleDateString() })
              : "";
            const templateKey = cv.template ? TEMPLATE_NAME_KEYS[cv.template] : undefined;
            // Unknown/legacy template ids fall back to the raw id string.
            const templateLabel = templateKey ? tCv(templateKey) : cv.template ?? "";
            const readyLabel = [dateLabel, templateLabel].filter(Boolean).join(" · ");
            const pdfUrl = cv.pdf_url ?? `${API_BASE}/api/cv/${cv.cv_id}/pdf`;

            return (
              <div
                key={cv.cv_id}
                data-testid="dossier-doc-row"
                className={cn(
                  "flex items-center justify-between gap-3 rounded-lg border px-4 py-3",
                  isPinned
                    ? "bg-success/10 border-success/20"
                    : "bg-surface-container border-outline-variant"
                )}
              >
                <div className="min-w-0 flex items-center gap-2">
                  {isPinned ? (
                    <span
                      data-testid="dossier-doc-pinned"
                      className="inline-flex items-center gap-1.5 text-sm font-bold text-success shrink-0"
                    >
                      <CheckCircle2 className="w-4 h-4 shrink-0" aria-hidden="true" />
                      {t("sentChipLabel")}
                    </span>
                  ) : (
                    <>
                      {isReady && <FileText className="w-4 h-4 text-on-surface-variant shrink-0" aria-hidden="true" />}
                      {isGenerating && (
                        <Loader2 className="w-4 h-4 text-on-surface-variant shrink-0 animate-spin" aria-hidden="true" />
                      )}
                      {isFailed && <AlertTriangle className="w-4 h-4 text-critical shrink-0" aria-hidden="true" />}
                    </>
                  )}
                  <div className="min-w-0">
                    {isReady && (
                      <p className="text-sm font-medium text-on-surface truncate">{readyLabel}</p>
                    )}
                    {isGenerating && (
                      <p className="text-sm text-on-surface-variant">{t("docGeneratingLabel")}</p>
                    )}
                    {isFailed && (
                      <p className="text-sm text-critical">{failedCvMessage(cv.error_code)}</p>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  {isReady && (
                    <button
                      type="button"
                      onClick={() => openPdf(pdfUrl)}
                      data-testid="dossier-doc-pdf"
                      className="text-xs font-bold px-3 py-1.5 rounded-lg bg-white border border-outline-variant text-on-surface hover:bg-surface-container"
                    >
                      {t("openPdf")}
                    </button>
                  )}
                  {isReady && isPinned && (
                    <button
                      type="button"
                      onClick={() => void handleUnpinCv()}
                      disabled={savingId === "unpin-cv"}
                      aria-label={t("submittedRemove")}
                      title={t("submittedRemove")}
                      data-testid="dossier-doc-unpin"
                      className="text-on-surface-variant hover:text-on-surface disabled:opacity-50"
                    >
                      <X className="w-4 h-4" aria-hidden="true" />
                    </button>
                  )}
                  {isReady && !isPinned && (
                    <button
                      type="button"
                      onClick={() => void handleMarkCvSent(cv.cv_id)}
                      disabled={savingId === cv.cv_id}
                      data-testid="dossier-doc-mark-sent"
                      className="inline-flex items-center gap-1.5 text-xs font-bold px-3 py-1.5 rounded-lg bg-teal-dim text-white hover:bg-primary disabled:opacity-50"
                    >
                      <Send className="w-3.5 h-3.5" aria-hidden="true" />
                      {t("markSentAction")}
                    </button>
                  )}
                </div>
              </div>
            );
          })}

          {showCoverLetter && coverLetter && (
            (() => {
              const isClPinned = application.submitted_cover_letter_id === coverLetter.cover_letter_id;
              const isClReady = coverLetter.status === "ready";
              const isClGenerating = coverLetter.status === "pending" || coverLetter.status === "generating";
              const isClFailed = coverLetter.status === "failed";
              const clPdfUrl = coverLetter.pdf_url ?? `${API_BASE}/api/cover-letter/${coverLetter.cover_letter_id}/pdf`;

              return (
                <div
                  data-testid="dossier-cl-row"
                  className={cn(
                    "flex items-center justify-between gap-3 rounded-lg border px-4 py-3",
                    isClPinned
                      ? "bg-success/10 border-success/20"
                      : "bg-surface-container border-outline-variant"
                  )}
                >
                  <div className="min-w-0 flex items-center gap-2">
                    {isClPinned ? (
                      <span
                        data-testid="dossier-doc-pinned"
                        className="inline-flex items-center gap-1.5 text-sm font-bold text-success shrink-0"
                      >
                        <CheckCircle2 className="w-4 h-4 shrink-0" aria-hidden="true" />
                        {t("sentChipLabel")}
                      </span>
                    ) : (
                      <>
                        {isClReady && <FileText className="w-4 h-4 text-on-surface-variant shrink-0" aria-hidden="true" />}
                        {isClGenerating && (
                          <Loader2 className="w-4 h-4 text-on-surface-variant shrink-0 animate-spin" aria-hidden="true" />
                        )}
                        {isClFailed && <AlertTriangle className="w-4 h-4 text-critical shrink-0" aria-hidden="true" />}
                      </>
                    )}
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-on-surface truncate">{t("coverLetterAction")}</p>
                      {isClGenerating && (
                        <p className="text-sm text-on-surface-variant">{t("docGeneratingLabel")}</p>
                      )}
                      {isClFailed && <p className="text-sm text-critical">{tCv("generationFailed")}</p>}
                    </div>
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    {application.flow_session_id && (
                      <Link
                        href={`/flow/${application.flow_session_id}/cover-letter`}
                        className="text-xs font-bold px-3 py-1.5 rounded-lg bg-white border border-outline-variant text-on-surface hover:bg-surface-container"
                      >
                        {t("coverLetterDraftLink")}
                      </Link>
                    )}
                    {isClReady && (
                      <button
                        type="button"
                        onClick={() => openPdf(clPdfUrl)}
                        data-testid="dossier-doc-pdf"
                        className="text-xs font-bold px-3 py-1.5 rounded-lg bg-white border border-outline-variant text-on-surface hover:bg-surface-container"
                      >
                        {t("openPdf")}
                      </button>
                    )}
                    {isClReady && isClPinned && (
                      <button
                        type="button"
                        onClick={() => void handleUnpinCl()}
                        disabled={savingId === "unpin-cl"}
                        aria-label={t("submittedRemove")}
                        title={t("submittedRemove")}
                        data-testid="dossier-doc-unpin"
                        className="text-on-surface-variant hover:text-on-surface disabled:opacity-50"
                      >
                        <X className="w-4 h-4" aria-hidden="true" />
                      </button>
                    )}
                    {isClReady && !isClPinned && (
                      <button
                        type="button"
                        onClick={() => void handleMarkClSent(coverLetter.cover_letter_id)}
                        disabled={savingId === coverLetter.cover_letter_id}
                        data-testid="dossier-doc-mark-sent"
                        className="inline-flex items-center gap-1.5 text-xs font-bold px-3 py-1.5 rounded-lg bg-teal-dim text-white hover:bg-primary disabled:opacity-50"
                      >
                        <Send className="w-3.5 h-3.5" aria-hidden="true" />
                        {t("markSentAction")}
                      </button>
                    )}
                  </div>
                </div>
              );
            })()
          )}
        </div>
      )}
    </Card>
  );
}
