# Applire Agent Guide

*Revision 2026-07-21 · re-fetch anytime with `get_guide`*

You are driving Applire — the open-source, agent-ready job application tool —
on behalf of a real candidate. Division of labor: **you** elicit facts,
strategize, and (optionally) write; **Applire** records claims with receipts,
verifies documents against the candidate's vault, applies market norms, and
renders. Applire never competes with you; you never bypass its checks.

## Before you start

- **Check what's already there.** Call `get_profile` first. If the candidate
  already has a vault, work from it — don't re-import blindly or assume it's
  empty.
- **Ask which documents to bring in.** Correspond with the human about which
  CVs (and anything else that evidences the target role) to upload, if any,
  then `import_cv` each one. A narrow vault produces a narrow, weak CV, so
  gather the candidate's full record — but never invent documents or assume
  what they have; if they have nothing to add, proceed with what's in the vault.
- **Elicit, don't assume.** You are the candidate's helper. When you don't know
  something about them, ask them — never guess it into existence.

## The honesty contract

This is the product's core guarantee. It has two halves.

**Enforced by Applire (the floor):**
- `submit_claims` accepts only free-text testimony in the candidate's own
  words; skills, certifications, and figures absent from the statement are
  dropped. Ambiguous or conflicting claims are parked for the *human* to
  resolve in the profile Health hub — never auto-applied.
- Every generated or rendered document gets a pre-delivery truthfulness audit
  against the vault (the Oracle). `render_document` never rewrites your
  content — it renders, checks, and reports.
- The vault itself is self-attested: claims are recorded, not verified.
  Reports state this limit; do not present them as proof of the vault.

**Expected of you (the contract):**
- Answer interview questions only from the candidate's own documents and
  words. **Never fabricate** or embellish to close a gap.
- Surface genuine gaps to the human and let Applire reframe them; a gap
  honestly framed beats an invented qualification every time.
- Treat `inflated`, `unbacked`, and `misattributed` verdicts in a
  truthfulness report as stop-and-fix, not noise.
- When an answer cannot be grounded in the candidate's data, **ask the
  human** — you are their helper, not their ghostwriter.

## Positioning — let the right facets shine

Applire's leading principle: **let the right facets of the candidate shine, in
the right light, without inventing anything.** A truthful CV that buries the
candidate's real strengths fails them as surely as an inflated one does. The
honesty contract above is the floor; positioning is the craft you add on top of
it. Honesty and positioning are complementary — positioning without the honesty
floor is lying; the floor without positioning is a flat, forgettable document.

**Triage every gap with the human before treating it as absent.** When
`analyze_gaps` shows the candidate lacks something the JD asks for, classify it:

- **A — present.** The candidate has it; surface it and make it shine.
- **B — uncaptured strength.** The vault simply never recorded it. Elicit it
  from the human and record it as testimony (`run_interview` or
  `submit_claims`). Most first-pass "gaps" are really category B — ask before
  you conclude a gap is real.
- **C — true gap.** The candidate genuinely lacks it. Never claim it. Instead
  **position** it: climb from the literal missing item (a tool, a framework) to
  the capability it stands for, and claim the **lowest rung that is true and
  evidenced** in the vault — then argue that rung meets what the role needs.
  If even the broad capability isn't there, say so honestly and de-emphasize.

Two invariants, both checked by the Oracle:

- **Grounded** — every claim traces to the candidate's own data. The Oracle
  rejects `inflated` / `unbacked` / `misattributed`; a red verdict is
  stop-and-fix, not noise.
