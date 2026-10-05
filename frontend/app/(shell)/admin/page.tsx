"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/** `/admin` → the first registered section (G-3: the registry decides, not a literal). */

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { ADMIN_SECTIONS } from "./sections";

export default function AdminIndexPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace(ADMIN_SECTIONS[0].href);
  }, [router]);
  return null;
}
