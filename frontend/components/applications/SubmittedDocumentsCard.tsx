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
import { useTranslations } from "next-intl";
import { CheckCircle2, FileText, Send, X } from "lucide-react";
import { Card } from "@/components/ui/card";
import { patchSubmittedCv } from "@/lib/api/applications";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

interface SubmittedDocumentsCardProps {
  applicationId: string;
  jobAnalysisId: string;
  submittedCvId: string | null;
  /** The pin's version identity — its creation date (E039/US219 read model). */
  submittedCvCreatedAt: string | null;
  submittedCoverLetterId: string | null;
}

/**
 * "What exactly do THEY have?" — the Branch G recall surface (E039/US219).
 *
 * Pinned: shows the submitted version (identified by its creation date) and
 * opens its PDF. Unpinned: shows the last generated version under the honest
 * label "last generated — not marked as sent" — never guess which version
 * went out (journey UX principle), but offer pinning it right here.
 */
export function SubmittedDocumentsCard({
  applicationId,
  jobAnalysisId,
  submittedCvId,
  submittedCvCreatedAt,
  submittedCoverLetterId,
}: SubmittedDocumentsCardProps) {
  const t = useTranslations("applications");
  // Local pin state so pin/unpin flips the card without a page reload.
  const [pin, setPin] = useState<{ cvId: string; createdAt: string | null } | null>(
    submittedCvId ? { cvId: submittedCvId, createdAt: submittedCvCreatedAt } : null,
  );
  // Newest ready CV for the job — the Branch G fallback. undefined = not yet loaded.
  const [lastGeneratedCvId, setLastGeneratedCvId] = useState<string | null | undefined>(
    undefined,
  );
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (pin) return; // the pinned path never needs the list
    if (lastGeneratedCvId !== undefined) return;
    let cancelled = false;
    async function loadFallback() {
      try {
        const res = await fetch(`${API_BASE}/api/cv?job_id=${jobAnalysisId}`);
        if (!res.ok) {
          if (!cancelled) setLastGeneratedCvId(null);
          return;
        }
        const list: Array<{ cv_id: string; status: string }> = await res.json();
        const newestReady = list.find((cv) => cv.status === "ready");
        if (!cancelled) setLastGeneratedCvId(newestReady?.cv_id ?? null);
      } catch {
        if (!cancelled) setLastGeneratedCvId(null);
      }
    }
    void loadFallback();
    return () => {
      cancelled = true;
    };
  }, [pin, jobAnalysisId, lastGeneratedCvId]);

  function openPdf(cvId: string) {
    window.open(`${API_BASE}/api/cv/${cvId}/pdf`, "_blank", "noopener,noreferrer");
  }

  function openCoverLetterPdf(clId: string) {
    window.open(
      `${API_BASE}/api/cover-letter/${clId}/pdf`,
      "_blank",
      "noopener,noreferrer",
    );
  }

  async function handleUnpin() {
    setSaving(true);
    try {
      await patchSubmittedCv(applicationId, null);
      setPin(null);
      setLastGeneratedCvId(undefined); // re-discover the fallback
    } catch {
      // Keep the pinned state — the card stays truthful.
    } finally {
      setSaving(false);
    }
  }

  async function handlePinLastGenerated(cvId: string) {
    setSaving(true);
    try {
      const res = await patchSubmittedCv(applicationId, cvId);
      setPin({ cvId, createdAt: res.submitted_cv_created_at ?? null });
    } catch {
      // Leave the fallback — a failed pin must not fake a "sent" state.
    } finally {
      setSaving(false);
    }
  }

  const visibleCvId = pin?.cvId ?? lastGeneratedCvId;
  // Nothing pinned and nothing generated — there is no document to recall.
  if (!pin && (lastGeneratedCvId === undefined || lastGeneratedCvId === null)) {
    return null;
  }

  return (
    <Card className="p-6">
      <h2 className="font-heading text-xl font-bold text-neutral-dark mb-4">
        {t("submittedDocsTitle")}
      </h2>
      <div className="space-y-3">
        {pin ? (
          <div
            className="flex items-center justify-between gap-3 rounded-lg bg-success/10 border border-success/20 px-4 py-3"
            data-testid="submitted-doc-pinned"
          >
            <div className="min-w-0">
              <p className="inline-flex items-center gap-2 text-sm font-bold text-success">
                <CheckCircle2 className="w-4 h-4 shrink-0" aria-hidden="true" />
                {t("submittedVersionLabel")}
              </p>
              {pin.createdAt && (
                <p className="text-xs text-on-surface-variant mt-0.5">
                  {t("versionFrom", {
                    date: new Date(pin.createdAt).toLocaleDateString(),
                  })}
                </p>
              )}
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <button
                type="button"
                onClick={() => openPdf(pin.cvId)}
                className="text-xs font-bold px-3 py-1.5 rounded-lg bg-white border border-outline-variant text-on-surface hover:bg-surface-container"
                data-testid="submitted-doc-open-pdf"
              >
                {t("openPdf")}
              </button>
              <button
                type="button"
                onClick={() => void handleUnpin()}
                disabled={saving}
                aria-label={t("submittedRemove")}
                title={t("submittedRemove")}
                className="text-on-surface-variant hover:text-on-surface disabled:opacity-50"
                data-testid="submitted-doc-unpin"
              >
                <X className="w-4 h-4" aria-hidden="true" />
              </button>
            </div>
          </div>
        ) : (
          visibleCvId && (
            <div
              className="flex items-center justify-between gap-3 rounded-lg bg-surface-container border border-outline-variant px-4 py-3"
              data-testid="submitted-doc-fallback"
            >
              <div className="min-w-0">
                <p className="inline-flex items-center gap-2 text-sm font-medium text-on-surface">
                  <FileText className="w-4 h-4 shrink-0" aria-hidden="true" />
                  {t("lastGeneratedNotMarked")}
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  type="button"
                  onClick={() => openPdf(visibleCvId)}
                  className="text-xs font-bold px-3 py-1.5 rounded-lg bg-white border border-outline-variant text-on-surface hover:bg-surface-container"
                  data-testid="submitted-doc-open-pdf"
                >
                  {t("openPdf")}
                </button>
                <button
                  type="button"
                  onClick={() => void handlePinLastGenerated(visibleCvId)}
                  disabled={saving}
                  className="inline-flex items-center gap-1.5 text-xs font-bold px-3 py-1.5 rounded-lg bg-teal-dim text-white hover:bg-primary disabled:opacity-50"
                  data-testid="submitted-doc-mark"
                >
                  <Send className="w-3.5 h-3.5" aria-hidden="true" />
                  {t("markSubmittedAction")}
                </button>
              </div>
            </div>
          )
        )}

        {submittedCoverLetterId && (
          <div className="flex items-center justify-between gap-3 rounded-lg bg-success/10 border border-success/20 px-4 py-3">
            <p className="inline-flex items-center gap-2 text-sm font-bold text-success min-w-0">
              <CheckCircle2 className="w-4 h-4 shrink-0" aria-hidden="true" />
              {t("coverLetterSubmitted")}
            </p>
            <button
              type="button"
              onClick={() => openCoverLetterPdf(submittedCoverLetterId)}
              className="shrink-0 text-xs font-bold px-3 py-1.5 rounded-lg bg-white border border-outline-variant text-on-surface hover:bg-surface-container"
              data-testid="submitted-doc-open-cl-pdf"
            >
              {t("openPdf")}
            </button>
          </div>
        )}
      </div>
    </Card>
  );
}
