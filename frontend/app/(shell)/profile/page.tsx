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


import { useCallback, useEffect, useState, type ComponentProps } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Bot } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AppTopbar } from "@/components/shell/AppTopbar";
import { Card } from "@/components/ui/card";
import { cn, displayValue } from "@/lib/utils";
import { PhotoManager } from "@/components/profile/PhotoManager";
import { AvailabilityCard, type AvailabilityValue } from "@/components/profile/AvailabilityCard";
import { SignatureManager } from "@/components/profile/SignatureManager";
import { TestimonyIntake } from "@/components/profile/TestimonyIntake";
import { ProfileImportView } from "@/components/profile/ProfileImportView";
import { ImportSummaryCard, matchPairKey } from "@/components/profile/ImportSummaryCard";
import {
  DeniedConceptsCard,
  type DeniedConceptItem,
  type WithdrawnDenialItem,
} from "@/components/profile/DeniedConceptsCard";
import { EnrichmentDrawer } from "@/components/profile/EnrichmentDrawer";
import { ProfileReviewDrawer } from "@/components/profile/ProfileReviewDrawer";
import {
  HealthPanel,
  type HubActions,
  type ProfileHealth,
  type HealthIssue,
} from "@/components/profile/HealthPanel";
import { EntryMergeProvider, useEntryMerge } from "@/components/profile/EntryMerge";
import {
  MERGE_SECTIONS,
  type EntryRef,
  type MergeSection,
  isMergeSection,
  keepEntriesSeparate,
  toEntryRef,
} from "@/lib/profile-merge";
import {
  adoptNotApplied,
  answerAspiration,
  hubHasContent,
  renderedIssues,
  saveBudgetUnit,
} from "@/lib/profile-health";
import {
  ProfileSectionBody,
  resolveSummary,
  type SummaryValue,
  type UiLanguage,
} from "@/components/profile/ProfileSectionCard";
import { useLocale } from "@/lib/providers/locale-provider";
import { formatUiDate } from "@/lib/format-date";
import {
  budgetUnitIssueLabels,
  countWorkEntryGaps,
  workEntryLabel,
  type WorkEntryGapFields,
} from "@/lib/profile-gaps";
import { enrichmentSourceKey } from "@/lib/enrichment-sources";
import { sectionForFieldRef } from "@/lib/profile-sections";
import { WorkExperienceEditor } from "@/components/profile/WorkExperienceEditor";
import { EducationEditor } from "@/components/profile/EducationEditor";
import { SkillsEditor } from "@/components/profile/SkillsEditor";
import { LanguagesEditor } from "@/components/profile/LanguagesEditor";
import { CertificationsEditor } from "@/components/profile/CertificationsEditor";
import { ProjectsEditor } from "@/components/profile/ProjectsEditor";
import { PublicationsEditor } from "@/components/profile/PublicationsEditor";
import { VolunteerEditor } from "@/components/profile/VolunteerEditor";
import { SummaryEditor } from "@/components/profile/SummaryEditor";
import { PersonalInfoEditor } from "@/components/profile/PersonalInfoEditor";
import type {
  Certification,
  EducationEntry,
  Language,
  PersonalInfo,
  ProjectEntry,
  Publication,
  Skill,
  VolunteerActivity,
  WorkEntry,
} from "@/lib/profile-entries";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

