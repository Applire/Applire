# Strawberry API contract — accounts, sessions, tokens, ownership

> **Status: frozen at W0 (2026-10-03).** This is the single document every
> Strawberry work package codes against (ADR-091 *Community ships accounts*,
> ADR-092 *Every row has an owner*). Code is the second source of truth:
> `backend/applire/schemas/{auth,me,admin}.py`, `backend/applire/auth/deps.py`,
> `backend/applire/auth/{tokens,links}.py`, `backend/applire/ownership.py`.
> Changing anything here is a **contract change** — agree it with the lead
> developer first; never change a shape silently in a package.
>
> **W0 behaviour:** every interface below exists with a harness-backed or stub
> body. On the NoAuth stub the application behaves exactly as before; the
> ownership guard is registered but **off**. "Owner" columns name the package
> that fills the body.

Contents: [1 Conventions](#1-conventions) · [2 Errors](#2-errors) ·
[3 Endpoints](#3-endpoints) · [4 Auth dependencies](#4-auth-dependencies-f2) ·
[5 Token and link resolvers](#5-token-and-link-resolvers-f3) ·
[6 Ownership API](#6-ownership-api-f4) · [7 Migrations](#7-migrations-f10) ·
[8 Model modules](#8-model-modules-f11) · [9 Test bootstrap](#9-test-bootstrap-f12) ·
[10 Services](#10-services-f5f9) · [11 Settings](#11-settings-for-the-registry) ·
[12 Decisions taken in W0](#12-decisions-taken-in-w0)

---

## 1. Conventions

| Topic | Rule |
|---|---|
| Base | Same origin as the UI; the browser calls relative `/api/...` (dev: `NEXT_PUBLIC_API_URL=""`, or the session cookie is lost cross-origin). |
| Session | Cookie `applire_session` — opaque 32 random bytes; server stores the sha256 in `auth_sessions`. `HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` iff `COOKIE_SECURE=true`. 14 days idle (refreshed at most hourly), 90 days absolute. A presented cookie is never adopted: every successful sign-in issues a fresh one. |
| Sign-in responses | `POST /api/auth/login`, `POST /api/setup` and `POST /api/auth/links/redeem` answer **204 with `Set-Cookie`** — the person is signed in. `POST /api/auth/logout` and `DELETE /api/me/account` clear it (`Max-Age=0`). |
| Bearer | `Authorization: Bearer apl_<prefix8>_<43 b64url>`. **If an `Authorization` header is present only the bearer is evaluated — the cookie is never read**; an invalid or wrong-scope bearer is 401. `api` tokens work on every authenticated route except the credential-management routes (`require_session_user`). `agent` tokens are never accepted over HTTP (stdio MCP only). `probe` tokens only on `GET /api/ops/health`. |
| CSRF / origin | Unsafe methods (POST/PUT/PATCH/DELETE) need `Origin` or `Referer` whose netloc (host **and** port) equals the request `Host` — or the `APPLIRE_BASE_URL` netloc when that is set and not the shipped default. Neither header, or `Origin: null` → 403 `origin_mismatch`. **Exempt only:** requests carrying a **valid** `api` bearer. With `APPLIRE_BASE_URL` set, an unsafe request whose `Host` is neither that netloc nor `localhost`/`127.0.0.1` is refused the same way (anti-rebinding). No state-changing GET exists. |
| Link tokens | Invite/reset tokens travel only in the page URL **fragment** (`/invite#<token>`, `/reset#<token>`) and in POST bodies — never in a request path or query. Set-password pages send `Referrer-Policy: no-referrer`. |
| Signed document links | Agent-door only: `…/html|pdf|docx?exp=<unix>&uid=<user uuid>&sig=<b64url>`, 60 min (`uid` carries the signer so the MAC is checked against `users` before any owned table is read — CONTRACT-CHANGE 1c-2, MD-16). Responses to document GETs carry `Referrer-Policy: no-referrer` and `Cache-Control: private, no-store`. REST-returned document URLs stay unsigned. |
| Foreign ids | A foreign id answers **exactly** like a missing one: `404 {"detail": "<kind> not found"}` (REST) / `not_found` (MCP). Never 403. |
| Throttle | Login is never refused for a correct password, only **delayed** (after 5 failures in 15 min per (casefolded email, client): 1 s doubling to 30 s). No error code — the response is slower; a `401 invalid_credentials` that was delayed carries the header **`X-Applire-Throttled: 1`** (identical for known and unknown emails — no enumeration, RD-8), from which the login page shows `auth.errorThrottled` (CONTRACT-CHANGE 1a, accepted 2026-10-03). |
| Emails | Compared case-insensitively (`UNIQUE(lower(email))`; SQL `lower(email) = lower(:x)` — never Python `casefold()`). |
| Passwords | 12–256 characters, no composition rules, not equal to the account email. Login accepts any length 1–256 (old passwords). |

## 2. Errors

Body shape — the codebase's existing structured-error form:

```json
{"detail": {"error_code": "unauthenticated", "message": "Sign in to continue."}}
```

`error_code` is stable and is what the frontend translates; `message` is English for
logs and scripts and may change. Two exceptions keep today's plain-string form:
validation errors (422 from FastAPI, `detail` = list) and resource-not-found
(`{"detail": "<kind> not found"}`, ADR-092 cl. 6).

| `error_code` | HTTP | Where | Meaning |
|---|---|---|---|
| `unauthenticated` | 401 | any non-public route | No valid session/bearer. **The only code the shell's global 401 handler acts on** (redirect to `/login?next=…`). |
| `forbidden` | 403 | `require_admin`, `admin_or_probe`, bearer on a session-only route | Authenticated, not allowed. |
| `invalid_credentials` | 401 at `POST /api/auth/login`; 403 at `POST /api/auth/password`, `DELETE /api/me/account` | wrong email/password; wrong *current* password on a signed-in re-check (403 so the shell does not treat it as a lost session) | Same body for unknown email, wrong password and password-less accounts. |
| `setup_done` | 409 | `POST /api/setup` | The instance is already claimed (atomic claim matched 0 rows). |
| `invalid_setup_token` | 403 | `POST /api/setup` | Code wrong or from an earlier boot ("a new code is printed at every start"). |
| `harness_active` | 409 | `POST /api/setup` | The test harness is serving this instance; setup is refused. |
| `harness_disabled` | 503 | every route | Harness flag on but a user now holds a credential ("harness disabled: instance claimed"). |
| `last_admin` | 409 | role change, disable, admin delete, self delete | Would leave zero active admins. |
| `link_invalid` | 404 | `POST /api/auth/links/inspect`, `/redeem` | Unknown/garbled token (no state to show). |
| `link_used` | 409 | `POST /api/auth/links/redeem` | Already redeemed (or superseded by a newer link of the same purpose). |
| `link_expired` | 410 | `/redeem`; signed document links (past or non-numeric `exp`) | Past `expires_at` / `exp`. |
| `origin_mismatch` | 403 | unsafe requests | Origin/Referer/Host check failed. `message` names **both remedies**: "your reverse proxy must forward the Host header, or set APPLIRE_BASE_URL to the address you open"; login and setup pages render it. |
| `email_taken` | 409 | `POST /api/admin/users` | An account with that email (case-insensitive) exists. |
| `password_policy` | 422 | setup, redeem, password change | Length or equals-email rule failed (length is also enforced by the schema → plain 422). |
| `reauth_required` | 403 | `DELETE /api/me/account`, `DELETE /api/me/oidc` | A password-less account (account delete) or any account (unlink) must complete `POST /api/me/reauth/start` first. |
| `last_credential` | 409 | `DELETE /api/me/oidc` | The account has no password — the OIDC binding is its only way to sign in, so unlinking is refused (ruling on CONTRACT-CHANGE 1d-1). |
| `user_not_pending` | 409 | `POST /api/admin/users/{id}/reinvite` | The person already has a credential — use a reset link. |
| `user_not_active` | 409 | `POST /api/admin/users/{id}/reset-link` | Pending (use reinvite) or disabled. |
| `oidc_failed` | — (redirect) | `GET /api/auth/oidc/callback` → `302 /login?error=oidc_failed` | Any IdP/state/claim check failed. |
| `oidc_no_account` | — (redirect) | callback → `302 /login?error=oidc_no_account` | Unknown identity and no pending invitation for its verified email ("ask your administrator for an invitation"). |
| `account_disabled` | 403 | `POST /api/auth/login` (and OIDC callback → `302 /login?error=account_disabled`) | Founder ruling W0B-3: returned **only after a correct password** (or a successful IdP round-trip) on a disabled account. A wrong password on a disabled account keeps `invalid_credentials` with identical body and timing — the code never reveals that an account exists. Requests on an existing session of a disabled account get `401 unauthenticated` (sessions are revoked on disable). |

## 3. Endpoints

Legend — **Dep**: the dependency the route takes (§4); `public` = allowlisted
(ADR-091 cl. 20, tested by the route-auth inventory). **Owner**: the W1/W3
package that implements the route. Schemas are in `backend/applire/schemas/`.

### 3.1 Liveness and operations

| Method · path | Dep | Request | Response | Errors | Owner |
|---|---|---|---|---|---|
| `GET /health` | public | — | `admin.LivenessResponse` `{status, edition, version}` | — | 1c |
| `GET /api/ops/health` | `admin_or_probe` | — | `admin.OpsHealthResponse` = today's ops report (`status`, `edition`, `version`, `llm_provider`, `checked_at`, `components`, …) **+** `upgrade_notice`, `debug_log_on`, `topology` (moved off `/health`) **+** `retired_profiles: int` (RD-9: count of older duplicate profiles migration 0074 set aside, 0 when none — never ids; MD-27) | 401, 403; 503 when `status=down` (unchanged) | 1c |

`/health` **loses** `llm_provider`, `upgrade_notice`, `debug_log_on`, `topology`,
`ops` (ADR-086/087 freeze break, recorded; CHANGELOG upgrade note). W0 still serves
the old `/health` body — 1c reduces it.

### 3.2 Sign-in and first run

| Method · path | Dep | Request | Response | Errors | Owner |
|---|---|---|---|---|---|
| `GET /api/auth/state` | public | — | `auth.AuthStateResponse` `{setup_required, oidc_enabled, oidc_button_label, smtp_enabled, harness}` | — | 1a |
| `POST /api/setup` | public | `auth.SetupRequest` `{setup_token, email, password}` | 204 + session cookie | 403 `invalid_setup_token`, 409 `setup_done`, 409 `harness_active`, 422 `password_policy`, 403 `origin_mismatch` | 1a |
| `POST /api/auth/login` | public | `auth.LoginRequest` `{email, password}` | 204 + session cookie | 401 `invalid_credentials`, 403 `account_disabled` (correct password only, W0B-3), 403 `origin_mismatch` | 1a |
| `POST /api/auth/logout` | `require_session_user` | — | 204, cookie cleared, session row revoked | 401 | 1a |
| `GET /api/auth/me` | `require_user` | — | `auth.MeResponse` `{id, email, role, has_password, oidc_linked, ui_language}` | 401 | 1a |
| `POST /api/auth/password` | `require_session_user` | `auth.PasswordChangeRequest` `{current, new}` | 204; the person's **other** sessions revoked | 403 `invalid_credentials`, 422 `password_policy` | 1a |

`setup_required` ⇔ no user holds a credential. While `harness` is true the
frontend shows the red banner on every page.

### 3.3 Invite and reset links (token in fragment + body only)

| Method · path | Dep | Request | Response | Errors | Owner |
|---|---|---|---|---|---|
| `POST /api/auth/forgot` | public | `auth.ForgotRequest` `{email}` | **202, no body — always** (mail sent in a background task, ≤ 3/h per account; nothing when SMTP is off) | 422 (schema only) | 1b |
| `POST /api/auth/links/inspect` | public | `auth.LinkInspectRequest` `{token}` | `auth.LinkInspectResponse` `{purpose: invite\|reset, email, state: valid\|expired\|used}` | 404 `link_invalid` | 1b |
| `POST /api/auth/links/redeem` | public | `auth.LinkRedeemRequest` `{token, password}` | 204 + session cookie (signed in) | 404 `link_invalid`, 409 `link_used`, 410 `link_expired`, 422 `password_policy` | 1b |

Invite links live 7 days, reset links 1 hour; a newer link of the same purpose
revokes the older one; redemption is one atomic `UPDATE … RETURNING`.

### 3.4 OIDC (only when `OIDC_ISSUER` is set; otherwise 404)

| Method · path | Dep | Request | Response | Owner |
|---|---|---|---|---|
| `GET /api/auth/oidc/start?next=<relative path>` | public | — | 302 to the IdP; signed 10-min httpOnly state cookie (state, nonce, PKCE verifier, `intent=login`) | 1d |
| `GET /api/auth/oidc/callback?code&state` | public | — | success: session cookie + 302 to `next` (default `/`), or `/settings?oidc=linked` for `intent=link`, or `/settings?reauth=<action>` for `intent=reauth` (grant verified); failure: 302 `/login?error=oidc_failed\|oidc_no_account\|account_disabled` for `intent=login`, `/settings?oidc=failed` for `intent=link\|reauth` (CONTRACT-CHANGE 1d-1) | 1d |
| `POST /api/me/oidc/link` | `require_session_user` | — | 200 `me.AuthorizeRedirectResponse` `{authorize_url}` + state cookie with `intent=link` and the session's `uid` | 1d |
| `POST /api/me/reauth/start` | `require_session_user` | `me.ReauthStartRequest` `{action: account.delete\|oidc.unlink, target_id}` | 200 `me.AuthorizeRedirectResponse` (IdP with `prompt=login&max_age=0`); 403 `forbidden` when `target_id` is not the caller's own id or the account has no OIDC binding; 503 `oidc_failed` when the IdP's discovery fails | 1d |
| `DELETE /api/me/oidc` | `require_session_user` | — | 204, binding cleared, audited `oidc.unlinked`; 204 no-op when not linked. Errors: 409 `last_credential` (no password), 403 `reauth_required` (no verified `oidc.unlink` grant for this session) | 1d |

`next` must be a same-origin relative path (`/…`, not `//…`); anything else → `/`.

`POST /api/me/oidc/link` also answers 503 `oidc_failed` when discovery fails. The
state cookie `applire_oidc_state` has `Path=/api/auth/oidc`, `HttpOnly`,
`SameSite=Lax`, 10 minutes; each state is accepted once. Re-auth: the IdP must
send `auth_time` (an IdP that omits it cannot confirm destructive actions — fail
closed).

### 3.5 My tokens and my account (session only — an `api` bearer gets 403 `forbidden`)

| Method · path | Dep | Request | Response | Errors | Owner |
|---|---|---|---|---|---|
| `GET /api/me/tokens` | `require_session_user` | — | `me.TokenListResponse` `{tokens: [TokenItem]}` — `agent` + `api`, not revoked; never the secret | 401 | 1c |
| `POST /api/me/tokens` | `require_session_user` | `me.TokenCreateRequest` `{name, scope: agent\|api}` | **201** `me.TokenCreatedResponse` = `TokenItem` + `token` (shown once) | 401, 422 | 1c |
| `DELETE /api/me/tokens/{token_id}` | `require_session_user` | — | 204 (revoking an `agent` token bumps `users.link_epoch`) | 404 `token not found` (foreign = missing) | 1c |
| `DELETE /api/me/account` | `require_session_user` | `me.AccountDeleteRequest` `{password?}` | 204, cookie cleared; account erased + tombstoned | 403 `invalid_credentials`, 403 `reauth_required`, 409 `last_admin` | 1b |

`TokenItem` = `{id, name, scope, prefix, created_at, last_used_at}`; `prefix` is
the 8 characters after `apl_`.

### 3.6 Admin (metadata only — never content)

`require_admin_session` (MD-34/MD-38) = `require_admin` + session only: an `api` bearer gets 403 `forbidden` on every route that creates or resets a credential (invite, reset link, role change, token revoke, probe tokens). The route walk in `backend/tests/unit/test_w4_fix_identity.py` pins which admin routes are which.

| Method · path | Dep | Request | Response | Errors | Owner |
|---|---|---|---|---|---|
| `GET /api/admin/users` | `require_admin` | — | `admin.AdminUserListResponse` `{users: [AdminUserItem]}` (tombstoned accounts not listed) | 401, 403 | 1b |
| `POST /api/admin/users` | `require_admin_session` | `admin.AdminUserCreateRequest` `{email, role=user, send_mail=true}` — `send_mail` only decides about the mail (sent iff SMTP is configured AND true); the link is always returned (MD-28) | **201** `admin.AdminUserCreatedResponse` `{user, link: IssuedLink}` — a pending account + its invite link | 409 `email_taken` | 1b |
| `PATCH /api/admin/users/{user_id}` | `require_admin_session` | `admin.AdminUserPatchRequest` `{role?, disabled?}` | `admin.AdminUserItem` | 404, 409 `last_admin`, 422 (empty body) | 1b |
| `DELETE /api/admin/users/{user_id}` | `require_admin` | — | 204 (erasure + tombstone) | 404, 409 `last_admin` | 1b |
| `POST /api/admin/users/{user_id}/reinvite` | `require_admin_session` | — | `admin.IssuedLink` (purpose `invite`) | 404, 409 `user_not_pending` | 1b |
| `POST /api/admin/users/{user_id}/reset-link` | `require_admin_session` | — | `admin.IssuedLink` (purpose `reset`) | 404, 409 `user_not_active` | 1b |
| `POST /api/admin/users/{user_id}/revoke-tokens` | `require_admin_session` | — | `admin.RevokeTokensResponse` `{revoked}`; bumps `link_epoch` | 404 | 1b |
| `GET /api/admin/probe-tokens` | `require_admin_session` | — | `admin.ProbeTokenListResponse` | 401, 403 | 1c |
| `POST /api/admin/probe-tokens` | `require_admin_session` | `admin.ProbeTokenCreateRequest` `{name}` | **201** `admin.ProbeTokenCreatedResponse` (token shown once) | 422 | 1c |
| `DELETE /api/admin/probe-tokens/{token_id}` | `require_admin_session` | — | 204 | 404 | 1c |

`AdminUserItem` = `{id, email, role, status: pending|active|disabled, created_at,
last_login_at, last_active_at, invite_expires_at, metadata: {application_count,
document_count, storage_bytes, ai_tokens_30d}}` — `invite_expires_at` = expiry of
the newest unused invite link of a pending account, else `null` (CONTRACT-CHANGE
1b-4); `ai_tokens_30d` = `llm_usage.total_tokens` of the last 30 days, `null` while
unattributable (CONTRACT-CHANGE 1b-1). `IssuedLink` = `{purpose, url, expires_at, mailed, mail_failed, mail_failed_reason}` (MD-38: `mail_failed_reason` = `base_url_unset` | `send_failed` | `null`; a mailed link is built from `APPLIRE_BASE_URL` only, MD-32 — the `url` shown here may use the admin's own browser origin)
with `url` = `<origin>/invite#<token>` or `<origin>/reset#<token>`. Every admin
action writes an audit row (user ids only, no IP). Adding a field to the users
list is a contract change.

### 3.7 Existing routes after the W0 swap

| Routes | Dep (from W0) |
|---|---|
| Every other existing `/api/*` route of `application`, `cover_letter`, `cv`, `cv_color`, `documents`, `flow`, `job`, `jobs`, `profile`, `profile_enrich`, `profile_roles`, `review`, `session`, `settings`, `signature` (90 routes) | `require_user` |
| `GET /api/cv/{cv_id}/{html,pdf,docx}`, `GET /api/cover-letter/{cl_id}/{html,pdf,docx}` (the six document GETs) | `user_or_signed_link` |
| `GET/POST /api/admin/color-schemes`, `POST …/preview`, `PATCH …/{id}/activate`, `DELETE …/{id}` | `require_admin` |
| `GET /api/admin/color-schemes/active` | public (the login page themes itself) |
| `POST /api/settings/upgrade-notice/dismiss` | `require_admin` |
| `GET /api/ops/health` | `admin_or_probe` |
| `/docs`, `/redoc`, `/openapi.json` | `require_user` — re-mounted by 1a (not yet in W0) |

Handlers that used `auth.get_current_user(request)` now receive
`current_user: User = Depends(require_user)`; the decorative parameters are
`_auth: User = Depends(...)`. W0 count: 105 routes — `require_user` 90,
`user_or_signed_link` 6, `require_admin` 6, `admin_or_probe` 1, public 2
(`GET /health`, `GET /api/admin/color-schemes/active`).

## 4. Auth dependencies (F2)

`backend/applire/auth/deps.py` — all `async def` (test:
`tests/unit/test_auth_deps_async.py` walks each one's dependant tree).

```python
async def require_user(request, provider=Depends(get_auth_provider), db=Depends(get_db)) -> User
async def require_admin(...) -> User                 # 401 anonymous, 403 forbidden
async def require_session_user(...) -> User          # 401; refuses bearer-authenticated requests (1a)
async def user_or_signed_link(...) -> User           # session | api bearer | ?exp=&sig= (1c, auth/deps_links.py)
async def admin_or_probe(...) -> User | None         # admin -> User; probe token -> None (1c)
AUTH_DEPENDENCIES = (require_user, require_admin, require_session_user, user_or_signed_link, admin_or_probe)
```

* Each one **raises itself** and calls `ownership.set_owner(user.id)` before
  returning, so the endpoint, its `BackgroundTasks` and `create_task` children all
  run under that owner. A probe-token request sets no owner (it reads no owned
  table — the ops report runs `unscoped("ops-aggregate")`).
* `get_auth_provider` (`applire/auth/__init__.py`) stays **the override point**
  (ADR-008) and is now `async def`. Tests override it, never the five.
* Provider contract: `async def get_current_user(self, request, db) -> User | None`
  — a live row (not disabled, not tombstoned) or `None`. The dependencies turn
  `None` into 401.
* W0 bodies: all five resolve through the provider; `require_admin` treats a user
  without a `role` attribute (the pre-0071 NoAuth stub) as admin. 1a replaces this
  with session/bearer resolution, `role == "admin"`, the CSRF check and the
  harness fences.

## 5. Token and link resolvers (F3)

`backend/applire/auth/tokens.py` (1c):

```python
TokenScope = Literal["agent", "api", "probe"]
class InvalidToken(Exception)          # one exception for every cause — no oracle
async def resolve_agent_token(db, raw: str) -> User            # raises InvalidToken
async def resolve_bearer(db, raw: str, scope: TokenScope) -> User | None
def bearer_from_request(request) -> str | None                 # None / "" / raw
async def request_bearer_user(request, db, scope: TokenScope = "api") -> User | None
async def is_csrf_exempt(request, db) -> bool                  # valid api bearer of an active user only
async def create_token(...); async def list_tokens(...); async def revoke_token(...)
async def revoke_all_for_user(db, user_id) -> int              # agent + api; bumps link_epoch
```

`backend/applire/auth/links.py` (1c):

```python
DocumentKind = Literal["cv", "cover_letter"]
class LinkExpired(Exception)           # -> 410 link_expired
def sign_document_url(kind: DocumentKind, doc_id: uuid.UUID, user: User, base: str) -> str
async def verify_document_link(kind, doc_id, exp: str, sig: str, db, uid: str | uuid.UUID | None) -> User | None
def derive_key(purpose: Literal["doc-link", "oidc-state"]) -> bytes
async def load_instance_secret(db) -> bytes | None
def set_instance_secret(value) -> None
```

`sig = HMAC(doc-link key, "<kind>:<doc_id>:<user_id>:<link_epoch>:<exp>")`; `uid`
carries `<user_id>` so the MAC is checked against `users` before any owned table is
read (CONTRACT-CHANGE 1c-2). Key derived from `instance_state.auth.instance_secret`; `base` is the unsigned URL
(it may already carry a query). W0 bodies raise `NotImplementedError`.

## 6. Ownership API (F4)

`backend/applire/ownership.py`:

```python
UnscopedReason = Literal["admin-metadata", "retention", "ops-aggregate", "orphan-scan",
                         "startup-backfill", "job-refcount", "migration", "tooling"]
IDENTITY_TABLES = {"users", "auth_sessions", "personal_tokens", "auth_links",
                   "reauth_grants", "llm_usage", "audit_events"}
GUARD_ENABLED = False                       # W0; 3e flips it

@dataclass(frozen=True) class OwnerContext(user_id: UUID | None, reason: str | None)
def current_owner() -> OwnerContext | None
def set_owner(user_id: UUID) -> Token       # used by the auth dependencies
def reset_owner(token) -> None
@contextmanager def owner_context(user_id: UUID)      # background tasks, MCP tool calls
@contextmanager def unscoped(reason: UnscopedReason)   # ValueError outside the closed list
def owned_tables() -> frozenset[str]        # derived from models with __owned__ = True
def owned_table_regex() -> re.Pattern       # word-boundary, case-insensitive, longest first
def check_statement(sql: str) -> None       # raises OwnerContextMissing
def install_guard(async_engine) -> None     # before_cursor_execute on async_engine.sync_engine
async def get_owned(db, Model, id, user_id, *, kind=None) -> Model   # raises OwnedNotFound
class OwnerContextMissing(RuntimeError)
class OwnedNotFound(HTTPException)          # 404 {"detail": "<kind> not found"}; .kind
```

* **Owned set** = `master_profiles`, `profile_snapshots`, `user_settings`,
  `applications`, `flow_sessions`, `uploads`, `cv_import_jobs`,
  `gap_analysis_jobs`, `generated_cvs`, `generated_cover_letters`,
  `gap_analyses`, `interview_sessions` — each model class carries
  `__owned__ = True` (per class, not inherited). A new per-user table must either
  carry the marker or be added to `IDENTITY_TABLES` by an ADR amendment; a test
  fails otherwise.
* The guard is registered once, in `applire/db/session.py`, on the **application
  engine's** `sync_engine` — never the `Engine` class (alembic's engine stays
  unguarded). Engines created in tests are unguarded unless the test calls
  `install_guard(engine)`.
* `get_owned` requires an `__owned__` model with a `user_id` column (the chain
  tables gain theirs in `0075`); it does not filter `deleted_at`.
* The ORM `with_loader_criteria` defence-in-depth hook (ADR-092 cl. 8b) is 3a's
  addition to this module.
* MCP maps `OwnedNotFound` to its `not_found` error (4b).

## 7. Migrations (F10)

No-op revisions committed in W0, chained `0070 ← 0071 ← … ← 0079`; one head,
asserted by `tests/unit/test_alembic_single_head.py`. Owners replace the bodies
and docstrings; revision ids and `down_revision` stay.

| Revision | Owner | Content |
|---|---|---|
| `0071_auth_users_columns` | 1a | `users` columns (`password_hash`, `role` default `user`, `oidc_issuer`, `oidc_subject` + UNIQUE together, `email_verified_at`, `disabled_at`, `last_login_at`, `last_active_at`, `link_epoch`), `UNIQUE(lower(email))`; sets no role on the stub |
| `0072_auth_tables` | 1a | `auth_sessions`, `personal_tokens`, `auth_links`, `reauth_grants` |
| `0073_audit_events` | 1b | `audit_events` + no-UPDATE trigger |
| `0074_ownership_owner_columns` | 3a | `master_profiles.user_id` + live-unique (dedupe RD-9), uploads/import/gap jobs NOT NULL + FK, `job_analyses.raw_text_origin` |
| `0075_ownership_chain_tables` | 3a | `user_settings` UNIQUE(user_id), chain-table `user_id` (backfill before NOT NULL) + re-keyed uniques, `llm_usage.user_id`, end-of-migration NULL assertion |
| `0076`–`0079` | 3a | reserved; may stay no-ops |

A package needing a revision outside its range asks the lead developer first.

## 8. Model modules (F11)

`backend/applire/models/auth.py` (1a: `AuthSession`, `PersonalToken`, `AuthLink`,
`ReauthGrant`) and `backend/applire/models/audit.py` (1b: `AuditEvent`) exist
empty and are imported by `applire/models/__init__.py`, which imports **every**
model module (tested — `owned_tables()` relies on it). Identity tables are **not**
`__owned__`.

## 9. Test bootstrap (F12)

* `backend/tests/support/owners.py`:
  `HARNESS_USER_ID` (the stub user), the **sync** autouse fixture
  `harness_owner_context`, marker `no_owner_context`,
  `make_user(db, *, email=None, user_id=None)`, `make_two_users(db) -> (A, B)`,
  fixture `two_users` (backend unit tree, on `async_db`).
* Both unit conftests import the fixture and register the marker; backend
  `async_db` depends on it, so `create_all` runs under the owner context.
  Opt out (`@pytest.mark.no_owner_context`) in guard mutation tests and the
  cross-user isolation suite only.
* Unit DB URL defaults: `tests/unit` → `postgresql+asyncpg://test:test@localhost/unit_test`
  (never connected), `backend/tests/unit` → `sqlite+aiosqlite://` (in-memory) —
  the harness's test-database proof (ADR-091 cl. 3 c).
* `tests/support/profile_factory.make_master_profile` defaults `user_id` to the
  harness user once the column exists; pass `user_id=` for another person.
* Resource factories for the isolation suite: 3a's `RESOURCE_FACTORIES`; other
  packages add `tests/support/owners_<pkg>.py`.
* A test overriding the provider uses `NoAuthProvider()` or an object whose
  `get_current_user` is awaitable and accepts `(request, db)`.

## 10. Services (F5–F9)

Owned by W0-A2: `docs/dev/api-contract-strawberry-services.md` — the
`user_id: uuid.UUID | None = None` keyword on every generation-service function
and background task, `services/profile.get_profile_for_user`,
`services/posting_labels.effective_posting_labels(job, application)`,
`services/safe_fetch.safe_get`, `services/erasure.erase(db, user_id, scope)`.

## 11. Settings for the registry

Declared by 1a in `settings_registry.py` (`introduced_in="0.43.0"`), listed here
because the contract reads them: `AUTH_PROVIDER` (`local`; `none` re-meant),
`AUTH_HARNESS`, `COOKIE_SECURE`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`,
`OIDC_CLIENT_SECRET`, `OIDC_SCOPES`, **`OIDC_BUTTON_LABEL` (default
`Single sign-on`; served as `/api/auth/state.oidc_button_label`)**, `SMTP_HOST`,
`SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_SECURITY`,
`AGENT_LINK_TTL_MINUTES`, `AUDIT_LOG_RETENTION_DAYS`, `APPLIRE_AGENT_TOKEN`,
`CORS_ORIGINS`. Instance-state keys: `auth.instance_secret`,
`auth.setup_token_hash`, `upgrade.retired_profiles`.

## 12. Decisions taken in W0

| # | Decision | Why |
|---|---|---|
| W0-1 | Error body = `{"detail": {"error_code", "message"}}`, not a top-level `{"code"}` | The codebase's existing structured-error shape (`routers/job.py`, `routers/session.py`); FastAPI renders it from `HTTPException(detail=…)` with no new exception handler. |
| W0-2 | `401` means "not signed in" only; a wrong *current* password on a signed-in request is `403 invalid_credentials` | The shell's global 401 handler redirects to login; it must not fire on a typo in the password-change form. |
| W0-3 | Setup and link redemption sign the person in (204 + cookie) | ADR-091 cl. 14 ("then … logs in") applied to both ways of setting a first password. |
| W0-4 | `get_auth_provider` is `async def` | It is in every route's dependant tree; a sync factory runs in the threadpool and fails the async-all-the-way-down test. |
| W0-5 | `OwnedNotFound` subclasses `HTTPException` | 404 rendering without touching `main.py`; MCP maps it explicitly. |
| W0-6 | `require_session_user` on logout | A bearer has no session to end. |
| W0-8 | `account_disabled` (403) per founder ruling W0B-3 | Only after a correct credential; never an existence oracle. |
| W0-7 | Codes added beyond the ADR's list: `harness_disabled`, `invalid_setup_token`, `link_invalid`, `link_expired`, `email_taken`, `password_policy`, `reauth_required`, `user_not_pending`, `user_not_active`, `oidc_failed`, `oidc_no_account` | Each is a distinct state a page must word differently. |
