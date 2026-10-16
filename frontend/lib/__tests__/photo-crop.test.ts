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

import { describe, expect, it } from "vitest";
import {
  MAX_ZOOM,
  PHOTO_ASPECT,
  clampCrop,
  cropRect,
  imageLayout,
  initialCrop,
  panCrop,
  zoomCrop,
} from "@/lib/photo-crop";

// #781's shape: a landscape shot (16:9) with the face in the right third.
const LANDSCAPE = { w: 1800, h: 1012 };
const PORTRAIT = { w: 480, h: 600 };

function inside(img: { w: number; h: number }, r: { sx: number; sy: number; sw: number; sh: number }) {
  const eps = 1e-6;
  return r.sx >= -eps && r.sy >= -eps && r.sx + r.sw <= img.w + eps && r.sy + r.sh <= img.h + eps;
}

describe("photo-crop geometry (#781)", () => {
  it("opens on what the templates show today: centred, top-flush, full height of a landscape", () => {
    const r = cropRect(LANDSCAPE, initialCrop(LANDSCAPE));
    expect(r.sh).toBeCloseTo(1012);
    expect(r.sw).toBeCloseTo(1012 * PHOTO_ASPECT);
    expect(r.sy).toBeCloseTo(0);
    expect(r.sx + r.sw / 2).toBeCloseTo(900); // horizontally centred
  });

  it("opens a portrait source as the full width, top-flush", () => {
    const r = cropRect(PORTRAIT, initialCrop(PORTRAIT));
    expect(r.sw).toBeCloseTo(480);
    expect(r.sh).toBeCloseTo(600);
    expect(r.sx).toBeCloseTo(0);
    expect(r.sy).toBeCloseTo(0);
  });

  it("always yields a 4:5 rectangle", () => {
    for (const zoom of [1, 1.7, 2.5, MAX_ZOOM]) {
      const r = cropRect(LANDSCAPE, { zoom, cx: 0.8, cy: 0.6 });
      expect(r.sw / r.sh).toBeCloseTo(PHOTO_ASPECT);
    }
  });

  it("dragging the image left moves the crop right — to the face in the right third", () => {
    const start = initialCrop(LANDSCAPE);
    const moved = panCrop(LANDSCAPE, start, -400, 0, 200);
    expect(cropRect(LANDSCAPE, moved).sx).toBeGreaterThan(cropRect(LANDSCAPE, start).sx);
  });

  it("never lets a drag or zoom push the crop outside the photo", () => {
    let s = initialCrop(LANDSCAPE);
    for (const [dx, dy] of [[-99999, 0], [99999, 99999], [0, -99999], [12, 7]]) {
      s = panCrop(LANDSCAPE, s, dx, dy, 240);
      expect(inside(LANDSCAPE, cropRect(LANDSCAPE, s))).toBe(true);
      s = zoomCrop(LANDSCAPE, s, s.zoom === 1 ? 3 : 1);
      expect(inside(LANDSCAPE, cropRect(LANDSCAPE, s))).toBe(true);
    }
  });

  it("clamps the zoom into [1, MAX_ZOOM] and repairs non-finite input", () => {
    expect(clampCrop(LANDSCAPE, { zoom: 0.2, cx: 0.5, cy: 0.5 }).zoom).toBe(1);
    expect(clampCrop(LANDSCAPE, { zoom: 99, cx: 0.5, cy: 0.5 }).zoom).toBe(MAX_ZOOM);
    const repaired = clampCrop(LANDSCAPE, { zoom: Number.NaN, cx: Number.NaN, cy: Number.NaN });
    expect(repaired.zoom).toBe(1);
    expect(inside(LANDSCAPE, cropRect(LANDSCAPE, repaired))).toBe(true);
  });

  it("zooming in halves the crop side at zoom 2 and keeps the centre", () => {
    const s = { zoom: 1, cx: 0.75, cy: 0.5 };
    const z2 = zoomCrop(LANDSCAPE, s, 2);
    const a = cropRect(LANDSCAPE, clampCrop(LANDSCAPE, s));
    const b = cropRect(LANDSCAPE, z2);
    expect(b.sw).toBeCloseTo(a.sw / 2);
    expect(b.sx + b.sw / 2).toBeCloseTo(0.75 * 1800);
  });

  it("lays the image out so the crop exactly fills the on-screen frame", () => {
    const s = { zoom: 2, cx: 0.8, cy: 0.45 };
    const frame = 240;
    const l = imageLayout(LANDSCAPE, s, frame);
    const r = cropRect(LANDSCAPE, s);
    const scale = frame / r.sw;
    expect(l.width).toBeCloseTo(1800 * scale);
    expect(-l.left / scale).toBeCloseTo(r.sx);
    expect(-l.top / scale).toBeCloseTo(r.sy);
  });
});

// #781 — the server applies the stored framing with `services/photo.crop_rect`,
// this module's twin. The SAME table is pinned in
// `tests/unit/test_photo_framing.py` (`PARITY_TABLE`): the expected rectangles
// were worked out from this algorithm by hand, so a drift in EITHER twin fails
// its own suite. Rows: [w, h, cx, cy, zoom] -> [sx, sy, sw, sh].
describe("parity with services/photo.crop_rect", () => {
  const PARITY_TABLE: Array<[number[], number[]]> = [
    // landscape 1800x1012: the 4:5 cover rectangle is 809.6 x 1012
    [[1800, 1012, 0.5, 0, 1], [495.2, 0, 809.6, 1012]], // centred, cy clamps to the only fit
    [[1800, 1012, 0, 0.5, 1], [0, 0, 809.6, 1012]], // cx=0 clamps to the left edge
    [[1800, 1012, 1, 0.5, 1], [990.4, 0, 809.6, 1012]], // cx=1 clamps to the right edge
    [[1800, 1012, 0.8, 0.5, 2], [1237.6, 253, 404.8, 506]], // face in the right third, zoom 2
    [[1800, 1012, 0.8, 0.1, 4], [1338.8, 0, 202.4, 253]], // zoom 4, cy clamps to the top
    // portrait / square sources
    [[480, 600, 0.3, 0.7, 1], [0, 0, 480, 600]], // exactly 4:5 — the whole image
    [[600, 1000, 0.9, 0.2, 1.5], [200, 0, 400, 500]], // taller than 4:5 — full width, both clamp
    [[600, 1000, 0.5, 0, 1], [0, 0, 600, 750]],
    [[1000, 1000, 0.5, 0.5, 1], [100, 0, 800, 1000]],
    [[1000, 1000, 0.5, 0.5, 3], [366.6666667, 333.3333333, 266.6666667, 333.3333333]],
    [[4000, 3000, 0.25, 0.25, 2], [400, 0, 1200, 1500]],
  ];

  it.each(PARITY_TABLE)("cropRect(%j) -> %j", ([w, h, cx, cy, zoom], [sx, sy, sw, sh]) => {
    const r = cropRect({ w, h }, { cx, cy, zoom });
    expect(r.sx).toBeCloseTo(sx, 5);
    expect(r.sy).toBeCloseTo(sy, 5);
    expect(r.sw).toBeCloseTo(sw, 5);
    expect(r.sh).toBeCloseTo(sh, 5);
  });
});
