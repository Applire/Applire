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
import { paginatePreview } from "@/components/document/previewPages";

interface CoverLetterDocumentProps {
  coverLetterId: string;
  /** Fires each time the preview document (re)loads — ADR-090 cl. 2 marks do not survive a reload. */
  onPreviewLoad?: () => void;
  /**
   * What the current letter was generated with. `null`/`undefined` = unknown
   * (e.g. a letter from an earlier session) — then no hint is shown, never a
   * guess.
   */
  generationInputs?: { availability?: string | null; motivation?: string | null } | null;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");
const CV_WIDTH = 794; // A4 at 96 dpi

export function CoverLetterDocument({ coverLetterId, onPreviewLoad, generationInputs }: CoverLetterDocumentProps) {
  const t = useTranslations("coverLetter");
  const tDelivery = useTranslations("documentReview.delivery");
  const containerRef = useRef<HTMLDivElement>(null);
  const [srcDoc, setSrcDoc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [containerWidth, setContainerWidth] = useState(0);
  const [containerHeight, setContainerHeight] = useState(0);

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
  // U-6: the iframe fills the preview box and scrolls inside (as the CV's
  // does) — a fixed one-A4 height clipped a two-page letter right at its page
  // band, so page 2 was out of reach on the phone. One A4 until measured.
  const iframeHeight = containerHeight > 0 ? containerHeight / scale : CV_WIDTH * Math.sqrt(2);

  // U-6 (UAT-2): a letter over one page shows where the PDF breaks.
  const handleLoad = (e: React.SyntheticEvent<HTMLIFrameElement>) => {
    const doc = e.currentTarget.contentDocument;
    const done = () => onPreviewLoad?.();
    if (!doc) return done();
    const run = () => {
      try {
        paginatePreview(doc, (page, total) => tDelivery("pageBand", { page, total }));
      } catch {
        // A preview aid only — never block the preview on it.
      }
      done();
    };
    const fonts = (doc as Document & { fonts?: FontFaceSet }).fonts;
    if (fonts?.ready) void fonts.ready.then(run, run);
    else run();
  };

  // L-1: the writer never invents a notice period or a reason for the change,
  // so a letter generated without them simply lacks both. One neutral line says so.
  const noticeMissing = generationInputs != null && !generationInputs.availability?.trim();
  const reasonMissing = generationInputs != null && !generationInputs.motivation?.trim();
  const hintKey =
    noticeMissing && reasonMissing
      ? "previewHintBoth"
      : noticeMissing
        ? "previewHintNotice"
        : reasonMissing
          ? "previewHintReason"
          : null;

  return (
    <div className="flex flex-col w-full h-full">
      {hintKey && (
        <p
          className="shrink-0 px-3 py-2 text-xs text-on-surface-variant bg-surface-container border-b border-outline-variant"
          data-testid="cover-letter-preview-hint"
        >
          {t(hintKey)}
        </p>
      )}
    <div
      ref={containerRef}
      className="relative w-full flex-1 min-h-0 overflow-hidden"
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
          onLoad={handleLoad}
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
    </div>
  );
}
