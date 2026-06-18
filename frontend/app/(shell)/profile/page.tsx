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
import { Button } from "@/components/ui/button";
import { AppTopbar } from "@/components/shell/AppTopbar";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { PhotoManager } from "@/components/profile/PhotoManager";
import { EnrichmentDrawer } from "@/components/profile/EnrichmentDrawer";
import { HealthPanel, type ProfileHealth } from "@/components/profile/HealthPanel";

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
    end_date?: string;
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
  changes: Array<{ section: string; field: string; action: string }>;
}

interface ProfileResponse {
  id: string;
  profile: {
    personal_info?: ProfileSection;
    professional_summary?: string;
    work_experience?: ProfileSection["work_experience"];
    education?: ProfileSection["education"];
    skills?: ProfileSection["skills"];
    languages?: ProfileSection["languages"];
    certifications?: ProfileSection["certifications"];
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
  | "certifications";

type SectionLabelKey =
  | "sectionPersonalInfo"
  | "sectionSummary"
  | "sectionWorkExperience"
  | "sectionEducation"
  | "sectionSkills"
  | "sectionLanguages"
  | "sectionCertifications";

const SECTION_LABEL_KEYS: Record<SectionKey, SectionLabelKey> = {
  personal_info: "sectionPersonalInfo",
  professional_summary: "sectionSummary",
  work_experience: "sectionWorkExperience",
  education: "sectionEducation",
  skills: "sectionSkills",
  languages: "sectionLanguages",
  certifications: "sectionCertifications",
};

function countWorkEntryGaps(entry: {
  description?: string | null;
  title?: string | null;
  company?: string | null;
}): number {
  let count = 0;
  if (!entry.description) count++;
  return count;
}

function hasProfileGaps(profile: {
  work_experience?: Array<{
    description?: string | null;
    title?: string | null;
    company?: string | null;
  }> | null;
  professional_summary?: string | null;
}): boolean {
  const work = profile.work_experience ?? [];
  if (work.some((e) => countWorkEntryGaps(e) > 0)) return true;
  if (!profile.professional_summary) return true;
  return false;
}

export default function ProfilePage() {
  const router = useRouter();
  const t = useTranslations("profile");
  const tCommon = useTranslations("common");
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

  const openEnrichForAll = () => {
    setEnrichScope(undefined);
    setEnrichDrawerOpen(true);
  };

  const openEnrichForEntry = (company: string, role: string) => {
    setEnrichScope(`work_experience:${company}:${role}`);
    setEnrichDrawerOpen(true);
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

          {/* US164: Master Profile Health panel — health read + nudge + Resolve */}
          {health && (health.issues.length > 0 || health.completeness.gaps.length > 0) && (
            <HealthPanel health={health} onResolve={() => openEnrichForAll()} />
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
              hasProfileGaps(profile.profile)
                ? "border-amber-500/30 bg-amber-500/5"
                : "border-green-500/30 bg-green-500/5"
            }`}>
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium">
                  {t("completenessLabel", { pct: Math.round((profile.completeness ?? 0) * 100) })}
                </span>
                {hasProfileGaps(profile.profile) && (
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
            const hasValue = value !== undefined && value !== null && value !== "";

            return (
              <Card key={section} className="p-4">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="font-heading text-base font-semibold text-neutral-dark">
                    {t(SECTION_LABEL_KEYS[section])}
                  </h3>
                  {!isEditing && (
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
                    {hasValue ? (
                      section === "work_experience" && Array.isArray(value) ? (
                        <div className="space-y-3">
                          {(value as Array<Record<string, unknown>>).map((entry, idx) => {
                            const company = (entry["company"] as string) ?? "";
                            const role = ((entry["role"] as string) ?? (entry["title"] as string) ?? "");
                            const entryHasGaps = countWorkEntryGaps(entry as { description?: string | null }) > 0;
                            return (
                              <div key={idx} className="bg-gray-50 rounded border border-gray-200">
                                <pre className="whitespace-pre-wrap text-xs p-3">
                                  {JSON.stringify(entry, null, 2)}
                                </pre>
                                {entryHasGaps && (
                                  <div className="px-3 pb-2">
                                    <Button
                                      size="sm"
                                      variant="ghost"
                                      className="text-amber-500 hover:text-amber-600 text-xs h-7 px-2"
                                      onClick={() => openEnrichForEntry(company, role)}
                                    >
                                      <span className="flex items-center gap-1">
                                        {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                                        <span aria-hidden="true">⚠</span>
                                        {t("enrichEntry")}
                                      </span>
                                    </Button>
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      ) : typeof value === "string" ? (
                        <p>{value}</p>
                      ) : (
                        <pre className="whitespace-pre-wrap text-xs bg-gray-50 p-3 rounded">
                          {JSON.stringify(value, null, 2)}
                        </pre>
                      )
                    ) : (
                      <p className="text-gray-400 italic">{t("notProvided")}</p>
                    )}
                  </div>
                )}
              </Card>
            );
          })}

          {/* Enrichment History */}
          {enrichmentHistory.length > 0 && (
            <Card className="p-4 mt-6">
              <h3 className="font-heading text-base font-semibold text-neutral-dark mb-4">
                {t("enrichmentHistory")}
              </h3>
              <div className="space-y-2">
                {enrichmentHistory.map((record, idx) => (
                  <div
                    key={idx}
                    className="text-sm p-3 bg-gray-50 rounded border border-gray-200"
                  >
                    <div className="flex justify-between text-xs text-gray-500 mb-1">
                      <span className="font-medium text-teal">{record.source}</span>
                      <span>{new Date(record.timestamp).toLocaleDateString()}</span>
                    </div>
                    {record.changes.map((change, cIdx) => (
                      <p key={cIdx} className="text-gray-600">
                        {t("changeLog", { action: change.action, section: change.section, field: change.field })}
                      </p>
                    ))}
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
    </div>
  );
}
