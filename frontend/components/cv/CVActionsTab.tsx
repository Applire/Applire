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

import { useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { RefreshCw, FileText, CheckCircle2, ArrowRight, Send, X, LayoutTemplate } from "lucide-react";
import { markApplicationHired } from "@/lib/profile-roles";
import { getApplication, patchSubmittedCv } from "@/lib/api/applications";

interface CVActionsTabProps {
  flowId: string;
  applicationId: string | null;
  coverLetterId: string | null;
  /** The CV version currently previewed — the pin target (E039/US219). */
  cvId: string | null;
  onGenerateCoverLetter: () => void;
  onRegenerateSame: () => void;
  onNext: () => void;
  /** E054/US289: the post-generation language switch (DocumentLanguageSwitch),
   *  slotted by the page so this tab stays decoupled from its wiring. */
  languageSwitch?: ReactNode;
  /** #737 (RULING E-1 = A): *Andere Vorlage wählen* — a new version in another template. */
  onChooseTemplate?: () => void;
  /** #737: the pinned facts that feed every new version (ADR-077, application-scoped). */
  pins?: ReactNode;
  /** #737: translated labels of the sections carrying the user's edit — not carried into a new version. */
  editedSections?: string[];
}

/**
 * CV "Aktionen" tab body (E038 / US210) — absorbs the actions that used to live
 * in the retired CVPageActionBar (regenerate, generate cover letter, mark hired,
 * next). Download moved to the shared top bar; CV↔Anschreiben switching moved to
 * the top-bar document toggle. Also hosts "Mark as submitted" (E039/US219): pins
 * the previewed version so Branch G can answer "what exactly do THEY have?".
 */
export function CVActionsTab({
  applicationId,
  coverLetterId,
  cvId,
  onGenerateCoverLetter,
  onRegenerateSame,
  onNext,
  languageSwitch,
  onChooseTemplate,
  pins,
  editedSections = [],
}: CVActionsTabProps) {
  const t = useTranslations("cv");
  const tEdit = useTranslations("editTab");
  const router = useRouter();
  const [hiring, setHiring] = useState(false);
  // null = pin state unknown (fetch pending/failed) — render nothing rather
  // than a control that would lie about the current pin.
  const [submittedCvId, setSubmittedCvId] = useState<string | null | undefined>(undefined);
  const [pinning, setPinning] = useState(false);

  useEffect(() => {
    if (!applicationId || !cvId) return;
    let cancelled = false;
    getApplication(applicationId)
      .then((app) => {
        if (!cancelled) setSubmittedCvId(app.submitted_cv_id ?? null);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [applicationId, cvId]);

  async function handleMarkHired() {
    if (!applicationId) return;
    setHiring(true);
    try {
      const res = await markApplicationHired(applicationId);
      router.push(res.redirect_url);
    } finally {
      setHiring(false);
    }
  }

  async function handleTogglePin(next: string | null) {
    if (!applicationId) return;
    setPinning(true);
    try {
      const res = await patchSubmittedCv(applicationId, next);
      setSubmittedCvId(res.submitted_cv_id ?? null);
    } catch {
      // Leave the current state — the control stays truthful.
    } finally {
      setPinning(false);
    }
  }

  const pinReady = applicationId && cvId && submittedCvId !== undefined;
  const isPinnedHere = pinReady && submittedCvId === cvId;

  return (
    <div className="p-4 flex flex-col gap-2">
      {/* #737 (RULING E-1 = A): everything that makes a NEW version, and what
          feeds it, in one block. The page puts NewVersionDialog in front of
          each regenerate; the notice here says the same before the click. */}
      <section className="flex flex-col gap-2" data-testid="cv-new-version">
        <p className="text-xs font-semibold uppercase tracking-wide text-on-surface-variant">
          {tEdit("newVersionTitle")}
        </p>
        <p className="text-xs text-on-surface-variant">{tEdit("newVersionIntro")}</p>
        {editedSections.length > 0 && (
          <p
            className="rounded-lg border border-warning/40 bg-warning-container px-3 py-2 text-xs leading-snug text-on-surface"
            data-testid="cv-new-version-loss"
          >
            {tEdit("newVersionLoss", { count: editedSections.length, sections: editedSections.join(", ") })}
          </p>
        )}
        <button
          type="button"
          onClick={onRegenerateSame}
          className="btn-glass w-full justify-center inline-flex items-center gap-2"
          data-testid="cv-actions-regenerate"
        >
          <RefreshCw className="w-4 h-4" aria-hidden="true" />
          {tEdit("regenerateSame")}
        </button>
        {onChooseTemplate && (
          <button
            type="button"
            onClick={onChooseTemplate}
            className="btn-glass w-full justify-center inline-flex items-center gap-2"
            data-testid="cv-actions-other-template"
          >
            <LayoutTemplate className="w-4 h-4" aria-hidden="true" />
            {tEdit("regenerateOther")}
          </button>
        )}

        {languageSwitch && (
          <div className="rounded-lg border border-outline-variant bg-surface-container/50 px-3 py-2">
            {languageSwitch}
          </div>
        )}

        {pins && (
          <div className="flex flex-col gap-2" data-testid="cv-new-version-pins">
            <p className="text-xs text-on-surface-variant">{tEdit("pinsLead")}</p>
            {pins}
          </div>
        )}
      </section>

      <p className="mt-3 text-xs font-semibold uppercase tracking-wide text-on-surface-variant">
        {tEdit("applicationTitle")}
      </p>

      {!coverLetterId && (
        <button
          type="button"
          onClick={onGenerateCoverLetter}
          className="btn-glass w-full justify-center inline-flex items-center gap-2"
          data-testid="cv-actions-generate-cl"
        >
          <FileText className="w-4 h-4" aria-hidden="true" />
          {t("pageActionCoverLetter")}
        </button>
      )}

      {pinReady && !isPinnedHere && (
        <button
          type="button"
          onClick={() => void handleTogglePin(cvId)}
          disabled={pinning}
          className="btn-glass w-full justify-center inline-flex items-center gap-2 disabled:opacity-50"
          data-testid="cv-actions-mark-submitted"
        >
          <Send className="w-4 h-4" aria-hidden="true" />
          {t("markSubmitted")}
        </button>
      )}

      {isPinnedHere && (
        <div className="flex items-center justify-between gap-2 rounded-lg bg-success/10 border border-success/20 px-3 py-2">
          <span className="inline-flex items-center gap-2 text-sm font-medium text-success">
            <CheckCircle2 className="w-4 h-4" aria-hidden="true" />
            {t("markSubmittedPinned")}
          </span>
          <button
            type="button"
            onClick={() => void handleTogglePin(null)}
            disabled={pinning}
            aria-label={t("markSubmittedRemove")}
            title={t("markSubmittedRemove")}
            className="shrink-0 text-on-surface-variant hover:text-on-surface disabled:opacity-50"
            data-testid="cv-actions-unmark-submitted"
          >
            <X className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>
      )}

      {applicationId && (
        <button
          type="button"
          onClick={() => void handleMarkHired()}
          disabled={hiring}
          className="btn-glass w-full justify-center inline-flex items-center gap-2 disabled:opacity-50"
          data-testid="cv-actions-hired"
        >
          <CheckCircle2 className="w-4 h-4" aria-hidden="true" />
          {t("pageActionHired")}
        </button>
      )}

      <button
        type="button"
        onClick={onNext}
        className="btn-glass w-full justify-center inline-flex items-center gap-2"
        data-testid="cv-actions-next"
      >
        {t("pageActionNext")}
        <ArrowRight className="w-4 h-4" aria-hidden="true" />
      </button>
    </div>
  );
}
