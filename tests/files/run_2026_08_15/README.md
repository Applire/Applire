# run_2026_08_15 — #552 fixture pinning (log reconstruction)

Fixtures for issue **#552** (critic blind spot: does denial-adjacent transfer
material live ONLY as `adjacent_evidence` on a denied concept, invisible to a
critic that reads only claimable/narrative-backed rows?). The original DB rows
this evidence run produced were lost to a fresh-DB-reset that preceded the
#538 evidence run (2026-08-16); these fixtures are a **log reconstruction**,
not a re-export of the original database rows. Persona is
`operations_marcus_de` (Stefan Brandt) — synthetic, already committed
elsewhere in this repo under `tests/files/panel_review_case/operations_marcus_de/`.

Source (read-only, not committed — internal `Documents/Runs/` residue):
`Documents/Runs/Tiramisu/adr076-wave2-evidence-run/llm-log-2026-08-15.jsonl`
(full LLM exchange log of the 2026-08-15 evening run) and
`gap_analysis_post_interview.json` (an API snapshot used only as a
cross-check, since its `adjacent_evidence` is null throughout — the API layer
does not carry that field).

## Files

- **`post_interview_keyword_ledger.json`** — the post-interview Keyword
  Ledger, rebuilt by running the REAL production pipeline
  (`applire.services.gap.ledger_input_from_classification` →
  `applire.services.keyword_ledger.build_keyword_ledger` →
  `applire.services.scope_requirements.build_scope_prompt_block` /
  `build_scope_ledger_entries` → `applire.services.keyword_ledger.
  assert_claimable_backed` → `applire.services.match_score.
  compute_match_score_from_ledger`) over inputs extracted from JSONL line
  147 (the classifier's last post-interview refresh response + its embedded
  JOB ANALYSIS / CANDIDATE PROFILE) and `profile.metadata.denied_concepts`
  reconstructed from every reconciler-response `denials` list up to and
  including line 146 (lines 28, 43, 47, 50, 56, 125, 131, 143, 146) — that
  field is durable on the persisted `MasterProfile` row and is never itself
  embedded in the classifier's own prompt (the classifier sees denials only
  via the prose `CANDIDATE INTERVIEW STATEMENTS` reconciliation diff-log).
  No merge/floor logic was reimplemented.

  **Cross-check:** all 38 rows, every field, and `match_score`
  (0.7450980392156863) match `gap_analysis_post_interview.json` exactly —
  zero divergence. This is strong corroboration that the reconstructed inputs
  (job analysis, profile, denied_concepts) and the real production code path
  are faithful to what actually ran on 2026-08-15.

  **#552 class-proof finding: REFUTED for this run, not confirmed.** None of
  the 6 denied rows (IFS, BRC, Verpackungsindustrie, Lebensmittelindustrie,
  Konsumgüter, Lebensmittel; all `denial_level: "direct"`) carries
  `adjacent_evidence` — every one is `null`. This is not an artefact of the
  reconstruction: production code (`_denied_row` / `_floored_row` in
  `keyword_ledger.py`, ADR-048 amended 2026-08-13 #526) **strips
  `adjacent_evidence` by design** on every denied or containment-floored row
  ("a denial is the candidate's own position on the term itself, so there is
  no substitute to promote"), and the raw classifier output never attached
  `adjacent_evidence` to any of these 6 concepts in any of the 4
  gap-classifier refresh calls across the whole interview (JSONL lines 21,
  57, 118, 147) either — `adjacent_evidence` is only ever emitted by the
  classifier on a `"partial"` verdict, and IFS/BRC/Verpackungsindustrie/
  Lebensmittelindustrie/Konsumgüter/Lebensmittel were classified `"gap"`
  (later floored to `"denied"`) at every refresh.

  What DOES carry the transfer material this run's testimony produced (a new
  skill, `"Hygiene- und Dokumentationsdisziplin"`, tied to the candidate's
  Sauberraum/cleanroom and ISO-9001-audit work at Weberit — reconciler
  op line 146) is a **different, non-denied** ledger row: `"Verpackungen"`
  (`status: "partial"`, `claimable: true`, `narrative_backed: true`,
  `adjacent_evidence: "Hygiene- und Dokumentationsdisziplin"`). That same
  skill also appears directly in the tailored CV's `skills` list (see
  `tailored_cv_data.json`) and in the delivered letter body (see
  `cover_letter_data.json`, paragraph 4). I.e. for this run, the transfer
  material is **regular, visible, claimable/narrative-backed vault material
  — not confined to denial-adjacent evidence.** If #552's blind-spot
  hypothesis holds elsewhere, this run is not its evidence; do not cite it as
  a positive case without re-checking against a run where the analogous
  material genuinely has no non-denied ledger row of its own.

- **`tailored_cv_data.json`** — the `cv_tailoring` loop's LAST generator
  output (JSONL line 159, attempt 5 of 5). **Not reviewer-approved**:
  attempts 1–5 (lines 150/152/154/156/158) all returned `approved: false`
  (blocking issues on framing precision — e.g. the 13-year
  Führungserfahrung figure, the OEE 61%→73% attribution); the loop exhausted
  its retry budget and shipped generator 5's output (this repo's documented
  "exhausted, never a delivery gate" settle path). It is also only the
  WRITER's section-level output (`summary`/`work`/`skills`) — this run
  predates #538/#539's terminal-review topology, and it has NOT been run
  through the full downstream `_compose_document` pipeline (join with
  untouched vault sections, the ADR-040 compose block, the un-migrated
  SIGNAL passes, the measure-and-condense loop), because that machinery
  cannot be faithfully replayed from this log alone. Pinned as the closest
  log-derivable form of the delivered `tailored_data`, not a byte-identical
  reconstruction of what the candidate would have downloaded.

- **`cover_letter_data.json`** — the `cover_letter_condense` loop's LAST
  generator output (JSONL line 183, attempt 5 of 5), which condenses the
  `cover_letter` loop's own settled output (line 173). **Not
  reviewer-approved**: both the `cover_letter` loop (lines 163–172) and the
  `cover_letter_condense` loop (lines 174–183) exhausted 5 reviewer attempts
  each, all `approved: false`. Reviewer 5's blocking issues (line 181)
  named the SAME class evidence this fixture pins for #552: the letter
  "does not deliver the required `gap_transfer_argument` for the gap
  'Produktion'" and an incomplete `scope_positioning` statement — i.e. the
  delivered letter shipped with the reviewer's own transfer-argument concern
  still open, even though the transfer material (Sauberraum/hygiene
  discipline) IS present in the delivered body text (paragraph 4). Lines
  184/185 (sentence-classification passes) and 186 (`findings: []`) run
  after this generator output but are audit/classification passes, not
  further generation.

- **`cv.pdf`**, **`letter.pdf`** — the rendered PDFs of this run's documents
  (already present before this fixture-pinning commit).

## Reconstruction script

Not committed (scratchpad only) — `rebuild_552_ledger.py` extracts JSONL
line 147, reconstructs `denied_concepts`, and calls the real production
functions listed above. Re-derivable from this README + the log if needed
again.