interface ProfileSection {
  name?: string;
  email?: string;
  phone?: string;
  location?: string;
  summary?: string;
  // US290/US291 — the full Work/Education/Skill/Language/Certification schema
  // (id, status, provenance, etc.); the structured editors below round-trip
  // every key.
  work_experience?: WorkEntry[];
  education?: EducationEntry[];
  skills?: Skill[];
  // #113(c) — `language` is the vault's field; `name` is a tolerated legacy alias.
  languages?: Language[];
  certifications?: Certification[];
  // US292 — the last three list sections get their structured editors.
  projects?: ProjectEntry[];
  publications?: Publication[];
  volunteer_activities?: VolunteerActivity[];
  photo_url?: string | null;
  // US292 — contact fields the PersonalInfoEditor edits (merge-patched).
  address?: string | null;
  nationality?: string | null;
  date_of_birth?: string | null;
  linkedin_url?: string | null;
  xing_url?: string | null;
  website_url?: string | null;
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
  // #707 — `match_existing` receipts: entries the import recognised as already
  // present under another name. A receipt for something that did NOT change.
  matched?: Array<{
    section: string;
    entity_id: string;
    incoming: string;
    existing: string;
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
    projects?: ProfileSection["projects"];
    publications?: ProfileSection["publications"];
    volunteer_activities?: ProfileSection["volunteer_activities"];
    // ADR-055 (E046) — read-only in the UI; written via the reconciler/API.
    signature_stories?: Array<Record<string, unknown>>;
    // D-B3V7 (rulings 787:L3 + V-4) — notice period / earliest start.
    availability?: AvailabilityValue | null;
    // G-5 (ADR-059 amended 2026-10-24) — the denials My Profile lists.
    metadata?: {
      denied_concepts?: DeniedConceptItem[];
      withdrawn_denials?: WithdrawnDenialItem[];
    };
  };
  completeness: number;
  // D-B3V7 (ruling V-4) — the dashboard reminder's condition.
  availability_reminder?: "missing" | "stale" | null;
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
  | "projects"
  | "publications"
  | "volunteer_activities"
  | "signature_stories";

type SectionLabelKey =
  | "sectionPersonalInfo"
  | "sectionSummary"
  | "sectionWorkExperience"
  | "sectionEducation"
  | "sectionSkills"
  | "sectionLanguages"
  | "sectionCertifications"
  | "sectionProjects"
  | "sectionPublications"
  | "sectionVolunteer"
  | "sectionSignatureStories";

const SECTION_LABEL_KEYS: Record<SectionKey, SectionLabelKey> = {
  personal_info: "sectionPersonalInfo",
  professional_summary: "sectionSummary",
  work_experience: "sectionWorkExperience",
  education: "sectionEducation",
  skills: "sectionSkills",
  languages: "sectionLanguages",
  certifications: "sectionCertifications",
  projects: "sectionProjects",
  publications: "sectionPublications",
  volunteer_activities: "sectionVolunteer",
  signature_stories: "sectionSignatureStories",
};

// ADR-055 — stories are reconciler/API-written and read-only in v1; the section
// hides entirely when empty instead of rendering a "not provided" shell.
// Every other section has a structured editor (E055, US290–US292) — the
// whole-section JSON textarea that used to live here is retired (US292).
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

/**
 * U-9 → U-11 (Hazelnut build 5): the hub's duplicate-station question "Ja,
 * zusammenführen …" opens the SAME merge preview a drag or the entry's merge
 * button opens — one merge act, two entry points. Lives inside the
 * `EntryMergeProvider` so it can reach `begin`.
 */
function HealthPanelWithMerge(
  props: ComponentProps<typeof HealthPanel> & { actions: HubActions },
) {
  const merge = useEntryMerge();
  const onMergeDuplicate = (issue: HealthIssue) => {
    const section = issue.section ?? "";
    const [targetId, sourceId] = issue.entry_ids ?? [];
    if (!merge || !isMergeSection(section) || !targetId || !sourceId) return;
    const refs = merge.refsFor(section);
    const target = refs.find((r) => r.id === targetId);
    const source = refs.find((r) => r.id === sourceId);
    if (target && source) merge.begin(source, target);
  };
  return (
    <HealthPanel
      {...props}
      actions={{ ...props.actions, onMergeDuplicate: merge ? onMergeDuplicate : undefined }}
    />
  );
}

export default function ProfilePage() {
  const router = useRouter();
  const t = useTranslations("profile");
  const tHealth = useTranslations("health");
  const tSpan = useTranslations("profileHealth.launderedSpan");
  const { locale } = useLocale();
  const uiLanguage: UiLanguage = locale === "de" ? "de" : "en";
  const [loading, setLoading] = useState(true);
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [health, setHealth] = useState<ProfileHealth | null>(null);
  // #764 — outcome note of a "recalculate from role dates" click. Lives outside
  // the HealthPanel's render condition: the panel unmounts once its last issue
  // is gone, and the note must outlive it.
  const [recomputeNote, setRecomputeNote] = useState<{ text: string; ok: boolean } | null>(null);
  const [enrichmentHistory, setEnrichmentHistory] = useState<EnrichmentRecord[]>([]);
  const [error, setError] = useState("");
  // #704 (founder UAT, 2026-09-15) — true only for a genuine "no Master
  // Profile yet" (the /api/profile fetch answered, just not ok); false for a
  // transient `loadFailed` (network/exception), which keeps the old plain
  // error + back-to-home rendering rather than misdirecting into an upload
  // prompt for a problem uploading would not fix.
  const [noProfileFound, setNoProfileFound] = useState(false);
  const [profilePhotoUrl, setProfilePhotoUrl] = useState<string | null>(null);
  const [enrichDrawerOpen, setEnrichDrawerOpen] = useState(false);
  const [enrichScope, setEnrichScope] = useState<string | undefined>(undefined);
  // US165: the standalone profile-review interview, launched from a health issue.
  const [reviewDrawerOpen, setReviewDrawerOpen] = useState(false);
  // F3b (run3): the issue the user clicked "Resolve" on. Passed into the review
  // drawer so a merge-loss/accuracy issue (no conflicts to walk) shows the real
  // problem + an action instead of a dead-end "All done".
  const [resolveIssue, setResolveIssue] = useState<HealthIssue | null>(null);
  // #705 (founder UAT, 2026-09-15) — the section a per-item "go to section"
  // action landed on, plus the labels the import did not carry over there.
  // Rendered as a dismissible callout at the top of that one section — never
  // a query param or navigation, since the review drawer and the sections
  // below are the SAME page instance.
  const [sectionCallout, setSectionCallout] = useState<{
    section: SectionKey;
    labels: string[];
  } | null>(null);

  const openEnrichForAll = () => {
    setEnrichScope(undefined);
    setEnrichDrawerOpen(true);
  };

  const openEnrichForEntry = (company: string, role: string) => {
    setEnrichScope(`work_experience:${company}:${role}`);
    setEnrichDrawerOpen(true);
  };

  // Map a health issue to the profile section it concerns, so "Resolve" can take
  // the user straight to the section (and its structured editor) that needs
  // attention — lib/profile-sections.ts, unit-tested.
  // #626: a `conflict` issue now carries its real section key directly
  // (`issue.section`, e.g. "projects") — authoritative, so it wins over the
  // `field_ref` substring guess. Every other thread still has no `section`
  // and falls back to the guess exactly as before.
  const sectionForIssue = (issue: HealthIssue): SectionKey =>
    issue.section && issue.section in SECTION_LABEL_KEYS
      ? (issue.section as SectionKey)
      : sectionForFieldRef(issue.field_ref);

  const handleResolve = (issue: HealthIssue) => {
    setResolveIssue(issue);
    setReviewDrawerOpen(true);
  };

  // F3b: the review drawer's action — close it and bring the affected section
  // (with its structured editor) into view so the user can add back what the
  // merge dropped. Never a dead end.
  const handleResolveAction = (issue: HealthIssue) => {
    setReviewDrawerOpen(false);
    setResolveIssue(null);
    const section = sectionForIssue(issue);
    if (typeof document !== "undefined") {
      document
        .getElementById(`section-${section}`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  };

  // #705 — the review drawer's PER-SECTION action for a `not_applied` issue's
  // full item list: bring THAT section into view (never "one of the affected
  // sections" at random) and show which labels the import did not carry over
  // there, in a callout the user can dismiss.
  const handleSectionAction = (section: string, labels: string[]) => {
    setReviewDrawerOpen(false);
    setResolveIssue(null);
    const key = (section in SECTION_LABEL_KEYS ? section : sectionForFieldRef(section)) as SectionKey;
    setSectionCallout({ section: key, labels });
    if (typeof document !== "undefined") {
      document
        .getElementById(`section-${key}`)
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
        // A re-fetch after an inline import (#704) succeeding: clear any
        // earlier "no profile" state so the empty state's own branch below
        // stops matching and the real profile renders instead.
        setError("");
        setNoProfileFound(false);
      } else {
        setError(t("noProfile"));
        // #704 — distinguishes "no profile yet" (this branch) from a
        // transient `loadFailed` (the catch block below): only the former
        // gets the inline upload empty state: a network hiccup showing an
        // upload prompt would misdirect the user.
        setNoProfileFound(true);
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
      setNoProfileFound(false);
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    loadProfile();
  }, [loadProfile]);

  // #764 — recompute a laundered skill span from the role dates. 409 = the item
  // was already recomputed/edited elsewhere: say so and refresh.
  const handleRecomputeSpan = async (issue: HealthIssue) => {
    setRecomputeNote(null);
    try {
      const res = await fetch(`${API_BASE}/api/profile/health/recompute-span`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skill_id: issue.skill_id }),
      });
      if (res.ok) {
        await loadProfile();
        setRecomputeNote({ text: tSpan("done", { skill: issue.skill_name ?? "" }), ok: true });
      } else if (res.status === 409) {
        await loadProfile();
        setRecomputeNote({ text: tSpan("stale"), ok: true });
      } else {
        setRecomputeNote({ text: tSpan("failed"), ok: false });
      }
    } catch {
      setRecomputeNote({ text: tSpan("failed"), ok: false });
    }
  };

