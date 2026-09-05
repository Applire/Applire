// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, expect, it } from "vitest";

import { SHARE_TARGET_ACTION, normalizeSharePayload, shareTargetRedirectPath } from "../share-target";

// US229 (E040, ADR-050 amendment 2026-09-05 clause 4b). The fixture list below IS
// the record of what Android senders actually send — JF-E-Q.5's proposed mitigation
// says to extend it when a new sender is observed rather than widening the regex on
// a hunch. The load-bearing case is `text-carrying-a-url`: Chrome's "Share link" and
// the LinkedIn app both put the posting URL in `text`, usually with the page title
// glued in front, so the obvious url→jd_url binding prefills nothing on the most
// common real share.
describe("normalizeSharePayload", () => {
  it("takes an explicit url param", () => {
    expect(normalizeSharePayload({ url: "https://jobs.example.com/senior-engineer" })).toEqual({
      jdUrl: "https://jobs.example.com/senior-engineer",
    });
  });

  it("extracts the url from text when url is absent (Chrome / LinkedIn share)", () => {
    expect(
      normalizeSharePayload({
        title: "Senior Software Engineer",
        text: "Senior Software Engineer at TechVision https://jobs.example.com/123",
      }),
    ).toEqual({ jdUrl: "https://jobs.example.com/123" });
  });

  it("prefers the explicit url param over one embedded in text", () => {
    expect(
      normalizeSharePayload({
        url: "https://jobs.example.com/canonical",
        text: "see also https://tracker.example.com/click?id=9",
      }),
    ).toEqual({ jdUrl: "https://jobs.example.com/canonical" });
  });

  it("routes a prose-only share to the text tab, title first", () => {
    expect(
      normalizeSharePayload({
        title: "Senior Engineer (m/w/d)",
        text: "Wir suchen eine erfahrene Ingenieurin ...",
      }),
    ).toEqual({ jdText: "Senior Engineer (m/w/d)\n\nWir suchen eine erfahrene Ingenieurin ..." });
  });

  it("uses the title alone when it is the only field", () => {
    expect(normalizeSharePayload({ title: "Senior Engineer (m/w/d)" })).toEqual({
      jdText: "Senior Engineer (m/w/d)",
    });
  });

  it("returns nothing for an empty or whitespace-only share", () => {
    expect(normalizeSharePayload({})).toEqual({});
    expect(normalizeSharePayload({ title: "  ", text: "\n\t" })).toEqual({});
  });

  it("does not treat a title duplicated inside text as prose to repeat", () => {
    expect(normalizeSharePayload({ title: "Backend Engineer", text: "Backend Engineer" })).toEqual({
      jdText: "Backend Engineer",
    });
  });

  it("ignores a non-http scheme rather than prefilling a url the scraper cannot fetch", () => {
    expect(normalizeSharePayload({ url: "javascript:alert(1)" })).toEqual({
      jdText: "javascript:alert(1)",
    });
    expect(normalizeSharePayload({ text: "mailto:jobs@example.com" })).toEqual({
      jdText: "mailto:jobs@example.com",
    });
  });

  it("strips a trailing sentence punctuation from an embedded url", () => {
    expect(normalizeSharePayload({ text: "Schau mal: https://jobs.example.com/42." })).toEqual({
      jdUrl: "https://jobs.example.com/42",
    });
  });
});

describe("shareTargetRedirectPath", () => {
  it("lands on the dashboard with an encoded jd_url", () => {
    expect(shareTargetRedirectPath({ jdUrl: "https://jobs.example.com/a b?x=1&y=2" })).toBe(
      "/dashboard?jd_url=https%3A%2F%2Fjobs.example.com%2Fa+b%3Fx%3D1%26y%3D2",
    );
  });

  it("lands on the dashboard with an encoded jd_text", () => {
    expect(shareTargetRedirectPath({ jdText: "Senior Engineer\n\nWir suchen" })).toBe(
      "/dashboard?jd_text=Senior+Engineer%0A%0AWir+suchen",
    );
  });

  it("lands on the bare dashboard when the share carried nothing usable", () => {
    expect(shareTargetRedirectPath({})).toBe("/dashboard");
  });

  it("exposes the action path the manifest declares", () => {
    // The manifest's share_target.action and the route handler's own path must be
    // the same string; a drift here is a share that 404s on a real phone and on
    // nothing else.
    expect(SHARE_TARGET_ACTION).toBe("/share-target");
  });
});
