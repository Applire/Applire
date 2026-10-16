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
 * #781 — the geometry of the profile-photo framing adjuster.
 *
 * Pure functions, no DOM: the dialog (`components/profile/PhotoCropDialog.tsx`)
 * keeps a {@link CropState} and asks this module where the crop lies in the
 * SOURCE image and where the image sits on screen. The browser then draws that
 * source rectangle onto a canvas and uploads only the cropped portrait, so the
 * server, every template and the Word export receive one framing and nothing
 * downstream changes (founder question F-2, option A).
 *
 * The output is a 4:5 portrait. The templates' photo boxes are 1:1 circles
 * (executive, modern_swiss, creative_sidebar) and a 45×55 mm portrait
 * (lebenslauf), all `object-fit: cover; object-position: center top` — a 4:5
 * crop fills the portrait box within 2 % and the circles show its top 80 %,
 * which the dialog draws as a dashed guide.
 */

/** Width ÷ height of the uploaded crop. */
export const PHOTO_ASPECT = 4 / 5;
/** The tightest zoom the slider offers (crop side = 1/4 of the cover crop). */
export const MAX_ZOOM = 4;
/** The widest side of the uploaded crop; smaller sources are never upscaled. */
export const OUTPUT_MAX_WIDTH = 800;

export interface ImageSize {
  w: number;
  h: number;
}

/**
 * The framing, independent of how large the dialog draws it: `zoom` 1 means
 * the crop is the largest 4:5 rectangle the image holds; `cx`/`cy` are the
 * crop's centre as fractions of the image's width and height.
 */
export interface CropState {
  zoom: number;
  cx: number;
  cy: number;
}

/** A rectangle in source-image pixels. */
export interface CropRect {
  sx: number;
  sy: number;
  sw: number;
  sh: number;
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(Math.max(v, lo), hi);
}

/** The crop's size in source pixels at `zoom` (before positioning). */
function cropSize(img: ImageSize, zoom: number, aspect: number): { sw: number; sh: number } {
  const z = clamp(zoom, 1, MAX_ZOOM);
  const cover =
    img.w / img.h > aspect
      ? { sw: img.h * aspect, sh: img.h } // wider than 4:5 — full height
      : { sw: img.w, sh: img.w / aspect }; // taller — full width
  return { sw: cover.sw / z, sh: cover.sh / z };
}

/**
 * Keep the zoom in range and the crop inside the image. Every state the
 * dialog stores has passed through here, so the crop can never show a strip
 * outside the photo.
 */
export function clampCrop(img: ImageSize, state: CropState, aspect = PHOTO_ASPECT): CropState {
  const zoom = clamp(Number.isFinite(state.zoom) ? state.zoom : 1, 1, MAX_ZOOM);
  const { sw, sh } = cropSize(img, zoom, aspect);
  const halfX = sw / 2 / img.w;
  const halfY = sh / 2 / img.h;
  return {
    zoom,
    cx: clamp(Number.isFinite(state.cx) ? state.cx : 0.5, halfX, 1 - halfX),
    cy: clamp(Number.isFinite(state.cy) ? state.cy : 0.5, halfY, 1 - halfY),
  };
}

/**
 * The framing the dialog opens with: exactly what the templates show today
 * (`object-fit: cover; object-position: center top`) — centred horizontally,
 * flush with the top. The candidate starts from the current result and moves
 * away from it, never from an arbitrary guess.
 */
export function initialCrop(img: ImageSize, aspect = PHOTO_ASPECT): CropState {
  return clampCrop(img, { zoom: 1, cx: 0.5, cy: 0 }, aspect);
}

/** Where the crop lies in the source image, in pixels. */
export function cropRect(img: ImageSize, state: CropState, aspect = PHOTO_ASPECT): CropRect {
  const s = clampCrop(img, state, aspect);
  const { sw, sh } = cropSize(img, s.zoom, aspect);
  return { sx: s.cx * img.w - sw / 2, sy: s.cy * img.h - sh / 2, sw, sh };
}

/**
 * Drag: the image follows the pointer, so moving it by (dx, dy) screen pixels
 * moves the crop the OTHER way by the same distance in source pixels scaled
 * by the frame's on-screen width.
 */
export function panCrop(
  img: ImageSize,
  state: CropState,
  dx: number,
  dy: number,
  frameWidthPx: number,
  aspect = PHOTO_ASPECT,
): CropState {
  const { sw } = cropSize(img, clampCrop(img, state, aspect).zoom, aspect);
  const srcPerPx = sw / Math.max(frameWidthPx, 1);
  return clampCrop(
    img,
    {
      zoom: state.zoom,
      cx: state.cx - (dx * srcPerPx) / img.w,
      cy: state.cy - (dy * srcPerPx) / img.h,
    },
    aspect,
  );
}

/** Zoom around the crop's centre (the slider, the +/− buttons, the wheel). */
export function zoomCrop(img: ImageSize, state: CropState, zoom: number, aspect = PHOTO_ASPECT): CropState {
  return clampCrop(img, { ...state, zoom }, aspect);
}

/**
 * How the image is drawn behind a frame `frameWidthPx` wide: its displayed
 * size and its offset from the frame's top-left corner (negative = the image
 * starts left of / above the frame).
 */
export function imageLayout(
  img: ImageSize,
  state: CropState,
  frameWidthPx: number,
  aspect = PHOTO_ASPECT,
): { left: number; top: number; width: number; height: number } {
  const r = cropRect(img, state, aspect);
  const scale = frameWidthPx / r.sw;
  return { left: -r.sx * scale, top: -r.sy * scale, width: img.w * scale, height: img.h * scale };
}

/** The pixel size of the uploaded crop: at most {@link OUTPUT_MAX_WIDTH} wide, never upscaled. */
export function outputSize(rect: CropRect, aspect = PHOTO_ASPECT): { w: number; h: number } {
  const w = Math.max(1, Math.round(Math.min(OUTPUT_MAX_WIDTH, rect.sw)));
  return { w, h: Math.max(1, Math.round(w / aspect)) };
}
