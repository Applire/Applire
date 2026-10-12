# Quality-bench fixture format (E066)

A fixture freezes **everything the CV and cover-letter pipelines read** for one
cell, so a bench run measures generation only (no import / interview variance).
One fixture = one directory:

```
tests/files/quality_bench/<case>/<cell>/
  fixture.json   required   identity + provenance (below)
  vault.json     required   the vault: master_profiles.profile_json, exactly as stored
  job.json       required   the posting analysis: job_analyses columns (below)
  posting.md     required   the posting text: job_analyses.raw_text
  gap.json       required*  the gap analysis row: gap_analyses columns incl. keyword_ledger
  ledger.json    optional   a bare keyword-ledger list; used only if gap.json has no keyword_ledger
```

`*` A fixture without a gap row still runs (the pipelines treat "no gap analysis" as an empty
ledger — cv.py:4064 `if gap is not None else []`, cover_letter.py:1341), but then no keyword is
claimable or forbidden and the bench measures a different pipeline path than production. Real
fixtures must carry `gap.json`.

`<cell>` is the directory name (`same_de`, `cross_en2de`, …). The bench's **cell id** is
`fixture.json["cell_id"]` if present, else `"<case>.<cell>"` (e.g. `it_backend_daniel.same_en`);
it is one path component in the run output and the key the scorers group by.

**One reader.** `scripts/quality_bench/fixture.py::load_fixture(dir)` loads and validates a
fixture; the seeding code and the scorers both use it, so a scorer sees exactly what the
generator was handed. Validate a directory by hand:
`python3 scripts/quality_bench/fixture.py tests/files/quality_bench/<case>/<cell>`.

All data is **synthetic**: contacts use `example.com`, names are invented. Never copy from
`Documents/testdata/RealProfiles/`.

---

## fixture.json

```json
{
  "case": "operations_marcus_de",
  "cell_type": "same",
  "document_language": "de",
  "provenance": {"how": "…", "model": "openai/gpt-5.6-luna", "date": "2026-10-12"},
  "template": "classic_german",
  "target_pages": 2,
  "letter_inputs": {"tone": "formal", "recipient_name": null, "motivation": null}
}
```

| Key | Req. | Meaning / who reads it |
|---|---|---|
| `case` | yes | person/case slug (`it_backend_daniel`, …) — scorers group by it |
| `cell_type` | yes | `same` \| `cross` \| `mixed` \| `upward` |
| `document_language` | yes | `de` \| `en` — the language the CV **and** letter must be written in. Seeded as `applications.language_override`, so it wins over JD detection (see *Language*) |
| `provenance` | yes | `{how, model, date}` — how the fixture was produced (which pipeline run / who authored the translation), model id or `null` for hand-built, ISO date |
| `cell_id` | no | overrides the derived `<case>.<cell>` |
| `template` | no | CV template, default `classic_german`. The letter inherits it from the CV (cover_letter.py:191-197) |
| `target_pages` | no | per-generation CV page target override; default = the DACH standard (`resolve_target_pages(None, None)`, cv.py:3306) |
| `letter_inputs` | no | the user-supplied `CoverLetterGenerateRequest` fields: `recipient_name`, `recipient_company`, `salary`, `availability`, `motivation`, `tone` (`formal` default). Stored as `pre_gen_inputs` (cover_letter.py:211-218) and read at cover_letter.py:1299 |

---

## What each pipeline reads from the database (the seeding contract)

`scripts/quality_bench/seed.py` creates exactly these rows. Reader = the product code that
consumes the field (file:line in `backend/applire/`, at the time of writing).

### Entry points

The harness calls the public service doors inline (no request lifecycle), the same path the
MCP/agent channel takes:

1. `services/cv.py:3269 generate_cv(job_id, db, provider, None, template=, target_pages=, user_id=)`
   — resolves the owner, `job_for_user` (3296), the profile (3301), the document language (3312-3315),
   creates the `GeneratedCV` row, then calls `_render_cv_background` inline (3341).
2. `services/cover_letter.py:160 generate_cover_letter(request, db, provider, None, user_id=)`
   — resolves the caller's **flow session** (176-188), the CV through it (191-197), the profile (201-203),
   pins the language (219-226), creates the `GeneratedCoverLetter`, calls `_render_cover_letter_background`
   inline (259).

