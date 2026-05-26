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


import { useRouter } from "next/navigation";
import { SchemeEditor } from "@/components/admin/scheme-editor";
import { ThemePreview } from "@/components/admin/theme-preview";

export default function AppearancePage() {
  const router = useRouter();
  return (
    <div className="min-h-screen flex flex-col" style={{ background: "var(--color-surface-dim)" }}>
      <header className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="max-w-6xl mx-auto flex items-center gap-4">
          <button
            onClick={() => router.push("/settings")}
            className="text-sm hover:underline"
            style={{ color: "var(--color-teal)" }}
          >
            ← Settings
          </button>
          <h1 className="text-2xl font-bold" style={{ color: "var(--color-neutral-dark, #2C3E50)" }}>
            Appearance
          </h1>
        </div>
      </header>

      <main className="flex-1 px-6 py-6">
        <div className="max-w-6xl mx-auto flex gap-5">
          <SchemeEditor />
          <ThemePreview />
        </div>
      </main>
    </div>
  );
}
