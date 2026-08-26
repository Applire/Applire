# US293 — receipt parity fixture (E055, ADR-063 clause 8)

**What this is.** The enrichment-trail receipt every manual section edit produced through the
REST door on **main before US290** (`1b410de902f36f3a2550afe10216cf7e4d7be867`, the last commit
whose profile page sent the *whole section* as JSON), recorded once, for every
`(section × add / update / rename / remove)` case of the vault's write vocabulary. `projects` had
no door then (422 — recorded as such); it is pinned against the form its list siblings share.

**Why it exists.** E055 changed the *payload shape* of a manual edit — lists that round-trip entry
ids, merge-patches of only the keys the user changed — but must not change the *write*: every save
is the `FieldEdit` intake on `commit_ops`, and every save leaves the per-entry receipt of the
2026-08-09 ADR-063 clause-8 ruling. `tests/unit/test_us293_receipt_parity.py` drives both doors
(REST `PATCH /api/profile/{section}`, MCP `update_profile`) with today's editor payloads and pins
the receipt to this recording (JF-F-H0.1 (D); System-FMEA SF-VAULT.6 / SF-VAULT.9).

**Files.**

- `record.py` — the shared module: the seed vault, the case table, the two body builders (the
  pre-epic whole-section body; today's editor body per `frontend/lib/sectionSave.ts` and
  `lib/profile-entries.ts` `makeEmpty*`), the receipt normaliser, and the recorder itself. The test
  loads it by path, so recorder and pin share one implementation.
- `fixture.json` — the recording: provenance (commit, path, sha256 of the six production files that
  produced the receipts), the seed and case-table digests, and per case the body sent, the HTTP
  outcome, the raw receipt (uuids masked) and its normalised shape.

**Re-recording** (only when the pre-epic form is re-derived on purpose — the recorder refuses any
other checkout):

```bash
git worktree add --detach ../wt-pre-epic 1b410de9
PYTHONPATH=../wt-pre-epic/backend python3 tests/files/us293_receipt_parity/record.py
git worktree remove ../wt-pre-epic
```

Synthetic data only — the seed is a fictional "Anna Bauer" vault; no real personal data.
