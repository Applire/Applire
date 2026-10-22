// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * One display form for the release version everywhere in the UI (polish b).
 *
 * The backend reports the PEP 440 form (`/health.version` → "0.44.0b0"); the
 * frontend build bakes the GitHub tag ("v0.44.0-beta"). Both render as the tag
 * form: `v` + SemVer pre-release.
 *
 *   0.44.0b0   → v0.44.0-beta        (a pre-release number of 0 is the bare tag)
 *   1.0.0rc1   → v1.0.0-rc.1
 *   1.0.0a2    → v1.0.0-alpha.2
 *   v0.44.0-beta, 1.0.0 → unchanged apart from the `v` prefix
 */
const PEP440 = /^v?(\d+(?:\.\d+)*)(?:[-_.]?(a|alpha|b|beta|rc|c|pre|preview)[-_.]?(\d*))?$/i;

const LABEL: Record<string, string> = {
  a: "alpha",
  alpha: "alpha",
  b: "beta",
  beta: "beta",
  rc: "rc",
  c: "rc",
  pre: "rc",
  preview: "rc",
};

export function formatVersion(raw: string | null | undefined): string {
  const v = (raw ?? "").trim();
  if (!v) return "";
  const m = PEP440.exec(v);
  if (!m) return v.startsWith("v") ? v : `v${v}`;
  const [, release, kind, num] = m;
  if (!kind) return `v${release}`;
  const label = LABEL[kind.toLowerCase()];
  const n = num === "" || num === "0" ? "" : `.${Number(num)}`;
  return `v${release}-${label}${n}`;
}
