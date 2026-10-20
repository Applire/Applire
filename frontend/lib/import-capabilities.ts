// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * What this instance's import doors can read (#789 L12).
 *
 * The upload surfaces advertise images, which go through the configured OCR backend
 * (`OCR_BACKEND`, default `mistral_vision`, which needs `MISTRAL_API_KEY`). An instance
 * without that key rejects an image while the dropzone invites one. The backend reports
 * the one bit that matters (`GET /api/profile/import-capabilities` -> `image_import`),
 * and the surfaces drop the image hint and the picker's image extensions when it is
 * false. The report is advisory UX: until it arrives, or if it cannot be read, images
 * stay on (today's behaviour) — a failed upload then names its cause (`ocr_unavailable`).
 */

import { useEffect, useState } from "react";

/** The image extensions `CV_UPLOAD_ACCEPT` offers — all of them go through OCR. */
export const IMAGE_UPLOAD_EXTENSIONS: readonly string[] = [
  ".jpg",
  ".jpeg",
  ".png",
  ".tiff",
  ".tif",
  ".bmp",
  ".webp",
];

/** An `<input accept>` list with the image extensions removed. */
export function acceptWithoutImages(accept: string): string {
  return accept
    .split(",")
    .filter((ext) => !IMAGE_UPLOAD_EXTENSIONS.includes(ext.trim().toLowerCase()))
    .join(",");
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

/**
 * Whether this instance can read image CVs. `true` until the backend says `false`.
 *
 * `enabled=false` skips the request — a surface that can render before sign-in (the
 * first screen) must not make a person-scoped call that would trip the global 401
 * redirect; pass its auth-ready flag.
 */
export function useImportCapabilities(enabled = true): { imageImport: boolean } {
  const [imageImport, setImageImport] = useState(true);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    fetch(`${API_BASE}/api/profile/import-capabilities`, { signal: controller.signal })
      .then((res) => (res.ok ? res.json() : null))
      .then((body: unknown) => {
        const flag = (body as { image_import?: unknown } | null)?.image_import;
        if (flag === false) setImageImport(false);
      })
      .catch(() => {
        /* advisory — keep images on */
      });
    return () => controller.abort();
  }, [enabled]);

  return { imageImport };
}
