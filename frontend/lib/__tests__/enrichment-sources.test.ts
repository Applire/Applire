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

import { describe, expect, it } from "vitest";
import de from "@/messages/de.json";
import en from "@/messages/en.json";
import { ENRICHMENT_SOURCE_KEYS, enrichmentSourceKey } from "../enrichment-sources";

// The full backend Literal (backend/applire/schemas/profile.py EnrichmentRecord.source)
// plus the E045 agent value. If the backend Literal grows, this list must grow with it.
const KNOWN_SOURCES = [
  "cv_upload",
  "cv_paste",
  "linkedin_import",
  "xing_import",
  "interview",
  "manual_edit",
  "manual_role_add",
  "agent_interview",
  "testimony",
];

type Catalog = Record<string, unknown>;

function sourcesNamespace(catalog: Catalog): Record<string, unknown> {
  const profile = catalog["profile"] as Record<string, unknown>;
  return (profile?.["sources"] ?? {}) as Record<string, unknown>;
}

describe("enrichment source label map (US256)", () => {
  it("covers every known backend source value", () => {
    for (const src of KNOWN_SOURCES) {
      expect(ENRICHMENT_SOURCE_KEYS[src], `missing label key for source "${src}"`).toBeTruthy();
    }
  });

  it("has no stale keys for sources the backend does not emit", () => {
    expect(Object.keys(ENRICHMENT_SOURCE_KEYS).sort()).toEqual([...KNOWN_SOURCES].sort());
  });

  it("every mapped key resolves in BOTH catalogs (profile.sources namespace)", () => {
    const deSources = sourcesNamespace(de as Catalog);
    const enSources = sourcesNamespace(en as Catalog);
    for (const key of Object.values(ENRICHMENT_SOURCE_KEYS)) {
      const short = key.replace(/^sources\./, "");
      expect(typeof deSources[short], `de.json profile.sources.${short}`).toBe("string");
      expect(typeof enSources[short], `en.json profile.sources.${short}`).toBe("string");
    }
  });

  it("enrichmentSourceKey returns the namespaced key for known sources", () => {
    expect(enrichmentSourceKey("agent_interview")).toBe("sources.agent_interview");
    expect(enrichmentSourceKey("cv_upload")).toBe("sources.cv_upload");
  });

  it("enrichmentSourceKey returns null for unknown sources (caller falls back to raw)", () => {
    expect(enrichmentSourceKey("some_future_source")).toBeNull();
  });

  it("agent_interview labels are the agreed wording", () => {
    expect(sourcesNamespace(de as Catalog)["agent_interview"]).toBe("Agent-Interview");
    expect(sourcesNamespace(en as Catalog)["agent_interview"]).toBe("Agent interview");
  });
});
