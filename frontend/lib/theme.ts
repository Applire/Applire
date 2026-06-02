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

// frontend/lib/theme.ts

export interface SeedColors {
  primary: string;
  accent: string;
  secondary: string;
}

export type DerivedScheme = Record<string, string>;

/** Canonical list of CSS custom properties produced by {@link deriveScheme}.
 *  Shared so consumers (e.g. the ThemeProvider) can cleanly remove every
 *  editor-applied override and fall back to the static globals.css palette. */
export const DERIVED_SCHEME_KEYS = [
  "--color-primary",
  "--color-primary-container",
  "--color-teal",
  "--color-teal-dim",
  "--color-teal-container",
  "--color-teal-container-light",
  "--color-gold",
  "--color-gold-dim",
  "--color-gold-container",
  "--color-surface-dim",
  "--color-surface-bright",
  "--color-surface-container",
  "--color-surface-container-high",
  "--color-surface-container-highest",
  "--color-neutral-light",
] as const;

/** Blend hex_color into white at the given opacity (0-1).
 *
 *  opacity=0 → #ffffff, opacity=0.2 → 20% primary + 80% white.
 */
function composeRgba(hexColor: string, opacity: number): string {
  const r = parseInt(hexColor.slice(1, 3), 16);
  const g = parseInt(hexColor.slice(3, 5), 16);
  const b = parseInt(hexColor.slice(5, 7), 16);
  const rI = Math.round(r * opacity + 255 * (1 - opacity));
  const gI = Math.round(g * opacity + 255 * (1 - opacity));
  const bI = Math.round(b * opacity + 255 * (1 - opacity));
  return "#" + [rI, gI, bI].map((v) => Math.max(0, Math.min(255, v)).toString(16).padStart(2, "0")).join("");
}

function hexToHsl(hex: string): [number, number, number] {
  const r = parseInt(hex.slice(1, 3), 16) / 255;
  const g = parseInt(hex.slice(3, 5), 16) / 255;
  const b = parseInt(hex.slice(5, 7), 16) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h = 0;
  if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
  else if (max === g) h = ((b - r) / d + 2) / 6;
  else h = ((r - g) / d + 4) / 6;
  return [h, s, l];
}

function hslToHex(h: number, s: number, l: number): string {
  const hue2rgb = (p: number, q: number, t: number): number => {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const r = Math.round(hue2rgb(p, q, h + 1 / 3) * 255);
  const g = Math.round(hue2rgb(p, q, h) * 255);
  const b = Math.round(hue2rgb(p, q, h - 1 / 3) * 255);
  return "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("");
}

function deriveColor(hex: string, lightness: number, saturation: number): string {
  const [h] = hexToHsl(hex);
  return hslToHex(h, saturation, lightness);
}

export function deriveScheme(seeds: SeedColors, surfaceLightness: number): DerivedScheme {
  // 1.0 = pure white surfaces, 0.0 = surfaces match primary color
  const tintOpacity = 1.0 - surfaceLightness;
  return {
    "--color-primary": seeds.primary.toLowerCase(),
    "--color-primary-container": deriveColor(seeds.primary, 0.90, 0.30),
    "--color-teal": seeds.accent.toLowerCase(),
    "--color-teal-dim": deriveColor(seeds.accent, 0.12, 1.00),
    "--color-teal-container": deriveColor(seeds.accent, 0.92, 0.40),
    "--color-teal-container-light": deriveColor(seeds.accent, 0.97, 0.15),
    "--color-gold": seeds.secondary.toLowerCase(),
    "--color-gold-dim": deriveColor(seeds.secondary, 0.20, 1.00),
    "--color-gold-container": deriveColor(seeds.secondary, 0.92, 0.60),
    "--color-surface-dim": composeRgba(seeds.primary, Math.max(0, tintOpacity - 0.08)),
    "--color-surface-bright": "#ffffff",
    "--color-surface-container": composeRgba(seeds.primary, Math.max(0, tintOpacity - 0.05)),
    "--color-surface-container-high": composeRgba(seeds.primary, Math.max(0, tintOpacity - 0.02)),
    "--color-surface-container-highest": composeRgba(seeds.primary, tintOpacity),
    "--color-neutral-light": composeRgba(seeds.primary, tintOpacity * 0.5),
  };
}
