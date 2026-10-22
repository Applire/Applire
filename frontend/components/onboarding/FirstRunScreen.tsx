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
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card } from "@/components/ui/card";
import { CV_UPLOAD_ACCEPT, Dropzone } from "@/components/ui/dropzone";
import { FileChip } from "@/components/ui/file-chip";
import { useFileUpload } from "@/lib/hooks/use-file-upload";
import { cn } from "@/lib/utils";
import { ProcessingOverlay } from "@/components/processing-overlay";
import { useImportCapabilities } from "@/lib/import-capabilities";

type JdMode = "url" | "text";

/** The five steps of a first application (mock F1-1 a). Step 4 is optional. */
const STEPS = [
  { n: 1, title: "step1Title", body: "step1Body", tag: null },
  { n: 2, title: "step2Title", body: "step2Body", tag: null },
  { n: 3, title: "step3Title", body: "step3Body", tag: null },
  { n: 4, title: "step4Title", body: "step4Body", tag: "step4Tag" },
  { n: 5, title: "step5Title", body: "step5Body", tag: "step5Tag" },
] as const;

/**
 * #789 "First run has no orientation" — the screen a person without a profile
 * sees, on `/` AND on `/dashboard` (ruling F1-3: rendered in place, no
 * redirect). It says what the whole way looks like and how long it takes
 * before it asks for a file, then the CV + job inputs as steps 1 and 2.
 */
