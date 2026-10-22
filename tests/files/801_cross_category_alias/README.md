<!-- Copyright (C) 2026 Tobias Rosenbaum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Bug #801 fixture — a technical skill bound to a soft one

Synthetic persona `it_backend_daniel` (`tests/files/panel_review_case/it_backend_daniel/`).

- `vault_before_second_import.json` — the vault before the second import, as the UX audit
  (2026-10-20, v0.44.0-beta) persisted it in `profile_snapshots` (enrichment history removed).
- `incoming_linkedin.json` — the same persona's LinkedIn extraction (`linkedin_daniel_kovac.md`) as the
  reconciler reads it; its skills and languages match the audit receipt's `incoming_entry` rows.
- `audit_binding_batch.json` — the audit's skill/language bindings, rebuilt from its import receipt and
  the persisted vault after the import. The REST binding is a TARGETED `upsert_skill` ("REST APIs",
  technical, target = the id of the soft skill "Mentoring", evidence = the Finleap Build job): the
  receipt alone cannot tell it from a `match_existing`, but the vault after the import carries the
  Finleap job on Mentoring's `experience_refs`, which only a targeted upsert writes (`match_existing`
  writes nothing; the enrichment carry matches exact names only). The replays of the same input chose
  exactly this op shape — aimed at "REST API design" — in 7 of 10 runs. The op for the identical
  "Mentoring" pair writes no receipt; it is included as the replays emitted it.
