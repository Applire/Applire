"use client";

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

import { useEffect } from "react";

/**
 * US229 (E040) — registers `public/sw.js` at root scope.
 *
 * ADR-050 amendment 2026-09-05 clause 4d: **production builds only**. `next dev`
 * serves `/_next/static/` URLs that are not content-hashed, so the worker's
 * cache-first branch would hand back stale chunks and break hot reload — and the
 * dev image is what the OQ lane and every local session run. The self-hosted and
 * Cloud images run `NODE_ENV=production` (frontend/Dockerfile), i.e. every shape
 * a real user meets.
 *
 * Consequence, stated because a green lane must not read as evidence it is not:
 * no Playwright tier exercises this component. The worker's own invariants are
 * gated by `lib/__tests__/sw.test.ts`, which drives `public/sw.js` directly.
 *
 * Renders nothing. Failure is deliberately silent — an unavailable or refused
 * service worker must never degrade the app, which works entirely without one.
 */
export function ServiceWorkerRegistration() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") return;
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
    // Registering during load contends with the first paint's requests; the app
    // gains nothing from an early worker because it caches no data.
    const register = () => {
      void navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
        /* installability is a convenience; never surface this to the user */
      });
    };
    if (document.readyState === "complete") register();
    else window.addEventListener("load", register, { once: true });
    return () => window.removeEventListener("load", register);
  }, []);

  return null;
}
