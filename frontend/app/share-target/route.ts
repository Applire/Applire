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

import { NextResponse } from "next/server";

import { normalizeSharePayload, shareTargetRedirectPath } from "@/lib/share-target";

/**
 * US229 (E040) — the Android share-sheet landing.
 *
 * `share_target.action` in the manifest; `method: "GET"`, so the payload arrives
 * as query parameters (ADR-050 amendment 2026-09-05 clause 4b). This handler
 * lives in the **frontend**: clause 3's "zero backend changes" holds, and the
 * FastAPI surface never learns that a share sheet exists.
 *
 * It does exactly two things — normalise (senders disagree about which param
 * carries the link; see `lib/share-target.ts`) and redirect. It never calls the
 * API. Clause 4c: capture is a prefill, and the analysis stays the user's tap.
 */

// A share intent is per-request and must never be prerendered or cached: the
// shared posting is in the query string, and a cached redirect would send the
// next share to the previous posting.
export const dynamic = "force-dynamic";

export function GET(request: Request): NextResponse {
  const params = new URL(request.url).searchParams;
  const prefill = normalizeSharePayload({
    title: params.get("title"),
    text: params.get("text"),
    url: params.get("url"),
  });
  // 303: the share sheet's GET is answered with a redirect the browser follows
  // as a fresh GET, and the share URL does not stay in the history entry.
  return NextResponse.redirect(new URL(shareTargetRedirectPath(prefill), request.url), 303);
}
