"use client";

// Copyright (C) 2026 Tobias Rosenbaum
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
 * #781 — choose the framing of the profile photo.
 *
 * The templates crop every photo `object-fit: cover; object-position: center
 * top`, so a landscape shot with the face off-centre lost the face. This
 * dialog opens on the stored framing (or on exactly that automatic one), lets
 * the candidate drag and zoom, and hands back the GEOMETRY — never a cropped
 * file (founder ruling F-2): the original stays stored as uploaded and the
 * server applies the framing at render (`services/photo.frame_photo`), so
 * re-framing is lossless. Geometry: `lib/photo-crop.ts`.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";
import { Minus, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  MAX_ZOOM,
  PHOTO_ASPECT,
  clampCrop,
  imageLayout,
  initialCrop,
  panCrop,
  zoomCrop,
  type CropState,
  type ImageSize,
} from "@/lib/photo-crop";

interface PhotoCropDialogProps {
  /** An object URL (or any same-origin URL) of the photo to frame. */
  src: string;
  /** The stored framing to start from; null/undefined = the automatic one. */
  initialCrop?: CropState | null;
  /** True while the caller saves the result — disables the buttons. */
  busy?: boolean;
  onCancel: () => void;
  onConfirm: (crop: CropState) => void;
}

const KEY_STEP_PX = 8;
const ZOOM_STEP = 0.1;

/** The image drawn so that the crop fills a frame `frameW` wide. */
function FramedImage({
  src,
  size,
  crop,
  frameW,
  className,
}: {
  src: string;
  size: ImageSize;
  crop: CropState;
  frameW: number;
  className?: string;
}) {
  const l = imageLayout(size, crop, frameW);
  return (
    // eslint-disable-next-line @next/next/no-img-element -- a local object URL, not an optimisable asset
    <img
      src={src}
      alt=""
      draggable={false}
      className={className}
      style={{
        position: "absolute",
        left: l.left,
        top: l.top,
        width: l.width,
        height: l.height,
        maxWidth: "none",
      }}
    />
  );
}

/**
 * The photo as the documents show it: `src` framed by `crop` (null = the
 * automatic framing) in a 4:5 box `widthPx` wide. Used for the profile
 * thumbnail so it never shows a framing the CV does not.
 */
export function FramedPhoto({
  src,
  crop,
  widthPx,
  alt,
  className,
}: {
  src: string;
  crop: CropState | null;
  widthPx: number;
  alt: string;
  className?: string;
}) {
  const [size, setSize] = useState<ImageSize | null>(null);
  useEffect(() => {
    const img = new Image();
    img.onload = () => setSize({ w: img.naturalWidth, h: img.naturalHeight });
    img.src = src;
  }, [src]);
  return (
    <div
      role="img"
      aria-label={alt}
      data-testid="framed-photo"
      className={`relative shrink-0 overflow-hidden bg-surface-container ${className ?? ""}`}
      style={{ width: widthPx, height: widthPx / PHOTO_ASPECT }}
    >
      {size && (
        <FramedImage
          src={src}
          size={size}
          crop={crop ? clampCrop(size, crop) : initialCrop(size)}
          frameW={widthPx}
        />
      )}
    </div>
  );
}

