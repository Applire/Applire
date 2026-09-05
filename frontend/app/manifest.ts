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

import type { MetadataRoute } from "next";

import { SHARE_TARGET_ACTION } from "@/lib/share-target";

/**
 * US229 (E040) — the web app manifest, served at `/manifest.webmanifest`.
 *
 * ADR-050 clause 4 deferred the PWA "earliest Stracciatella"; the 2026-09-05
 * amendment lands it and fixes what it may do. Two clauses show up in this file:
 *
 *   4b — `share_target` is `method: "GET"`, so Android delivers the shared
 *        posting as query parameters to a Next Route Handler. A POST/multipart
 *        target would need a body-consuming endpoint and would break clause 3
 *        (zero backend changes).
 *   4c — the action is a *prefill* deep link. Nothing here starts an analysis.
 *
 * The strings are intentionally not run through next-intl: a manifest is served
 * once, statically, before any locale is known (the locale lives behind
 * `/api/settings`, see `lib/providers/locale-provider.tsx`). German is the
 * product's default UI language, so the install prompt speaks German; `lang` and
 * `dir` say so. This is the one place in the frontend where a user-visible
 * string is legitimately not a translation key.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/dashboard",
    name: "Applire — Bewerbungen, die stimmen",
    short_name: "Applire",
    description:
      "Lebenslauf und Anschreiben aus dem eigenen Profil — geprüft, nachvollziehbar, DACH-konform.",
    lang: "de",
    dir: "ltr",
    start_url: "/dashboard",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    // --color-primary (EU Navy) and --color-neutral-light from app/globals.css.
    theme_color: "#003399",
    background_color: "#F5F7FA",
    categories: ["productivity", "business"],
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-256.png", sizes: "256x256", type: "image/png", purpose: "any" },
      { src: "/icons/icon-384.png", sizes: "384x384", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      {
        src: "/icons/icon-maskable-192.png",
        sizes: "192x192",
        type: "image/png",
        purpose: "maskable",
      },
      {
        src: "/icons/icon-maskable-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
    share_target: {
      action: SHARE_TARGET_ACTION,
      method: "GET",
      params: { title: "title", text: "text", url: "url" },
    },
  };
}
