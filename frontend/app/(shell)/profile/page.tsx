"use client";

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


import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Bot } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AppTopbar } from "@/components/shell/AppTopbar";
import { Card } from "@/components/ui/card";
import { cn, displayValue } from "@/lib/utils";
import { PhotoManager } from "@/components/profile/PhotoManager";
import { EnrichmentDrawer } from "@/components/profile/EnrichmentDrawer";
import { ProfileReviewDrawer } from "@/components/profile/ProfileReviewDrawer";
import { HealthPanel, type ProfileHealth, type HealthIssue } from "@/components/profile/HealthPanel";
import {
  ProfileSectionBody,
  resolveSummary,
  type SummaryValue,
  type UiLanguage,
} from "@/components/profile/ProfileSectionCard";
import { useLocale } from "@/lib/providers/locale-provider";
import { countWorkEntryGaps, type WorkEntryGapFields } from "@/lib/profile-gaps";
import { enrichmentSourceKey } from "@/lib/enrichment-sources";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

interface ProfileSection {
  name?: string;
  email?: string;
  phone?: string;
  location?: string;
  summary?: string;
  work_experience?: Array<{
    title?: string;
    company?: string;
    start_date?: string;
    end_date?: string | null;
    // #155 — tri-state current-position marker (true = ongoing, false = ended,
    // null/undefined = unknown)
    is_current?: boolean | null;
    description?: string;
  }>;
  education?: Array<{
    degree?: string;
    institution?: string;
    year?: string;
  }>;
  skills?: string[];
  languages?: Array<{ name?: string; level?: string }>;
  certifications?: Array<{ name?: string; issuer?: string; year?: string }>;
  photo_url?: string | null;
}

interface EnrichmentRecord {
  timestamp: string;
  source: string;
  changes: Array<{
    section: string;
    field: string;
    action: string;
    old_value?: unknown;
    new_value?: unknown;
    rationale?: string | null;
  }>;
}

const stringifyValue = displayValue;

interface ProfileResponse {
  id: string;
  profile: {
    personal_info?: ProfileSection;
    // {de, en} localized pair (legacy records may carry a plain string).
    professional_summary?: SummaryValue;
    work_experience?: ProfileSection["work_experience"];
    education?: ProfileSection["education"];
    skills?: ProfileSection["skills"];
    languages?: ProfileSection["languages"];
    certifications?: ProfileSection["certifications"];
    // ADR-055 (E046) — read-only in the UI; written via the reconciler/API.
    signature_stories?: Array<Record<string, unknown>>;
  };
  completeness: number;
  merge_conflicts: Array<{
    conflict_id: string;
    section: string;
    field: string;
    source: string;
  }>;
  created_at: string;
  updated_at: string;
}

type SectionKey =
  | "personal_info"
  | "professional_summary"
  | "work_experience"
  | "education"
  | "skills"
  | "languages"
  | "certifications"
  | "signature_stories";

type SectionLabelKey =
  | "sectionPersonalInfo"
  | "sectionSummary"
  | "sectionWorkExperience"
  | "sectionEducation"
  | "sectionSkills"
  | "sectionLanguages"
  | "sectionCertifications"
  | "sectionSignatureStories";

const SECTION_LABEL_KEYS: Record<SectionKey, SectionLabelKey> = {
  personal_info: "sectionPersonalInfo",
  professional_summary: "sectionSummary",
  work_experience: "sectionWorkExperience",
  education: "sectionEducation",
  skills: "sectionSkills",
  languages: "sectionLanguages",
  certifications: "sectionCertifications",
  signature_stories: "sectionSignatureStories",
};

// ADR-055 — stories are reconciler/API-written; the raw-JSON section editor is
// suppressed for them (read-only v1), and the section hides entirely when empty
// instead of rendering a "not provided" shell.
const READ_ONLY_SECTIONS: ReadonlySet<SectionKey> = new Set(["signature_stories"]);
const HIDE_WHEN_EMPTY_SECTIONS: ReadonlySet<SectionKey> = new Set(["signature_stories"]);

// F9.2 — a summary is "missing" only when NO language has one. A profile with an
// English summary but no German one is NOT incomplete; the missing-language nuance
// is surfaced inline by ProfileSectionBody, not as a whole-section gap.
function hasProfileGaps(
  profile: {
    work_experience?: Array<WorkEntryGapFields> | null;
    professional_summary?: SummaryValue;
  },
  uiLanguage: UiLanguage,
): boolean {
  const work = profile.work_experience ?? [];
  if (work.some((e) => countWorkEntryGaps(e) > 0)) return true;
  if (resolveSummary(profile.professional_summary, uiLanguage).missing) return true;
  return false;
}

