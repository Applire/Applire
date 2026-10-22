<!-- Copyright (C) 2026 Tobias Rosenbaum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Bug #801 fixture — a technical skill bound to a soft one

Synthetic persona `it_backend_daniel` (`tests/files/panel_review_case/it_backend_daniel/`).

- `vault_before_second_import.json` — the vault before the second import, as the UX audit
  (2026-10-20, v0.44.0-beta) persisted it in `profile_snapshots` (enrichment history removed).
- `incoming_linkedin.json` — the same persona's LinkedIn extraction (`linkedin_daniel_kovac.md`) as the
  reconciler reads it; its skills and languages match the audit receipt's `incoming_entry` rows.
- `audit_binding_batch.json` — the audit's skill/language bindings, rebuilt from its import receipt:
  `match_existing` "REST APIs" on the id of the soft skill "Mentoring" (the #801 row), plus the
  genuine pairs of the same import. The receipt does not record the model's other ops, and an op
  for the identical "Mentoring" pair writes no receipt; it is included as the replays emitted it.
