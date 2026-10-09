// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, expect, it } from "vitest";

import { auditActionLabel, auditDetailParts, auditValueKey, failedKindKey, failedReason, KNOWN_AUDIT_ACTIONS } from "../admin-format";
import de from "@/messages/de.json";

describe("failed jobs", () => {
  it("maps the stable classifier codes to plain-language reasons", () => {
    expect(failedReason("llm_timeout")).toEqual({ key: "reasonTimeout" });
    expect(failedReason("llm_truncated")).toEqual({ key: "reasonTruncated" });
    expect(failedReason("rate_limited")).toEqual({ key: "reasonRateLimited" });
    expect(failedReason("gap_failed")).toEqual({ key: "reasonOther", params: { code: "gap_failed" } });
    expect(failedReason(null)).toBeNull(); // cover letters carry no code (contract §5.1)
  });

  it("names every job kind the contract defines", () => {
    expect(["import", "gap", "cv", "cover_letter"].map(failedKindKey)).toEqual(["kindImport", "kindGap", "kindCv", "kindLetter"]);
  });
});

describe("audit labels", () => {
  it("turns an action into a nesting-safe catalog key", () => {
    expect(auditActionLabel("user.role_changed", {})).toEqual({ key: "action.user_role_changed" });
    expect(auditActionLabel("tokens.revoked_all", {})).toEqual({ key: "action.tokens_revoked_all" });
  });

  it("a secret change gets the key label, an unknown action shows its code", () => {
    expect(auditActionLabel("settings.changed", { key: "OPENROUTER_API_KEY", write_only: true })).toEqual({ key: "action.settings_secretChanged" });
    expect(auditActionLabel("foo.bar", {})).toEqual({ key: "action.unknown", params: { action: "foo.bar" } });
  });

  it("every action label key exists once the catalog has the adminAudit namespace", () => {
    const ns = (de as Record<string, unknown>).adminAudit as { action?: Record<string, string> } | undefined;
    if (!ns) return; // catalog strings land after the founder gate (C2-1)
    for (const a of KNOWN_AUDIT_ACTIONS) {
      expect(ns.action?.[a.replace(/\./g, "_")], a).toBeTypeOf("string");
    }
  });
});

describe("audit detail", () => {
  it("never renders a secret's value — even if one slipped into detail", () => {
    const parts = auditDetailParts("settings.changed", { key: "REQUESTY_API_KEY", write_only: true, to_value: "sk-LEAK", from_value: "sk-OLD" });
    expect(parts).toEqual([{ key: "detail.secret", params: { setting: "REQUESTY_API_KEY" } }]);
    expect(JSON.stringify(parts)).not.toContain("sk-");
  });

  it("shows a non-secret setting change as from → to", () => {
    expect(auditDetailParts("settings.changed", { key: "LLM_PROVIDER", write_only: false, from_value: "openrouter", to_value: "requesty" })).toEqual([
      { key: "detail.setting", params: { setting: "LLM_PROVIDER", from: "openrouter", to: "requesty" } },
    ]);
    expect(auditDetailParts("settings.changed", { key: "RETENTION_ENABLED", write_only: false, from_value: true, to_value: false })[0].params).toEqual({
      setting: "RETENTION_ENABLED",
      from: "true",
      to: "false",
    });
  });

  it("renders role changes, mail flags, scopes, counts and who deleted — ids never", () => {
    expect(auditDetailParts("user.role_changed", { from_role: "user", to_role: "admin" })).toEqual([{ key: "detail.roleChange", params: { from: "user", to: "admin" } }]);
    expect(auditDetailParts("user.created", { role: "user", mailed: false })).toEqual([{ key: "detail.role", params: { value: "user" } }, { key: "detail.mailedNo" }]);
    expect(auditDetailParts("token.created", { token_id: "t-1", scope: "agent" })).toEqual([{ key: "detail.scope", params: { value: "agent" } }]);
    expect(auditDetailParts("tokens.revoked_all", { count: 3 })).toEqual([{ key: "detail.count", params: { count: 3 } }]);
    expect(auditDetailParts("user.deleted", { by: "self", erased_rows: 120 })).toEqual([{ key: "detail.bySelf" }]);
    expect(JSON.stringify(auditDetailParts("reset_link.issued", { link_id: "L-9", via: "admin", mailed: true }))).not.toContain("L-9");
  });

  it("maps role and boolean raw values to catalog words", () => {
    expect(auditValueKey("admin")).toBe("shell.roleAdmin");
    expect(auditValueKey("user")).toBe("shell.roleUser");
    expect(auditValueKey("false")).toBe("adminAudit.detail.off");
    expect(auditValueKey("requesty")).toBeNull();
  });
});