export function FirstRunScreen({ importReady = true }: { importReady?: boolean }) {
  const t = useTranslations("home");
  const tErrors = useTranslations("errors");
  const { files, addFiles, removeFile } = useFileUpload();
  const [jdMode, setJdMode] = useState<JdMode>("url");
  const [jdUrl, setJdUrl] = useState("");
  const [jdText, setJdText] = useState("");
  const [error, setError] = useState("");
  const [showOverlay, setShowOverlay] = useState(false);
  const [guided, setGuided] = useState(false);
  // #789 polish d — "upload at least one" is said after an attempt, not
  // shouted (in orange) at a person who has not done anything yet.
  const [attempted, setAttempted] = useState(false);

  // #789 L12 — only ask once signed in (the caller knows when that is).
  const { imageImport } = useImportCapabilities(importReady);

  const hasFiles = files.length > 0;
  const canSubmit = hasFiles && !showOverlay;

  function handleSubmit() {
    if (showOverlay) return;
    if (!hasFiles) {
      setAttempted(true);
      return;
    }
    setError("");
    setGuided(false);
    setShowOverlay(true);
  }

  // No-CV onboarding (US156, FMEA 2.6): build the profile through a guided
  // interview instead of an upload. The guided interview needs a job, so a JD
  // is required to start.
  function handleNoCv() {
    const jd = jdMode === "url" ? jdUrl.trim() : jdText.trim();
    if (!jd) {
      setError(t("noCvNeedJd"));
      return;
    }
    setError("");
    setGuided(true);
    setShowOverlay(true);
  }

  return (
    <>
      {showOverlay && (
        <ProcessingOverlay
          files={guided ? [] : files.map(({ file }) => file)}
          jdMode={jdMode}
          jdUrl={jdUrl}
          jdText={jdText}
          guided={guided}
          onCancel={() => setShowOverlay(false)}
        />
      )}
      <main className="flex-1 px-4 py-6 md:px-8 md:py-10" data-testid="first-run">
        <div className="max-w-[1000px] mx-auto">
          <h1 className="font-heading text-2xl md:text-[26px] font-extrabold text-neutral-dark tracking-tight">
            {t("welcomeTitle")}
          </h1>
          <p className="mt-1.5 max-w-[720px] text-sm text-on-surface-variant">{t("welcomeLead")}</p>

          {/* The way, with durations. Desktop: five cards in a row. Below md:
              one compact line per step, so the dropzone starts early. */}
          <ol data-testid="first-run-steps" className="mt-5 mb-7 grid grid-cols-1 gap-1.5 md:grid-cols-5 md:gap-2.5">
            {STEPS.map((s) => (
              <li
                key={s.n}
                data-testid="first-run-step"
                className="flex items-center gap-3 rounded-xl border border-outline-variant bg-white px-3 py-2 md:block md:p-3"
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    "flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold md:mb-2",
                    s.tag === "step4Tag"
                      ? "border-2 border-teal bg-white text-teal"
                      : "bg-teal text-white",
                  )}
                >
                  {s.n}
                </span>
                <span className="sr-only">{t("stepLabel", { n: s.n })}</span>
                <span className="min-w-0 flex-1 md:block">
                  <span className="block text-[13px] font-bold text-on-surface">{t(s.title)}</span>
                  <span className="hidden md:block mt-0.5 text-xs leading-snug text-on-surface-variant">{t(s.body)}</span>
                </span>
                {s.tag && (
                  <span className="shrink-0 rounded-full bg-primary-container px-2 py-0.5 text-[11px] font-semibold text-teal md:mt-1.5 md:inline-block">
                    {t(s.tag)}
                  </span>
                )}
              </li>
            ))}
          </ol>

          <div className="grid grid-cols-1 md:grid-cols-[60%_40%] gap-6 md:gap-8">
            {/* Step 1 — CV upload */}
            <div>
              <p className="text-[11px] font-bold uppercase tracking-wide text-teal">{t("stepLabel", { n: 1 })}</p>
              <h2 className="font-heading text-xl font-bold text-neutral-dark mb-1">{t("yourCVs")}</h2>
              <p className="text-sm text-on-surface-variant mb-4">{t("cvUploadHint")}</p>

              <Dropzone
                data-testid="upload-area"
                onDrop={(fileList) => addFiles(fileList)}
                accept={CV_UPLOAD_ACCEPT}
                imageImport={imageImport}
                multiple
                disabled={showOverlay}
              />

              <p className="text-xs text-on-surface-variant mt-2">{t("linkedinHint")}</p>

              {files.length > 0 && (
                <div className="mt-4 space-y-2">
                  {files.map(({ id, file }) => (
                    <FileChip key={id} filename={file.name} size={file.size} onRemove={() => removeFile(id)} />
                  ))}
                  {files.length < 4 && (
                    <p className="text-xs text-on-surface-variant mt-2">{t("addMoreFiles")}</p>
                  )}
                </div>
              )}
            </div>

            {/* Step 2 — the job */}
            <div>
              <p className="text-[11px] font-bold uppercase tracking-wide text-teal">{t("stepLabel", { n: 2 })}</p>
              <h2 className="font-heading text-xl font-bold text-neutral-dark mb-1">{t("jobDescription")}</h2>
              <p className="text-sm text-on-surface-variant mb-4">{t("jdHint")}</p>

              <Card className="p-4">
                <div className="flex gap-1 mb-4 border-b border-gray-200">
                  <button
                    type="button"
                    onClick={() => setJdMode("url")}
                    className={cn(
                      "px-4 py-2 text-sm font-medium transition-colors relative",
                      jdMode === "url" ? "text-teal" : "text-gray-500 hover:text-neutral-dark",
                    )}
                  >
                    {t("jdUrlTab")}
                    {jdMode === "url" && <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-teal" />}
                  </button>
                  <button
                    type="button"
                    data-testid="jd-mode-text"
                    onClick={() => setJdMode("text")}
                    className={cn(
                      "px-4 py-2 text-sm font-medium transition-colors relative",
                      jdMode === "text" ? "text-teal" : "text-gray-500 hover:text-neutral-dark",
                    )}
                  >
                    {t("pasteText")}
                    {jdMode === "text" && <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-teal" />}
                  </button>
                </div>

                {jdMode === "url" ? (
                  <Input
                    type="url"
                    placeholder="https://www.stepstone.de/..."
                    value={jdUrl}
                    onChange={(e) => setJdUrl(e.target.value)}
                    disabled={showOverlay}
                  />
                ) : (
                  <textarea
                    data-testid="jd-text-input"
                    className="flex min-h-[180px] w-full rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm text-neutral-dark placeholder:text-gray-400 transition-colors focus:border-teal focus:outline-none focus:ring-2 focus:ring-teal/20 disabled:cursor-not-allowed disabled:opacity-50 resize-y"
                    placeholder={t("jdTextPlaceholder")}
                    value={jdText}
                    onChange={(e) => setJdText(e.target.value)}
                    disabled={showOverlay}
                  />
                )}

                <p className="text-xs italic text-gray-400 mt-3">{t("optional")}</p>
              </Card>
            </div>
          </div>

          {error && (
            <div data-testid="error-message" className="mt-6 p-4 rounded-lg bg-critical/10 border border-critical/20">
              <p className="text-sm text-critical">{error}</p>
            </div>
          )}

          <div className="mt-8 flex flex-col items-center">
            {/* aria-disabled, not disabled: the click on an empty form must
                reach handleSubmit so the hint can answer it (polish d). */}
            <Button
              size="lg"
              aria-disabled={!canSubmit}
              onClick={handleSubmit}
              className={cn("min-w-[260px]", !canSubmit && "cursor-not-allowed bg-gray-300 text-gray-500 opacity-60 hover:bg-gray-300")}
              data-testid="submit-button"
            >
              {t("analyzeButton")}
            </Button>
            {attempted && !hasFiles && !showOverlay && (
              <p data-testid="upload-at-least-one" role="status" className="text-xs text-on-surface-variant mt-2">
                {tErrors("uploadAtLeastOne")}
              </p>
            )}
            <p className="text-xs text-on-surface-variant mt-3 text-center">{t("usuallyTakes")}</p>

            {/* No-CV onboarding entry (US156, FMEA 2.6) — first-class, not hidden behind a failed upload */}
            <div className="mt-6 flex flex-col items-center border-t border-gray-200 pt-5 w-full max-w-[420px]">
              <button
                type="button"
                data-testid="no-cv-button"
                onClick={handleNoCv}
                disabled={showOverlay}
                className="text-sm font-medium text-teal underline hover:no-underline disabled:opacity-50"
              >
                {t("noCv")}
              </button>
              <p className="text-xs text-on-surface-variant mt-1 text-center">{t("noCvSub")}</p>
            </div>
          </div>
        </div>
      </main>
    </>
  );
}
