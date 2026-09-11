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

/**
 * #359 — the signature image: upload, preview, delete, and the two per-document
 * toggles.
 *
 * Shaped on `PhotoManager`, with three deliberate differences:
 *
 * 1. **No consent checkbox.** The photo's gate discharges GDPR Art. 9(2)(a);
 *    Art. 9 is a closed list and a signature image is not on it (it is personal
 *    data, and biometric *material*, but it is not processed here for the
 *    purpose of uniquely identifying a natural person — Art. 4(14)). Copying
 *    the gate would have asked the user for a consent the law does not require
 *    and implied the two are the same kind of data. See ADR-088.
 * 2. **The toggles live here, beside the upload.** They are `user_settings`
 *    columns read at RENDER time, so they are global and take effect on the
 *    next download without a regeneration. A switch on one document's screen
 *    would read as "for this document" — the scope confusion the frontend
 *    collector already tracks for `review_mode`. (Design deviation from the
 *    work-package brief's "the toggles where the document is generated",
 *    flagged rather than taken silently — founder question F-4.)
 * 3. **A checkerboard behind the preview.** A signature is normally a
 *    transparent PNG; on a white card the user cannot tell a transparent
 *    background from a white one, and only one of those renders correctly on a
 *    coloured template.
 */

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

interface SignatureManagerProps {
  /** Called after a successful upload or delete so the parent can refresh. */
  onSignatureChange?: (hasSignature: boolean) => void;
}

type Settings = { signature_in_letter: boolean; signature_in_cv: boolean };

export function SignatureManager({ onSignatureChange }: SignatureManagerProps) {
  const t = useTranslations("profile.signature");
  const [previewSrc, setPreviewSrc] = useState<string | null>(null);
  const [hasSignature, setHasSignature] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Load the stored image. A 404 is the ordinary "none on file" answer, not an
  // error the user needs to see.
  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    fetch(`${API_BASE}/api/profile/signature`)
      .then((r) => (r.ok ? r.blob() : null))
      .then((blob) => {
        if (cancelled || !blob) return;
        objectUrl = URL.createObjectURL(blob);
        setPreviewSrc(objectUrl);
        setHasSignature(true);
      })
      .catch(() => {
        /* non-fatal */
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, []);

  useEffect(() => {
    fetch(`${API_BASE}/api/settings`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data: Settings | null) => {
        if (!data) return;
        setSettings({
          signature_in_letter: Boolean(data.signature_in_letter),
          signature_in_cv: Boolean(data.signature_in_cv),
        });
      })
      .catch(() => {
        /* non-fatal — the toggles simply stay hidden until settings load */
      });
  }, []);

  async function handleUpload(file: File) {
    setError(null);
    setUploading(true);
    try {
      const body = new FormData();
      body.append("file", file);
      const res = await fetch(`${API_BASE}/api/profile/signature`, { method: "POST", body });
      if (!res.ok) {
        const err = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(err.detail ?? t("uploadError"));
      }
      setPreviewSrc((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return URL.createObjectURL(file);
      });
      setHasSignature(true);
      onSignatureChange?.(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("uploadError"));
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete() {
    setError(null);
    setDeleting(true);
    try {
      const res = await fetch(`${API_BASE}/api/profile/signature`, { method: "DELETE" });
      if (!res.ok) throw new Error(t("deleteError"));
      setPreviewSrc((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
      setHasSignature(false);
      onSignatureChange?.(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("deleteError"));
    } finally {
      setDeleting(false);
    }
  }

  async function toggle(field: keyof Settings, value: boolean) {
    if (!settings) return;
    const previous = settings;
    // Optimistic, then reverted on failure: a switch that does not move when
    // pressed reads as broken, and a switch that moves and silently did not
    // save is worse.
    setSettings({ ...settings, [field]: value });
    try {
      const res = await fetch(`${API_BASE}/api/settings`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [field]: value }),
      });
      if (!res.ok) throw new Error();
    } catch {
      setSettings(previous);
      setError(t("toggleError"));
    }
  }

  return (
    <div className="space-y-4" data-testid="signature-manager">
      <div>
        <h3 className="text-sm font-semibold text-gray-900">{t("title")}</h3>
        <p className="text-xs text-gray-500 mt-0.5">{t("subtitle")}</p>
      </div>

      {error && (
        <p
          data-testid="signature-error"
          className="text-xs text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2"
        >
          {error}
        </p>
      )}

      {hasSignature ? (
        <div className="flex gap-4 items-start p-3 bg-gray-50 rounded-lg border border-gray-200">
          {previewSrc && (
            /* The checkerboard is how a transparent PNG becomes visibly
               transparent — on a plain card it is indistinguishable from a
               white box, and only one of the two renders correctly. */
            <span
              className="shrink-0 rounded border border-gray-200 p-1"
              style={{
                backgroundImage:
                  "linear-gradient(45deg,#e5e7eb 25%,transparent 25%,transparent 75%,#e5e7eb 75%),linear-gradient(45deg,#e5e7eb 25%,transparent 25%,transparent 75%,#e5e7eb 75%)",
                backgroundSize: "12px 12px",
                backgroundPosition: "0 0, 6px 6px",
              }}
            >
              <img
                src={previewSrc}
                alt={t("altText")}
                data-testid="signature-preview"
                className="h-12 w-auto max-w-[160px] object-contain"
              />
            </span>
          )}
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium text-gray-900 truncate">{t("labelCurrent")}</p>
            <div className="flex gap-2 mt-2">
              <Button
                variant="outline"
                size="sm"
                data-testid="signature-replace"
                onClick={() => inputRef.current?.click()}
                disabled={uploading}
              >
                {uploading ? t("uploadingLabel") : t("replaceCta")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="border-red-200 text-red-600 hover:bg-red-50"
                data-testid="signature-delete"
                onClick={handleDelete}
                disabled={deleting}
              >
                {deleting ? t("deletingLabel") : t("deleteCta")}
              </Button>
            </div>
          </div>
        </div>
      ) : (
        <div
          className="border-2 border-dashed border-gray-300 rounded-lg p-6 text-center cursor-pointer hover:border-blue-400 hover:bg-blue-50/30 transition-colors"
          data-testid="signature-dropzone"
          onClick={() => inputRef.current?.click()}
        >
          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
          <div className="text-2xl mb-2">✍️</div>
          <p className="text-sm font-medium text-gray-700">{t("uploadPrompt")}</p>
          <p className="text-xs text-gray-400 mt-1">{t("fileHint")}</p>
        </div>
      )}

      <input
        ref={inputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        className="hidden"
        data-testid="signature-file-input"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void handleUpload(file);
          e.target.value = "";
        }}
      />

      {settings && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-gray-700">{t("placementTitle")}</p>
          <label className="flex items-start gap-2 text-xs text-gray-700">
            <input
              type="checkbox"
              data-testid="signature-toggle-letter"
              checked={settings.signature_in_letter}
              onChange={(e) => void toggle("signature_in_letter", e.target.checked)}
              className="mt-0.5 shrink-0"
            />
            <span>{t("placementLetter")}</span>
          </label>
          <label className="flex items-start gap-2 text-xs text-gray-700">
            <input
              type="checkbox"
              data-testid="signature-toggle-cv"
              checked={settings.signature_in_cv}
              onChange={(e) => void toggle("signature_in_cv", e.target.checked)}
              className="mt-0.5 shrink-0"
            />
            <span>{t("placementCv")}</span>
          </label>
          <p className="text-xs text-gray-400">{t("placementHint")}</p>
        </div>
      )}
    </div>
  );
}
