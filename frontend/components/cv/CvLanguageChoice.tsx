"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { DocumentLanguageControl } from "@/components/gaps/DocumentLanguageControl";

interface CvLanguageChoiceProps {
  applicationId: string;
  jobId: string;
  apiBase: string;
}

/**
 * 789:264 (Hazelnut build 7, WP-F) — the CV's language says where it comes from,
 * on the last screen before the CV is written.
 *
 * Priya's CV came out English because the posting was English; the only control
 * sat on the Match page and nothing on the CV page said so. This mounts the SAME
 * control (E054/US288 `DocumentLanguageControl`, unchanged — ADR-038 cl. 6: any
 * click writes `language_override`) plus one sentence naming the default. Both
 * reads are best-effort: on a failure the control stays hidden, as on the Match
 * page.
 */
export function CvLanguageChoice({ applicationId, jobId, apiBase }: CvLanguageChoiceProps) {
  const t = useTranslations("cv");
  const [detected, setDetected] = useState<"de" | "en" | null | undefined>(undefined);
  const [override, setOverride] = useState<"de" | "en" | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBase}/api/job/${jobId}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((j: { jd_language?: "de" | "en" | null } | null) => {
        if (!cancelled) setDetected(j ? (j.jd_language ?? null) : undefined);
      })
      .catch(() => {});
    fetch(`${apiBase}/api/applications/${applicationId}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((a: { language_override?: "de" | "en" | null } | null) => {
        if (!cancelled && a) setOverride(a.language_override ?? null);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [apiBase, applicationId, jobId]);

  if (detected === undefined || override === undefined) return null;

  return (
    <div data-testid="cv-language-choice" className="flex max-w-xs flex-col items-end gap-1 text-right">
      <DocumentLanguageControl
        applicationId={applicationId}
        detectedLanguage={detected}
        initialOverride={override}
        apiBase={apiBase}
      />
      <p className="text-xs text-on-surface-variant" data-testid="cv-language-follows-posting">
        {t("languageFollowsPosting")}
      </p>
    </div>
  );
}
