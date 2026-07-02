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

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { RefreshCw, FileText, CheckCircle2, ArrowRight } from "lucide-react";
import { markApplicationHired } from "@/lib/profile-roles";

interface CVActionsTabProps {
  flowId: string;
  applicationId: string | null;
  coverLetterId: string | null;
  onGenerateCoverLetter: () => void;
  onRegenerateSame: () => void;
  onNext: () => void;
}

/**
 * CV "Aktionen" tab body (E038 / US210) — absorbs the actions that used to live
 * in the retired CVPageActionBar (regenerate, generate cover letter, mark hired,
 * next). Download moved to the shared top bar; CV↔Anschreiben switching moved to
 * the top-bar document toggle.
 */
export function CVActionsTab({
  applicationId,
  coverLetterId,
  onGenerateCoverLetter,
  onRegenerateSame,
  onNext,
}: CVActionsTabProps) {
  const t = useTranslations("cv");
  const router = useRouter();
  const [hiring, setHiring] = useState(false);

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

  return (
    <div className="p-4 flex flex-col gap-2">
      <button
        type="button"
        onClick={onRegenerateSame}
        className="btn-glass w-full justify-center inline-flex items-center gap-2"
        data-testid="cv-actions-regenerate"
      >
        <RefreshCw className="w-4 h-4" aria-hidden="true" />
        {t("regenerateCurrentTemplate")}
      </button>

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
