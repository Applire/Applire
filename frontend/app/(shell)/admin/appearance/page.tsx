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


import { useTranslations } from "next-intl";
import { SchemeEditor } from "@/components/admin/scheme-editor";
import { ThemePreview } from "@/components/admin/theme-preview";
import { AppTopbar } from "@/components/shell/AppTopbar";

export default function AppearancePage() {
  const t = useTranslations("admin");
  return (
    <div className="flex flex-col flex-1 overflow-hidden" style={{ background: "var(--color-surface-dim)" }}>
      <AppTopbar
        mode="detail"
        backHref="/dashboard"
        backLabelKey="shell.dashboard"
        pageTitle={t("appearanceTitle")}
      />
      <main className="flex-1 px-6 py-6 overflow-y-auto">
        <div className="max-w-6xl mx-auto flex gap-5">
          <SchemeEditor />
          <ThemePreview />
        </div>
      </main>
    </div>
  );
}
