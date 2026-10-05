// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/** US330 — the frontend auth seam (Strawberry 2a). Import from `@/lib/auth`. */

export {
  API_BASE,
  AUTH_PAGES,
  fetchAuthState,
  fetchMe,
  isAuthPage,
  isSharePrefillNext,
  loginPathFor,
  postJson,
  readErrorCode,
  readErrorMessage,
  safeNextPath,
  type AuthState,
  type CurrentUser,
  type LinkInspect,
  type MeResult,
  type Role,
} from "./api";
export { installAuthFetch, isApiUrl, markSessionKnown, suppressAuthRedirect } from "./fetch-patch";
export {
  CurrentUserProvider,
  useCurrentUser,
  type CurrentUserProviderProps,
  type CurrentUserStatus,
  type CurrentUserValue,
} from "./current-user";
export { clearUserBrowserState, getStorageUserId, setStorageUserId, userScopedKey } from "./storage";
