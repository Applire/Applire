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
import { getApiErrorMessage, isLeakyDetail, translateApiError } from "../errors";

function jsonResponse(status: number, body: unknown): Response {
  return {
    status,
    statusText: "Error",
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

describe("isLeakyDetail (F3 — no raw framework noise to users)", () => {
  it("flags pydantic UUID / validation spew", () => {
    expect(
      isLeakyDetail("Input should be a valid UUID, invalid character: found `n` at 1"),
    ).toBe(true);
    expect(
      isLeakyDetail("2 validation errors for MasterProfileData certifications.0.date_obtained"),
    ).toBe(true);
    expect(isLeakyDetail("input_value='2023', input_type=str")).toBe(true);
    expect(
      isLeakyDetail("For further information visit https://errors.pydantic.dev/2.11/"),
    ).toBe(true);
  });

  it("passes through genuinely human messages", () => {
    expect(isLeakyDetail("We couldn't read this file. Please try a PDF.")).toBe(false);
    expect(isLeakyDetail("Datei konnte nicht verarbeitet werden")).toBe(false);
  });
});

describe("getApiErrorMessage", () => {
  it("never surfaces a leaked UUID; falls back to a friendly 422 message", async () => {
    const res = jsonResponse(422, {
      detail: [{ msg: "Input should be a valid UUID, invalid character" }],
    });
    const msg = await getApiErrorMessage(res);
    expect(msg).not.toMatch(/UUID/i);
    expect(msg).not.toMatch(/pydantic/i);
    expect(msg).toBe("Invalid input. Please check your entries.");
  });

  it("still surfaces a human-written 422 detail", async () => {
    const res = jsonResponse(422, { detail: "Datei konnte nicht verarbeitet werden" });
    expect(await getApiErrorMessage(res)).toBe("Datei konnte nicht verarbeitet werden");
  });
});

describe("translateApiError", () => {
  it("treats an empty/suppressed detail as absent", () => {
    expect(translateApiError(422, "")).toBe("Invalid input. Please check your entries.");
  });
});