  // FS-5 — a suspect binding is answered by the pair's own acts: "Ja" stamps
  // the receipt confirmed (never asked again), "Nein" is the #717 undo, which
  // also takes the merged evidence back. Either way the panel refreshes.
  const handleAnswerBinding = async (issue: HealthIssue, same: boolean): Promise<boolean> => {
    try {
      const init: RequestInit = {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entity_id: issue.skill_id, incoming: issue.incoming_name }),
      };
      const res = same
        ? await fetch(`${API_BASE}/api/profile/matches/confirm`, { ...init, method: "POST" })
        : await fetch(`${API_BASE}/api/profile/matches/separate`, { ...init, method: "POST" });
      if (!res.ok) return false;
      await loadProfile();
      return true;
    } catch {
      return false;
    }
  };

  // V-5 (c) — a `denial_scope` ask answered in the Health hub: the answer is
  // the act ("Angabe zurücknehmen" withdraws the denial; no second dialog).
  // 409 = already answered elsewhere: refresh and let the item disappear.
  const handleAnswerConfirmation = async (issue: HealthIssue, option: string): Promise<boolean> => {
    try {
      const res = await fetch(`${API_BASE}/api/profile/confirmations/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation_id: issue.confirmation_id, chosen_option: option }),
      });
      if (!res.ok && res.status !== 409) return false;
      await loadProfile();
      return true;
    } catch {
      return false;
    }
  };

  // UAT-3d / U-9 — the hub's one-action resolvers. Each refreshes the page on
  // success, so the issue disappears on the recompute it was built to clear.
  const hubActions: HubActions = {
    onAdoptItem: async (issue, itemKey) => {
      const updated = await adoptNotApplied(issue.id, itemKey);
      if (!updated) return false;
      await loadProfile();
      return true;
    },
    onSaveUnit: async (issue, unit) => {
      if (!issue.entry_id) return false;
      const updated = await saveBudgetUnit(issue.entry_id, unit);
      if (!updated) return false;
      await loadProfile();
      return true;
    },
    onKeepSeparate: async (issue) => {
      if (!issue.section || !issue.entry_ids || issue.entry_ids.length < 2) return false;
      const ok = await keepEntriesSeparate(issue.section, issue.entry_ids);
      if (ok) await loadProfile();
      return ok;
    },
    onGoToSection: (section, labels) => handleSectionAction(section, labels),
    // U-7 — "Ist das erreicht?" (INTERFACE A1-3).
    onAspiration: async (issue, achieved, outcome) => {
      const ok = await answerAspiration(issue.id, achieved, outcome);
      if (ok) await loadProfile();
      return ok;
    },
  };

  // U-11 — every mergeable entry's identity, built by the same function the
  // editors use, so the picker and the hub's pair name entries identically.
  const mergeRefs: Partial<Record<MergeSection, EntryRef[]>> = {};
  for (const section of MERGE_SECTIONS) {
    const list = (profile?.profile[section] ?? []) as Array<Parameters<typeof toEntryRef>[1]>;
    mergeRefs[section] = list
      .map((e) =>
        toEntryRef(section, e, { present: t("present"), notProvided: t("notProvided") }, uiLanguage),
      )
      .filter((r): r is EntryRef => r !== null);
  }

  // ADR-099 cl. 10 — a recognised pair the hub asks about is not asked again
  // by the import summary below it.
  const hubPairKeys = new Set(
    renderedIssues(health)
      .filter((i) => i.thread === "suspect_binding" && i.skill_id && i.incoming_name)
      .map((i) => matchPairKey(i.skill_id!, i.incoming_name!)),
  );

  const completenessScore = profile?.completeness ?? 0;

  // #382 — the work entries whose budget figure states no unit, as decided by
  // the backend (`unit` health thread). Empty until /api/profile/health has
  // answered, so nothing is asserted about a field before the rule has run.
  const budgetUnitLabels = budgetUnitIssueLabels(health?.issues);

  if (loading) {
    return (
      <div className="flex flex-col flex-1 items-center justify-center bg-surface-dim">
        <p className="text-gray-500">{t("loading")}</p>
      </div>
    );
  }

  // #704 (founder UAT, 2026-09-15) — a first-time user with no Master Profile
  // saw a dead end ("No profile found. Please import a CV first." + "Back to
  // Home") with no way to actually import a CV from this page. Reuses the
  // same upload component the welcome screen offers instead of redirecting
  // away (`ProfileImportView`, already usable standalone with no flowId — the
  // same mount `/profile/upload?action=upload` uses); a successful import
  // re-fetches the profile in place via `onImported`, no full page reload.
  if (noProfileFound && !profile) {
    return (
      <div className="flex flex-col flex-1 overflow-hidden bg-surface-dim">
        {/* The same page bar the loaded profile renders, so the empty state
            is still "My Profile" (title, bell, avatar) and the embedded
            import view's own bar is suppressed via hideTopbar. */}
        <AppTopbar mode="section" titleKey="shell.profile" />
        <main className="flex-1 overflow-y-auto px-4 py-8">
          <div className="max-w-3xl mx-auto text-center mb-2">
            <h2 className="font-heading text-lg font-semibold text-neutral-dark">
              {t("noProfileHeading")}
            </h2>
          </div>
          <ProfileImportView hideTopbar onImported={loadProfile} />
          <div className="max-w-3xl mx-auto text-center mt-4">
            <Button variant="ghost" onClick={() => router.push("/dashboard")}>
              {t("backToHome")}
            </Button>
          </div>
        </main>
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
      <EntryMergeProvider
        entriesBySection={mergeRefs}
        onProfileChanged={loadProfile}
        profileUpdatedAt={profile?.updated_at ?? null}
      >
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
          {recomputeNote && (
            <p
              role="status"
              data-testid="health-recompute-note"
              className={cn(
                "rounded-lg border px-3 py-2 text-sm text-neutral-dark",
                recomputeNote.ok
                  ? "bg-success/10 border-success/20"
                  : "bg-critical/10 border-critical/20",
              )}
            >
              {recomputeNote.text}
            </p>
          )}
          {health && hubHasContent(health) && (
            <HealthPanelWithMerge
              health={health}
              actions={hubActions}
              onResolve={handleResolve}
              onRecomputeSpan={handleRecomputeSpan}
              onAnswerBinding={handleAnswerBinding}
              onAnswerConfirmation={handleAnswerConfirmation}
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

          {/* #359 — the signature image and the two per-document toggles.
              Beside the photo because both are "a picture of you that goes on
              the document", and because the toggles are user-level settings
              read at render time: a switch on one document's screen would read
              as "for this document". Deliberately NOT inside PhotoManager —
              the photo's GDPR Art. 9 consent gate has no twin here (ADR-088). */}
          <Card className="p-4">
            <SignatureManager />
          </Card>

          {/* D-B3V7 (rulings 787:L3 + V-4) — the notice period belongs to the
              current contract: stored once, "Stand: <date>", pre-fills every
              letter. The dashboard reminder links here. */}
          <Card id="availability" className="p-4 scroll-mt-24">
            <AvailabilityCard
              value={profile?.profile.availability}
              apiBase={API_BASE}
              locale={uiLanguage}
              onProfileUpdated={(updated) => setProfile(updated as unknown as ProfileResponse)}
            />
          </Card>

          {/* #258 — free-text testimony intake ("anything else recruiters
              should know"), reconciled into the vault with receipts exactly
              like an interview answer; reload on success so the sections and
              enrichment history below reflect the new receipts immediately. */}
          <TestimonyIntake onSubmitted={loadProfile} />

          {/* Profile Sections */}
          {(Object.keys(SECTION_LABEL_KEYS) as SectionKey[]).map((section) => {
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
                </div>

                {/* #705 (founder UAT, 2026-09-15) — the labels an import did
                    NOT carry over into THIS section, named at the point where
                    the user can add them back. Dismissible; never reappears
                    on its own (no re-fetch resurrects it). */}
                {sectionCallout && sectionCallout.section === section && (
                  <div
                    data-testid="not-applied-callout"
                    className="mb-3 flex items-start justify-between gap-2 rounded-lg border border-warning/40 bg-warning-container px-3 py-2"
                  >
                    <p className="text-xs text-on-surface-variant">
                      {tHealth("notAppliedCallout", {
                        labels: sectionCallout.labels.join(", "),
                      })}
                    </p>
                    <Button
                      size="sm"
                      variant="ghost"
                      data-testid="not-applied-callout-dismiss"
                      onClick={() => setSectionCallout(null)}
                    >
                      {tHealth("dismissNudge")}
                    </Button>
                  </div>
                )}

                {READ_ONLY_SECTIONS.has(section) ? (
                  <div className="text-sm text-gray-700">
                    {/* F8 (#76): structured cards, never raw JSON; internal fields hidden. */}
                    <ProfileSectionBody
                      section={section}
                      value={value}
                      uiLanguage={uiLanguage}
                    />
                  </div>
                ) : (
                  <div className="text-sm text-gray-700">
                    {/* E055 (US290–US292) — every editable section has a structured
                        editor; each save runs through PATCH /api/profile/{section}
                        (commit_ops) with the profile's updated_at as its basis. */}
                    {section === "professional_summary" ? (
                      <>
                        <ProfileSectionBody
                          section={section}
                          value={value}
                          uiLanguage={uiLanguage}
                        />
                        <SummaryEditor
                          value={value as SummaryValue}
                          uiLanguage={uiLanguage}
                          apiBase={API_BASE}
                          profileUpdatedAt={profile?.updated_at ?? ""}
                          onProfileUpdated={(updated) =>
                            setProfile(updated as unknown as ProfileResponse)
                          }
                        />
                      </>
                    ) : section === "personal_info" ? (
                      <>
                        <ProfileSectionBody
                          section={section}
                          value={value}
                          uiLanguage={uiLanguage}
                        />
                        <PersonalInfoEditor
                          value={(value as PersonalInfo | undefined) ?? {}}
                          apiBase={API_BASE}
                          profileUpdatedAt={profile?.updated_at ?? ""}
                          onProfileUpdated={(updated) =>
                            setProfile(updated as unknown as ProfileResponse)
                          }
                        />
                      </>
                    ) : section === "work_experience" ? (
                      <WorkExperienceEditor
                        entries={(value as WorkEntry[]) ?? []}
                        apiBase={API_BASE}
                        profileUpdatedAt={profile?.updated_at ?? ""}
                        onProfileUpdated={(updated) =>
                          setProfile(updated as unknown as ProfileResponse)
                        }
                      />
                    ) : section === "education" ? (
                      <EducationEditor
                        entries={(value as EducationEntry[]) ?? []}
                        apiBase={API_BASE}
                        profileUpdatedAt={profile?.updated_at ?? ""}
                        onProfileUpdated={(updated) =>
                          setProfile(updated as unknown as ProfileResponse)
                        }
                      />
                    ) : section === "skills" ? (
                      <SkillsEditor
                        entries={(value as Skill[]) ?? []}
                        apiBase={API_BASE}
                        profileUpdatedAt={profile?.updated_at ?? ""}
                        onProfileUpdated={(updated) =>
                          setProfile(updated as unknown as ProfileResponse)
                        }
                      />
                    ) : section === "languages" ? (
                      <LanguagesEditor
                        entries={(value as Language[]) ?? []}
                        apiBase={API_BASE}
                        profileUpdatedAt={profile?.updated_at ?? ""}
                        onProfileUpdated={(updated) =>
                          setProfile(updated as unknown as ProfileResponse)
                        }
                      />
                    ) : section === "certifications" ? (
                      <CertificationsEditor
                        entries={(value as Certification[]) ?? []}
                        apiBase={API_BASE}
                        profileUpdatedAt={profile?.updated_at ?? ""}
                        onProfileUpdated={(updated) =>
                          setProfile(updated as unknown as ProfileResponse)
                        }
                      />
                    ) : section === "projects" ? (
                      <ProjectsEditor
                        entries={(value as ProjectEntry[]) ?? []}
                        apiBase={API_BASE}
                        profileUpdatedAt={profile?.updated_at ?? ""}
                        onProfileUpdated={(updated) =>
                          setProfile(updated as unknown as ProfileResponse)
                        }
                      />
                    ) : section === "publications" ? (
                      <PublicationsEditor
                        entries={(value as Publication[]) ?? []}
                        apiBase={API_BASE}
                        profileUpdatedAt={profile?.updated_at ?? ""}
                        onProfileUpdated={(updated) =>
                          setProfile(updated as unknown as ProfileResponse)
                        }
                      />
                    ) : (
                      <VolunteerEditor
                        entries={(value as VolunteerActivity[]) ?? []}
                        apiBase={API_BASE}
                        profileUpdatedAt={profile?.updated_at ?? ""}
                        onProfileUpdated={(updated) =>
                          setProfile(updated as unknown as ProfileResponse)
                        }
                      />
                    )}
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
                    {/* #382 (PO decision 2026-08-08, Option A): a budget figure
                        with no unit is omitted from every generated document —
                        never guessed, never shown as a bare magnitude. The PO
                        condition is that the omission is addressed to the user
                        AT THE FIELD, not only as a Health-hub item, so the
                        one-question fix is offered where the data lives. The
                        backend owns the rule and names the affected entries
                        (ADR-066); this only places its answer. */}
                    {section === "work_experience" && Array.isArray(value) && (
                      <div className="mt-2 flex flex-wrap gap-2">
                        {(value as Array<Record<string, unknown>>)
                          .filter((entry) =>
                            budgetUnitLabels.has(
                              workEntryLabel(entry as { role?: string; company?: string }),
                            ),
                          )
                          .map((entry, idx) => {
                            const company = (entry["company"] as string) ?? "";
                            const role =
                              (entry["role"] as string) ??
                              (entry["title"] as string) ??
                              "";
                            return (
                              <Button
                                key={`budget-unit-${idx}`}
                                size="sm"
                                variant="ghost"
                                data-testid="budget-unit-hint"
                                className="text-amber-500 hover:text-amber-600 text-xs h-7 px-2"
                                onClick={() => openEnrichForEntry(company, role)}
                              >
                                <span className="flex items-center gap-1">
                                  {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                                  <span aria-hidden="true">⚠</span>
                                  {t("budgetNeedsUnit")}
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

          {/* G-5 (ADR-059 amended 2026-10-24) — every denial, with "Zurücknehmen". */}
          {profile && (
            <DeniedConceptsCard
              denied={profile.profile.metadata?.denied_concepts ?? []}
              withdrawn={profile.profile.metadata?.withdrawn_denials ?? []}
              onWithdrawn={loadProfile}
            />
          )}

          {/* R2-8 (9) (ruling V-2 = A) — "See what was combined" lands on the
              last import's summary (#import-summary); the Enrichment History
              (#67/#69, #import-log) stays inside it as the folded technical log. */}
          <ImportSummaryCard reloadKey={enrichmentHistory} onChanged={loadProfile} hubPairKeys={hubPairKeys}>
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
                      <span>{formatUiDate(record.timestamp, locale)}</span>
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
                    {/* #707 — the bindings the reconciler recorded instead of
                        writing: "English → Englisch". Not a change; rendered as
                        its own line so a wrong binding is readable somewhere. */}
                    {(record.matched ?? []).map((m, mIdx) => (
                      <div
                        key={`m-${mIdx}`}
                        className="text-gray-600"
                        data-testid="enrichment-matched"
                      >
                        {t("matchedLog", {
                          section: m.section,
                          incoming: m.incoming,
                          existing: m.existing,
                        })}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </Card>
          )}
          </ImportSummaryCard>
        </div>
      </main>
      </EntryMergeProvider>

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
        onSectionAction={handleSectionAction}
        onClose={() => {
          setReviewDrawerOpen(false);
          setResolveIssue(null);
          loadProfile();
        }}
      />
    </div>
  );
}
