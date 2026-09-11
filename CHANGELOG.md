# Changelog

All notable changes to Applire are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- **A narrow "no" no longer swallows a broad "yes" in the gap analysis.** A candidate who
  said they had *never produced directly for food customers* had the job's bare requirement
  *Produktion* published back to them as a critical gap — while the same screen still listed
  it under their strengths — because the denial floor asked whether a requirement is denied
  using every name the analysis has for it, and asked whether the vault affirms it using only
  one. German compounding did the rest: the role they hold, *Produktionsleiter*, is not the
  word *Produktion* at a word boundary. Both questions now run over the same set of names,
  through one instrument used at every place the floor is applied; a denial that names the
  term itself is still absolute. And `strengths` is now filtered against the same analysis's
  own ledger, so one response can no longer call a concept a strength and a critical gap at
  once.
- **A turn that wrote nothing now says why.** The receipt that appears when you say something
  and nothing lands used to read "nothing was recorded from what you said" in all three
  cases. The reconciler now names the reason, and you read the one that fits: *that is
  already in your profile, as stated* · *your question arrived — nothing was changed for it* ·
  or, when the reason is anything else or the model does not answer, the original wording plus
  what to do next. The fail-safe is the wide one, so a model that ignores the question never
  costs you the receipt.
- **`docker-compose.yml` takes its database credentials from the environment.** `${POSTGRES_USER:-applire}` / `${POSTGRES_PASSWORD:-applire}` / `${POSTGRES_DB:-applire}` feed both the postgres service and `DATABASE_URL`. The defaults are today's values, so an install that changes nothing behaves identically; setting real credentials is now a three-line `.env` edit instead of a compose-file edit. PostgreSQL reads them only when the data volume is first created — the runbook says how to change them on an install that already has data.
- **`docker-compose.override.yml` announces itself.** It sets `APPLIRE_TOPOLOGY=dev`, so the backend logs a startup WARNING and `GET /health` reports `"topology": "dev"`. Compose applies that override automatically whenever it sits beside the compose file — i.e. in every source clone — which publishes an unauthenticated API on `:8001` and PostgreSQL on `:5433`, and until now said so nowhere. The production file never sets the variable.
- **While `LLM_DEBUG_LOG` is on, the instance says so** at every startup and on `GET /health`. That log records CV and interview PII and deliberately has no size or age cap: a cap on a diagnostic tool truncates evidence silently, so you are told instead.
- **`:latest` can no longer move on a release whose install assets are missing.** `release.yml` runs the compose install guard as a gate *before* the jobs that publish tags, and in full afterwards. `v0.41.1-beta` was published without its `docker-compose.yml` and `env.example`; the guard fired, but only after `:latest` had already moved, so the documented install 404ed for everyone in the meantime.
- **The reviewer and the corrector are shown the same document, and repetition may now block (#668).**
  In the terminal round the reviewer read the composed CV while the corrector patched the
  prose draft, and the gap ran both ways: the corrector could not see the assembled
  sections a finding named, **and** it could still see bullets the length budget had
  deleted — so a true blocking finding read as false and 2 of 3 corrections never reached
  the document. The corrector now receives the composed document read-only beside its own
  draft; it still returns only the prose shape, so no vault-verbatim field is ever
  LLM-authored. With that closed, `repetition` leaves the minor-by-definition line on both
  CV doors and becomes named check 8, REDUNDANCY. Replay of the captured exchange that
  shipped the six near-duplicate bullets, n=5 per arm: blocking redundancy findings 0/5 →
  4/5, corrector emitted a `projects` array 0/5 → 5/5, delivered near-duplicate pairs
  2.0 → 1.0. **Halved, not cleared — #659's acceptance criterion is not claimed.** The
  cover letter is deliberately unchanged (it ships with #664).
- **The per-role bullet cap yields to a bullet the round's review loop asked for (#666, ADR-072 clause 4).**
  The under-claim signal asked for a bullet and the cap deleted it inside the same round,
  so no retry budget could help — 1 of 4 demanded bullets survived on the 2026-09-05
  delivery run. A demanded bullet is now outside the cap's reach (a partition, not a
  ranking tier, which a tight ceiling silently defeats) and the cap takes an unrequested
  bullet instead; where the ceiling still binds it yields under a
  `BUDGET_VS_SIGNAL_CONFLICT` warning naming both producers. Coverage for a demanded
  concept is read over the bullet corpus, not the whole document, because a skills tag is
  what the demand said was insufficient. Measured at the delivery point on the captured
  run: implementation fraction 2 of 4 → 3 of 4 on identical inputs, with the per-role
  ceiling intact in both arms.
- **The loop stops asking for a bullet the document already delivers (#666).**
  Honouring every demand bought the bullet "Deutsch als Muttersprache." at the price of a
  quantified safety figure and a budget bullet, on a CV whose Languages section already
  said it. A concept the composed document carries in a vault-joined structured section —
  languages, certifications, education — is delivered, not under-claimed. The skills list
  is deliberately not in that set.
- **Reading the keyword ledger now fixes and re-scores it (#670, ADR-048).**
  All four document-facing reads persist the refreshed row and re-source the match score,
  so a generated document, the Gaps screen and the score cannot disagree. ADR-061's
  affirmative invariant runs at a read for the first time, which catches a profile that
  **shrank** since the analysis before the document is written. **A score you have already
  been shown may move, down as well as up** — the monotonic-up clamp that applies where
  evidence can only be added is deliberately not applied here.
- **The vault-evidence digest offers every sense of a concept, not the wordiest one (#415).**
  For each claimable concept the digest picked "the longest matching vault sentence", and
  on a coarse concept longest is not most specific: for `HGB` it picked the *Monatsabschluss*
  sentence over "Betreuung der Wirtschaftsprüfer im **Jahresabschluss** (HGB)" — 134
  characters against 57 — and the delivered CV contained neither `Jahresabschluss` nor
  `Wirtschaftsprüfer` while the blind reviewer scored the role's first-named duty only
  partly met. Choosing between two qualifying sentences is a judgement, so the selector
  stops choosing: it offers up to three and the writer decides. The CV chain's digest
  ceiling rises with it, because widening per concept under the old ceiling buys the
  answering sentence by starving the others. Measured on the captured run: the answering
  sentence absent → present, represented concepts 8 → 9, +3.6 % on that writer prompt.
- **Fact pins, said plainly (#680).** The pin control changed what it says, not what it does. On the gaps page it is now a teaser card directly above the decision buttons ("Gibt es Fakten, die unbedingt in deinen Dokumenten stehen müssen?" · *Fakten festlegen*) instead of a "(0/10)" panel inside the job-ad block; the panel's title is the promise (*Muss in diesem Dokument stehen*), the counter appears only once a pin exists, a collapsed *Wie funktioniert das?* carries the explanation, each quote shows the profile entry it comes from, and target and fate are one chip (*Lebenslauf · enthalten*). The picker asks plain questions and skips the statement step for single-statement entries. A first-use explainer (*Bevor du Fakten festlegst*) with *Nicht mehr anzeigen* precedes the first pin. German says *festlegen* everywhere; *Vault* left the user-facing copy in both languages. Two incidental fixes: the at-cap tooltip rendered next-intl's error fallback, and the picker repeated a skill's name as its statement.

- **The reconciler stops losing an answer that opens with a denial, and stops crediting one employer's work to another (US311 step 3).** Three changes to the one prompt that writes your vault, each measured before and after on three models. Rule 9 said *"an answer that does not address the question contributes nothing about the question's topic"* and stated its own empty-output escape a second time; a model reading *"I have not worked with insulin in particular, but I have 15+ years at A, at B and now at C"* had two explicit instructions telling it to record nothing, and eight of eleven models measured took one of them. It now says a denial is about its own item and nothing else, and the escape is stated once. Rule 1 gained the missing instruction for a sentence naming several employers — bind each clause to the employer that clause names, never fold the whole sentence onto the one your profile happens to hold — which is the failure that would have credited fifteen years of work at two other companies to your current job. And every operation now states its required fields beside itself, including that they are required when merging into an entry that already exists. Measured on the harness (n=10 per shape, three input shapes): `z-ai/glm-5.3-flash` went from sub-par to qualified on the first change alone, and spent 48 % fewer output tokens doing it; `mistralai/ministral-8b-2512` went from sub-par to usable-with-caveats on the second. The prompt now carries a version header recording each rule change with the number it moved.
- **A reasoning model's thinking is never mistaken for its answer.** Some models emit a chain of thought inline, in front of the JSON they were asked for. That made the parse fail, and a failed parse on the vault path was swallowed as "nothing to merge" — your answer gone, with nothing on screen to say so. Every provider now strips the trace before parsing, on one shared implementation; Anthropic's thinking blocks and Ollama's `thinking` field route to the same place. The trace is never stored and never fed back into a later turn: it reaches only the developer debug log, which is off by default. A call that bills output tokens and returns no answer at all — a model that spent its whole budget thinking — is now reported as a truncation and retried once, instead of looking like a model that chose to say nothing.
- **`OPENROUTER_DISABLE_THINKING` stops costing you a wasted request on every call.** Some models refuse to have reasoning turned off and reject the request with an error; Applire has always retried correctly, but it re-sent the doomed parameter on every subsequent call. It now remembers the refusal for the life of the process, as the Requesty provider already did. If you run such a model — `z-ai/glm-5.3-flash` is one — the setting saved you nothing before and cost you latency and a higher chance of timing out; now it costs one wasted request per restart. The setting is a request the model may refuse, not a guarantee.

### Added
- **The instance says what an upgrade changed, and what you can configure (#687, US310).** Every environment variable the backend reads is declared once in `backend/applire/settings_registry.py` — the 39 typed settings, the 29 that `constants.py` read directly (all five GDPR retention TTLs among them, which `.env.example` never mentioned), and the deployment variables — each with its default, the release it appeared in, and the release its *meaning* changed in. `.env.example` is now **generated** from that registry and a unit test fails when the two drift, so the shipped template can no longer contradict the code the way `MISTRAL_MODEL` did. A second test asserts declared == read in both directions: a new variable costs a registry entry or the suite is red. After an upgrade the backend compares the release that last ran against the running one and names two things — settings introduced since then that your environment does not set, and settings you *do* set whose meaning changed — as a WARNING block on the log, as `upgrade_notice` on `GET /health`, and as a dismissable notice on the dashboard. Dismissing records the running version as seen. New table `instance_state` (Alembic 0062) holds those facts about the installation. `GET /health` also gains `debug_log_on` and `topology`; its four original fields are unchanged, because the compose healthcheck and every uptime probe read them.
- **Two silent losses in the vault now leave a receipt (US311 step 3).** A turn where you said something and nothing was recorded — the answer states a fact, and the merge produced no change, no question, no conflict and no other receipt — is recorded as such and shows up on your profile's health hub, instead of the interview simply reading as "nothing changed". And a bullet whose own words name a different employer than the entry it is being attached to is now turned into a question rather than written: the attribution guard reads the bullet's own text as well as the sentence it came from, and knows about employers this same answer is creating, not only the ones already in your profile.
- **`LLM_STRUCTURED_OUTPUT` — your model is handed the vault's operation vocabulary as a schema, not only as prose. On by default (`auto`).** The one call that writes your profile now carries a JSON schema of the 15 operations it may emit, generated from the same types that validate the answer, so the two cannot drift; it shows exactly what the prompt asks for and nothing more. Measured on the same fixtures as the model table: one model's station coverage on a one-employer answer went from 0.10 to 1.00 and its malformed-operation rate halved, with no regression on the two models that were already clean. It costs about 2,300 extra input tokens per interview turn — set `LLM_STRUCTURED_OUTPUT=off` to save them. An endpoint that does not support schemas rejects it once, Applire falls back to plain JSON mode for the rest of that process, and the turn it happens on still completes.
- **Backup and restore, documented and scripted (US314).** New [`docs/SELF-HOSTING.md`](docs/SELF-HOSTING.md): the two compose topologies and how to tell them apart, backup, verify, restore, secrets, TLS, disk and pruning, upgrading, and troubleshooting. `scripts/backup.sh` archives the database **and** the `applire_uploads` volume in one file — a backup with only one of them is not a backup — records the timestamp in `instance_state`, and `--verify` checks an archive without restoring it (both volumes present, `pg_restore --list` runs, size > 0). `scripts/restore.sh` verifies the archive first, refuses a database that already has tables unless you pass `--force`, and never runs `down -v`. The restore was exercised once end to end on the production compose topology before this shipped. The `down -v` warning now stands where operators actually look: both READMEs' update section, the runbook, the `docker-compose.yml` header and `docs/CI_CD_GUIDE.md`.
- **The instance says whether it is healthy, and what it costs (E060 / US312 #145, US313; ADR-086).**
  A self-hosted Applire had no operator-side monitoring at all: `GET /health` returned four static
  fields and answered "ok" with Postgres gone, the GDPR retention worker printed a JSON report to
  stdout that nothing read, a provider 402 was once misdiagnosed as latency for hours, and no token
  was ever counted. New: **`GET /api/ops/health`** aggregates seven probes — database, migration
  head, retention last-run age plus a deletion-count anomaly check, free disk, last backup age,
  provider reachability and credit, and a rolling error count — behind one verdict, with the verdict
  in the HTTP status (200 ok/degraded, 503 down) so an external uptime probe can alert without
  parsing. `GET /health` keeps its four fields byte-for-byte and gains a cached `ops` summary. The
  retention worker now writes a `retention_runs` row per run (the same JSON it still prints), which
  is what makes "the worker has not run for 51 hours" visible at all. Every provider call records its
  token counts in `llm_usage` — numbers and ids only, never prompt or answer text, kept 365 days —
  aggregated per day, per document and per application; counts a provider does not report are
  estimated and **labelled** as estimated. The admin page shows the same facts as one quiet line
  while the instance is fine. The provider check is configurable
  (`OPS_PROVIDER_PROBE` = `off` / `reachability` / `credit` / `both`) because a credit check makes no
  sense for a local Ollama. Alembic 0063.
- **A first-use explainer can be dismissed for good, and the mechanism is general (#679).** `user_settings` gains `dismissed_explainers`, a set of explainer ids the user has turned off with *Nicht mehr anzeigen*, served on `GET /api/settings` and written additively with `PATCH {dismiss_explainer}` against a server-side allowlist (unknown id → 422; Alembic 0061). The first entry is the fact-pin explainer; the next explainer costs an allowlist entry rather than a migration. `hide_predownload_notice` is unchanged, and no setting is exposed over MCP.

### Removed
- **`NGINX_PROXY_TIMEOUT` is gone from the documentation, because it was never read by anything.** Both READMEs and the old env template offered it; the reverse proxy's `proxy_read_timeout` is 300 s and is baked into the `applire-nginx` image. Keep `LLM_TIMEOUT` below 300, or bind-mount your own nginx config.

### Upgrade notes

New and re-meant environment variables in this release. Nothing here requires action on an existing install — every default reproduces current behaviour.

| Variable | Code default | What it does | Required? |
|---|---|---|---|
| `POSTGRES_USER` | `applire` | Database user; the compose file feeds it to postgres and to `DATABASE_URL`. | optional |
| `POSTGRES_PASSWORD` | `applire` | Database password. Set it on any host where the database port could be reachable. PostgreSQL reads it only when the data volume is first created — see `docs/SELF-HOSTING.md` §Secrets before changing it on an existing install. | optional |
| `POSTGRES_DB` | `applire` | Database name. | optional |
| `NOTICE_AUTO_DISMISS_SECONDS` | `30` | Seconds before an unattended in-app notice pop-up hides itself; `0` = never. Instance-wide, served read-only on `GET /api/settings` as `notice_auto_dismiss_seconds`. | optional |
| `APPLIRE_TOPOLOGY` | `production` | Which compose topology this instance runs. Set by `docker-compose.override.yml` to `dev`, never by hand; surfaced as a startup WARNING and on `GET /health`. | do not set |
| *(WP-O1's `LLM_USAGE_RETENTION_DAYS` and `OPS_*` variables are added here at integration — placeholder, replace with O1's report patch)* | | | |
| `LLM_STRUCTURED_OUTPUT` | `auto` | **On by default.** `auto` sends your model the reconciler's operation vocabulary as a JSON schema alongside the prompt, on the one call that writes your vault. It costs about 2,300 extra input tokens per interview turn. An endpoint without schema support rejects it once and Applire falls back to plain JSON mode for that process; the turn still completes. Set `off` to save the tokens. | optional |

**Re-meant since your last release** — these keep their names and no longer mean what they did:

| Variable | Changed in | What changed |
|---|---|---|
| `INTERVIEW_MAX_QUESTIONS_TARGETED` | 0.41.0 | It is now a **cap** applied on top of a budget derived from the session's own gap plan, not the budget itself (ADR-080). Setting it below the derived budget truncates interviews on gap-rich jobs. This shipped in v0.41.0-beta without a changelog line; it is recorded here so the instance's own upgrade notice and this file agree. |
| `INTERVIEW_MAX_QUESTIONS_GUIDED` | 0.41.0 | Same change, for guided (MODE B) interviews. |

**Not re-meant, but worth knowing** — `OPENROUTER_DISABLE_THINKING` and `REQUESTY_DISABLE_THINKING` keep their names, their `false` defaults and their meaning. What changed is the honest description: they are a *request* the model may refuse. On a model that mandates reasoning the setting saves nothing, and until this release it cost a rejected request on every single call. `LLM_MAX_OUTPUT_TOKENS` likewise keeps its meaning — but on a reasoning model, note that it caps the budget the answer and the thinking *share*, so setting it low on such a model can leave no room for the answer.


## [0.41.1-beta] – 2026-09-06

The Stracciatella release, published as this patch on top of the unpublished
v0.41.0-beta candidate (everything in that entry ships here). Three founder rulings
of 2026-09-05 land as code: ADR-076 clause 6's rank gate engages on the CV drafting
loop, the agent door's provenance mark says what Applire can attest, and the mark no
longer names the operator's model provider.

### Changed
- **The CV coverage rank gate now engages on the drafting loop (ADR-076 clause 6).**
  `cv_coverage_budget`'s occupancy measure read `work_history`, but the two reviewer
  chains that review the writer's PROSE draft — `cv_tailoring` (the drafting loop)
  and `cv_language` — hand it a draft whose work list is named `work`. It therefore
  measured 0 bullets on every round of both loops, `under_pressure` was permanently
  false, and no below-rank coverage demand was ever withheld; only
  `cv_terminal_review`, which reviews the composed document, ever saw a real
  occupancy. The measure now goes through the same prose/composed adapter the
  under-claim signal uses, moved to the module that owns the narrative-corpus rule
  so both readers share one definition of narrative space. Measured on the
  2026-09-05 delivery run: 31 of 31 captured drafting-loop rounds measured 0 before
  and their true bullet count after.
- **A document rendered through the agent door is marked composite (ADR-085).**
  A CV or cover letter whose content the caller's own agent supplied verbatim
  through the BYOI door now carries `DigitalSourceType =
  compositeWithTrainedAlgorithmicMedia` — Applire rendered it and cannot attest its
  authorship. The mark is derived from the persisted row's `origin`, so it is
  correct on the PDF and on the `.docx`, on the first download and on every later
  one. Documents Applire's own writer produced are unchanged.

### Removed
- **`modelProvider` is no longer part of the provenance mark (ADR-085).** The XMP
  packet's `applireAI:modelProvider`, the PDF Info dictionary's `/AIModelProvider`
  and the `.docx` `AIModelProvider` custom property are gone. The mark carries the
  generator, its version, the timestamp, the `DigitalSourceType` and the marking
  spec — which is what Art. 50(2) asks for; which model vendor produced the text is
  the deployer's business, not the document's.

## [0.41.0-beta] – 2026-09-05

The Stracciatella release candidate: the document gets the screen and the
findings get the user's questions, the job posting is treated as data, every
delivered document declares its provenance, and the review loop learns to say
what it could not finish. Seven work packages, one database migration (0060).

> **Completed 2026-09-09.** As first written, this entry covered only the release
> candidate's seven work packages (PR #663). Roughly ten pull requests merged
> between 2026-08-31 and 2026-09-04 shipped inside the same tag and were in no
> entry at all — including the Word export, which is a headline feature. They are
> folded into the sections below and marked *(backfilled)*. Found while writing the
> release announcement, by reading the commit range instead of the changelog.

### Added
- **The document review surface (E058)** — the generated document is the largest
  thing on the screen; what the system found about it renders as four questions in
  a fixed order (in the document but not in my profile · missing although my
  profile covers it · missing and not covered · is the craft sound) headed by one
  verdict sentence whose number is the count of rows actually rendered. Two
  reading modes over one data model, `overview` and `guided`, with `auto`
  following the document (guided while it still has unwalked findings, keyed on
  the generated-document id). Group 2 names the length trade and the three
  existing handles instead of offering a fix that would lie. A producer that did
  not run reads as *unknown*, never as zero (US299–US302; ADR-081, amended;
  migration 0060 `user_settings.review_mode`).
- **Untrusted job-posting text is data, not instructions (ADR-084)** — one
  boundary helper marks the posting and every posting-derived string at all 33
  points where they enter a prompt, in two forms (a fence for contiguous
  third-party text, a provenance note for blocks that interleave our own rules
  with posting terms the writer must reproduce verbatim); MCP tool results that
  hand posting-derived text to the user's agent carry an `untrusted_content`
  marker; a 12-posting injection corpus with benign twins is the measurable
  control in the real-provider tier (#445, #446, #447).
- **Every rendered document says, machine-readably, that AI made it (ADR-085,
  EU AI Act Art. 50(2), in force 2026-12-02)** — an indirect XMP packet (IPTC
  `DigitalSourceType = trainedAlgorithmicMedia` plus a documented `applireAI`
  namespace) and Info-dictionary duplicates on every PDF, applied at the single
  render seam below all templates, both languages and both doors; the `.docx`
  export carries the same claim in OOXML custom properties and finally a Title.
  The mark records the generation event and nothing else; downstream
  re-processing strips it, which is documented and pinned rather than hidden
  (#577).
- **The review reports whether the review finished** — a `terminal-review` check
  on the CV and cover-letter reports carries the terminal review's own outcome
  (approved / exhausted / cycle) and its unresolved findings, carried forward
  across re-audits so an edit cannot launder an exhausted review; a
  `narrative-evidence` check on the CV names claimable, JD-relevant concepts the
  document carries only as a skills tag (#563, #542; ADR-039 amended).
- **Under-claiming becomes a bound signal (ADR-076 clause 5)** — a claimable
  concept present in the vault and absent from the CV's narrative enters the
  terminal review round as an issue the corrector must act on (bounded to the two
  highest-weighted, satisfied only by a bullet or sentence, never by a tag), riding
  the ADR-083 transport; exhaustion disposition ship-and-report (#542).
- **The terminal review asks the whole-document questions (ADR-076 clause 9)** —
  named checks for claim balance and voice (mechanical uniformity as the tell),
  visibility-only, one finding per document; a reviewer replay went from 0/5 to
  5/5 on the blind panel's own tells with zero findings on a clean document (#545).
- **Applire installs on a phone and receives a shared posting (E040, US229)** —
  a web-app manifest, icons and a service worker that caches the app shell and
  nothing the user owns (`/api/` and `/static/` are never cached, so no Cache
  Storage entry can outlive the retention TTLs); an Android `share_target` and a
  `?jd_url=` / `?jd_text=` deep link prefill the Quick Tailor intake — prefill
  only, analysis stays user-triggered. A posting URL shared from Chrome or
  LinkedIn arrives inside `text`, and is recognised there (ADR-050 amended).
- **The remaining pages work at 390 px (E040, US230)** — profile hub, My Documents
  and the application dossier stack single-column; the mobile lane's overflow
  predicate was rewritten so it can fire (the old body-scroll assertion could not,
  and had hidden a squeezed language chip and a clipped Expires column). The CV
  page's real editing surface opens and saves on a phone, and a sentence that
  told mobile users to use a computer — which also pushed Save behind the
  keyboard — is gone (US228, edit/save half; reorder is not claimed).
- **The Word export — an editable working copy of every document (E057)**
  *(backfilled)* — CVs and cover letters download as `.docx` alongside the PDF,
  rendered by a dedicated `office_export` package straight from the tailored data:
  no HTML, no template engine, no subprocess, and no tables, text boxes, positioned
  frames or borders, verified by direct XML inspection. The layout is built for
  editing rather than for reproducing the seven PDF designs — the measurement
  behind that choice is that automated checks could not separate the two designs
  that survive conversion from the five that do not, only looking could. Both
  files carry the same truthfulness and ATS audit, and the download states that
  edits made afterwards are not re-checked (US296–US298; ADR-079; #642).
- **A GitHub Sponsors link and a "Support the project" section** in both READMEs
  *(backfilled)* — the first funding surface the project has had (#572).

### Changed
- **The DO-NOT-CLAIM list can no longer contradict the profile beside it** — the
  persisted Keyword Ledger is re-derived against the current vault at every
  generation and report read, so a term the vault has learned since the analysis
  is not forbidden to the writer and enforced against the candidate's own words
  (#592; ADR-048 amended). A coverage demand now names the position that owns the
  evidence, so a corrector cannot weld a term onto the wrong employer (#525).
- **The cover letter names the employer once per paragraph, not once per
  sentence** — the position-anchoring rule in the writer, the shared reviewer
  check, the corrector and the condense prompt is rescoped from "every sentence
  carrying a figure" to "the first sentence of an employer run in a paragraph";
  a false justification (a guard that has not dropped unanchored figures since
  #299) is removed. Replay: the old reviewer door demanded per-sentence anchors
  5/5, the new door 0/5, while an unanchored paragraph is still caught 4/5 (#565;
  ADR-021 amended).
- **A limit the candidate stated in their own words is now an obligation, not a
  permission (ADR-075, in force)** — the writer receives the denied concepts as
  its own block, the reviewer has a named check for a missing disclosure, and the
  positioning inputs carry it; replay on the run that shipped silent: disclosure
  2/5 → 4/5 (#532).
- **A cover-letter section override other than  is rejected at the door
  with 422** instead of being accepted and silently dropped at render (#641), and
  the letter's identity re-entry now carries the fact pins it used to lose (#601).
- **The cover-letter dialog prefills the recipient company** from the job
  analysis, and the upload dropzone lists what the backend actually accepts (#604).
- **Vault housekeeping** — a targeted `set_field` on an existing entry is no longer
  reported as a lost import; schema-rejected reconcile ops are logged at WARNING
  with their type; `skills[].last_used` survives a merge import; an ALL-CAPS
  employer name from a layout source yields to a mixed-case rendering; a mismatching
  incoming scalar in personal info raises a conflict instead of vanishing (#602,
  #620).
- **The mock provider now recognises the skill-estimation prompt**, so that seam is
  exercised by the mock-stack E2E tiers; an enumeration test fails when any
  `*SYSTEM*` prompt lacks a fingerprint (#658).
- **The gap interview's question budget is derived from its own gap plan
  (ADR-080)** *(backfilled)* — the budget was the flat constant 12 while an
  analysis produces 5–12 gap clusters costing one to two answers each, so a
  targeted interview stopped with clusters it had identified itself never asked,
  and those became honest gaps that both writers then read. It is now
  `INTERVIEW_MAX_QUESTIONS_PER_GAP × critical gaps + 2`, with the old constants
  demoted to an upper bound (defaults 12/20 → 30/30). Measured on the real loop
  before the change: 8 clusters → 6 closed / 2 never asked; 12 → 6 closed / 6
  never asked (#646, #648).
- **The gaps page no longer promises three minutes** *(backfilled)* — "Quick
  Interview (3 min)" was a fixed string connected to nothing, while a 12-cluster
  posting runs to a worst case of 26 questions; it is now "Full Interview" with no
  minutes claim, and progress counts against the session's derived budget. The
  string was the credited time-estimate control on Marcus's journey FMEA row
  JF-M-5.5, which a wrong figure actively harms (#649).
- **Prose redundancy in the delivered document is detected, not manufactured
  (ADR-082)** *(backfilled)* — #659 and #424 both proposed widening
  `skills_near_dupe` from skills to bullets; measured on the delivered artefact it
  returns False on all 15 bullet pairs, because a threshold calibrated for
  2–5-token names does not transfer to 30-token sentences. A prose-shaped
  detector was built instead, reporting only — repair is deliberately out of
  scope (#661).
- **A reviewer's blocking findings reach the corrector that acts on them
  (ADR-083)** *(backfilled)* — the transport folds them into corrector feedback
  rather than leaving the corrector to re-derive them; the neighbouring doctrine
  question (role framing vs. a named check) was measured at 0/5 against 5/5 and
  settled in favour of the named check (#662).
- **The data destination is the operator's choice, not a project rule**
  *(backfilled)* — `.env.example` now states per provider where data goes (Ollama
  local, Mistral/Requesty EU, OpenRouter/Anthropic/OpenAI US) and marks its own
  value an example rather than a recommendation (#657).
- **A document's PDF title follows the document's language**, and My Documents
  says so when a document failed instead of showing nothing *(backfilled)*
  (#604, #647).

### Measured, not changed
- ADR-076 clause 6's rank gate never fired on the CV drafting loop (the coverage
  measure reads `work_history` while the writer's draft says `work`); the
  reconcile rule ONE CONTAINER held 14/14 on a real provider; near-duplicate
  re-adding was confirmed in a production log but not reproduced by replay (0/16);
  JD extraction stability after PR #623 still varies across identical runs,
  including the language its terms are rendered in — all recorded as collector
  lines and FMEA rows, none silently fixed.

### Fixed
- The `.docx` export identifies itself: a language-correct Title in the document
  properties (#601).
- The documented Ollama path said nothing about pulling a model first and nothing
  about the CPU-inference timeout; both READMEs and `.env.example` now do (found by
  the pre-release blind install test).
- **A tabular Word CV imported as an empty one** *(backfilled)* — the extractor
  read `document.paragraphs` only, and Word table cells are not paragraphs of the
  body, so the layout standard across German-speaking Europe lost its entire
  employment and education history before the extraction model was called; the
  headings survived, so the text looked structured while carrying nothing. Text
  boxes, used for contact blocks and side columns, were invisible for the same
  reason. It now delegates to the document-order walker built for E057, one
  implementation for one operation. Measured on three real Word CVs: a
  paragraph-only CV byte-identical, a tabular CV 0/10 → 10/10 facts, a tab-stop CV
  unchanged bar one character class (#640, #643).
- **Angle brackets were silently deleted from the delivered document**
  *(backfilled)* — `select_autoescape(["html"])` decides per filename suffix and
  every Applire template is `*.html.j2`, so autoescape never fired on a single
  one. Free text containing angle brackets was emitted verbatim and swallowed by
  Chromium as an unknown tag: "Koordination mit &lt;Projekt Phoenix&gt; und
  R&D-Teams" shipped as "Koordination mit und R&D-Teams" — still grammatical,
  which is why it went unnoticed. Closed at the cause and at detection, with the
  ATS audit extended to the candidate's own prose (#634; ADR-039 amended, #636).
- **#547's orphan case, measured rather than assumed** *(backfilled)* — a
  per-template real-render sweep with a calibrated regression gate; the driver is
  each template's own page-1 capacity, not the shared signature margin, and two
  rendered hypotheses were refuted rather than shipped as dead CSS (#636).
- **An abandoned Gap-Click micro-session was resurrected by the next interview
  start** *(backfilled)* — `create_session`'s idempotency branch returned any
  active session for a job regardless of kind, so a micro-session closed without
  answering came back in place of a full interview. One predicate now separates
  the two (#627).
- **Merge conflicts named the field but not the entry** *(backfilled)* — the
  entity→label ladder moved out of the Health hub into one shared resolver, so the
  live interview's conflict card and the Health hub speak one convention; the
  Health-hub conflict card had no E2E coverage at all and now has one (#626,
  #604, #644, #647).
- **Vault reconcile gaps** *(backfilled)* — `flag_conflict` reaches all nine
  id-bearing sections, `set_field` no longer vanishes silently on some of them,
  certifications and education use their section-aware duplicate classifiers
  instead of the generic one on two-source imports, and an op batch writes once
  per entity with one container per project (#633, #618, #424, #632, #644).
- **The import doors disagreed about which fields exist** *(backfilled)* — the
  flat and MCP door schemas gained the 17+1 fields the CV extractor already
  produced, and the profile-extraction door gained projects, publications and
  volunteer activities; a null project role no longer rejects an import, found by
  the first real run through the flat door (#228, #619).
- **LinkedIn reached the delivered document again** in the contact block
  *(backfilled)* (#644).
- **A local image rebuild could bake user data into the image** *(backfilled)* —
  no `.dockerignore` existed anywhere, while the backend Dockerfile does
  `COPY . .` and the tracked dev override bind-mounts `./backend:/app`, so the
  running stack wrote its LLM debug log and uploaded CVs straight into its own
  build context. On a machine that had served real traffic that is prompts and
  model output plus uploaded CVs, going into the next image. Images published from
  CI were never affected — verified, not assumed: the `:edge` backend image has no
  `/app/logs` and no `/app/data` (#660).
- **Dependency updates** *(backfilled)* — pypdf 6.16.1 (#650), browserslist
  (#645), brace-expansion (#631).

## [0.40.0-beta] – 2026-08-30

An interim release inside Stracciatella: the vault gets structured editors
instead of a JSON field, the user gets the language and the facts of their
documents in hand, and three defect classes that hit every submitted application
are closed. 19 commits, two database migrations (0057, 0058).

### Added
- **The document language is a decision, not a detection (E054)** — a DE/EN
  control on the gap view governs the language of every document generated for an
  application; each document pins its own language, so switching later never
  repaints an existing CV or letter. After generation the language can be
  switched, and every document carries a language badge. Agent channel:
  `update_application(language_override=…)`, and `analyze_jd` reports the detected
  `jd_language` (US288, US289; ADR-038 amendment; migration 0057).
- **Structured profile editors replace the text field (E055)** — work experience
  and education are edited in their own forms, skills, languages and
  certifications through chip editors, projects through a write door of their own.
  The JSON textarea is retired. An edit made on stale data can be refused instead
  of silently overwriting (US290–US293; ADR-063 amended).
- **Fact pins (E056)** — the user can mark a fact as required for one
  application; pinned facts keep their place in the document's budget and are
  attributed as such in the review report (US294, US295; ADR-077; migration 0058).

### Changed
- **The CV reviewer respects pinned facts** — a backstop with a precedence rule
  and attribution stops the review loop from working a pin back out (#580;
  ADR-077 amended).
- **The model sees the vault's content, not its bookkeeping** — a dedicated
  prompt-facing profile view replaces the raw dump: −89.4 % prompt volume on a
  real profile, with no loss of content (#593; ADR-078).
- **Team-size statements mean what they say** — a managed span is no longer
  inferred upward from an aside (#562).
- **The import says what it did not apply** — every import returns a merge status
  and a list of what was not applied, on both doors (#615; ADR-063 amended).
- **The install instructions and the running image agree again** — the documented
  commands fetch `docker-compose.yml` and `env.example` from the newest release
  rather than from `main`, which can run a whole flavour ahead of it — including
  the quick-start comment inside the shipped `docker-compose.yml` itself, which
  still pointed readers back at `main`. A post-publish check fails if either file
  is missing from the release.

### Fixed
- **Importing a second source silently lost whole sections** — the cause was in
  the prompt: the extraction's ids read as "an entity that already exists" at the
  reconcile stage, and 10 of 11 merges lost content with them, 0 of 5 without. The
  prompt's input view no longer carries those fields (#615).
- **Cover letters without a salutation** — the salutation is now a control of its
  own on both doors, alongside the rule in the prompt (#564).
- **Continuation pages had no margin** — the visual margin now lives in `@page`,
  which Chromium re-applies on every page; text on page 2 previously began at
  0.5 mm. Page 1 of the letter keeps its capacity through `@page :first` (#621,
  all 14 templates).
- **Page breaks cut entries in half** — the break policy now works over
  keep-together groups instead of `break-before/after: avoid`, which was measured
  to have no effect here (#622).
- **Cover letters grew back to two pages after being condensed** — a final length
  floor now sits between the terminal loop and the identity gate, measuring the
  delivered version rather than the one repaired in between (#547).
- **The job analysis eroded its own requirements** — 5 of 21 survived, because the
  reviewer discarded verbatim-grounded terms as fabricated. It is now handed the
  grounded terms as facts (#617).
- **Vault receipts** — a revert makes the receipt fail rather than pass silently;
  an empty string clears like a missing value (#597, #595).
- **Partially covered testimony** — a statement only partly applied is reported as
  such instead of being dropped (#370, #371).
- **Academic cover letters** were too thin and are densified (#431).
- **Rule 7 addressed the wrong target** in the cover-letter review (#391).

### Security
- `next` to 15.5.24, `sharp` to 0.35.4, `postcss` to 8.5.26 — closing eight
  reported advisories on the web surface and one in the image processing of
  uploaded photos. Also `pypdf` 6.15.0, `nanoid` 3.3.18, `js-yaml` 4.3.1.

## [0.39.0-beta] – 2026-08-19

The Tiramisu closing release: truthfulness becomes architecture. One write path
into the vault, one review loop over the composed document, and documents that
earn blind hiring-panel invites — the release gate ran all four canonical
panel cases end-to-end (8/8 invite decisions, every designed denial recorded
and never claimed). 93 commits, five database migrations (0052–0056).

### Added
- **One vault write path (`commit_ops`)** — every writer (interview, import,
  section editor, conflict/confirmation resolution, role lifecycle, metadata,
  first-profile creation) routes through a single committed write seam with
  per-entry receipts; the strict clause-6 guard deleted the twentieth ad-hoc
  writer (#480, PRs 1–9; ADR-063).
- **Terminal review over the composed document** — the CV and cover-letter
  verdicts now close over the document as assembled, not the draft before
  composition; ship-and-report bounds re-entry (ADR-076 waves 1–3; #537, #538,
  #539, #540, #543).
- **Compliance instrument on the real corpus** — measurement reach grew from
  2% to 58%/73% per class; the fallback fires on every early-settle path
  (#537, #540, #551, #553).
- **Outcome critic on both mounts** — a persisted critic report for the
  assembled CV and letter, advisory-only, with dropped-citation accounting
  (E049 49.6/49.7; migrations 0052/0053).
- **The attested partial is deliverable** — an honestly bounded partial
  (assisted-not-independent, in-progress) reaches documents as data instead of
  being dropped (ADR-070, #411).
- **A JD's stated bar is data** — scope requirements, leadership-vs-hands-on
  weighting, decomposition-never-demotion (ADR-069, #271, #387, #397;
  migrations 0054/0056).
- **The interview can produce a partial match** — denial chips carry mirrored
  evidence conditions; the denial floor is reachable on the agent door
  (ADR-064, #341–#344, #347).

### Changed
- **Presence is a fact the letter reviewer is told, not asked** — blocking
  demands dropped 70→52, false demands 10→4, false presence 2→0 (ADR-021
  amendment; #530, #531, #534, #535).
- **CV prose custody** — the writer emits prose only; facts are joined
  deterministically, and the deterministic tail may never silently delete
  evidence (E049 / ADR-067, ADR-071, ADR-072).
- **Test strategy** — a replay tier, mutation-verified detection credit, and a
  coverage gate that measures every tree CI actually invokes (ADR-073, #438,
  #444).
- German UI pinned to Du; review rationale keys localized de+en (#311, #523).

### Fixed
- The review loop's unsatisfiable cell, closed at its cause (#526) — and the
  corrector is told the coverage it already holds (#306).
- A denied requirement is never a gap at the read seams; denying a compound is
  not denying its head noun; a retraction demotes a confirmed skill and
  reverses upgrades on both doors (#351, #352, #383, #485).
- Oracle precision: durations and date fragments are not figures; currency
  multipliers fold numerically; stated tenure may not exceed the vault's
  derivable span; sentence triage gates letter grading (#214, #215, #220,
  #309, #373, #403, #469).
- CV rendering: position blocks stay atomic across page breaks; projects with
  no bullets never render a heading; no duplicate project blocks or bilingual
  language rows; skills passes stop reshaping the page (#312, #357, #386,
  charter-run-11 batch).
- Profile/vault: parked confirmations survive the next import; every
  confirmation a turn raises is asked; `_meta.na_fields` survives schema
  round-trips; unrecognised declared proficiency falls to basic (#319, #333,
  #353, #505).
- Interview: retry follow-up hints name the gap, not its cluster id; denial
  statements are write-once and recorded on every turn (#301, #348, #380).
- Education entries with unknown start dates render without a dangling dash;
  language-field seam and silent summary replacement fixed (#113 partial,
  #461).

## [0.38.0-beta] – 2026-07-28

Two development cycles in one release — **Spaghettieis** (the returning-user
journey: portfolio, mobile, application lifecycle) and **Tiramisu**
(truthfulness and the agent channel). This is the largest release since the
project opened: 295 commits, nine database migrations, and the completion of
the bring-your-own-agent surface.

The theme running through it is that Applire now **checks its own output**. The
Truthfulness Oracle grades every generated document against the vault before it
is delivered and reports what it cannot ground; the keyword ledger records what
the candidate has explicitly said they *cannot* claim and treats that as a
floor no generator may cross. Where earlier releases tried to write well, this
one tries to be checkable.

Migrations run automatically on backend startup — self-hosters update with
`docker compose pull && docker compose up -d`. No `.env` changes are required.

**Two things to know before updating:**

- **Docker Engine 25 or newer is now required.** The shipped `docker-compose.yml`
  uses healthcheck `start_interval`, which older engines reject.
- **Uploaded files now persist across image updates.** A named `applire_uploads`
  volume is mounted at `/app/data/uploads`; previously CV uploads and profile
  photos lived inside the container and were lost on every `docker compose pull`.
  The volume is deliberately scoped to the uploads directory rather than
  `/app/data`, so read-only release content shipped in the image is not shadowed
  by stale volume state.

Container logs are now size-capped (10 MB × 3 files). Without rotation the
json-file driver grows unbounded and can fill the host disk, which takes
PostgreSQL down with it.

### Added

- **Truthfulness Oracle** — every generated CV and cover letter is audited
  against the Master Profile before delivery, and the report is available in the
  UI and over the agent channel (`audit_document`). Each claim is graded
  `grounded`, `unverifiable`, `unbacked`, `inflated` or `misattributed`, with
  the vault evidence that backs it — including which employer a figure belongs
  to, so a number cannot silently migrate between roles.
- **The agent channel is feature-complete** (ADR-054). `render_document`
  produces a PDF from supplied data, `submit_claims` lets an external agent
  contribute evidence through the same honesty checks as the UI, `resolve_gap`
  closes a single gap statelessly, and `audit_document` exposes the Oracle.
  An agent-usage guide and an explicit honesty contract ship with the server
  (ADR-056), so a connecting agent is told the rules rather than guessing them.
- **Signature Stories** are a first-class vault entity (ADR-055) — a
  challenge/mechanism/outcome narrative the candidate owns, reusable across
  applications instead of being re-invented per document.
- **Application portfolio and dossier view** — applications are tracked through
  their lifecycle, with a detail view per application, staleness signals when the
  underlying CV has moved on, and cancellation with a short retention window.
- **The mobile journey** — capture, triage and CV review work on a phone, with
  a dedicated mobile end-to-end test lane.
- **Length-aware tailoring** (ADR-051) — page targets are region-aware, bullets
  are budgeted per role before drafting, and a bounded condense loop trims to fit
  without discarding quantified evidence.
- **Denials are a real ledger status** (ADR-059/ADR-061). "I have not done that"
  is recorded as the candidate's own position with their verbatim wording, kept
  distinct from "nobody knows yet", and enforced as a floor at every point where
  something is written to the vault.

### Changed

- **Positioning** — Applire describes itself as the open-source, agent-ready job
  application tool for Europe. README, public docs and `AGENTS.md` were rewritten
  to match, and a condensed public architecture digest was added.
- **DACH document conventions** are applied consistently: MM/YYYY dates, the
  Anrede on its own line, and no comma after the Grußformel. One template factory
  now backs every rendering path, so a convention fix reaches all of them.
- **Deterministic rules no longer make judgement calls** (ADR-062). Several text
  heuristics that decided questions of meaning — whether a denial limits a claim,
  whether a negation attaches to a concept — were deleted rather than tuned. They
  had been narrowed repeatedly after real-model incidents and were firing on
  precisely the honest output the product exists to produce. Facts stay in code;
  what a sentence *means* goes to the model with the evidence attached.

### Fixed

- Quantified evidence is retained through tailoring: a claim the vault backs with
  a figure can no longer reach the page as a bare keyword without the guard
  noticing (#315). See *Known issues* — the delivery half of this is not complete.
- A cover-letter figure can no longer be attributed to the wrong employer, and the
  guard that enforces this no longer deletes whole sentences over a tenure figure
  or a nested project's employer count.
- Interview answers no longer arrive as fabricated skills: an off-topic answer
  cannot mint a claim, and a denied technology cannot become a work-entry tag.
- Gap surfaces show grounded chips and canonical counts, long-running steps report
  honest progress, and the follow-up flow scopes its gap view to the right context.
- Numerous import-fidelity fixes across CV, LinkedIn and XING sources —
  certifications, publications, current-position detection, and entity dedupe.

### Known issues

Recorded because they are visible in generated output and are being worked, not
because they block use:

- A claim the vault holds only inside a denial statement, or only in a typed field
  such as `budget_managed`, can reach the cover letter and not the CV. Both
  documents remain individually truthful; the pair can read as padded to a careful
  reader (#322, #328, #326).
- The Anschreiben can break to a second page carrying only the signature (#320).
- The cover-letter review loop can exhaust its retries and ship the last draft
  unreviewed (#306).

## [0.37.2-beta] – 2026-07-06

Truthfulness patch for the Chocolate line. Two blockers surfaced by the
2026-07-04 blind real-LLM journey run: an honest "I don't have that experience"
could become a claim on the generated CV, and English project bullets could
ship untranslated in a German CV. Both are generation-pipeline fixes — no
schema change, no migration; existing installs update by pulling the images.

### Fixed
- **A denied skill can no longer become a CV claim** (ADR-046 amended, #127).
  The profile reconciler encoded an interview answer's explicitly *denied*
  technology ("no production RAG experience") as a work-entry technology, and
  could fabricate a skill from an off-topic answer. The reconciler prompt now
  carries an explicit stance rule and must declare every denied item; a
  deterministic guard inside the engine strips any operation matching the
  model's own denials (never-claim outranks claim) and drops interview-turn
  skill/technology claims that appear nowhere in the turn's question and
  answer. Verified by replaying the failing interview turns verbatim against
  the real LLM.
- **Project bullets now pass the document-language check** (ADR-038 amended).
  Project bullets — including those copied verbatim from the Master Profile —
  entered the generated CV *after* the output-language review, so English
  project text could ship inside a German CV. Project nesting now runs before
  the language pass, and the language reviewer covers project bullets (project
  *names* are treated as proper nouns and kept).

### Added
- **CI guard: the shipped `docker-compose.yml` must pull clean** (#126). A new
  workflow verifies — after every release publish, weekly, and on any change to
  the shipped compose file — that every referenced image resolves on its
  registry, so a missing published image (the v0.37.1 `applire-nginx:latest`
  gap) is caught at publish time instead of at a self-hoster's first
  `docker compose pull`.

## [0.37.1-beta] – 2026-07-05

The Chocolate release, finalised: the fix batch accumulated on top of the
0.37.0-beta pre-release — claimable keyword coverage now self-heals inside the
generation pipeline, the reverse proxy ships as a published image, and the
retention worker runs clean on Postgres.

### Changed
- **The pre-download notice is reduced to the AI-content warning** (ADR-040 amended).
  The red-flag diff rows were retired: their references resolved incorrectly, the
  texts were cut off, and the net effect discouraged rather than informed. The notice
  now shows only the AI-generated-content warning with the "don't show this again"
  checkbox (and remains informational — never a gate). A redesign is parked for a
  later flavour.
- **The reverse proxy now ships as a published image — self-hosting needs no config
  files on the host** (ADR-033/ADR-032 amended). Previously `nginx/self-hosted.conf`
  had to be present on the host and bind-mounted, so anyone who wrote their own compose
  or fetched only `docker-compose.yml` got an nginx with no routing ("site not found").
  The config is now baked into `ghcr.io/applire/applire-nginx` (built by the edge and
  release lanes alongside the backend and frontend images), so `docker compose pull &&
  up -d` fetches a complete, working stack with nothing to place on the host. The
  self-hosting quick-start drops the separate config-fetch step. Operators who want a
  custom domain or TLS can still bind-mount their own file over
  `/etc/nginx/conf.d/default.conf`; the dev stack is unchanged.

### Security
- **The self-hosted stack no longer publishes PostgreSQL on the host.** The shipped
  `docker-compose.yml` mapped the database to `0.0.0.0:5433` with the default
  `applire:applire` credentials — on a machine with an open firewall that is an
  internet-reachable database. The port mapping moved to the development override;
  in the self-hosted topology the database is reachable only on the internal compose
  network. (Found by the pre-release blind install test.)

### Fixed
- **Self-hosting quick-start drift.** The manual `alembic upgrade head` step is gone
  from the README and compose header — migrations run automatically on backend
  startup, and the documented step implied a broken install when it "did nothing".
  The README API examples now show the async CV import (`POST /api/profile/import-jobs`
  + poll) instead of the retired synchronous `/api/profile/upload`. A new
  "Self-hosting from source" section documents the build-it-yourself fallback on the
  production topology — and warns that a plain `docker compose up` inside a clone
  auto-applies the development override.
- **Generated documents no longer ship with system-detected claimable-keyword misses**
  (#122, ADR-048 amended). One deterministic presence predicate (surface-form union +
  plural/hyphen fold) is now shared by the ATS panel, the gap hints, and the generation
  pipeline itself: a verified missing-claimable list is recomputed before every reviewer
  pass and blocks approval until each term is surfaced from its profile evidence — or
  explicitly waived, because grounding still outranks coverage. Applies to the CV and
  the cover letter; the panel's "supported by your profile but not yet in the document"
  row is no longer Marcus's manual repair work.
- **The coverage gate also watches the pipeline's last writer** (#122 follow-up).
  The output-language pass (ADR-038) runs *after* tailoring and could translate a
  covered keyword into an unlisted synonym after the gate had already approved the
  draft ("Efficiency Improvements" → "Effizienzsteigerung"). The language reviewer now
  carries the same verified-coverage check, prescribes the exact required-language
  surface form as a word-choice correction, and its refine output is re-reviewed
  instead of shipping unseen (language-review retry ceiling 1 → 2).
- **Retention worker no longer crashes on Postgres.** The `cv_import_jobs` and
  `gap_analysis_jobs` TTL purges bound their `expires_at` cutoff as an ISO-8601 *string*;
  asyncpg infers the bind type from the `timestamptz` column and rejects a `str`
  (`expected a datetime … got 'str'`), so the daily retention run aborted on every real
  Postgres deployment. The cutoff is now bound as a `datetime` like the other purges.
  (Hidden because the SQLite unit harness compared the string lexically; a type-asserting
  regression test now guards the bind.)
- **CV view consistency — gap hints, ATS checks and match score no longer contradict
  each other** (#117, ADR-019/ADR-048 amended). "Related gaps" are now derived at read
  time from the Keyword Ledger intersected with the *current document* — a keyword
  already in the CV no longer shows as an open gap next to itself; saving a section no
  longer deletes honest-gap records from the stored analysis. Gap chips split by
  evidence: profile-backed gaps keep "Write it myself / Let Kaile help", honest gaps
  route to a profile interview instead of inviting an unsupported claim. The ATS panel
  gains a truthfulness warning for keywords present in the document but not backed by
  the profile, and the two numbers are labelled for what they measure ("Profile match"
  vs "Document coverage").
- **Concurrent positions render in the correct order** (#118). Work experience is now
  sorted reverse-chronologically by start date (ties break on end date, ongoing roles
  stay on top), enforced where the CV data is established; two overlapping "present"
  roles previously kept their incidental profile order. The ATS reading-order check's
  failure message now states what was compared instead of guessing at column
  interleaving.

## [0.37.0-beta] – 2026-07-03

The **Chocolate** release: truthful tailoring end-to-end. Everything the app claims
about a candidate is now grounded, classified, and verifiable — from ATS keyword
coverage through gap interviews to the generated CV and cover letter.

### Added
- **ATS parseability engine + panel** (ADR-039, US138–141, #33-era main commits). Deterministic
  local audit of every generated CV and cover letter (machine-readable contact, section
  extraction order, keyword coverage), persisted per document (migration 0033), surfaced
  as a compact panel with keyword ring on both previews, exposed via REST and the
  `get_cv_ats_report` MCP tool, and gated in CI by a blocking Playwright PDF
  extraction round-trip suite (`tests/ats`).
- **Keyword Ledger — truthful ATS keyword coverage** (E037/ADR-048, #107, #116). One
  ledger classifies every JD concept as *present*, *claimable* ("supported by your
  profile but not yet in the document") or an *honest gap*; the match score is re-sourced
  from the ledger (US199), CV and cover-letter generators consume claimable/forbidden
  lists (US200/201), the reviewer enforces them (US202), the ATS panel annotates them
  (US203), and honest gaps route into the interview for enrichment (US204). Score
  wobble is frozen via a gap-input fingerprint (migration 0040).
- **Profile Reconciliation Engine** (E035/ADR-046, #86, #88, #92, #94). CV imports and
  interview answers reconcile into the master profile through a typed operation
  vocabulary applied deterministically — fuzzy employer matching, hierarchy/duplicate
  migration, conflict surfacing, idempotent application, token-budget-aware prompts.
- **Unified document workspace** (E038, #109). CV and cover letter live in one flow
  document view with tabs and a shared actions sidebar.
- **Async jobs for the slow paths**: CV import (job + poll, migration 0038, #95),
  gap analysis (E037 N2), and cover-letter generation — long LLM steps survive
  refreshes and proxies instead of dying on request timeouts.
- **Master Profile Health hub** (E033/ADR-041/042, #59) with severity-tiered checks,
  profile snapshots/undo, and two hub-launched no-JD interviews (conflict resolution
  and Mode-C enrichment).
- **CV import fidelity** (E034/ADR-044, #62, #63): unified experience model so
  volunteer/project work counts toward experience years and skills; extraction prompt
  hygiene.
- **Input integrity** (E032/E024, #45): FMEA-derived mitigations incl. the no-CV guided
  onboarding path (ADR-016 amended).
- **Truthful output hardening** (E031/ADR-040, #37/#38) and **generation grounding** (#61).
- **EU provider options** (#41): Anthropic and Requesty-EU providers join the
  bring-your-own-key roster.
- **`OPENROUTER_REASONING_EFFORT`** (`low`/`medium`/`high`; default unset = model decides).
  Caps how much a thinking model reasons so reasoning tokens don't crowd out the answer.
  Accepted even by models that mandate reasoning. Deployment-wide for now; finer per-operation
  control is planned. (#85.)
- **`LLM_DEBUG_LOG`** (#96): per-call LLM request/response logging for diagnosis.
- **Rolling `:edge` images**: every merge to `main` now publishes
  `ghcr.io/applire/applire-{backend,frontend}:edge` plus an immutable `:sha-<commit>`
  tag, and a `docker-compose.edge.yml` overlay runs a persistent edge/UAT environment
  on those images. `:latest` and semver tags remain reserved for releases.

### Changed
- **Cap-safe segmented generation** (E036/ADR-047, #95, US188–196): CV generation is
  budget-aware and segmentation-first — an outline call plus per-section calls keep every
  LLM response inside the output-token cap by contract, with truncation-integrity guards
  (#84) instead of silently clipped documents.
- **Output language consistency** (ADR-038 follow-through, #39): LLM-content reviewer
  plus `labels[lang]` chrome routing fixed the four remaining mixed-language causes.
- **Completeness scoring unified** (#82) across dashboard, health hub, and profile.
- **Flow navigation** hardened against step desync (#33, `lib/flow-routing.ts`,
  migration 0034).
- **Release image tagging is now pre-release-aware.** Publishing a GitHub *pre-release*
  builds `:X.Y.Z` + `:sha` tags only and does **not** move `:latest`; `:latest` and
  `:X.Y` move only when a full (non-pre-) release is published — so self-hosters pinned
  to `:latest` never receive a beta by surprise. A qualified pre-release is promoted by
  simply unticking its "pre-release" box.

### Fixed
- **Self-hosting on SELinux hosts no longer leaves the app unreachable.** The production
  `docker-compose.yml` mounted the nginx reverse-proxy config read-only without the `:z`
  SELinux relabel flag; on enforcing hosts (RHEL, Fedora, Rocky, Alma, openSUSE) nginx
  could not read its config and exited, so nothing answered on port 80 — breaking the
  primary "no clone required" self-hosting path. The mount is now `:ro,z` (a no-op on
  non-SELinux hosts). Caught by a full production-topology install test before this
  release; the dev stack had masked it because its `dev.conf` mount already carried `:z`.
- **Blind-PQ release blockers** (2026-07-02 run, #116): refresh during multi-CV
  onboarding no longer silently drops queued CVs (all import jobs are created up-front,
  processed serially per user, with a dashboard banner while imports run); the
  cover-letter tab shows a real empty state + generate CTA instead of a fake eternal
  "Generating…"; the cover letter targets the job's role title and keeps the typed
  recipient and a role-bearing subject line; an honest interview denial can no longer
  surface as "supported by your profile" (honest-gap verdicts outrank claimable
  surface-form aliases, and gap prompts carry stance-labeled interview statements);
  certifications now flow deterministically from the master profile into the tailored
  CV and all seven templates; section-editor master saves no longer crash on date
  fields.
- **Chocolate pre-release fix batch** (#69, #77–#81): interview resume, merge review,
  partial cert dates, merge promotion collapse, profile UX cluster, import CTA and
  bullet dedup.
- **CV import no longer fails on reasoning ("thinking") models.** Provider extraction
  was capped at 8192 tokens; on a thinking model the reasoning tokens share that budget,
  so a full CV truncated mid-JSON and the upload returned a 500 ("We couldn't read any of
  your CVs"). The CV→profile extraction budget is now 16384 (`CV_EXTRACTION_MAX_TOKENS`),
  with thinking kept on for accuracy. (#85, follow-up to #84/F-B.)
- **A model that forbids disabling reasoning no longer 500s.** Short "chrome" generations
  (interview questions, CV-section assists) ask the model to skip reasoning; some models
  (e.g. Google's Gemini Flash thinking models) reject that with HTTP 400 "Reasoning is
  mandatory … cannot be disabled". The OpenRouter provider now treats `disable_thinking`
  as best-effort: it retries once with reasoning left on and the budget raised, so the
  call succeeds with no operator configuration. (#85.)
- **CV and cover-letter generation no longer truncate on reasoning ("thinking") models.**
  On a thinking model `max_tokens` covers reasoning *and* output together, and some models
  (Gemini Flash) over-think — burning the budget before the document is written, so
  generation failed with a truncation error. Two fixes: the generation budget is raised to
  16384 (`CV_GENERATION_MAX_TOKENS`), and a new `OPENROUTER_REASONING_EFFORT` setting
  (default unset) bounds reasoning via OpenRouter's cross-vendor `reasoning.effort` — set it
  to `low` to stop a model over-thinking simple transforms. (#85.)
- React StrictMode double-mount aborted every CV upload on the real browser path (#95).

### Security
- Dependency bumps closing all high-severity alerts: python-multipart 0.0.31 (both
  manifests, #50/#57), form-data 4.0.6 (#56), js-yaml 4.3.0 (#108), pypdf 6.13.3 (#60).

### Notes
- **Reasoning ("thinking") models add noticeable latency to the interview.** Interview
  questions are intentionally generated *without* reasoning — they're short and near
  deterministic, so reasoning tokens are pure overhead. Models that **mandate** reasoning
  and won't let it be disabled (e.g. Gemini Flash thinking models) therefore run each
  interview question with reasoning on, which is markedly slower. For the snappiest
  interview, configure a model that allows disabling reasoning (or a non-thinking model)
  for the conversational paths. Operator-level control of this tradeoff is planned.

## [0.36.2-beta] – 2026-06-10

Bug-fix release driven by a full Milan-persona QA run (English CVs + German job ad,
real LLM) that exercised the ADR-038 document-language path end-to-end.

### Fixed
- **Cover-letter date is now system-injected.** The prompt asked the LLM for "today's
  date", which it cannot know — every letter was dated "10. Oktober 2023". The date now
  comes from the server clock, formatted per document language ("10. Juni 2026" /
  "10 June 2026") with locale-independent month names.
- **Document language is routed deterministically** (ADR-038 amended). The cover letter
  resolved its language from `language_requirement` — an LLM-extracted *candidate*
  requirement (e.g. "Bilingual DE/EN"), which misrouted a German job ad to an English
  letter — and CV tailoring had no language input at all. A new `job_analyses.jd_language`
  column (migration 0032) stores the language the job ad is *written in*, detected in code
  by a dependency-free stopword/umlaut scorer; both document generators route on it, with
  raw-text fallback for pre-migration rows.
- **CV tailoring now translates.** The hallucination guards ("keep facts EXACTLY as
  provided") read as a translation ban, so work-history bullets stayed in the source-CV
  language. The prompt (v3) carries an explicit `OUTPUT LANGUAGE` directive and states
  that translating prose is required and is not invention; proper nouns, dates, and
  metrics stay unchanged.
- **Interview answers no longer duplicate positions.** An answer mentioning a known
  employer by a shortened name ("TWENTYONE" vs "TWENTYONE Digital") with a loose title
  created a spurious undated position. The interview enrichment path now shares the
  CV-upload merge's fuzzy employer matching: known employers are enriched in place
  (bullets accumulate as achievements, differing titles become role aliases); only dated,
  non-overlapping stints or new employers create new entries. Answer bullets — previously
  discarded on match and stored under a schema-invisible key on append — now land in
  `achievements` in both cases.
- **A null CV summary no longer fails the whole generation.** The tailoring LLM
  occasionally returns `"summary": null`; this now degrades to an empty section instead
  of a validation error requiring a manual retry.
- **The sidebar version label matches the release.** It was frozen at v0.31.0-beta
  (read from `frontend/package.json`, which releases never bumped). Release images now
  bake the GitHub tag in via an `APP_VERSION` build arg (release workflow → Dockerfile →
  `next.config.ts`); dev builds fall back to `package.json`, now kept in sync.

### Changed
- Database schema: migration 0032 adds nullable `job_analyses.jd_language` — applied
  automatically on backend startup, no manual action needed.

## [0.36.1-beta] – 2026-06-09

Documentation-only patch release — no runtime code changes.

### Changed
- **Public docs audited and repositioned.** README reframes LLM providers as
  bring-your-own-key (no advertised default; "EU-hosted" kept as a neutral note on
  Mistral), rewrites "Who is Applire for" around use cases (multi-version CV, DACH
  conversion, agent-first), corrects the CV template list to the 7 real templates,
  bumps the roadmap to v0.36.0-beta, and notes the conversation-vs-document language
  split (ADR-038).
- `docs/ARCHITECTURE.md` is now the canonical home of the port topology (new port
  table); ADR-009 summary made provider-neutral; temperature defaults grounded in code.
- GitHub org move completed: GHCR images now publish under `ghcr.io/applire/*` and all
  repo links point to `Applire/Applire` (compose files, release CI, badges,
  issue templates, `package.json`).

### Fixed
- Self-host quickstart actually works again: download `nginx/self-hosted.conf` and
  access the app via `http://localhost` (backend/frontend are internal-only).
- Corrected the gap-analysis REST path in the README to `/api/session/{id}/analyze-gaps`.
- Removed dangling references to `docs/TRACEABILITY.md` (TESTING.md) and `docs/mcp.md`
  (docker-compose.yml).
- `backend/pyproject.toml` version brought back in sync with the release tag (the
  0.36.0-beta tag shipped with the version still reading 0.35.1-beta).

## [0.36.0-beta] – 2026-06-09

### Fixed
- **Interview & enrichment questions now follow the UI language** (ADR-038, US137).
  Previously the gap-interview and profile-enrichment questions drifted to whatever
  language the input material was in — e.g. an English-UI user who pasted a German job
  description was interviewed in German, because the JD-language `jd_context` was injected
  into the question prompt with no output-language directive. Questions and their answer
  choices are now generated in the user's `ui_language` regardless of the profile/JD/context
  language. The split is explicit: **conversation** (interview/enrichment questions) follows
  the **UI language**; **documents** (tailored CV, cover letter) continue to follow the
  **job-description language**.

### Added
- `with_language()` directive applied to all question system prompts (MODE A targeted,
  MODE B guided, follow-up probes, Mode C enrichment), forcing the output language and
  instructing the model never to mirror the context language.
- Language-verification pass over generated questions, reusing the ADR-021
  `review_and_refine` loop (`prompts/review_question_language.py`), gated by
  `INTERVIEW_QUESTION_LANG_REVIEW_MAX_RETRIES` (default 1; `0` disables).

### Changed
- `UserSettings.ui_language` is now **non-nullable, default `en`** and is the single
  authoritative source for conversation language (resolved via `get_ui_language()`).
- Removed the now-dead `Accept-Language` auto-detect branch in `GET /api/settings`; the
  settings response `ui_language` is now non-optional.

### Migration
- Alembic migration 0031: backfills `user_settings.ui_language` NULL → `'en'`, then sets
  the column `NOT NULL` with server default `'en'`. No data loss; reversible.

## [0.35.1-beta] – 2026-06-02

### Fixed
- **UI color-scheme flash on load** (#30). The active palette is now injected server-side
  onto `<html>` during SSR, so the first paint already uses it. Previously the static
  `globals.css` default rendered first and the client `ThemeProvider` swapped in the DB
  scheme a few frames later — a visible flash.
- Corrected the mislabeled **"EU Blue"** built-in scheme: it now carries the canonical
  Continental Excellence EU palette (`#003399` / `#fecb00`) instead of an unrelated
  murky slate-teal. "GNOME Blue" is unchanged — its name accurately describes its palette.

### Migration
- Alembic migration 0030: re-colors the EU Blue built-in scheme in place (idempotent
  `UPDATE` by id); fixes both fresh and existing databases. No schema change.

## [0.35.0-beta] – 2026-06-02

### Changed
- **Match Score is now computed deterministically** (ADR-035). The LLM classifies each
  job requirement as `direct` / `partial` / `gap`; Python computes the score as a weighted
  ratio (required = 1.0, nice-to-have = 0.5). The headline percentage can no longer disagree
  with the gap categories, is reproducible (same inputs → same score), and is explainable.
- Recalibrated interview score bands: ≥ 60% (minor gaps), 40–59% (moderate), < 40% (low fit).

### Added
- `requirement_breakdown` field on gap analyses — stores `{requirement, source, status,
  slot, earned, reason}` per requirement for explainability and audit.

### Fixed
- Admin appearance editor no longer blacks out the UI on exit; saturation ceiling lowered
  (88 → 50) for legible palettes.
- nginx re-resolves upstreams to avoid stale-DNS `502`s after container rebuilds (self-hosting).
- Patched moderate XSS advisory in the `postcss` transitive dependency (#47).

### Migration
- Alembic migration 0029: `match_score` becomes nullable; adds `requirement_breakdown` column.
  Existing rows keep their stored scores and are not retroactively recomputed.

## [0.34.0-beta] – 2026-06-01

### Added
- **Continental Excellence design system** — Manrope type, glassmorphism, gold-pill accents,
  and AI-card light-leak styling across the app.
- Unified application shell: `AppTopbar` with section / detail / flow modes, sidebar user-strip,
  and an Admin nav entry; `/admin` and `/match` now share the shell sidebar.
- Reworked CV workspace: `CVPageActionBar` (Download · Anschreiben · Eingestellt · Weiter),
  `RefinementHeader` with a match-score ring and expiry chip, and a 2-tab `RefinementPanel`.
- **MCP agent channel expansion** — new tools `get_cv_status` (US048), `start_flow` /
  `advance_flow` / `get_flow_state` with a `flow://` resource (US109), `create_application`
  (US110), `import_cv` (US107), `add_role` (US108), and JD-URL scraping in `analyze_jd` (US056);
  CV now renders inline over stdio.
- `frontend-lint` CI job enforcing ESLint + de/en i18n key parity on every push.

### Changed
- Full i18n sweep: remaining hard-coded JSX strings replaced with `t()` calls, a11y aria-labels
  localized, and de/en catalog parity enforced via `eslint-plugin-formatjs`.

### Fixed
- Flow advance is now idempotent; stop over-drilling a gap the candidate already declined.
- Each gap maps to a single CV section; refinement panel de-duplicated.
- Master-Profil tiles derive from real profile data; in-app back/home actions route to `/dashboard`.
- Static assets (template thumbnails, favicon) served reliably in the standalone runner.

### Security
- Upgraded FastAPI 0.115.6 → 0.136.3 to patch Starlette CVEs.
- Patched `ws` and `brace-expansion` advisories via npm audit; bumped Vitest 1.6 → 4.1.

## [0.33.1-beta] – 2026-05-18

### Added
- **Post-hire flow** — "Mark as Hired" action on the dashboard, a `hired` user status, and a
  `POST /applications/{id}/mark-hired` endpoint; `application_id` exposed in the flow state.
- **Add-role / profile-update flow** — `ProfileUpdateChooser` and `AddRoleView` (manual entry,
  JD-paste, multi-role close-out, and pre-fill from an application), backed by
  `POST /api/profile/roles`; new `profileUpdate` i18n namespace.
- Retry-refinement prompts for CV extraction, profile extraction, CV tailoring, and response
  parsing — reviewers now quote source text in feedback.

### Fixed
- CI builds multi-arch images on native runners to avoid the 6-hour emulation timeout.

## [0.32.0-beta] – 2026-05-15

> Consolidates the self-hosting hardening line (tags `v0.32.0-beta`–`v0.32.4-beta`).

### Added
- **Self-hosting reverse proxy** — `applire-nginx` image with baked-in config plus a
  pull-based `docker-compose.yml` for self-hosters and a build-from-source
  `docker-compose.override.yml` for development.
- Release pipeline now publishes `applire-backend`, `applire-frontend`, and `applire-nginx`
  images to GHCR.

### Fixed
- API routing and CORS now work for non-localhost deployments.
- Made the env file optional with a `DATABASE_URL` default for platform deployments;
  renamed `.env.dev` → `.env`.

### Changed
- Bumped `next` 15.3.6 → 15.5.18, `next-intl` 4.9.1 → 4.9.2.

## [0.31.0-beta] – 2026-05-13 (First public release)

### Added
- AI-powered CV tailoring for the DACH job market (Germany, Austria, Switzerland)
- CV section editor with smart gap analysis and interview preparation
- Job description URL ingestion with skill extraction
- Cover letter generation
- Multilingual UI (de/en) via next-intl
- CV export to PDF with multiple color profiles and templates
- Photo management (upload, crop, remove)
- LLM review layer with OpenRouter / Mistral AI support
- Comprehensive CI/CD pipeline (GitHub Actions, GHCR)
- Docker Compose setup for self-hosting
- Offline mode with service worker
- MCP server integration (Kaile agent channel)
- AGPL-3.0 Community Edition open-source release

### Tech Stack
- Backend: FastAPI 0.115, Python 3.12, SQLAlchemy 2, Alembic
- Frontend: Next.js 15.2, React 19, TypeScript 5, Tailwind CSS 4
- AI: OpenRouter (multi-model), Mistral AI, MCP tool integration
- Database: SQLite (dev), PostgreSQL (prod)

[Unreleased]: https://github.com/Applire/Applire/compare/v0.35.0-beta...HEAD
[0.35.0-beta]: https://github.com/Applire/Applire/compare/v0.34.0-beta...v0.35.0-beta
[0.34.0-beta]: https://github.com/Applire/Applire/compare/v0.33.1-beta...v0.34.0-beta
[0.33.1-beta]: https://github.com/Applire/Applire/compare/v0.32.4-beta...v0.33.1-beta
[0.32.0-beta]: https://github.com/Applire/Applire/compare/v0.31.2-beta...v0.32.4-beta
[0.31.0-beta]: https://github.com/Applire/Applire/releases/tag/v0.31.0-beta
