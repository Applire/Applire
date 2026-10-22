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
import { FirstRunScreen } from "@/components/onboarding/FirstRunScreen";
import { HarnessBanner } from "@/components/shell/HarnessBanner";
import { AppSidebar } from "@/components/shell/AppSidebar";
import { AppTopbar } from "@/components/shell/AppTopbar";
import { ShellUserProvider } from "@/components/shell/ShellUserContext";
import { loginPathFor, useCurrentUser } from "@/lib/auth";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

export default function Home() {
  const t = useTranslations("home");
  const tCommon = useTranslations("common");
  const router = useRouter();
  const [isReturningUser, setIsReturningUser] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);

  // US330 (ADR-091): the first screen is for a signed-in person too.
  const { status: authStatus, authState } = useCurrentUser();
  const authReady = authStatus === "authenticated" || authStatus === "error";


  useEffect(() => {
    if (authStatus !== "unauthenticated") return;
    router.replace(
      authState?.setup_required
        ? "/setup"
        : loginPathFor({ pathname: "/", search: window.location.search }),
    );
  }, [authStatus, authState?.setup_required, router]);

  useEffect(() => {
    if (!authReady) return;
    async function checkProfile() {
      try {
        const res = await fetch(`${API_BASE}/api/profile/exists`);
        if (res.ok) {
          const data = await res.json();
          setIsReturningUser(data.exists);
        } else {
          setIsReturningUser(false);
        }
      } catch {
        setIsReturningUser(false);
      } finally {
        setLoading(false);
      }
    }
    checkProfile();
  }, [authReady]);

  // Returning user → Dashboard. Navigation must happen in an effect, not during
  // render — calling router.replace() in the render body updates the Router's
  // state mid-render and triggers a "setState during render" warning (bug 6).
  useEffect(() => {
    if (isReturningUser) {
      router.replace("/dashboard");
    }
  }, [isReturningUser, router]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-surface-dim">
        <p className="text-gray-500">{tCommon("loading")}</p>
      </div>
    );
  }

  // Returning user → Dashboard (navigation handled by the effect above)
  if (isReturningUser) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-surface-dim">
        <p className="text-gray-500">{tCommon("loading")}</p>
      </div>
    );
  }

  // Strawberry int-1 (approved mock user-menu screen 3): a signed-in account
  // without a profile sees the onboarding INSIDE the shell — e-mail in the
  // sidebar, the nav, the account menu with sign-out, Administration for
  // admins. The harness (single-user) first run keeps its standalone page.
  if (authStatus === "authenticated" && authState?.harness !== true) {
    return (
      <ShellUserProvider userName={null}>
        <div className="flex h-screen flex-col overflow-hidden bg-surface-dim" data-testid="onboarding-in-shell">
          <div className="flex flex-1 min-h-0 overflow-hidden">
            <AppSidebar userName={null} />
            <div className="flex flex-col flex-1 min-w-0 overflow-y-auto">
              <AppTopbar mode="section" titleKey="shell.welcome" />
              <FirstRunScreen importReady={authReady} />
            </div>
          </div>
        </div>
      </ShellUserProvider>
    );
  }

  // New user → Screen 1 (CV upload + JD input)
  return (
    <div className="min-h-screen flex flex-col bg-surface-dim">
      <HarnessBanner />
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-4 py-4">
        <div className="max-w-4xl mx-auto flex items-center justify-between">
          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
          <h1 className="font-heading text-2xl font-bold text-neutral-dark">Applire</h1>
          <p className="text-sm text-gray-500 hidden sm:block">
            {t("tagline")}
          </p>
        </div>
      </header>

      {/* Main Content */}
      <FirstRunScreen importReady={authReady} />

      {/* Footer */}
      <footer className="bg-white border-t border-gray-200 px-4 py-4">
        <div className="max-w-4xl mx-auto text-center">
          <p className="text-sm text-gray-500">
            {t("footerTagline")}
          </p>
        </div>
      </footer>
    </div>
  );
}