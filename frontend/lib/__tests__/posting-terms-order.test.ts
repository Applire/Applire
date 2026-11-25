// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * DH item 5 (Sorbet invite-gate delivery run, 2026-11-25): at 0 remaining the web asked
 * the 13-term tick list first and refused after the person had answered it. The
 * allowance is asked before the list.
 */
import { afterEach, describe, expect, it } from "vitest";

import { ALLOWANCE_REFUSED_EVENT } from "@/components/plan/events";
import { termsBeforeGeneration } from "../posting-terms";

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

function serve(refusal: object | null) {
  const urls: string[] = [];
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    urls.push(url);
    if (url.includes("/api/me/allowance")) return new Response(JSON.stringify({ application_refusal: refusal }), { status: 200 });
    if (url.includes("/posting-terms")) {
      return new Response(
        JSON.stringify({ terms: [{ term: "Kafka", their_words: [] }], declined: [], left_open: [], confirmed: [], answered_at: null, language: "de" }),
        { status: 200 },
      );
    }
    return new Response("{}", { status: 404 });
  }) as typeof fetch;
  return urls;
}

describe("termsBeforeGeneration", () => {
  it("a refused application opens the dialog and never reads the tick list", async () => {
    const urls = serve({ error_code: "allowance_exhausted", action: "application", renews_on: "2026-12-01" });
    const seen: Event[] = [];
    const on = (e: Event) => seen.push(e);
    window.addEventListener(ALLOWANCE_REFUSED_EVENT, on);
    expect(await termsBeforeGeneration("job-9")).toBe("refused");
    window.removeEventListener(ALLOWANCE_REFUSED_EVENT, on);
    expect(seen).toHaveLength(1);
    expect(urls.some((u) => u.includes("/posting-terms"))).toBe(false);
  });

  it("an admitted application gets the tick list as before", async () => {
    const urls = serve(null);
    const state = await termsBeforeGeneration("job-9");
    expect(state && state !== "refused" ? state.terms.map((t) => t.term) : state).toEqual(["Kafka"]);
    expect(urls[0]).toContain("/api/me/allowance?job_id=job-9");
  });
});
