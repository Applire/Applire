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
 * So: network-first for navigations with one static offline page as the only
 * fallback, and everything else passed straight through untouched.
 *
 * Amendment 2026-10-11 (Firefox, local-network access): the worker no longer
 * answers ANY sub-resource request — it used to serve `/_next/static/` and
 * `/icons/` cache-first. A request the worker fetches on a page's behalf is
 * judged by the browser's network policy WITHOUT the page's context: Firefox's
 * local-network-access check rejected every chunk fetch from the worker on an
 * instance served from a Tailscale (100.x) address, while the same request from
 * the page itself was allowed. The page then never hydrated — an endless
 * "Loading", a login and setup form that submitted natively and reloaded. A
 * worker that answers a sub-resource owns its failure, and the app works
 * entirely without one, so sub-resources are the browser's again; Next's
 * hashed `/_next/static/` URLs are `immutable` in the HTTP cache anyway.
 *
 * System-FMEA SF-FE.3 (the worker retains what it must not), SF-FE.4 (the
 * worker serves a shell that no longer matches its backend) and SF-FE.5 (the
 * worker's own fetch fails where the page's would not) are the rows this file
 * is the control for.
 */

// Bump on any change to PRECACHE or to what the fetch handler answers.
// `activate` deletes every cache whose name is not this one, so a bump is also
// the eviction (v2: the v1 cache held build chunks the worker no longer serves).
const CACHE_NAME = "applire-shell-v2";

const OFFLINE_URL = "/offline.html";

// The offline page and the icon set — read only by the offline fallback.
const PRECACHE = [
  OFFLINE_URL,
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/icon-maskable-192.png",
  "/icons/icon-maskable-512.png",
  "/icons/apple-touch-icon.png",
];

/**
 * Checked before the navigation branch, and before any cache read. A
 * navigation to one of these (a PDF opened directly, the share-target landing)
 * is left to the browser so its response can never meet the cache.
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

  if (request.mode === "navigate") {
    event.respondWith(networkFirstNavigation(request));
  }
  // Anything else — scripts, styles, images, fonts: the browser's own network
  // path, no worker fetch and no cache involvement (amendment 2026-10-11).
});