export default function ProfilePage() {
  const router = useRouter();
  const t = useTranslations("profile");
  const tCommon = useTranslations("common");
  const { locale } = useLocale();
  const uiLanguage: UiLanguage = locale === "de" ? "de" : "en";
  const [loading, setLoading] = useState(true);
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [health, setHealth] = useState<ProfileHealth | null>(null);
  const [enrichmentHistory, setEnrichmentHistory] = useState<EnrichmentRecord[]>([]);
  const [editingSection, setEditingSection] = useState<SectionKey | null>(null);
  const [editValue, setEditValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [profilePhotoUrl, setProfilePhotoUrl] = useState<string | null>(null);
  const [enrichDrawerOpen, setEnrichDrawerOpen] = useState(false);
  const [enrichScope, setEnrichScope] = useState<string | undefined>(undefined);
  // US165: the standalone profile-review interview, launched from a health issue.
  const [reviewDrawerOpen, setReviewDrawerOpen] = useState(false);
  // F3b (run3): the issue the user clicked "Resolve" on. Passed into the review
  // drawer so a merge-loss/accuracy issue (no conflicts to walk) shows the real
  // problem + an action instead of a dead-end "All done".
  const [resolveIssue, setResolveIssue] = useState<HealthIssue | null>(null);

  const openEnrichForAll = () => {
    setEnrichScope(undefined);
    setEnrichDrawerOpen(true);
  };

  const openEnrichForEntry = (company: string, role: string) => {
    setEnrichScope(`work_experience:${company}:${role}`);
    setEnrichDrawerOpen(true);
  };

  // Map a health issue to the profile section it concerns, so "Resolve" can take
  // the user straight to the editable section that needs attention.
  const sectionForIssue = (issue: HealthIssue): SectionKey => {
    const ref = (issue.field_ref ?? "").toLowerCase();
    if (ref.includes("skill")) return "skills";
    if (ref.includes("summary")) return "professional_summary";
    if (ref.includes("cert")) return "certifications";
    if (ref.includes("educat")) return "education";
    if (ref.includes("language") || ref.includes("lang")) return "languages";
    if (
      ref.includes("work") ||
      ref.includes("experience") ||
      ref.includes("role") ||
      ref.includes("title") ||
      ref.includes("company") ||
      ref.includes("date") ||
      ref.includes("budget") ||
      ref.includes("team")
    )
      return "work_experience";
    return "skills";
  };

  const handleResolve = (issue: HealthIssue) => {
    setResolveIssue(issue);
    setReviewDrawerOpen(true);
  };

  // F3b: the review drawer's action — close it and open the affected section's
  // editor so the user can add back what the merge dropped. Never a dead end.
  const handleResolveAction = (issue: HealthIssue) => {
    setReviewDrawerOpen(false);
    setResolveIssue(null);
    const section = sectionForIssue(issue);
    handleEdit(section);
    if (typeof document !== "undefined") {
      document
        .getElementById(`section-${section}`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  };

  const loadProfile = useCallback(async () => {
    try {
      const [profileRes, enrichmentRes, healthRes] = await Promise.all([
        fetch(`${API_BASE}/api/profile`),
        fetch(`${API_BASE}/api/profile/enrichment-history`),
        fetch(`${API_BASE}/api/profile/health`),
      ]);

      if (profileRes.ok) {
        const data: ProfileResponse = await profileRes.json();
        setProfile(data);
        setProfilePhotoUrl(
          data.profile.personal_info?.photo_url ?? null
        );
      } else {
        setError(t("noProfile"));
      }

      if (enrichmentRes.ok) {
        const data: EnrichmentRecord[] = await enrichmentRes.json();
        setEnrichmentHistory(data.slice(-10).reverse());
      }

      if (healthRes.ok) {
        setHealth(await healthRes.json());
      }
    } catch (err) {
      console.error("Failed to load profile:", err);
      setError(t("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    loadProfile();
  }, [loadProfile]);

  const handleEdit = (section: SectionKey) => {
    if (!profile) return;
    const value = profile.profile[section];
    setEditValue(typeof value === "string" ? value : JSON.stringify(value, null, 2));
    setEditingSection(section);
    setError("");
  };

  const handleSave = async () => {
    if (!profile || !editingSection) return;
    setSaving(true);
    try {
      const originalValue = profile.profile[editingSection];
      const isStringSection = typeof originalValue === "string";
      let parsed: unknown;
      if (isStringSection) {
        parsed = editValue;
      } else {
        try {
          parsed = JSON.parse(editValue);
        } catch {
          setError(t("invalidJson"));
          setSaving(false);
          return;
        }
      }

      const res = await fetch(`${API_BASE}/api/profile/${editingSection}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(parsed),
      });

      if (res.ok) {
        const updated: ProfileResponse = await res.json();
        setProfile(updated);
        setEditingSection(null);
        setEditValue("");
      } else {
        const err = await res.json();
        setError(err.detail || t("saveFailed"));
      }
    } catch (err) {
      console.error("Save failed:", err);
      setError(t("saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    setEditingSection(null);
    setEditValue("");
    setError("");
  };

  const completenessScore = profile?.completeness ?? 0;

  if (loading) {
    return (
      <div className="flex flex-col flex-1 items-center justify-center bg-surface-dim">
        <p className="text-gray-500">{t("loading")}</p>
      </div>
    );
  }

  if (error && !profile) {
    return (
      <div className="flex flex-col flex-1 items-center justify-center bg-surface-dim">
        <p className="text-critical mb-4">{error}</p>
        <Button onClick={() => router.push("/dashboard")}>{t("backToHome")}</Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col flex-1 overflow-hidden bg-surface-dim">
      <AppTopbar mode="section" titleKey="shell.profile" />

      {/* Main Content */}
      <main className="flex-1 overflow-y-auto px-4 py-8">
        <div className="max-w-4xl mx-auto space-y-4">
          {error && (
            <div className="p-4 rounded-lg bg-critical/10 border border-critical/20">
              <p className="text-sm text-critical">{error}</p>
            </div>
          )}

          {/* US164: Master Profile Health panel — health read + nudge + Resolve.
              US165: Resolve launches the standalone profile-review interview.
              US166: Improve launches the standalone Mode C enrichment conversation. */}
          {health && (health.issues.length > 0 || health.completeness.gaps.length > 0) && (
            <HealthPanel
              health={health}
              onResolve={handleResolve}
              onImprove={openEnrichForAll}
            />
          )}

          {profile && (
            <div className="flex justify-end mb-2">
              <span className={cn(
                "text-xs font-medium px-3 py-1 rounded-full",
                completenessScore >= 0.8 ? "bg-success text-white" :
                completenessScore >= 0.5 ? "bg-warning text-white" :
                "bg-gray-400 text-white"
              )}>
                {t("complete", { pct: Math.round(completenessScore * 100) })}
              </span>
            </div>
          )}

          {/* Completeness banner */}
          {profile && (
            <div className={`rounded-lg border p-4 mb-6 ${
              hasProfileGaps(profile.profile, uiLanguage)
                ? "border-amber-500/30 bg-amber-500/5"
                : "border-green-500/30 bg-green-500/5"
            }`}>
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium">
                  {t("completenessLabel", { pct: Math.round((profile.completeness ?? 0) * 100) })}
                </span>
                {hasProfileGaps(profile.profile, uiLanguage) && (
                  <Button size="sm" variant="outline" onClick={openEnrichForAll}>
                    {t("enrichProfile")}
                  </Button>
                )}
              </div>
              <div className="w-full bg-muted rounded-full h-1.5">
                <div
                  className="bg-primary h-1.5 rounded-full transition-all"
                  style={{ width: `${Math.round((profile.completeness ?? 0) * 100)}%` }}
                />
              </div>
            </div>
          )}

          {/* Photo Section */}
          <Card className="p-4">
            <PhotoManager
              currentPhotoUrl={profilePhotoUrl}
              onPhotoChange={(url) => setProfilePhotoUrl(url)}
            />
          </Card>

          {/* Profile Sections */}
          {(Object.keys(SECTION_LABEL_KEYS) as SectionKey[]).map((section) => {
            const isEditing = editingSection === section;
            const value = profile?.profile[section];

            if (
              HIDE_WHEN_EMPTY_SECTIONS.has(section) &&
              (!Array.isArray(value) || value.length === 0)
            ) {
              return null;
            }

            return (
              <Card key={section} id={`section-${section}`} className="p-4 scroll-mt-24">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="font-heading text-base font-semibold text-neutral-dark">
                    {t(SECTION_LABEL_KEYS[section])}
                  </h3>
                  {!isEditing && !READ_ONLY_SECTIONS.has(section) && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleEdit(section)}
                    >
                      {t("edit")}
                    </Button>
                  )}
                </div>

                {isEditing ? (
                  <div className="space-y-3">
                    <textarea
                      className="w-full rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm text-neutral-dark min-h-[120px] focus:border-teal focus:outline-none focus:ring-2 focus:ring-teal/20"
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value)}
                    />
                    {error && (
                      <p className="text-sm text-critical">{error}</p>
                    )}
                    <div className="flex gap-2">
                      <Button onClick={handleSave} disabled={saving}>
                        {saving ? t("saving") : tCommon("save")}
                      </Button>
                      <Button variant="outline" onClick={handleCancel}>
                        {tCommon("cancel")}
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="text-sm text-gray-700">
                    {/* F8 (#76): structured cards, never raw JSON; internal fields hidden. */}
                    <ProfileSectionBody
                      section={section}
                      value={value}
                      uiLanguage={uiLanguage}
                    />
                    {/* Per-entry enrichment affordance for work experience with gaps. */}
                    {section === "work_experience" && Array.isArray(value) && (
                      <div className="mt-2 flex flex-wrap gap-2">
                        {(value as Array<Record<string, unknown>>)
                          .filter(
                            (entry) =>
                              countWorkEntryGaps(entry as WorkEntryGapFields) > 0,
                          )
                          .map((entry, idx) => {
                            const company = (entry["company"] as string) ?? "";
                            const role =
                              (entry["role"] as string) ??
                              (entry["title"] as string) ??
                              "";
                            return (
                              <Button
                                key={idx}
                                size="sm"
                                variant="ghost"
                                className="text-amber-500 hover:text-amber-600 text-xs h-7 px-2"
                                onClick={() => openEnrichForEntry(company, role)}
                              >
                                <span className="flex items-center gap-1">
                                  {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                                  <span aria-hidden="true">⚠</span>
                                  {t("enrichEntry")}
                                  {(role || company) && (
                                    <span className="text-gray-500">
                                      {role || company}
                                    </span>
                                  )}
                                </span>
                              </Button>
                            );
                          })}
                      </div>
                    )}
                  </div>
                )}
              </Card>
            );
          })}

          {/* Enrichment History — the relocated merge/import review (#67/#69).
              The post-merge CTA on the import page deep-links here (#import-log). */}
          {enrichmentHistory.length > 0 && (
            <Card id="import-log" className="p-4 mt-6 scroll-mt-24">
              <h3 className="font-heading text-base font-semibold text-neutral-dark mb-4">
                {t("enrichmentHistory")}
              </h3>
              <div className="space-y-2">
                {enrichmentHistory.map((record, idx) => (
                  <div
                    key={idx}
                    className="text-sm p-3 bg-gray-50 rounded border border-gray-200"
                  >
                    <div className="flex justify-between items-center gap-2 text-xs text-gray-500 mb-1">
                      {/* US256: every source renders via profile.sources.*; the
                          agent_interview provenance gets a distinct chip (E044
                          agent-authored badge parity). Chip is a non-truncating
                          shrink-0 element, never nested in a truncate node. */}
                      {record.source === "agent_interview" ? (
                        <span
                          data-testid="enrichment-source-agent"
                          className="inline-flex items-center gap-1 shrink-0 text-[11px] font-semibold px-2 py-0.5 rounded-md bg-primary/10 text-primary"
                        >
                          <Bot className="w-3 h-3 shrink-0" aria-hidden="true" />
                          {t("sources.agent_interview")}
                        </span>
                      ) : (
                        <span className="font-medium text-teal">
                          {(() => {
                            const key = enrichmentSourceKey(record.source);
                            // Unknown future source values fall back to the raw
                            // string rather than crashing on a missing key.
                            return key ? t(key) : record.source;
                          })()}
                        </span>
                      )}
                      <span>{new Date(record.timestamp).toLocaleDateString()}</span>
                    </div>
                    {record.changes.map((change, cIdx) => {
                      const oldStr = stringifyValue(change.old_value);
                      const newStr = stringifyValue(change.new_value);
                      return (
                        <div key={cIdx} className="text-gray-600">
                          <span>
                            {t("changeLog", { action: change.action, section: change.section, field: change.field })}
                          </span>
                          {oldStr !== "" && (
                            <span>
                              {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- punctuation separator */}
                              {": "}
                              <span className="text-gray-400 line-through">{oldStr}</span>
                              {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative before→after arrow */}
                              {" → "}
                              <span className="text-gray-700">{newStr}</span>
                            </span>
                          )}
                          {change.rationale && (
                            <span className="block text-xs text-gray-400">{change.rationale}</span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ))}
              </div>
            </Card>
          )}
        </div>
      </main>

      <EnrichmentDrawer
        open={enrichDrawerOpen}
        scope={enrichScope}
        onClose={() => {
          setEnrichDrawerOpen(false);
          loadProfile();
        }}
      />

      <ProfileReviewDrawer
        open={reviewDrawerOpen}
        issue={resolveIssue}
        onAction={handleResolveAction}
        onClose={() => {
          setReviewDrawerOpen(false);
          setResolveIssue(null);
          loadProfile();
        }}
      />
    </div>
  );
}