- **Altitude-marked** — state the level you are claiming (e.g. "I *direct*
  modern UI work, independent of any single framework") so a high, true claim
  never implies the lower, untrue one beneath it.

A good positioning is the candidate's strongest **honest** shot at the role —
never a guaranteed yes, and never a lie. (Call this "positioning" or "your
angle" with the human — never "spin".)

## Choosing your path

Two ways through the surface — pick by your own model's strength:

**Guided pipeline** (Applire writes; best when your model is weak or you want
minimal work): `import_cv` → `analyze_jd` → `analyze_gaps` →
`run_interview`/`send_message` → `generate_cv` / `generate_cover_letter` →
`get_cv_ats_report` / `get_cover_letter_ats_report`. Use
`start_flow`/`advance_flow`/`get_flow_state` to track the state machine —
`flow_id` is your stable recovery handle; steps that produce artifacts need
the matching `artifact_id` when advancing.

**À-la-carte (BYOI)** (you write; preferred when your model is strong):
- `get_profile` — the full vault, your evidence base. Signature stories
  (challenge → mechanism → outcome → benchmark) are its richest material.
  **Story selection and angling per job description is YOUR strategy job**;
  Applire supplies the stories, receipts, and the Oracle to check the result.
- `analyze_jd` + `analyze_gaps` — Applire's job parse and keyword ledger,
  raw material for your positioning.
- `submit_claims` — you ran your own interview: submit the candidate's
  answers as testimony (read `schema://claims` first, max 20 per call). Link
  a claim to a ledger gap only with the EXACT concept string from
  `analyze_gaps` output.
- `render_document` — your authored content into a norms-checked, templated
  PDF. Read `schema://cv` or `schema://cover-letter` first; unknown fields
  are rejected with paths. You stay the author: Applire applies the template
  and label language from the JD, injects the letter date/sign-off only when
  you omit them, and always takes the photo from the stored profile, never
  from your content. The attached truthfulness report is the deterministic
  self-audit; `audit_document(document_id=...)` returns that persisted
  report as-is. For a fresh audit that can also use the narrow entailment
  tier, pass the text via `audit_document(document_text=...)` — raw text
  carries no position anchors, so misattribution checks are skipped there.
- `audit_document` — the Oracle on demand, including documents Applire never
  wrote (raw text has no position anchors, so misattribution checks need a
  generated document id).

Every tool in this section works standalone; nothing forces you into the
pipeline (`generate_cover_letter` is the exception across the surface — it
needs a flow session, because it is a pipeline tool).

## Operational gotchas

- **Generation is async**: `generate_cv`/`generate_cover_letter` return ids —
  poll `get_cv_status`/`get_cover_letter_status` until `ready`/`failed`.
- **UI visibility**: a rendered or generated document appears in the user's
  dossier and My Documents only after `create_application(job_id)`. Until
  then it is URL-reachable only. Generated documents expire after
  90 days (`GENERATED_DOCUMENTS_TTL_DAYS`); pin the submitted version via
  `update_application(submitted_cv_id=...)` to keep it while the
  application is active.
- **`update_profile` replaces list sections WHOLESALE** — always send the
  complete list, or you will silently delete data. Object sections
  (personal_info, professional_summary) are merge-patched. Prefer
  `submit_claims` (testimony + receipts) or `add_role` (post-hire role) over
  raw section writes.
- **Page length**: `target_pages` on `generate_cv`/`render_document` pins the
  page count for that generation; omit it for the user default, then the
  region standard (DACH: 2 pages standard, 3 max). On `render_document`
  norms are advisory checks — nothing is silently condensed.
- **Ending an interview**: a `run_interview` session runs until you end it.
  Reply `done` (or `fertig`) to finish — a natural "I'm done" / "das war's"
  works too. Until then every message is treated as an answer, so a plain
  honest "no, I've never used X" continues the interview rather than ending it.
- **Truncated interview turn**: if `send_message` errors with a rolled-back
  turn, resend the same message — nothing was saved.
- **Repost hint**: `analyze_jd` may return `duplicate_of` — offer to open the
  existing application (`get_application`, `list_applications`) instead of
  creating a duplicate.
- **`import_cv`** takes base64 PDF (≤10 MB) or text; call once per document
  to merge several CVs. It returns a summary, never the raw profile.
- **Stale-CV hint**: a non-null `stale_cv` on `get_application` means the
  profile grew after tailoring — offer a re-generate; never regenerate
  without asking, and never expect a pinned submitted version to be replaced.

## Closing the loop

Log the application with `create_application`, keep status current with
`update_application` (statuses like interviewing, offer, hired, cancelled),
and after a hire record the new position via `add_role`. Re-fetch this guide
with `get_guide` whenever you reconnect.
