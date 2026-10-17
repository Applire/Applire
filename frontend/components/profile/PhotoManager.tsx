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


import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { FramedPhoto, PhotoCropDialog } from "@/components/profile/PhotoCropDialog";
import type { CropState } from "@/lib/photo-crop";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

interface PhotoManagerProps {
  /** Called after a successful upload or delete so the parent can refresh. */
  onPhotoChange?: (photoUrl: string | null) => void;
  /** Current photo_url from the profile (file path, not data URI). */
  currentPhotoUrl?: string | null;
}

export function PhotoManager({ onPhotoChange, currentPhotoUrl }: PhotoManagerProps) {
  const t = useTranslations("profile.photo");
  // Consent defaults to true when the user already has a photo (Replace path — no re-consent needed)
  const [consent, setConsent] = useState(!!currentPhotoUrl);
  const [uploading, setUploading] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [previewSrc, setPreviewSrc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploadedAt, setUploadedAt] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // #781 (founder ruling F-2) — the framing dialog. `cropSrc` is the image it
  // shows: a just-picked file (`pendingFile` set — uploaded on confirm,
  // unmodified, WITH the chosen framing) or the stored photo when the
  // candidate re-frames it (only the geometry is saved). The original is
  // never cropped in the browser: every template and the Word export apply
  // the framing at render, so re-framing stays lossless.
  const [cropSrc, setCropSrc] = useState<string | null>(null);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [savedCrop, setSavedCrop] = useState<CropState | null>(null);
  const [savingCrop, setSavingCrop] = useState(false);

  const hasPhoto = !!(currentPhotoUrl || previewSrc);

  // Load preview via GET /api/profile/photo when a photo exists on the server.
  // Revoke the previous object URL to avoid memory leaks.
  useEffect(() => {
    if (!currentPhotoUrl) return;
    let objectUrl: string | null = null;
    fetch(`${API_BASE}/api/profile/photo`)
      .then((r) => (r.ok ? r.blob() : null))
      .then((blob) => {
        if (blob) {
          objectUrl = URL.createObjectURL(blob);
          setPreviewSrc(objectUrl);
        }
      })
      .catch(() => { /* non-fatal */ });
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [currentPhotoUrl]);

  // #781 — the stored framing, so the thumbnail shows what the documents show
  // and the dialog re-opens where the candidate left it.
  useEffect(() => {
    if (!currentPhotoUrl) return;
    let cancelled = false;
    fetch(`${API_BASE}/api/profile`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data: { profile?: { personal_info?: { photo_crop?: CropState | null } } } | null) => {
        if (!cancelled) setSavedCrop(data?.profile?.personal_info?.photo_crop ?? null);
      })
      .catch(() => { /* non-fatal — the thumbnail falls back to the default framing */ });
    return () => {
      cancelled = true;
    };
  }, [currentPhotoUrl]);

  function closeCrop() {
    if (pendingFile && cropSrc) URL.revokeObjectURL(cropSrc);
    setPendingFile(null);
    setCropSrc(null);
  }

  /** #781 — a picked file is framed first, then uploaded with its framing. */
  function handlePick(file: File) {
    setError(null);
    if (!consent) {
      setError(t("consentError"));
      return;
    }
    setPendingFile(file);
    setCropSrc(URL.createObjectURL(file));
  }

  async function handleFramed(crop: CropState) {
    if (pendingFile && cropSrc) {
      const ok = await handleUpload(pendingFile, crop, cropSrc);
      if (ok) {
        // The dialog's object URL now IS the preview — keep it alive.
        setPendingFile(null);
        setCropSrc(null);
      }
      return;
    }
    // Re-framing the stored photo: only the geometry changes.
    setError(null);
    setSavingCrop(true);
    try {
      const res = await fetch(`${API_BASE}/api/profile/photo/crop`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cx: crop.cx, cy: crop.cy, zoom: crop.zoom }),
      });
      if (!res.ok) throw new Error(t("crop.saveError"));
      setSavedCrop(crop);
      setCropSrc(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("crop.saveError"));
    } finally {
      setSavingCrop(false);
    }
  }

  async function handleUpload(file: File, crop: CropState | null, localSrc: string): Promise<boolean> {
    setError(null);
    if (!consent) {
      setError(t("consentError"));
      return false;
    }
    setUploading(true);
    try {
      const body = new FormData();
      body.append("file", file);
      const params = new URLSearchParams({ consent: "true" });
      if (crop) {
        params.set("cx", String(crop.cx));
        params.set("cy", String(crop.cy));
        params.set("zoom", String(crop.zoom));
      }
      const res = await fetch(`${API_BASE}/api/profile/photo?${params.toString()}`, {
        method: "POST",
        body,
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error((err as { detail?: string }).detail ?? t("uploadError"));
      }
      const data = (await res.json()) as { photo_url: string; consent_at: string };
      setUploadedAt(
        new Date(data.consent_at).toLocaleDateString("en-GB", {
          day: "numeric",
          month: "short",
          year: "numeric",
        }),
      );
      // Revoke any previous preview and show the new one immediately.
      setPreviewSrc((prev) => {
        if (prev && prev !== localSrc) URL.revokeObjectURL(prev);
        return localSrc;
      });
      setSavedCrop(crop);
      onPhotoChange?.(data.photo_url);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : t("uploadError"));
      return false;
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete() {
    setError(null);
    setDeleting(true);
    try {
      const res = await fetch(`${API_BASE}/api/profile/photo`, { method: "DELETE" });
      if (!res.ok) throw new Error(t("deleteError"));
      setPreviewSrc((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
      setUploadedAt(null);
      setSavedCrop(null);
      setConsent(false);
      onPhotoChange?.(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("deleteError"));
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-sm font-semibold text-gray-900">{t("title")}</h3>
        <p className="text-xs text-gray-500 mt-0.5">
          {t("subtitle")}
        </p>
      </div>

      {error && (
        <p className="text-xs text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">
          {error}
        </p>
      )}

      {hasPhoto ? (
        /* Filled state */
        <div className="flex gap-4 items-start p-3 bg-gray-50 rounded-lg border border-gray-200">
          {previewSrc && (
            <FramedPhoto
              src={previewSrc}
              crop={savedCrop}
              widthPx={56}
              alt={t("altText")}
              className="h-[70px] w-14 rounded"
            />
          )}
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium text-gray-900 truncate">{t("labelCurrent")}</p>
            {uploadedAt && (
              <p className="text-xs text-gray-500 mt-0.5">{t("uploadedLabel", { date: uploadedAt })}</p>
            )}
            <div className="flex flex-wrap gap-2 mt-2">
              {previewSrc && (
                <Button
                  data-testid="photo-adjust"
                  variant="outline"
                  size="sm"
                  onClick={() => setCropSrc(previewSrc)}
                  disabled={uploading || savingCrop}
                >
                  {t("adjustCta")}
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={() => inputRef.current?.click()}
                disabled={uploading}
              >
                {uploading ? t("uploadingLabel") : t("replaceCta")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="border-red-200 text-red-600 hover:bg-red-50"
                onClick={handleDelete}
                disabled={deleting}
              >
                {deleting ? t("deletingLabel") : t("deleteCta")}
              </Button>
            </div>
          </div>
        </div>
      ) : (
        /* Empty state */
        <>
          <div
            className="border-2 border-dashed border-gray-300 rounded-lg p-6 text-center cursor-pointer hover:border-blue-400 hover:bg-blue-50/30 transition-colors"
            onClick={() => inputRef.current?.click()}
          >
            {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
            <div className="text-2xl mb-2">📷</div>
            <p className="text-sm font-medium text-gray-700">{t("uploadPrompt")}</p>
            <p className="text-xs text-gray-400 mt-1">{t("fileHint")}</p>
          </div>

          <div className="flex gap-2 items-start bg-blue-50 border border-blue-200 rounded-lg px-3 py-2">
            <input
              type="checkbox"
              id="photo-consent"
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
              className="mt-0.5 flex-shrink-0"
            />
            <label
              htmlFor="photo-consent"
              className="text-xs text-blue-800 leading-relaxed cursor-pointer"
            >
              {t("consentLabel")}
              {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
              {" "}
              <a href="/privacy" className="underline">
                {t("privacyLink")}
              </a>
            </label>
          </div>

          <Button
            className="w-full"
            disabled={!consent || uploading}
            onClick={() => inputRef.current?.click()}
          >
            {uploading ? t("uploadingLabel") : t("uploadCta")}
          </Button>
        </>
      )}

      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) handlePick(file);
          e.target.value = "";
        }}
      />

      {cropSrc && (
        <PhotoCropDialog
          src={cropSrc}
          initialCrop={pendingFile ? null : savedCrop}
          busy={uploading || savingCrop}
          onCancel={closeCrop}
          onConfirm={(crop) => void handleFramed(crop)}
        />
      )}

      {hasPhoto && (
        <p className="text-xs text-green-700 bg-green-50 border border-green-200 rounded px-3 py-2">
          {t("successMessage")}
        </p>
      )}
    </div>
  );
}
