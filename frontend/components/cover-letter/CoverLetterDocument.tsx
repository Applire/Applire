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

interface CoverLetterDocumentProps {
  coverLetterId: string;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");
const CV_WIDTH = 794; // A4 at 96 dpi

export function CoverLetterDocument({ coverLetterId }: CoverLetterDocumentProps) {
  const t = useTranslations("coverLetter");
  const containerRef = useRef<HTMLDivElement>(null);
  const [srcDoc, setSrcDoc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [containerWidth, setContainerWidth] = useState(0);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      setContainerWidth(entry.contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!coverLetterId) return;
    let cancelled = false;

    async function load() {
      try {
        const res = await fetch(`${API_BASE}/api/cover-letter/${coverLetterId}/html`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const html = await res.text();
        if (!cancelled) setSrcDoc(html);
      } catch (err: unknown) {
        if (!cancelled)
          setError(err instanceof Error ? err.message : t("errorPreviewUnavailable"));
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coverLetterId]);

  const scale = containerWidth > 0 ? Math.min(1, containerWidth / CV_WIDTH) : 1;
  const iframeHeight = CV_WIDTH * Math.sqrt(2); // A4 aspect ratio ≈ 1123px

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full overflow-hidden"
    >
      {error ? (
        <div className="flex items-center justify-center h-full text-sm text-red-500">
          {error}
        </div>
      ) : !srcDoc ? (
        <div className="flex items-center justify-center h-full text-sm text-neutral-400">
          {t("previewLoading")}
        </div>
      ) : (
        <iframe
          srcDoc={srcDoc}
          title={t("previewTitle")}
          data-testid="cover-letter-iframe"
          sandbox="allow-same-origin"
          style={{
            width: CV_WIDTH,
            height: iframeHeight,
            transform: `scale(${scale})`,
            transformOrigin: "top left",
            border: "none",
            display: "block",
          }}
        />
      )}
    </div>
  );
}
