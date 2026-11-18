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

import { describe, expect, it } from "vitest";

import { GET } from "../share-target/route";

// The request URL is Next's own origin (a container name, an internal port);
// the redirect must resolve against the address the browser used instead.
const INTERNAL = "http://3f4dd2d77705:3000";

describe("share target redirect", () => {
  it("answers 303 with a path, never an origin", () => {
    const res = GET(new Request(`${INTERNAL}/share-target?text=Senior%20QA%20https%3A%2F%2Fjobs.example.org%2F42`));
    expect(res.status).toBe(303);
    const location = res.headers.get("location") ?? "";
    expect(location).toBe("/dashboard?jd_url=https%3A%2F%2Fjobs.example.org%2F42");
    expect(location).not.toContain("3f4dd2d77705");
    expect(location.startsWith("/") && !location.startsWith("//")).toBe(true);
  });

  it("an empty share lands on the dashboard, still as a path", () => {
    const res = GET(new Request(`${INTERNAL}/share-target`));
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("/dashboard");
  });
});
