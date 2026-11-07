// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
/** UX audit round 4 (Emma) — typed conflict values read as words. */
import { describe, expect, it } from "vitest";
import { conflictValueText } from "../conflict-display";

const t = ((key: string) => `<${key}>`) as unknown as Parameters<typeof conflictValueText>[0];

describe("conflictValueText", () => {
  it.each([
    ["is_current", "true (current position, no end date)", "<conflictValue.current>"],
    ["is_current", "True", "<conflictValue.current>"],
    ["is_current", "false (ended 2024-02)", "<conflictValue.ended>"],
    ["is_current", "False", "<conflictValue.ended>"],
    ["end_date", "none (current position)", "<conflictValue.noEndDate>"],
    ["end_date", "null (current position)", "<conflictValue.noEndDate>"],
    ["end_date", "", "<conflictValue.noEndDate>"],
    ["end_date", "2024-02 (ended)", "2024-02"],
    ["end_date", "2020-01", "2020-01"],
    ["start_date", "null", "<conflictValue.noDate>"],
    ["company", "true (sic)", "true (sic)"],
    ["is_current", "maybe", "maybe"],
  ])("%s %j → %j", (field, value, expected) => {
    expect(conflictValueText(t, field, value)).toBe(expected);
  });
});
