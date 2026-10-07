# Admin API contract — instance settings, audit view, usage, dashboard (Epic C, Strawberry build 2)

> **Status: SEAM-READY (C1, 2026-10-07).** The admin UI (WP-C2) codes against this
> document. Code is the second source of truth: `backend/applire/schemas/admin.py`
> (section "Epic C"). Governing decision: ADR-093 *Runtime instance settings*
> (with amendments to ADR-001, ADR-005/017 and ADR-086). Any change after
> SEAM-READY is a **CONTRACT-CHANGE**, agreed with the lead developer first.
>
> Conventions, error body shape, CSRF/origin and bearer rules are those of
> [`api-contract-strawberry.md`](api-contract-strawberry.md) §1–§2 and are not repeated.

Contents: [1 Endpoints](#1-endpoints) · [2 Settings](#2-instance-settings) ·
[3 Audit view](#3-audit-view) · [4 Usage](#4-usage) · [5 Dashboard and notices](#5-dashboard-and-notices) ·
[6 #726 refusal at both doors](#6-726-linkedin-refusal-at-both-doors) · [7 Errors](#7-errors) ·
[8 Open founder questions this contract assumes](#8-assumed-rulings)

---

## 1. Endpoints

All routes are admin only. An anonymous caller gets **401 `unauthenticated`**, and a signed-in non-admin gets **403 `forbidden`**. Tests assert both at the endpoint. Reads use `require_admin`, so an admin `api` bearer may read them. Writes use `require_admin_session`, so they need a browser session: a leaked admin bearer cannot reroute every user's LLM traffic or swap the key.

| Method + path | Auth | Request | 200 response (`schemas/admin.py`) | Errors |
|---|---|---|---|---|
| `GET /api/admin/settings` | `require_admin` | — | `InstanceSettingsResponse` | 401, 403 |
| `PUT /api/admin/settings` | `require_admin_session` | `InstanceSettingsUpdate` `{changes: {KEY: value}}` | `InstanceSettingsResponse` (after the write) | 401, 403, 422 `unknown_setting` / `invalid_setting_value`, 409 `provider_not_ready`, 503 `settings_secret_unavailable` |
| `DELETE /api/admin/settings/{key}` | `require_admin_session` | — | `InstanceSettingsResponse` (override removed: back to env/default) | 401, 403, 404 `unknown_setting` |
| `GET /api/admin/audit` | `require_admin` | query, see §3 | `AuditPageResponse` | 401, 403, 422 `invalid_cursor` |
| `GET /api/admin/usage?days=30` | `require_admin` | `days` 1–365, default 30 | `AdminUsageResponse` | 401, 403, 422 |
| `GET /api/admin/dashboard` | `require_admin` | — | `AdminDashboardResponse` | 401, 403 |
| `GET /api/admin/notices` | `require_admin` | — | `AdminNoticesResponse` | 401, 403 |

`GET /health` stays reduced (build 1: `status`, `edition`, `version`). The effective
provider is visible at `GET /api/ops/health` (`llm_provider`) and on the dashboard
(`health.llm_provider`, `health.llm_model`). Both reflect a switch on the next request.

## 2. Instance settings

### 2.1 The panel-editable set (closed list; adding one is a CONTRACT-CHANGE)

| `key` | `group` | `kind` | `provider` | Notes |
|---|---|---|---|---|
| `LLM_PROVIDER` | llm | enum | — | `choices`: `mistral`, `openrouter`, `requesty`, `anthropic`, `openai`, `ollama` (`mock` is never offered) |
| `MISTRAL_MODEL` / `MISTRAL_API_KEY` | llm | string / secret | `mistral` | The Mistral key is also the OCR key (see `dependencies`) |
| `OPENROUTER_MODEL` / `OPENROUTER_API_KEY` | llm | string / secret | `openrouter` | |
| `REQUESTY_MODEL` / `REQUESTY_API_KEY` | llm | string / secret | `requesty` | |
| `ANTHROPIC_MODEL` / `ANTHROPIC_API_KEY` | llm | string / secret | `anthropic` | |
| `OPENAI_MODEL` / `OPENAI_API_KEY` | llm | string / secret | `openai` | key optional (a keyless local server) |
| `OLLAMA_MODEL` | llm | string | `ollama` | no key |
| `SCRAPER_FETCH_LINKEDIN_GUEST_PAGES` | scraper | bool | — | #726, default **true** (ruling E-3) |
| `RETENTION_ENABLED` | retention | bool | — | #738, default **true** |

Base URLs, timeouts, reasoning knobs and every other setting stay **env-only**. A base URL decides where every CV is sent, so it is not a panel field (ADR-093 cl. 2).

### 2.2 Item semantics (`InstanceSettingItem`)

- `value` is the **effective** value. **For `kind: "secret"` it is always `null`.** A secret is never echoed: not masked, not truncated, not "last 4". The UI shows `is_set` ("Schlüssel hinterlegt" / "kein Schlüssel") and offers "replace".
- `source`: `panel` (an admin override in the database; **it wins over env**, ruling C1-2), `env` (the operator's environment / `.env`), `default` (neither; the code default).
- `env_value` / `env_is_set`: what `DELETE /api/admin/settings/{key}` ("reset to environment value") falls back to.
- `updated_at`, `updated_by_user_id`, `updated_by_email` (resolved live; `null` for an erased account): only when `source == "panel"`.

### 2.3 `providers` (`ProviderStatus[]`)

One row per selectable provider: the effective `model`, plus `key_required`, `has_key`, `ready` (= `has_key or not key_required`) and `active`. The provider select should disable or warn for rows with `ready == false`.

**Qualification (CONTRACT-CHANGE MD2-6 (1)):** `qualification` ∈ `qualified` | `not_qualified` | `unmeasured`, plus `qualification_reason` (English, one sentence, or `null`) and `qualification_as_of` (an ISO date, e.g. `"2026-09-16"`). These describe the row's **effective model** according to the published matrix (`docs/llm-models.md`, #688). The data lives in `backend/applire/data/model_qualification.json`, which wave-2 #688 maintains. A model that is not listed reads as `unmeasured`, which does not mean "bad".

### 2.4 `dependencies` (`SettingDependency[]`)

`{code: "ocr_needs_mistral_key", satisfied, keys: ["OCR_BACKEND", "MISTRAL_API_KEY"]}`: present when `OCR_BACKEND=mistral_vision`. `satisfied` is false when no Mistral key is effective, whatever the LLM provider is. Further codes are additive; the UI renders unknown codes generically.

### 2.5 Writes

- `PUT` reads the body as raw JSON `{"changes": {KEY: value}}` with 1–20 keys and no other top-level key. It is deliberately **not** validated by FastAPI: its default 422 echoes the failing `input`, which for this body would be the secrets. Every refusal is `invalid_setting_value` / `unknown_setting` with `detail.key` only.
- `PUT` applies all `changes` atomically. It validates every key against §2.1. Enums are checked against `choices`, bools must be JSON booleans, and strings are trimmed with a 1–200 char limit. A secret cannot be `""`; to remove an override, use `DELETE`.
- Setting `LLM_PROVIDER` to a provider whose key is required and not effective (after this request's own changes) is refused with **409 `provider_not_ready`** and `detail.provider`. To switch and enter the key in one step, send both in one `PUT`.
- Every changed key writes one audit row (`settings.changed`, §3.2) in the same transaction.
- **Effect timing (ADR-093 cl. 5, ruling C1-4):** the web process applies the change immediately. Every other process (MCP stdio, the retention worker, further web workers) picks it up at its next request, tool call or run, at most 2 s later for web workers. **Work already running keeps the settings it started with.** A generation, its review loop, self-audit and critic all run on the provider that was current when the request started.

## 3. Audit view

### 3.1 Query

`GET /api/admin/audit?action=&actor_id=&target_user_id=&since=&until=&limit=&cursor=`

| Param | Type | Meaning |
|---|---|---|
| `action` | string, repeatable | exact action names (see `actions` in the response) |
| `actor_id` | uuid | rows written by this user |
| `target_user_id` | uuid | rows about this user |
| `since` / `until` | ISO-8601 datetime | `at >= since`, `at < until` |
| `limit` | 1–200, default 50 | page size |
| `cursor` | opaque string | `next_cursor` of the previous page; 422 `invalid_cursor` if tampered |

Newest first, keyset paging (stable under concurrent inserts). The response carries metadata only (RD-6, RD-11): `actor_email` and `target_email` are resolved live from `users`, and are `null` for the system or an erased account. `detail` is the stored scalar map: ids, roles, reasons, counts, setting keys and **non-secret** values. There is no IP address.

### 3.2 Actions added by Epic C (existing actions: `services/audit.py`)

| Action | `detail` keys | Written when |
|---|---|---|
| `settings.changed` | `key`, `write_only` (bool: a secret), `from_source`, `to_source`, `from_value`, `to_value` (both **absent for secrets**; `null` if email-shaped) | an admin `PUT` changes a setting |
| `settings.reset` | `key`, `write_only`, `from_value` (absent for secrets), `to_source` | an admin `DELETE` removes an override |
| `settings.env_observed` | `key`, `to_value`, `to_source`, `from_value` | at boot, an env/default-sourced tracked setting (`LLM_PROVIDER`, `SCRAPER_FETCH_LINKEDIN_GUEST_PAGES`, `RETENTION_ENABLED`) differs from the value the last boot saw. An `.env` edit leaves a trace too. |
| `retention.skipped` | `source` | a retention run found `RETENTION_ENABLED=false` and skipped the personal-data TTLs (§5.3) |

`actor_user_id` is the admin for `settings.*` and NULL (the system) for `settings.env_observed` and `retention.skipped`.

## 4. Usage

`GET /api/admin/usage?days=30`: aggregates of `llm_usage` (one row per provider call, no text column) over `created_at >= now - days`.

- `totals`: all calls in the window.
- `users`: **every** account that is not erased, zero rows included, sorted by `totals.total_tokens` descending. Fields: `email`, `role` and `status` (`pending`/`active`/`disabled`, same derivation as `/api/admin/users`), plus `last_call_at`.
- `unattributed`: calls with `user_id` NULL (system work, rows written before 0075, erased accounts).
- `by_document_kind` (CONTRACT-CHANGE MD2-6 (2)): `{cv, cover_letter, other}` → `UsageTotals`. `other` = every call not attributed to a document.
- The UI's 7/30/90-day choice maps to `days=7|30|90`.
- `by_provider`: grouped by (`provider`, `model`), sorted by `total_tokens` descending. The exact model id is shown here because this is the admin surface; ADR-086 cl. 4 keeps it off the unauthenticated surface.

## 5. Dashboard and notices

### 5.1 `GET /api/admin/dashboard` (`AdminDashboardResponse`)

| Field | Content |
|---|---|
| `health` | `status` (`ok`/`degraded`/`down`), `version`, `edition`, `topology`, `debug_log_on`, effective `llm_provider` + `llm_model`, `checked_at`, `components[] {name, status, checked_at}`, from the ops layer's `collect()` (ADR-086) without its usage block. **The provider component is never measured for this request** (adv-admin ADM-5). It is the last background check, with its own `checked_at`, and `unknown` with `checked_at: null` while no check about the CURRENT provider/model/key exists. A stale or missing one starts a single background check. |
| `users` | counts: `total` (not erased), `active`, `pending`, `disabled`, `admins` |
| `usage_30d` | `UsageTotals` for the last 30 days |
| `failed_jobs` | `window_days: 7`, `count`, newest 20 `items {kind: cv\|cover_letter\|import\|gap, id, user_id, user_email, failed_at, error_code}`. `failed_at` is the job's **creation** time, because the tables keep no failure timestamp. `error_code` is always `null` for `cover_letter` (that table has no code column). **Never the error message text** (it can quote document content). |
| `upgrade_notice` | same object as `/api/ops/health.upgrade_notice` (US310), or null |
| `retention` | `enabled`, `source`, `last_run_at`, `last_run_ok`, `last_run_skipped` (#738). CONTRACT-CHANGE MD2-6 (3): `enabled_since` (while ON: the newest OFF→ON audit row of `RETENTION_ENABLED`, else the instance claim time (`setup.claimed`), else `null`; `null` while OFF), and `changed_by_email` (while OFF through the panel: the current email of the admin who switched it off; `null` otherwise, including OFF through the environment). CONTRACT-CHANGE MD2-8: `ttl_days {uploads, interview_sessions, generated_documents, cancelled_applications, profile_inactivity, audit_log}`, the effective TTLs as ints in days (`0` = that rule never expires anything) |
| `notices` | `AdminNotice[]`, same as §5.2 |

### 5.2 `GET /api/admin/notices` (`AdminNoticesResponse`)

This is the cheap call behind the admin-only one-line signal on the user dashboard ("Instanz: 2 Hinweise → Admin"). The frontend calls it **only when `me.role == "admin"`**. A non-admin gets 403, and the UI renders nothing then. `items[] {code, severity}`:

| `code` | `severity` | When |
|---|---|---|
| `health_down` | critical | ops `status == down` |
| `health_degraded` | warning | ops `status == degraded` |
| `upgrade_notice` | warning | an undismissed version-jump notice exists |
| `retention_disabled` | warning | `RETENTION_ENABLED` is effectively false (the banner of ruling C1-3) |
| `failed_jobs` | warning | ≥ 1 failed job in the last 7 days |
| `debug_log_on` | warning | `LLM_DEBUG_LOG=true` (records CV PII) |
| `dev_topology` | warning | `APPLIRE_TOPOLOGY=dev` |
| `provider_not_ready` | critical | the active provider needs a key and none is effective |
| `ocr_needs_mistral_key` | warning | `OCR_BACKEND=mistral_vision` without a Mistral key |
| `settings_secret_unreadable` | critical | a stored panel secret can no longer be decrypted (instance secret rotated). It is treated as unset. |

`notices` on `/api/admin/notices` skips the network-bound ops probes (DB only) so it stays cheap. On the dashboard, `health_*` comes from the full `collect()`.

### 5.3 What `RETENTION_ENABLED=false` suspends (ADR-005/017 amendment, ruling C1-3)

**Suspended:** the calendar TTLs on personal data. That means uploads (7 d), interview sessions (30 d), generated CVs and cover letters (calendar TTL), orphan postings, and the 730-day inactivity tombstones for profiles, applications and users.
**Never suspended:** account erasure (self or admin delete), the purge of documents of an application the user cancelled, auth housekeeping (expired links and sessions), import/gap job handles and stale-job reaping, the orphan-file scan, the audit-log and `llm_usage` age rules, and the `retention_runs` trim.
Every run records `retention_enabled` and its `source` in the run report. A run that skipped the TTLs also writes `retention.skipped` (§3.2).

## 6. #726 LinkedIn refusal at both doors

`SCRAPER_FETCH_LINKEDIN_GUEST_PAGES=false` refuses a `linkedin.com` URL (any subdomain) before any fetch. The text is the manual-paste fallback (ADR-001 tier 3) and names the setting:

- **Web:** `POST /api/job/analyze` with `url` → **422** `{"detail": {"error_code": "linkedin_guest_fetch_disabled", "message": "…paste the job description…SCRAPER_FETCH_LINKEDIN_GUEST_PAGES…"}}`.
- **MCP:** `analyze_jd(url=…)` → `invalid_input` error whose `data` is `{"reason": "linkedin_guest_fetch_disabled"}`. Every other scrape failure carries `{"reason": "jd_fetch_failed"}`.

The UI translates `linkedin_guest_fetch_disabled` like the other `jd_*` codes.

## 7. Errors

| `error_code` | HTTP | Where | Meaning |
|---|---|---|---|
| `unknown_setting` | 422 (`PUT`) / 404 (`DELETE`) | settings | key not in §2.1; `detail.key` |
| `invalid_setting_value` | 422 | `PUT /api/admin/settings` | wrong type, not in `choices`, empty secret, too long; `detail.key` |
| `provider_not_ready` | 409 | `PUT /api/admin/settings`, `DELETE /api/admin/settings/{key}` | `PUT`: `LLM_PROVIDER` → a provider with no usable key. `DELETE`: removing the override would leave the active provider without one (ruling MD2-17). `detail.provider` + `detail.key` (the missing key, e.g. `ANTHROPIC_API_KEY`). `openai` needs a key unless `OPENAI_BASE_URL` is set |
| `settings_secret_unavailable` | 503 | `PUT` with a secret | the instance secret is not loaded (cannot encrypt). Never written in plain text. |
| `invalid_cursor` | 422 | `GET /api/admin/audit` | tampered or foreign cursor |
| `linkedin_guest_fetch_disabled` | 422 | `POST /api/job/analyze` | §6 |

## 8. Rulings this contract rests on

C1-2 **ruled A** (MD2-1): a panel override wins, with a source badge and a reset via `DELETE`. C1-4 **ruled A** (MD2-2): in-flight work keeps its provider. C1-1 **ruled A** (founder): the key is stored in the DB, Fernet-encrypted with the instance secret. C1-3 **ruled A** (founder): scope as in §5.3; OFF is allowed with more than one user, audited, with a banner.

## 9. Change log

- 2026-10-07 `d7cb97c5`: first SEAM-READY.
- 2026-10-07 (fix-admin, adversarial findings): `provider_not_ready` also on `DELETE`, with `detail.key`. `openai` needs a key without `OPENAI_BASE_URL`. A secret value must be printable ASCII without spaces (`\x21`-`\x7e`), otherwise 422 `invalid_setting_value` with `detail.key` only. `health.components[].checked_at` is new and the dashboard never pings the provider inline. `retention.enabled_since` never spans a skipped worker run, and `notices` carries `retention_disabled` when the newest run skipped. A write of the same key is an upsert (last write wins, both audited). The LinkedIn refusal also covers redirect hops, tier-2 requests and WHATWG/Python parser differences.
- 2026-10-07 (second SEAM-READY): CONTRACT-CHANGE MD2-6 from C2 added `qualification*`, `by_document_kind`, `enabled_since`, `changed_by_email` and `updated_by_email`. Audit detail key `secret` → `write_only`, because the audit suite forbids "secret" as a detail key name. The `PUT` body is validated raw (no FastAPI 422 echo). The `failed_at` semantics are clarified. CONTRACT-CHANGE MD2-8 added `retention.ttl_days`.
