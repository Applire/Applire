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

// frontend/components/cv/CVDocument.tsx
"use client";

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { useTranslations } from "next-intl";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");
const CV_WIDTH = 794; // A4 at 96 dpi

export interface CVDocumentHandle {
  refresh: () => void;
}

interface CVDocumentProps {
  cvId: string;
  className?: string;
}

export const CVDocument = forwardRef<CVDocumentHandle, CVDocumentProps>(
  function CVDocument({ cvId, className }, ref) {
    const t = useTranslations("cv");
    const containerRef = useRef<HTMLDivElement>(null);
    const [htmlContent, setHtmlContent] = useState<string | null>(null);
    const [error, setError] = useState(false);
    const [containerWidth, setContainerWidth] = useState(0);
    const [containerHeight, setContainerHeight] = useState(0);
    const [retryToken, setRetryToken] = useState(0);

    useImperativeHandle(ref, () => ({
      refresh: () => setRetryToken((t) => t + 1),
    }));

    useEffect(() => {
      const el = containerRef.current;
      if (!el) return;
      const ro = new ResizeObserver(([entry]) => {
        setContainerWidth(entry.contentRect.width);
        setContainerHeight(entry.contentRect.height);
      });
      ro.observe(el);
      return () => ro.disconnect();
    }, []);

    useEffect(() => {
      let cancelled = false;
      setHtmlContent(null);
      setError(false);

      fetch(`${API_BASE}/api/cv/${cvId}/html`)
        .then((r) => {
          if (!r.ok) throw new Error("Failed to load preview");
          return r.text();
        })
        .then((html) => {
          if (!cancelled) setHtmlContent(html);
        })
        .catch(() => {
          if (!cancelled) setError(true);
        });

      return () => {
        cancelled = true;
      };
    }, [cvId, retryToken]);

    const scale =
      containerWidth > 0 ? Math.min(1, containerWidth / CV_WIDTH) : 1;

    return (
      <div
        ref={containerRef}
        className={`relative bg-white overflow-hidden ${className ?? ""}`}
      >
        {error ? (
          <div className="flex flex-col items-center justify-center h-full gap-3">
            <p className="text-sm text-gray-500">
              {t("previewLoadFailed")}
            </p>
            <button
              type="button"
              onClick={() => {
                setError(false);
                setRetryToken((prev) => prev + 1);
              }}
              className="text-sm text-teal underline hover:opacity-80"
            >
              {t("retryPreview")}
            </button>
          </div>
        ) : !htmlContent ? (
          <div
            className="w-full h-full animate-pulse bg-gray-100 rounded"
            data-testid="cv-loading"
          />
        ) : scale < 1 ? (
          <iframe
            srcDoc={htmlContent}
            sandbox="allow-same-origin"
            title={t("iframeTitle")}
            style={{
              width: CV_WIDTH,
              height: containerHeight > 0 ? containerHeight / scale : "100%",
              transform: `scale(${scale})`,
              transformOrigin: "top left",
              border: "none",
              display: "block",
            }}
            data-testid="cv-iframe"
          />
        ) : (
          // Container is wider than A4 — render at natural width, centred
          <iframe
            srcDoc={htmlContent}
            sandbox="allow-same-origin"
            title={t("iframeTitle")}
            style={{
              width: CV_WIDTH,
              height: "100%",
              border: "none",
              display: "block",
              margin: "0 auto",
            }}
            data-testid="cv-iframe"
          />
        )}
      </div>
    );
  }
);
