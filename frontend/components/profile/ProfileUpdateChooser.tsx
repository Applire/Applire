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

"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { AppTopbar } from "@/components/shell/AppTopbar";
import { ImportInProgressBanner } from "@/components/dashboard/ImportInProgressBanner";
import { FirstRunScreen } from "@/components/onboarding/FirstRunScreen";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

export function ProfileUpdateChooser() {
  const t = useTranslations("profileUpdate.chooser");
  const tp = useTranslations("profileUpdate");
  // Hazelnut b3 A-11 — the sidebar entry "Lebenslauf oder Stelle hinzufügen"
  // lands here for everyone. A person without a profile has nothing that
  // "changed" and no new role to add: they get the first-run screen in place,
  // as /dashboard does (ruling F1-3). Unknown (loading) renders nothing rather
  // than flashing the returning-user frame; a failed check falls back to it.
  const [hasProfile, setHasProfile] = useState<boolean | null>(null);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/profile/exists`);
        const d = res.ok ? ((await res.json()) as { exists?: boolean }) : null;
        if (!cancelled) setHasProfile(d?.exists === false ? false : true);
      } catch {
        if (!cancelled) setHasProfile(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (hasProfile === null) {
    return <div className="flex-1" data-testid="profile-update-loading" />;
  }

  if (hasProfile === false) {
    return (
      <div className="flex flex-col flex-1 overflow-hidden" data-testid="profile-update-first-run">
        <AppTopbar mode="section" titleKey="shell.welcome" />
        <div className="flex-1 overflow-y-auto">
          <div className="px-4 pt-5 md:px-8">
            <ImportInProgressBanner onAllDone={() => setHasProfile(true)} />
          </div>
          <FirstRunScreen />
        </div>
      </div>
    );
  }

  return (
    <>
      <AppTopbar
        mode="detail"
        backHref="/profile"
        backLabelKey="shell.profile"
        pageTitle={tp("chooserTitle")}
      />
    <section className="mx-auto max-w-3xl px-6 py-10" data-testid="profile-update-chooser">
      <p className="text-sm text-gray-500 mt-1">{t("subheading")}</p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-8">
        <Link
          href="/profile/upload?action=upload"
          className="block rounded-2xl border bg-white p-6 hover:shadow-md hover:border-primary-container transition-all"
        >
          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- Material Symbols icon name */}
          <span className="material-symbols-outlined text-primary" style={{ fontSize: 32 }}>description</span>
          <h2 className="mt-3 text-base font-bold text-gray-900 font-manrope">{t("uploadTitle")}</h2>
          <p className="mt-1 text-sm text-gray-600">{t("uploadBody")}</p>
        </Link>

        <Link
          href="/profile/upload?action=add-role&source=manual"
          className="block rounded-2xl border bg-white p-6 hover:shadow-md hover:border-primary-container transition-all"
        >
          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- Material Symbols icon name */}
          <span className="material-symbols-outlined text-primary" style={{ fontSize: 32 }}>work</span>
          <h2 className="mt-3 text-base font-bold text-gray-900 font-manrope">{t("addRoleTitle")}</h2>
          <p className="mt-1 text-sm text-gray-600">{t("addRoleBody")}</p>
        </Link>
      </div>
    </section>
    </>
  );
}
