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

/**
 * Applire service worker — US229 (E040), ADR-050 amendment 2026-09-05.
 *
 * Its whole job is installability plus a static shell. Read clause 4a before
 * changing anything below:
 *
 *   The worker caches NOTHING THE USER OWNS. No `/api/*` response, no
 *   `/static/*` response (that is the backend's assets, rendered documents
 *   among them), no navigation HTML. The vault, the generated CVs and letters
 *   and the ATS/truthfulness reports are exactly the data ADR-005's retention
 *   TTLs and the data-minimising default keep off disk — and a Cache Storage
 *   entry survives logout, a retention deletion and a GDPR erasure request
 *   without appearing anywhere in the erasure order (arc42 §6.4).
 *
 * So: cache-first for content-hashed build assets and the icon set, network-
 * first for navigations with one static offline page as the only fallback, and
 * everything else passed straight through untouched. `ALLOW_CACHE_PREFIXES` is
 * the entire privacy guard, and it is why `__tests__/sw.test.ts` exists.
 *
 * System-FMEA SF-FE.3 (the worker retains what it must not) and SF-FE.4 (the
 * worker serves a shell that no longer matches its backend) are the two rows
 * this file is the control for.
 */

// Bump on any change to PRECACHE or to the cache-first allowlist. `activate`
// deletes every cache whose name is not this one, so a bump is also the eviction.
const CACHE_NAME = "applire-shell-v1";

const OFFLINE_URL = "/offline.html";

const PRECACHE = [
  OFFLINE_URL,
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/icon-maskable-192.png",
  "/icons/icon-maskable-512.png",
  "/icons/apple-touch-icon.png",
];

/**
 * Cache-first is allowed ONLY over paths whose URLs are content-addressed or
 * static brand assets. `/_next/static/` is safe because Next hashes those URLs,
 * so a new build asks for new URLs and can never read a stale entry (SF-FE.4's
 * O=1 rests entirely on that property). Adding a path here that is NOT
 * content-hashed re-opens SF-FE.4 at O=3; adding one that can carry user
 * content re-opens SF-FE.3.
 */
const ALLOW_CACHE_PREFIXES = ["/_next/static/", "/icons/"];

/**
 * Checked BEFORE the allowlist, and before any cache read or write. Redundant
 * with the allowlist by construction — deliberately. A future edit that widens
 * `ALLOW_CACHE_PREFIXES` still cannot reach user content through these paths.
 */
const NEVER_TOUCH_PREFIXES = ["/api/", "/static/", "/share-target"];

function startsWithAny(pathname, prefixes) {
  for (const prefix of prefixes) {
    if (pathname.startsWith(prefix)) return true;
  }
  return false;
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHE))
      // A precache miss (a renamed icon) must not brick installation — the
      // worker is a convenience, and a failed install would take the app's
      // navigations with it on the next load.
      .catch(() => undefined)
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(names.map((name) => (name === CACHE_NAME ? undefined : caches.delete(name)))),
      )
      .then(() => self.clients.claim()),
  );
});

async function cacheFirst(request) {
  const hit = await caches.match(request);
  if (hit) return hit;
  const response = await fetch(request);
  // `basic` = same-origin, fully readable. An opaque cross-origin response
  // would be cached blind, and a non-2xx would pin an error page.
  if (response && response.ok && response.type === "basic") {
    const cache = await caches.open(CACHE_NAME);
    await cache.put(request, response.clone());
  }
  return response;
}

async function networkFirstNavigation(request) {
  try {
    // Deliberately NOT cached on success: navigation HTML is the one document
    // that would let a stale or private page survive (clause 4a).
    return await fetch(request);
  } catch {
    const offline = await caches.match(OFFLINE_URL);
    if (offline) return offline;
    return new Response("", { status: 503, statusText: "Offline" });
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  // Only same-origin GETs are ours. Everything else — POST/PATCH/DELETE, a font
  // from Google, the backend behind another origin — is left to the browser
  // untouched, which is also why no mutation can be replayed from this cache.
  if (request.method !== "GET") return;

  let url;
  try {
    url = new URL(request.url);
  } catch {
    return;
  }
  if (url.origin !== self.location.origin) return;
  if (startsWithAny(url.pathname, NEVER_TOUCH_PREFIXES)) return;

  if (startsWithAny(url.pathname, ALLOW_CACHE_PREFIXES)) {
    event.respondWith(cacheFirst(request));
    return;
  }
  if (request.mode === "navigate") {
    event.respondWith(networkFirstNavigation(request));
  }
  // Anything else: the browser's own network path, no cache involvement.
});
