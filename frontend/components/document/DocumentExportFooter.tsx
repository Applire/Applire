"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
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

import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Download } from "lucide-react";

/**
 * F-4b (founder ruling, 2026-09-11): the per-document signature override —
 * a nullable tri-state read at BOTH render seams of this document's kind
 * (PDF/HTML and DOCX) ahead of the kind-level default on `user_settings`.
 * `null` means "use the kind default"; the caller owns fetching/persisting,
 * this component only renders the three-state control and raises the choice.
 */
export interface DocumentSignatureControlProps {
  /** Whether ANY signature image is on file at all — independent of the
   * toggle/override. `false` hides the control behind a disabled hint
   * pointing at the profile card, since there is nothing to switch on. */
  available: boolean;
  /** The kind-level default (`signature_in_letter` / `signature_in_cv`) —
   * words the "Standard (an|aus)" option so it never claims a state the
   * document would not actually render. */
  kindDefaultOn: boolean;
  /** The document's own stored override. `null` = "use the kind default". */
  override: boolean | null;
  /** Raised with the newly selected value; the caller PATCHes and updates
   * `override` (and `available`/`kindDefaultOn` if they can change). */
  onChange: (value: boolean | null) => void;
  /** True while a PATCH is in flight — disables the control so a second
   * click cannot race the first. */
  saving?: boolean;
}

interface DocumentExportFooterProps {
  onDownloadPdf: () => void;
  /**
   * US298 (E057 task 1.5, ADR-058 cl.2/ADR-066): the ADR-079 editable Word
   * export. Optional — when omitted, no .docx CTA renders. Both document pages
   * supply it. Gated by the SAME pre-download notice as the PDF button
   * (ADR-040 cl.4) — the caller owns that gate, exactly as it already does for
   * `onDownloadPdf`.
   */
  onDownloadDocx?: () => void;
  downloadDisabled?: boolean;
  /** F-4b: the per-document signature override control. Omitted — e.g.
   * before the document's status has loaded — renders nothing extra. */
  signature?: DocumentSignatureControlProps;
}

interface SignatureOverrideOption {
  testId: string;
  value: boolean | null;
  label: string;
}

/** The three fixed options the control offers, in order — a plain function
 * (not inlined JSX) so its `value: boolean | null` typing does not fight
 * with `as const`'s literal-type inference. */
function signatureOverrideOptions(
  kindDefaultOn: boolean,
  t: (key: string) => string,
): SignatureOverrideOption[] {
  return [
    {
      testId: "signature-override-default",
      value: null,
      label: kindDefaultOn ? t("signatureDefaultOn") : t("signatureDefaultOff"),
    },
    { testId: "signature-override-on", value: true, label: t("signatureWith") },
    { testId: "signature-override-off", value: false, label: t("signatureWithout") },
  ];
}

/**
 * The workspace panel's PINNED FOOTER: the exports.
 *
 * E058/US299, ADR-081 clause 1 — the second half of the dissolved
 * `DocumentTopBar`. It sits at the bottom of the panel in every tab and in
 * every review mode, so the export is never behind a tab; the test ids
 * (`document-download-btn`, `document-download-docx-btn`) are carried over
 * unchanged, because the OQ and PQ specs that drive the download are testing
 * the same action in a new place.
 *
 * ADR-040 clause 4 is untouched: this component raises the caller's handler,
 * and the pre-download notice still stands between that handler and every file.
 */
export function DocumentExportFooter({
  onDownloadPdf,
  onDownloadDocx,
  downloadDisabled = false,
  signature,
}: DocumentExportFooterProps) {
  const t = useTranslations("document");
  const router = useRouter();

  return (
    <div className="flex flex-col gap-2" data-testid="document-export-footer">
      {signature && (
        <div className="flex items-center gap-2 text-xs" data-testid="signature-override-section">
          {signature.available ? (
            <>
              <span className="text-on-surface-variant">{t("signatureControlLabel")}</span>
              <div
                role="group"
                aria-label={t("signatureControlLabel")}
                className="inline-flex overflow-hidden rounded-full border border-outline-variant"
                data-testid="signature-override-control"
              >
                {signatureOverrideOptions(signature.kindDefaultOn, t).map((option) => {
                  const selected = signature.override === option.value;
                  return (
                    <button
                      key={option.testId}
                      type="button"
                      data-testid={option.testId}
                      aria-pressed={selected}
                      disabled={signature.saving}
                      onClick={() => signature.onChange(option.value)}
                      className={`px-2.5 py-1 transition disabled:opacity-50 disabled:pointer-events-none ${
                        selected
                          ? "bg-primary text-white"
                          : "bg-surface-container text-on-surface-variant hover:bg-surface-container-high"
                      }`}
                    >
                      {option.label}
                    </button>
                  );
                })}
              </div>
            </>
          ) : (
            <p className="text-on-surface-variant" data-testid="signature-override-unavailable-hint">
              {t("signatureUnavailableHint")}
              {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
              {" "}
              <a
                href="/profile"
                className="text-primary underline hover:no-underline"
                onClick={(e) => {
                  e.preventDefault();
                  router.push("/profile");
                }}
              >
                {t("signatureUnavailableLink")}
              </a>
            </p>
          )}
        </div>
      )}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onDownloadPdf}
          disabled={downloadDisabled}
          data-testid="document-download-btn"
          className="btn-pill-primary inline-flex flex-1 items-center justify-center gap-2 hover:shadow-md active:scale-95 transition disabled:opacity-50 disabled:pointer-events-none"
        >
          <Download className="w-4 h-4" aria-hidden="true" />
          {t("downloadPdf")}
        </button>

        {/* US298: the office (.docx) export — secondary style (.btn-glass) so the
            PDF stays the one visually primary action. */}
        {onDownloadDocx && (
          <button
            type="button"
            onClick={onDownloadDocx}
            disabled={downloadDisabled}
            data-testid="document-download-docx-btn"
            className="btn-glass inline-flex items-center justify-center gap-2 disabled:opacity-50 disabled:pointer-events-none"
          >
            <Download className="w-4 h-4" aria-hidden="true" />
            {t("downloadDocx")}
          </button>
        )}
      </div>
    </div>
  );
}
