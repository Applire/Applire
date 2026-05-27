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

"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { markApplicationHired } from "@/lib/profile-roles";

export interface CVPageActionBarProps {
  flowId: string;
  applicationId: string | null;
  coverLetterId: string | null;
  onDownloadPdf: () => void;
  onGenerateCoverLetter: () => void;
  onNext: () => void;
}

export function CVPageActionBar({
  flowId,
  applicationId,
  coverLetterId,
  onDownloadPdf,
  onGenerateCoverLetter,
  onNext,
}: CVPageActionBarProps) {
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
    <div
      className="surface-glass flex flex-wrap items-center gap-2 px-3 py-2 rounded-xl"
      data-testid="cv-page-action-bar"
    >
      <button
        type="button"
        className="btn-pill-primary"
        onClick={onDownloadPdf}
        data-testid="page-action-download"
      >
        {t("pageActionDownload")}
      </button>

      {coverLetterId ? (
        <Link
          href={`/flow/${flowId}/cover-letter`}
          className="btn-glass"
          data-testid="page-action-cover-letter-view"
        >
          {t("pageActionCoverLetterView")}
        </Link>
      ) : (
        <button
          type="button"
          className="btn-glass"
          onClick={onGenerateCoverLetter}
          data-testid="page-action-cover-letter-generate"
        >
          {t("pageActionCoverLetter")}
        </button>
      )}

      {applicationId && (
        <button
          type="button"
          className="btn-glass"
          onClick={() => void handleMarkHired()}
          disabled={hiring}
          data-testid="page-action-hired"
        >
          {t("pageActionHired")}
        </button>
      )}

      <button
        type="button"
        className="btn-glass ml-auto"
        onClick={onNext}
        data-testid="page-action-next"
      >
        {t("pageActionNext")}
        {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative directional arrow */}
        {" →"}
      </button>
    </div>
  );
}
