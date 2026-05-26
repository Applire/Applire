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

import Link from "next/link";
import { useTranslations } from "next-intl";
import { AppTopbar } from "@/components/shell/AppTopbar";

export function ProfileUpdateChooser() {
  const t = useTranslations("profileUpdate.chooser");
  const tp = useTranslations("profileUpdate");

  return (
    <>
      <AppTopbar
        mode="detail"
        backHref="/profile"
        backLabelKey="shell.profile"
        pageTitle={tp("chooserTitle")}
      />
    <section className="mx-auto max-w-3xl px-6 py-10">
      <p className="text-sm text-gray-500 mt-1">{t("subheading")}</p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-8">
        <Link
          href="/profile/upload?action=upload"
          className="block rounded-2xl border bg-white p-6 hover:shadow-md hover:border-primary-container transition-all"
        >
          <span className="material-symbols-outlined text-primary" style={{ fontSize: 32 }}>
            description
          </span>
          <h2 className="mt-3 text-base font-bold text-gray-900 font-manrope">{t("uploadTitle")}</h2>
          <p className="mt-1 text-sm text-gray-600">{t("uploadBody")}</p>
        </Link>

        <Link
          href="/profile/upload?action=add-role&source=manual"
          className="block rounded-2xl border bg-white p-6 hover:shadow-md hover:border-primary-container transition-all"
        >
          <span className="material-symbols-outlined text-primary" style={{ fontSize: 32 }}>
            work
          </span>
          <h2 className="mt-3 text-base font-bold text-gray-900 font-manrope">{t("addRoleTitle")}</h2>
          <p className="mt-1 text-sm text-gray-600">{t("addRoleBody")}</p>
        </Link>
      </div>
    </section>
    </>
  );
}
