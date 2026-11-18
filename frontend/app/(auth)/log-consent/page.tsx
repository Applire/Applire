"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { LogConsentInterstitial } from "../_components/log-consent-interstitial";

/** Board B2 — asked after sign-in when this instance logs content and there is no current consent. */
export default function Page() {
  return <LogConsentInterstitial />;
}
