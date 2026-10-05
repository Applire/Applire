// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

import { SetPasswordPage } from "../_components/set-password";

/** US330 — /invite#<token> (token in the fragment only; see _components/set-password.tsx). */
export default function Page() {
  return <SetPasswordPage purpose="invite" />;
}
