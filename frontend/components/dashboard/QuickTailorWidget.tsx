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
import { cn } from "@/lib/utils";
import {
  DuplicateJdDialog,
  type DuplicateOfHint,
} from "@/components/applications/DuplicateJdDialog";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");
type JdMode = "url" | "text";

export function QuickTailorWidget() {
  const t = useTranslations("quickTailor");
  const tDash = useTranslations("dashboard");
  const router = useRouter();
  const [mode, setMode] = useState<JdMode>("url");
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  // E039/US216: where the posting was found — only asked for on the text tab
  // (the URL tab's link is auto-persisted server-side via JobAnalysis.source_url)
  const [sourceUrl, setSourceUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  // E039/US220 (journey Branch F): analysis matched a job already in the
  // pipeline — hold the create step until the user picks open-existing /
  // continue-as-new / dismiss. Recognition, never a gate.
  const [duplicatePrompt, setDuplicatePrompt] = useState<{
    jobId: string;
    hint: DuplicateOfHint;
  } | null>(null);

  const canSubmit = (mode === "url" && url.trim()) || (mode === "text" && text.trim());

  async function createApplicationAndRoute(jobId: string) {
    setLoading(true);
    setError("");
    try {
      const createBody: Record<string, unknown> = {
        job_analysis_id: jobId,
        start_workflow: true,
      };
      if (mode === "text" && sourceUrl.trim()) {
        createBody.source_url = sourceUrl.trim();
      }
      const createRes = await fetch(`${API_BASE}/api/applications`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(createBody),
      });
      if (!createRes.ok) {
        const err = await createRes.json();
        setError(createRes.status === 409 ? tDash("errorAppExists") : (err.detail ?? tDash("errorCreateAppFailed")));
        return;
      }
      const appData = await createRes.json();
      // Route via the flow index — it advances the state machine and picks
      // the correct step (returning users skip cv_import entirely).
      router.push(`/flow/${appData.flow_session_id}`);
    } catch {
      setError(tDash("errorUnexpected"));
    } finally {
      setLoading(false);
    }
  }

  async function handleSubmit() {
    if (!canSubmit) return;
    setLoading(true);
    setError("");
    try {
      const jdPayload = mode === "url" ? { url } : { text };
      const analyzeRes = await fetch(`${API_BASE}/api/job/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(jdPayload),
      });
      if (!analyzeRes.ok) {
        const err = await analyzeRes.json();
        setError(err.detail ?? tDash("errorAnalysisFailed"));
        return;
      }
      const jobData = await analyzeRes.json();

      if (jobData.duplicate_of) {
        setDuplicatePrompt({ jobId: jobData.id, hint: jobData.duplicate_of });
        return;
      }
      await createApplicationAndRoute(jobData.id);
    } catch {
      setError(tDash("errorUnexpected"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      data-testid="quick-tailor-widget"
      className="w-full bg-white rounded-[14px] border border-gray-200 shadow-sm px-4 sm:px-[22px] py-5 relative overflow-hidden"
    >
      {duplicatePrompt && (
        <DuplicateJdDialog
          hint={duplicatePrompt.hint}
          onOpenExisting={() =>
            router.push(`/applications/${duplicatePrompt.hint.application_id}`)
          }
          onContinueNew={() => {
            const jobId = duplicatePrompt.jobId;
            setDuplicatePrompt(null);
            void createApplicationAndRoute(jobId);
          }}
          onDismiss={() => setDuplicatePrompt(null)}
        />
      )}
      {/* gradient top-border */}
      <div className="absolute top-0 left-0 right-0 h-[3px] bg-gradient-to-r from-gold via-primary to-gold" />

      <p className="font-extrabold text-[15px] text-neutral-dark mb-1 font-manrope flex items-center gap-1.5">
        {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
        <span className="material-symbols-outlined text-gold" aria-hidden="true" style={{ fontSize: 18 }}>auto_awesome</span>
        {t("title")}
      </p>
      <p className="text-[12px] text-gray-500 mb-3.5">{t("subtitle")}</p>

      {error && (
        <p className="text-xs text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2 mb-3">
          {error}
        </p>
      )}

      {/* Tab toggle */}
      <div className="flex border-b-2 border-gray-100 mb-3.5">
        {(["url", "text"] as JdMode[]).map((m) => (
          <button
            key={m}
            data-testid={`quick-tailor-tab-${m}`}
            onClick={() => setMode(m)}
            className={cn(
              "px-4 pb-2 text-[13px] font-semibold relative font-manrope transition-colors",
              mode === m ? "text-primary" : "text-gray-500 hover:text-gray-800"
            )}
          >
            {m === "url" ? t("tabUrl") : t("tabText")}
            {mode === m && (
              <span className="absolute bottom-[-2px] left-0 right-0 h-[2px] bg-primary rounded-t" />
            )}
          </button>
        ))}
      </div>

      {/* Inputs — US224: stacked below sm so the row never squashes on a
          phone screen; row layout returns at sm and up. */}
      <div className="flex flex-col sm:flex-row gap-2.5 sm:items-end">
        {mode === "url" ? (
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
            placeholder={t("urlPlaceholder")}
            disabled={loading}
            data-testid="quick-tailor-url-input"
            className="w-full sm:flex-1 h-10 border-[1.5px] border-gray-300 rounded-lg px-3.5 text-[13px] outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 disabled:opacity-50"
          />
        ) : (
          <div className="w-full sm:flex-1 flex flex-col gap-2">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={t("textPlaceholder")}
              disabled={loading}
              data-testid="quick-tailor-text-input"
              className="min-h-[88px] resize-y border-[1.5px] border-gray-300 rounded-lg px-3.5 py-2.5 text-[13px] outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 disabled:opacity-50"
            />
            <input
              type="url"
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
              placeholder={t("sourcePlaceholder")}
              aria-label={t("sourceLabel")}
              disabled={loading}
              data-testid="quick-tailor-source-input"
              className="h-9 border-[1.5px] border-gray-200 rounded-lg px-3.5 text-[12px] outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 disabled:opacity-50"
            />
          </div>
        )}
        <button
          onClick={handleSubmit}
          disabled={!canSubmit || loading}
          data-testid="quick-tailor-submit"
          className="w-full sm:w-auto h-10 px-5 bg-primary text-white rounded-lg text-[13px] font-bold font-manrope sm:self-end whitespace-nowrap hover:bg-teal-dim disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {loading ? t("analysing") : t("analyseButton")}
        </button>
      </div>
    </div>
  );
}