export function PhotoCropDialog({ src, initialCrop: startCrop, busy = false, onCancel, onConfirm }: PhotoCropDialogProps) {
  const t = useTranslations("profile.photo.crop");
  const frameRef = useRef<HTMLDivElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinchStart = useRef<{ dist: number; zoom: number } | null>(null);

  const [size, setSize] = useState<ImageSize | null>(null);
  const [crop, setCrop] = useState<CropState | null>(null);
  const [frameW, setFrameW] = useState(240);
  const [failed, setFailed] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  // Load the source once to learn its natural size (EXIF orientation is
  // already applied by the browser to both `naturalWidth` and `drawImage`).
  useEffect(() => {
    const img = new Image();
    img.onload = () => {
      const s = { w: img.naturalWidth, h: img.naturalHeight };
      setSize(s);
      setCrop(startCrop ? clampCrop(s, startCrop) : initialCrop(s));
    };
    img.onerror = () => setFailed(true);
    img.src = src;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the start framing is read once per image
  }, [src]);

  // The frame's on-screen width drives the drag scale and the image layout.
  useEffect(() => {
    const el = frameRef.current;
    if (!el) return;
    const read = () => setFrameW(el.clientWidth || 240);
    read();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, [size]);

  useEffect(() => {
    stageRef.current?.focus();
  }, [size]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !busy) onCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);

  const setZoom = useCallback(
    (zoom: number) => {
      if (!size) return;
      setCrop((c) => (c ? zoomCrop(size, c, zoom) : c));
    },
    [size],
  );

  function onPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    (e.currentTarget as HTMLDivElement).setPointerCapture?.(e.pointerId);
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.current.size === 2 && crop) {
      const [a, b] = [...pointers.current.values()];
      pinchStart.current = { dist: Math.hypot(a.x - b.x, a.y - b.y), zoom: crop.zoom };
    }
  }

  function onPointerMove(e: React.PointerEvent<HTMLDivElement>) {
    const prev = pointers.current.get(e.pointerId);
    if (!prev || !size) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.current.size === 2 && pinchStart.current) {
      const [a, b] = [...pointers.current.values()];
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      setZoom((pinchStart.current.zoom * dist) / Math.max(pinchStart.current.dist, 1));
      return;
    }
    const dx = e.clientX - prev.x;
    const dy = e.clientY - prev.y;
    setCrop((c) => (c ? panCrop(size, c, dx, dy, frameW) : c));
  }

  function onPointerUp(e: React.PointerEvent<HTMLDivElement>) {
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinchStart.current = null;
  }

  function onWheel(e: React.WheelEvent<HTMLDivElement>) {
    if (!crop) return;
    setZoom(crop.zoom * Math.exp(-e.deltaY * 0.0015));
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (!size || !crop) return;
    const step = e.shiftKey ? KEY_STEP_PX * 5 : KEY_STEP_PX;
    const moves: Record<string, [number, number]> = {
      ArrowLeft: [step, 0],
      ArrowRight: [-step, 0],
      ArrowUp: [0, step],
      ArrowDown: [0, -step],
    };
    if (moves[e.key]) {
      e.preventDefault();
      // The arrow moves the FRAME over the photo (ArrowRight = look further
      // right), which is the image moving the other way.
      const [dx, dy] = moves[e.key];
      setCrop(panCrop(size, crop, dx, dy, frameW));
    } else if (e.key === "+" || e.key === "=") {
      e.preventDefault();
      setZoom(crop.zoom + ZOOM_STEP);
    } else if (e.key === "-") {
      e.preventDefault();
      setZoom(crop.zoom - ZOOM_STEP);
    }
  }

  function confirm() {
    if (crop) onConfirm(crop);
  }

  if (!mounted) return null;

  const ready = size !== null && crop !== null;

  const dialog = (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="photo-crop-title"
      data-testid="photo-crop-dialog"
      className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
    >
      <div className="bg-white rounded-t-2xl md:rounded-xl shadow-xl w-full md:max-w-lg max-h-[92vh] overflow-y-auto p-5 md:p-6">
        <div aria-hidden="true" className="md:hidden mx-auto mb-3 h-1 w-10 rounded-full bg-gray-300" />
        <h3 id="photo-crop-title" className="text-base font-bold text-on-surface">
          {t("title")}
        </h3>
        <p id="photo-crop-hint" className="text-sm text-on-surface-variant mt-1 mb-4 leading-relaxed">
          {t("hint")}
        </p>

        {failed ? (
          <p data-testid="photo-crop-error" className="text-sm text-critical mb-4">
            {t("loadError")}
          </p>
        ) : (
          <>
            {/* The stage: the photo moves under a fixed 4:5 frame; what lies
                outside the frame is dimmed, not hidden, so the candidate sees
                what they are leaving out. */}
            <div
              ref={stageRef}
              data-testid="photo-crop-stage"
              role="group"
              aria-label={t("stageLabel")}
              aria-describedby="photo-crop-hint"
              tabIndex={0}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp}
              onWheel={onWheel}
              onKeyDown={onKeyDown}
              className="relative mx-auto h-[300px] md:h-[340px] w-full overflow-hidden rounded-lg bg-neutral-dark select-none touch-none cursor-grab active:cursor-grabbing focus:outline-none focus-visible:ring-2 focus-visible:ring-teal focus-visible:ring-offset-2"
            >
              <div
                ref={frameRef}
                className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 h-[260px] md:h-[300px]"
                style={{ aspectRatio: String(PHOTO_ASPECT) }}
              >
                {ready && (
                  <FramedImage src={src} size={size} crop={crop} frameW={frameW} className="pointer-events-none" />
                )}
                {/* Dim everything outside the frame. */}
                <div
                  aria-hidden="true"
                  className="pointer-events-none absolute inset-0 rounded-[2px] ring-2 ring-white"
                  style={{ boxShadow: "0 0 0 2000px rgba(20, 27, 43, 0.62)" }}
                />
                {/* What the round templates show: the frame's top square. */}
                <div
                  aria-hidden="true"
                  data-testid="photo-crop-circle-guide"
                  className="pointer-events-none absolute left-0 top-0 w-full aspect-square rounded-full border-2 border-dashed border-white/80"
                />
              </div>
            </div>

            <div className="mt-4 flex items-center gap-3">
              <button
                type="button"
                onClick={() => crop && setZoom(crop.zoom - ZOOM_STEP * 2)}
                disabled={!ready || (crop?.zoom ?? 1) <= 1}
                aria-label={t("zoomOut")}
                className="h-9 w-9 shrink-0 inline-flex items-center justify-center rounded-full border border-outline-variant text-on-surface hover:bg-surface-container disabled:opacity-40"
              >
                <Minus aria-hidden="true" className="h-4 w-4" />
              </button>
              <input
                type="range"
                data-testid="photo-crop-zoom"
                min={1}
                max={MAX_ZOOM}
                step={0.01}
                value={crop?.zoom ?? 1}
                disabled={!ready}
                onChange={(e) => setZoom(Number(e.target.value))}
                aria-label={t("zoomLabel")}
                className="flex-1 accent-teal"
              />
              <button
                type="button"
                onClick={() => crop && setZoom(crop.zoom + ZOOM_STEP * 2)}
                disabled={!ready || (crop?.zoom ?? 1) >= MAX_ZOOM}
                aria-label={t("zoomIn")}
                className="h-9 w-9 shrink-0 inline-flex items-center justify-center rounded-full border border-outline-variant text-on-surface hover:bg-surface-container disabled:opacity-40"
              >
                <Plus aria-hidden="true" className="h-4 w-4" />
              </button>
            </div>

            {/* How the two template shapes will show it. */}
            {ready && (
              <div className="mt-4 flex items-end gap-5">
                <figure className="flex flex-col items-center gap-1">
                  <div className="relative h-14 w-14 overflow-hidden rounded-full ring-1 ring-outline-variant">
                    <FramedImage src={src} size={size} crop={crop} frameW={56} />
                  </div>
                  <figcaption className="text-[11px] text-on-surface-variant">{t("previewRound")}</figcaption>
                </figure>
                <figure className="flex flex-col items-center gap-1">
                  <div className="relative h-[70px] w-14 overflow-hidden rounded-[2px] ring-1 ring-outline-variant">
                    <FramedImage src={src} size={size} crop={crop} frameW={56} />
                  </div>
                  <figcaption className="text-[11px] text-on-surface-variant">{t("previewPortrait")}</figcaption>
                </figure>
                <p className="text-xs text-on-surface-variant leading-relaxed pb-5">{t("guide")}</p>
              </div>
            )}
          </>
        )}

        <div className="mt-5 flex items-center justify-between gap-2">
          <button
            type="button"
            data-testid="photo-crop-reset"
            onClick={() => size && setCrop(initialCrop(size))}
            disabled={!ready || busy}
            className="text-[13px] font-semibold text-teal hover:underline disabled:opacity-40"
          >
            {t("reset")}
          </button>
          <div className="flex items-center gap-2">
            <Button type="button" variant="outline" size="sm" onClick={onCancel} disabled={busy}>
              {t("cancel")}
            </Button>
            <Button
              type="button"
              size="sm"
              data-testid="photo-crop-confirm"
              onClick={confirm}
              disabled={!ready || busy || failed}
            >
              {busy ? t("saving") : t("confirm")}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );

  return createPortal(dialog, document.body);
}
