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

function flattenKeys(obj: unknown, prefix = ""): string[] {
  if (obj === null || typeof obj !== "object") return [prefix];
  const out: string[] = [];
  for (const [k, v] of Object.entries(obj)) {
    const next = prefix ? `${prefix}.${k}` : k;
    if (v !== null && typeof v === "object" && !Array.isArray(v)) {
      out.push(...flattenKeys(v, next));
    } else {
      out.push(next);
    }
  }
  return out.sort();
}

describe("i18n key parity", () => {
  it("de.json and en.json contain exactly the same key set", () => {
    const deKeys = flattenKeys(de);
    const enKeys = flattenKeys(en);
    const onlyInDe = deKeys.filter((k) => !enKeys.includes(k));
    const onlyInEn = enKeys.filter((k) => !deKeys.includes(k));
    expect({ onlyInDe, onlyInEn }).toEqual({ onlyInDe: [], onlyInEn: [] });
  });
});
