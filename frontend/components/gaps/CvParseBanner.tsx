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

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";

// CV Parse-Status Banner — shown when some (not all) uploaded CVs failed to
// parse, surfaced via cv_parsed/cv_total query params (US153, FMEA JF-M-2.2).

/** V-2 = C (founder): the names of the CVs the person chose to continue
 *  without. V's upload overlay writes them to sessionStorage — never to the
 *  URL, since a file name often carries the person's own name. */
export function readFailedImportNames(flowId: string): string[] {
  try {
    const raw = JSON.parse(sessionStorage.getItem(`applire.failedImports.${flowId}`) ?? "[]");
    return Array.isArray(raw) ? raw.filter((n): n is string => typeof n === "string" && n.length > 0) : [];
  } catch {
    return [];
  }
}

function CvParseBannerInner({ flowId }: { flowId: string }) {
  const searchParams = useSearchParams();
  const t = useTranslations("gaps");
  const tc = useTranslations("common");
  const [dismissed, setDismissed] = useState(false);

  const parsedRaw = searchParams.get("cv_parsed");
  const totalRaw = searchParams.get("cv_total");
  if (!parsedRaw || !totalRaw || dismissed) return null;
  const parsed = Number(parsedRaw);
  const total = Number(totalRaw);
  if (!Number.isFinite(parsed) || !Number.isFinite(total) || parsed >= total) return null;
  const names = readFailedImportNames(flowId);

  return (
    <div
      data-testid="cv-parse-banner"
      className="mb-6 flex items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3"
    >
      <svg
        className="mt-0.5 h-4 w-4 shrink-0 text-amber-600"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
          d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"
        />
      </svg>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-amber-800">
          {names.length
            ? t("cvParsedPartialNamed", {
                parsed,
                total,
                files: names.map((name) => t("failedFileQuoted", { name })).join(", "),
              })
            : t("cvParsedPartial", { parsed, total })}
        </p>
      </div>
      <button
        data-testid="cv-parse-dismiss"
        type="button"
        aria-label={tc("ariaDismiss")}
        className="shrink-0 text-amber-500 hover:text-amber-700 transition-colors"
        onClick={() => setDismissed(true)}
      >
        {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative close symbol */}
        {"×"}
      </button>
    </div>
  );
}

export function CvParseBanner({ flowId }: { flowId: string }) {
  return (
    <Suspense fallback={null}>
      <CvParseBannerInner flowId={flowId} />
    </Suspense>
  );
}