The letter reads the **delivered CV** (`flow.generated_cv_id` → `cv.tailored_data`,
cover_letter.py:1271-1273). The harness sets `flow.generated_cv_id` between the two stages, as the flow
orchestrator would. If the CV is not ready the letter is not run (a letter built from the raw
vault is a different pipeline path; ADR-061 cl. 3 branch at cover_letter.py:1279).

### Rows

| Row (model) | Seeded from | Why it must exist / reader |
|---|---|---|
| `users` | synthetic `bench-user@example.com` | every owned row carries `user_id` (ADR-092); `resolve_owner` refuses to run without one (services/owner_resolution.py:62) |
| `master_profiles` | `vault.json` → `profile_json` | `get_profile_for_user` (cv.py:3301, cover_letter.py:203); cv.py:4089 `profile.profile_json`; cv.py:4487 raw copy; cover_letter.py:1367-1394 |
| `job_analyses` | `job.json` + `posting.md` | `job_for_user` (cv.py:3296), `db.get(JobAnalysis)` (cv.py:4014), cover_letter.py:1246 |
| `applications` | link to the posting; `role_title`/`company_name` from `job.json`; `language_override` = `fixture.document_language` | `job_for_user` reaches a posting **only** through the user's application (ADR-092 cl. 5c); `resolve_document_language(application, job)` (utils/language_detection.py:119, called cv.py:3315 / cover_letter.py:224-226); `effective_posting_labels` (cv.py:4076 `_posting_labels_for`, cover_letter.py:1264, services/posting_labels.py:44) |
| `gap_analyses` | `gap.json` (+ `ledger.json`) | newest live row per (job, user): cv.py:4033-4045, cover_letter.py:1316-1328 |
| `flow_sessions` | synthetic, `job_id` + `gap_analysis_id` + `application_id` | `generate_cover_letter` aborts without the caller's flow for the job (cover_letter.py:176-188) |
| `generated_cvs`, `generated_cover_letters` | created by the pipeline doors | outputs |

Not seeded because not read on this path: `user_settings` (`target_cv_pages` defaults to the DACH
standard unless `fixture.target_pages` overrides), `fact_pins` (`applications.pinned_facts` stays
null → no pins; cv.py:4161-4204, cover_letter.py:1497-1510 read it), colour profiles, interview
sessions, `profile_snapshots`.

### vault.json — `master_profiles.profile_json`

The shape is `schemas/profile.py::MasterProfileData`; store the column **verbatim as the real
pipeline wrote it** (ids, `status` fields, `_meta`, enrichment history and all — the bench exists to
catch defects that live in exactly those shapes). Fields the generators consume:

| Vault field | Reader |
|---|---|
| `work_experience[]` (`id`, `company`, `role`, `start_date`, `end_date`, `is_current`, `responsibilities`, `achievements`, `technologies`, `team_size`, `budget_managed`, `projects`) | sorted & validated by `WorkEntry` (cv.py:4090-4097); budget cv.py:4111-4125; the id-keyed **vault join** composes the vault's own bullets verbatim into the CV (`_compose_document`, cv.py:4488). Entries need stable ids |
| `personal_info`, `professional_summary` (`de`/`en`), `education`, `certifications`, `languages`, `skills`, `projects` | prompt view (cv.py:4303) and the deterministic join/passthrough (cv.py:171-244) |
| `skills[]`/`languages[]`/`certifications[]` `status` | entries with `status` `unconfirmed` or `denied` are **dropped** before generation (`exclude_unconfirmed`, services/profile/reconcile/stance.py:1066; cv.py:4102, cover_letter.py:1295) |
| `signature_stories[]` | letter positioning (cover_letter.py:1367, services/cover_letter_positioning.py), CV vault-evidence digest |
| `metadata.denied_concepts[]` | STATED LIMITS block for both documents (cv.py:4135, cover_letter.py:1394) |
| `metadata.enrichment_history[]` | letter availability testimony (cover_letter.py:1386) |
| other `metadata` | reduced to the ADR-078 allowlist before any prompt (`prompt_profile_view`, cv.py:4303); the unfiltered copy feeds the deterministic passes and the ledger |

**Vault language** is whatever the fixture needs: `same` = vault and posting in the document
language; `cross` = vault in the other language than the posting; `mixed` = main language plus
LinkedIn-sourced items in the other (set per item); `upward` = same language, posting one level up.

### job.json — `job_analyses` columns

Required: `role_title`, `required_skills`. Everything else is optional and defaults to the model default.

