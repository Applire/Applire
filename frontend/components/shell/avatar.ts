// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Avatar letters for the shell (sidebar strip, mobile drawer, topbar).
 * Two initials from the profile name; with no profile yet, the first letter of
 * the account e-mail (W0-B user-menu mock screen 3 — no name without a profile).
 */
export function avatarInitials(name: string | null | undefined, email?: string | null): string {
  const fromName = (name ?? "")
    .split(" ")
    .filter(Boolean)
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
  if (fromName) return fromName;
  return (email ?? "").trim().charAt(0).toUpperCase();
}

/** One letter for the topbar's account-menu button: name first, else e-mail. */
export function avatarLetter(name: string | null | undefined, email?: string | null): string {
  return avatarInitials(name, email).charAt(0);
}
