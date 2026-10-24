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


import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

/** From this score on the card stops asking for more. */
const HIGH_SCORE = 90;

type ChecklistKey = "profileCheckWorkExp" | "profileCheckSkills" | "profileCheckEducation" | "profileCheckSummary";

interface ChecklistItem {
  labelKey: ChecklistKey;
  done: boolean;
}

/**
 * R2-10 (lead G hunk): the summary lives in `professional_summary.{de,en}`
 * (`MasterProfileData`), not in `personal_info` — the card read a field the
 * schema does not have, so every strong profile was told "Optional: add
 * Summary" next to its summary (round 2, felix-031).
 */
function hasSummary(profile: Record<string, unknown>): boolean {
  const ps = profile.professional_summary as Record<string, unknown> | string | null | undefined;
  if (typeof ps === "string") return ps.trim().length > 0;
  if (ps && typeof ps === "object") {
    return Object.values(ps).some((v) => typeof v === "string" && v.trim().length > 0);
  }
  return false;
}

function buildChecklist(profile: Record<string, unknown> | null): ChecklistItem[] {
  if (!profile) return [];
  const work = (profile.work_experience as unknown[]) ?? [];
  const skills = (profile.skills as unknown[]) ?? [];
  const edu = (profile.education as unknown[]) ?? [];
  return [
    { labelKey: "profileCheckWorkExp", done: work.length > 0 },
    { labelKey: "profileCheckSkills",  done: skills.length > 0 },
    { labelKey: "profileCheckEducation", done: edu.length > 0 },
    { labelKey: "profileCheckSummary", done: hasSummary(profile) },
  ];
}

export function ProfileStrengthCard() {
  const t = useTranslations("dashboard");
  const router = useRouter();
  const [score, setScore] = useState<number | null>(null);
  const [checklist, setChecklist] = useState<ChecklistItem[]>([]);

  useEffect(() => {
    async function load() {
      try {
        const [existsRes, profileRes] = await Promise.all([
          fetch(`${API_BASE}/api/profile/exists`),
          fetch(`${API_BASE}/api/profile`),
        ]);
        if (existsRes.ok) {
          const d = await existsRes.json();
          setScore(Math.round((d.completeness_score ?? 0) * 100));
        }
        if (profileRes.ok) {
          const d = await profileRes.json();
          setChecklist(buildChecklist(d.profile ?? null));
        }
      } catch {
        // non-fatal — card stays in skeleton state
      }
    }
    void load();
  }, []);

  const barWidth = score !== null ? `${score}%` : "0%";

  // A strong profile is not nagged (UX audit 2026-10-20: "99" beside "Fehlende
  // Abschnitte ergänzen"): from HIGH_SCORE the hint names the one missing
  // section as optional, or says nothing when nothing is missing.
  const strong = score !== null && score >= HIGH_SCORE;
  const firstMissing = checklist.find((item) => !item.done);

  let hint: string | null;
  if (!strong) hint = t("profileStrengthHint");
  else if (firstMissing) hint = t("profileStrengthOptional", { section: t(firstMissing.labelKey) });
  else hint = null;

  return (
    <div
      data-testid="profile-strength-card"
      className="rounded-[14px] p-3.5 md:p-5 text-white flex flex-row items-center justify-between gap-3 md:flex-col md:items-stretch md:justify-start bg-gradient-to-br from-teal-dim to-primary shadow-lg shadow-teal-dim/20"
    >
      {/* Below md the card is one row: number + label + link. */}
      <div className="flex items-baseline gap-2.5 md:flex-col md:items-stretch md:gap-0">
        <p className="text-[11px] font-bold uppercase tracking-widest text-white/60 md:mb-1.5 md:order-first">
          {t("profileStrengthTitle")}
        </p>

        {score === null ? (
          <div className="h-8 w-12 md:h-12 md:w-20 rounded bg-white/10 animate-pulse md:mb-3" />
        ) : (
          <p className="text-[28px] md:text-[46px] font-extrabold leading-none font-manrope md:mb-2.5">{score}</p>
        )}
      </div>

      {/* Progress bar */}
      <div className="hidden md:block h-[5px] bg-white/20 rounded-full mb-2">
        <div
          className="h-[5px] bg-gold rounded-full transition-all duration-700"
          style={{ width: barWidth }}
        />
      </div>
      {hint ? (
        <p data-testid="profile-strength-hint" className="hidden md:block text-[11.5px] text-white/60 mb-3">
          {hint}
        </p>
      ) : null}

      {/* Checklist */}
      <div className="hidden md:flex flex-col gap-1.5 mb-4">
        {checklist.map((item) => {
          // Icon name as variable to avoid JSX literal rule (material-symbols identifiers are not user-facing text)
          const checkIcon: string = item.done ? "check_circle" : "radio_button_unchecked";
          return (
            <div key={item.labelKey} className="flex items-center gap-2 text-[11.5px]">
              <span
                className="material-symbols-outlined"
                aria-hidden="true"
                style={{
                  fontSize: 14,
                  color: item.done ? "#4ade80" : "rgba(255,255,255,0.25)",
                }}
              >
                {checkIcon}
              </span>
              <span className={item.done ? "text-white/75" : "text-white/40"}>{t(item.labelKey)}</span>
            </div>
          );
        })}
      </div>

      <button
        onClick={() => router.push("/profile")}
        className="md:mt-auto shrink-0 text-[12px] font-bold text-gold flex items-center gap-1 hover:opacity-80 transition-opacity"
      >
        {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
        <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 15 }}>arrow_forward</span>
        {t("profileCompleteButton")}
      </button>
    </div>
  );
}