| Key | Reader |
|---|---|
| `role_title`, `company_name` | writer target title & letter recipient company (cv.py:4076 via the application labels, cover_letter.py:1264); also masked as non-claims by the ATS audit (services/ats_audit.py:1242) |
| `required_skills`, `nice_to_have_skills`, `keywords`, `seniority_level`, `company_culture_signals`, `language_requirement` | CV writer `job_dict` (cv.py:4077-4084). **The scorers' U-omit judge reads `required_skills` / `nice_to_have_skills` as the JD's required and preferred items** |
| `jd_language` | `resolve_jd_language` (utils/language_detection.py:104) — the fallback when no `language_override`; defaults to `fixture.document_language` |
| `leadership_emphasis` | `{…}` facet read as `getattr(job, "leadership_emphasis")` for the vault-evidence digest (cv.py:4271, cover_letter.py:1693); `null` falls back to the legacy JD marker check |
| `education_requirement`, `scope_requirements` | stored for completeness; not read by the generators directly (scope material reaches them only through the ledger rows) |
| `source_url` | **never seeded** (any value is ignored): a posting URL makes `_render_cv_background` run company-colour detection (cv.py:4027 → services/color_detection.py:296-331) — favicon/meta-tag fetches over the network and an extra LLM colour-fallback call. A bench run must do neither |
| `posting.md` → `raw_text` | JD excerpt in both prompts (`build_jd_excerpt`, cv.py:4251, cover_letter.py:1671); recipient extraction (`extract_recipient_from_jd`, cover_letter.py:1301); `raw_text_hash` is computed |

### gap.json — `gap_analyses` columns

| Key | Reader |
|---|---|
| `keyword_ledger` (list of ledger rows: `concept`, `surface_forms`, `claimable`, `status` `direct`/`partial`/`gap`/`denied`, `evidence`, `sources`, `fit_weight`, …) | **the load-bearing field.** Drives claimable-vs-forbidden keywords, coverage demands, the vault-evidence digest, the per-role bullet budget (cv.py:4064-4072, 4111-4125; cover_letter.py:1341-1349) |
| `keyword_gaps` (list of str) | CV writer prompt (cv.py:4046) |
| `category_c`, `critical_gaps`, `minor_gaps`, `strengths`, `category_a/b`, `gap_clusters`, `requirement_breakdown`, `match_score` | letter gap-transfer positioning via `askable_gap_inputs` (cover_letter.py:1376, services/gap.py:503); the rest is stored for completeness |

**The pipeline rewrites the ledger before using it.** `refresh_persist_and_rescore`
(services/keyword_ledger.py:2721; called cv.py:4064 and cover_letter.py:1341) re-derives the ledger
against the vault the run was handed — it can lift a still-open `gap` row the vault backs and demote a
`claimable` row the vault does not back. The harness therefore writes the **effective** ledger after
each run to `ledger_effective.json`; scorers that need "what the generator actually saw" read that,
and `gap.json` for "what was frozen". A real fixture's ledger should already be consistent with its
vault (it was produced by gap analysis against that vault).

### Language

`resolve_document_language(application, job)` returns `applications.language_override` if set, else
`job.jd_language`, else detection on `raw_text`. The harness always sets the override from
`fixture.document_language`, then `generate_cv` / `generate_cover_letter` pin the result on their rows
(`generated_cvs.document_language`, `generated_cover_letters.document_language`), and the render
reads the pinned value (cv.py:4023, cover_letter.py:1312). So `document_language` is the single knob
for "which language must the documents be in".

---

## Producing a real fixture (summary)

1. Produce the vault with the real pipeline (import + interview), copy `master_profiles.profile_json` → `vault.json`.
2. Analyse the posting once → copy the `job_analyses` row's columns above → `job.json`; the text → `posting.md`.
3. Run gap analysis once against that vault → copy the `gap_analyses` columns above (incl. `keyword_ledger`) → `gap.json`.
4. Write `fixture.json` (`provenance` names the model and the run that produced steps 1-3).
5. `python3 scripts/quality_bench/fixture.py <dir>` to validate, then
   `PYTHONPATH=backend python3 scripts/quality_bench/run.py --fixture <dir> --arm smoke --provider mock --out <tmp> --runs 1`
   to prove the seed works (mock output is canned and unrelated to the fixture — it proves plumbing only).

`_smoke/same_de/` is a tiny hand-built example of the format; it is **not** part of the corpus.
