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
import { AppTopbar } from "@/components/shell/AppTopbar";
import { QuickTailorWidget } from "@/components/dashboard/QuickTailorWidget";
import { ProfileStrengthCard } from "@/components/dashboard/ProfileStrengthCard";
import { DashboardApplicationCard } from "@/components/dashboard/DashboardApplicationCard";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");
const MAX_CARDS = 6;

interface Application {
  id: string;
  role_title: string | null;
  company_name: string | null;
  workflow_status: string;
  user_status?: string;
  flow_session_id: string | null;
  updated_at: string;
}

export default function DashboardPage() {
  const t = useTranslations("dashboard");
  const router = useRouter();
  const [applications, setApplications] = useState<Application[]>([]);
  const [userName, setUserName] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const [appsRes, profileRes] = await Promise.all([
          fetch(`${API_BASE}/api/applications`),
          fetch(`${API_BASE}/api/profile`),
        ]);
        if (appsRes.ok) {
          const d = await appsRes.json();
          setApplications(d.items ?? []);
        }
        if (profileRes.ok) {
          const d = await profileRes.json();
          setUserName(d.profile?.personal_info?.name ?? null);
        }
      } catch {
        // non-fatal
      } finally {
        setLoading(false);
      }
    }
    void load();
  }, []);

  async function handleStartFlow(appId: string) {
    try {
      const res = await fetch(`${API_BASE}/api/applications/${appId}/start`, { method: "POST" });
      if (res.ok) {
        const d = await res.json();
        if (d.flow_session_id) router.push(`/flow/${d.flow_session_id}/import`);
      }
    } catch {
      // non-fatal
    }
  }

  const firstName = userName?.split(" ")[0] ?? null;
  const inProgress = applications.filter((a) => a.workflow_status !== "none").length;

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      <AppTopbar mode="section" titleKey="shell.dashboard" />

      <main className="flex-1 overflow-y-auto px-8 py-7">
        {/* Page header */}
        <div className="mb-5">
          <h1 className="text-[22px] font-extrabold text-neutral-dark font-manrope tracking-tight">
            {firstName ? t("welcomeBackUser", { name: firstName }) : t("welcomeBack")}
          </h1>
          <p className="text-[13px] text-gray-500 mt-0.5">
            {t("activeApplicationsSubtitle", { count: inProgress })}
          </p>
        </div>

        {/* Top row: Quick Tailor + Profile Strength */}
        <div className="grid grid-cols-[1fr_260px] gap-4 mb-6">
          <QuickTailorWidget />
          <ProfileStrengthCard />
        </div>

        {/* Active applications */}
        <div className="flex items-center justify-between mb-3.5">
          <h2 className="text-[15px] font-extrabold text-neutral-dark font-manrope">
            {t("activeApplications", { count: applications.length })}
          </h2>
          {applications.length > MAX_CARDS && (
            <button
              onClick={() => router.push("/documents")}
              className="text-[12px] font-bold text-teal hover:underline"
            >
              {t("viewAllInDocuments")}
            </button>
          )}
        </div>

        {loading ? (
          <div className="grid grid-cols-2 gap-3.5">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-36 bg-white rounded-xl border border-gray-200 animate-pulse" />
            ))}
          </div>
        ) : applications.length === 0 ? (
          <div className="flex items-center justify-center h-40 bg-white rounded-xl border border-dashed border-gray-300">
            <p className="text-[13px] text-gray-400">{t("noApplications")}</p>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3.5">
            {applications.slice(0, MAX_CARDS).map((app) => (
              <DashboardApplicationCard
                key={app.id}
                applicationId={app.id}
                roleTitle={app.role_title}
                companyName={app.company_name}
                workflowStatus={app.workflow_status}
                userStatus={app.user_status}
                flowSessionId={app.flow_session_id}
                updatedAt={app.updated_at}
                onStartFlow={() => handleStartFlow(app.id)}
              />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
