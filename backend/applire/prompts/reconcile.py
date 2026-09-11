# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""ADR-046 — the single-call profile-reconciler prompt.

The reconciler receives the WHOLE current master profile plus a chunk of new
information and emits a typed batch of ops (see ``services/profile/reconcile/
ops.py``) that the deterministic applier folds into the profile. One LLM call,
no tool loop. The distinctive phrase "profile reconciler" is the mock fingerprint
(``providers/llm/mock.py``) — keep it in the system prompt.

Version history — every rule change with its MEASURED effect
------------------------------------------------------------

The rule against which this header exists: **a rule may not be added, widened or
removed without a measurement of what it changed.** The 2026-08-31 incident that
made it a rule — a rule widened to cover a shape the model was already ignoring,
668 prompt chars for nothing — is recorded in the ``applire-prompt-first`` skill.
Numbers below are from ``scripts/model_matrix.py``, n=10 per shape, shapes
S6/S7/S8, three models over OpenRouter with reasoning on; records in
``Documents/Runs/Nougat/build-1/p/runs/``. Rates are lost-turn / malformed /
wrong-slot per shape; "qualified" is RULING O3-1's threshold set.

* **15,118 chars, 14 rules** — the state the 11-model matrix measured
  (2026-09-09). Every model sub-par; two structural diseases named in
  ``o3/failure-taxonomy-2026-09-09.md``.
* **16,244 (#684, ruling V-0/V-6, 2026-09-08).** ``UpsertWork.role`` optional +
  "role only when stated"; ``add_bullets`` gains "a bullet carries ONLY the
  clause about ITS OWN entity"; ``upsert_skill.years_experience``.
  *Measured:* ``glm-5.3-flash`` malformed 40 %→0 % on S7 (all 8 rejected ops had
  been ``upsert_work.role:string_type``); ``gpt-5.6-luna`` sub-par → **qualified**
  (S7 lost 20 %→0 %, wrong-slot 30 %→0 %).
* **16,660 (M-1, 2026-09-09).** Rule 9's "an answer that does not address the
  question contributes nothing about the question's topic" replaced by "A DENIAL
  IS ABOUT ITS OWN ITEM AND NOTHING ELSE …", with the S6/S7/S8 sentence as the
  worked example; rule 9's own empty-output escape deleted, leaving the single
  one in the output-format preamble (contradiction C2 of ``o3/prompt-health.md``).
  *Measured:* ``glm-5.3-flash`` sub-par → **qualified** (S7 lost 10 %→0 %), and
  its completion tokens for the same 30 turns fell 109,253 → 57,202 (-48 %) with
  S6/S7 p50 latency 45 s→6 s / 63 s→7 s — the model had been spending reasoning
  budget on the contradiction. ``ministral-8b`` malformed 10/20/10 → 0/20/0.
  No regression on ``gpt-5.6-luna``.
* **17,599 (M-2, 2026-09-09).** Rule 1 gains "ONE SENTENCE NAMING SEVERAL
  EMPLOYERS IS SEVERAL FACTS", the split-per-employer instruction and the worked
  NovaRNA/Helvetia/Blutspendedienst example (the document-harm class, 7 of 11
  models — taxonomy §3.3).
  *Measured:* ``ministral-8b`` sub-par → **caveat** (S7 malformed 20 %→0 %; its
  remaining rejection is one ``upsert_work.ref:missing``). No regression on the
  two qualified models.
* **19,007 (M5.1.1 (3), 2026-09-11).** Rule 4's ``NEVER infer`` scoped to fact
  CONTENT (+289 chars). The prompt told the model "NEVER infer, embellish, or
  fabricate" in rule 4 while rule 1 REQUIRES a semantic entity match across
  DE/EN, synonyms and abbreviations — the one inference the reconciler must make
  was forbidden by the rule next to it (contradiction C? of
  ``o3/prompt-health.md``). Rule 4 now states what it governs (WHAT a fact says)
  and names what it does not (WHICH entity it belongs to), with rule 1's own
  worked pair as the example. *Measured* (arm A3 vs A2, n=10 × S6/S7/S8 + S9 +
  S10): **all three models qualified in both arms**; on A3 every model was 0.00
  on malformed, wrong-slot and error across S6/S7/S8, and ``glm-5.3-flash`` was
  transport-clean for the first time in the sequence. ``ministral-8b``'s station
  coverage moved S6 0.90→0.80 / S7 0.90→0.90 / S8 1.00→0.93 — inside this
  harness's n=10 noise, not claimed as an effect.
* **NOT SHIPPED — M5.1.1 (2), measured 2026-09-11 (arm A4).** Rules 2's ONE
  CONTAINER clause, rule 7 and rule 13 were collapsed into ONE rule 7 ("THE SAME
  THING SAID AGAIN") with three branches — (a) same entity, new name → an upsert,
  (b) same fact, changed claim → ``flag_conflict``, (c) same fact, same claim →
  stay silent — removing the forward cross-reference and the three-way verdict.
  Size-neutral (19,007 → 19,050), 13 rules → 12. **Reverted**, per ruling
  M5.1.1's own condition that a change degrading a model is not shipped:
  ``ministral-8b`` crossed the malformed-op threshold on S8 (0.00 → **0.20**),
  and the rejections are a failure shape that appears NOWHERE in arms A0-A3 —
  ``upsert_work.ref:missing``, twice, against ``company:missing`` every other
  time. ``gpt-5.6-luna`` was 0.00 on everything and 10/10 on S9.
  **This measurement confirms the open hypothesis of ``o3/prompt-health.md`` §2**
  ("``ref`` is required by the schema on all three entity ops and appears only in
  the shared preamble and in rule 3 — 1,400-3,300 chars away from the per-op line
  a model re-reads"): the collapsed rule 7 is 2,634 chars and pushes rule 3's
  ``ref`` instruction further from the ops that need it, while branch (a)'s own
  imperative names ``target`` and never ``ref``. **The next attempt** should name
  ``ref`` inside branch (a) (M-3a's own lesson, per-op REQUIRED lines) and be
  re-measured; the collapse itself is not refuted, its wording is.
  *Also measured on the way, and worth keeping:* shape **S9** gives rule 13 —
  the longest rule in the prompt, never exercised by any shape until now — its
  first measurement: ``flag_conflict`` on **10/10 turns for all three models**
  in arm A3, and nothing else emitted. Rule 13 earns its 1,399 chars.
* **18,819 (M5.1.4, 2026-09-11).** The output envelope gains an OPTIONAL
  ``empty_reason`` (``already_known`` | ``question_only`` | ``nothing_actionable``),
  asked for only when ``ops`` is empty (+257 chars in the preamble; the USER
  prompt's own envelope line is untouched, so the #688 goldens are byte-identical).
  *Measured* (arm A1 vs A0, n=10 × S6/S7/S8 + the new S10 "restated fact, nothing
  new"): on S10 ``gpt-5.6-luna`` and ``glm-5.3-flash`` emitted zero ops AND the
  correct ``already_known`` **10/10 each**; ``ministral-8b`` wrote ops for the
  restated fact 10/10 (pre-existing behaviour) and therefore correctly omitted the
  field. No model crossed a threshold on S6/S7/S8 in either arm. Known
  non-compliance, harmless by construction: ``ministral-8b`` volunteers the field
  on a NON-empty batch 3 of 40 turns, against the "omit it whenever ops carries an
  op" clause — the witness only ever reads it when nothing landed.
* **18,703 (M5.1.1 (1), 2026-09-11).** Rules 12 and 14 merged into ONE quantified-
  role-facts rule with branches (a) where the figure lives and (b) what
  ``team_size`` counts (−105 chars, 14 rules → 13; branch (b) keeps the literal
  phrase ``TEAM_SIZE SEMANTICS``, which
  ``tests/unit/test_team_size_semantics_prompt_parity.py`` pins across all three
  emitters — the first cut of this merge dropped it and the parity test caught it.
  **Arms A2 and A3 measured that first cut**, whose branch heading read "WHAT
  ``team_size`` COUNTS (#562)"; the shipped text differs from it by that heading
  alone and is what arm A4 measures). The two halves used to sit
  ~4,000 chars apart and each restated the other's closing sentence ("the figure
  still belongs in the bullet text"). *Measured* (arm A2 vs A1, n=10 × S6/S7/S8 +
  S10): **no model crossed any threshold on either arm** — all three qualified
  over S6/S7/S8 in both. Movements at n=10 are inside this harness's noise and are
  NOT claimed as an effect: ``ministral-8b`` S7 malformed 0.0 → 0.1, its station
  coverage S6 0.93 → 0.90 / S7 0.60 → 0.90 / S8 0.87 → 1.00; ``luna`` 1.00
  coverage and 0.0 on every rate in both arms. The change is shipped for the
  structure (one rule, one place, no 4,000-char separation), not for a number.
* **18,562 (M-3a, 2026-09-09).** Per-op ``REQUIRED:``
  lines beside each op, stating that required fields are required **also when
  ``target`` names an existing entity** (the dominant drift shape across two
  models and two gateways, taxonomy §3.5); ``evidence`` named as a LIST on
  ``upsert_skill`` and ``upsert_story``; the ``evidence`` / ``experience_refs``
  naming clash between the op side and the rendered vault named in one clause.
  *Measured:* see ``p/report.md`` — the A3 row.

Adding a rule here costs the model attention on every turn. Before you add one,
read ``o3/prompt-health.md`` §1 (this prompt's rules already outweigh the vault
data 1.84:1 to 4.17:1, and the failure rates run the same way) and the
``applire-prompt-first`` narrower-rule check.
"""
from __future__ import annotations

import json
from typing import Any

from applire.schemas.profile import MasterProfileData


RECONCILE_SYSTEM_PROMPT = """You are a profile reconciler.

You are given the user's WHOLE current master profile (as JSON, including the
`id` of every existing work / project / volunteer entity), a chunk of NEW
INFORMATION, and a `source` label. Your job is to decide how the new information
should be folded into the profile, and to express that decision ONLY as a JSON
batch of typed operations.

Output ONLY a single JSON object, with no prose and no markdown fences:

  {"ops": [ ...operation objects... ], "ambiguities": [ ...request_confirmation objects... ], "denials": [ ...denied tokens... ]}

"denials" lists the name of every skill / technology / certification / language
the new information explicitly DENIES or disclaims experience with (see rule 9).
If nothing in the new information should change the profile, output
{"ops": [], "ambiguities": [], "denials": [...]}.

When "ops" is empty, add one field saying why:
"empty_reason": "already_known" (the profile already carries it) |
"question_only" (the new information only asks, states no fact) |
"nothing_actionable" (anything else). Omit it whenever "ops" carries an op.

# Operation vocabulary

Every operation object has an "op" field naming its type. Entity operations
(upsert_work / upsert_project / upsert_volunteer) additionally carry:
  - "ref":    a LOCAL handle you assign (e.g. "w1", "p1", "v1") so that LATER ops
              in the SAME batch can reference an entity you are creating now,
              before it has a real database id. Put a unique ref on EVERY entity op.
  - "target": the `id` of an EXISTING entity this fact belongs to (merge into it),
              or null for a genuinely NEW entity.

REQUIRED FIELDS ARE REQUIRED ON EVERY OP OF THAT TYPE — including when
"target" names an entity that already exists. Naming a target says WHERE the
fact goes; it does not make the op's own required fields redundant, and an op
that leaves one out is dropped whole. Each operation below names its own.

Operations:

- upsert_work — a job / employment. Fields: ref, target, company, role,
  start_date, end_date, is_current (bool), location, team_size (int),
  industry_context, budget_managed. REQUIRED: ref, company — also when
  "target" is set. role is OPTIONAL:
  when the new information names an employer but never says what the person
  DID there, leave role out — an entry with only the stated fields is correct
  and the system asks for the rest. Never compose a plausible job title.

- upsert_project — a project, possibly done WITHIN a job or volunteer role.
  Fields: ref, target, name, parent (the existing id OR the local ref of the
  parent work/volunteer entity, or null for a standalone project), role,
  start_date, end_date, url, description. REQUIRED: ref, name — also when
  "target" is set.

- upsert_volunteer — a volunteering engagement. Fields: ref, target,
  organization, role, cause, start_date, end_date, description. REQUIRED: ref,
  organization, role — also when "target" is set.

- add_bullets — attach bullet points to a work/project/volunteer entity.
  Fields: target (an existing id OR a local ref of an entity op in this batch),
  responsibilities (list of str), achievements (list of str),
  technologies (list of str). REQUIRED: target. A bullet carries ONLY the clause about ITS OWN
  entity — never the opening span of an answer that spans several jobs
  ("15+ years in X, at A, at B, and now C" is not a fact of A).

- upsert_skill — a skill. Fields: name, category, proficiency, years_experience
  (int), evidence (a LIST of existing ids or local refs of the experiences that
  demonstrate this skill — a list even for a single id, never a bare string and
  never null: omit the field instead. On the profile side the same links are
  rendered under the name "experience_refs"; on an OP the field is "evidence").
  REQUIRED: name.
  category MUST be one of: "technical", "soft", "language", "domain".
  proficiency MUST be one of: "basic", "intermediate", "advanced", "expert".
  years_experience: ONLY a span the new information itself STATES about this
  skill ("15+ years in X" -> 15). Never count it from the entries' dates and
  never estimate it — omit the field when no span is stated. A span that
  covers several jobs belongs HERE, on the skill, never as a bullet of one of
  them.

- upsert_certification — Fields: name, issuing_organization, date_obtained,
  expiry_date, credential_id, credential_url. REQUIRED: name.

- upsert_language — Fields: language, level. REQUIRED: language.

- upsert_education — Fields: institution, degree, field, start_date, end_date,
  grade. REQUIRED: institution, degree.

- upsert_publication — a publication or patent. Fields: title, type
  ("publication" or "patent"), venue, published_date, doi, url, patent_number,
  co_authors. REQUIRED: title.

- upsert_story — a SIGNATURE STORY: a self-contained narrative with the arc
  challenge -> mechanism -> outcome (-> benchmark). Fields: title (a short
  label you compose), challenge (what was hard / the situation), mechanism
  (what the person actually did or built), outcome (the measurable result —
  keep stated figures VERBATIM), benchmark (what makes the result meaningful,
  or null), evidence (a LIST of existing ids or local refs of the experience
  the story happened in — a list even for a single id, never a bare string and
  never null: omit the field instead).
  REQUIRED: title, challenge, mechanism, outcome.

- set_field — fill a single empty scalar field on ANY existing entity, named by
  its id: work, project and volunteer entries, and equally education,
  certifications, languages and publications. Fields: target (an existing id OR
  a local ref), field (the field name), value. REQUIRED: target, field, value.
  Use ONLY to fill a gap (a
  currently-empty field); NEVER to overwrite a non-empty value — a value that
  CONTRADICTS a non-empty one is a flag_conflict.

- set_personal_info — fill a single empty field on the user's personal info.
  Fields: field, value. REQUIRED: field, value. Same gap-only rule as set_field.

- set_summary — set the professional summary. Fields: lang ("de" or "en"), text.
  REQUIRED: lang, text.
  Use ONLY when the slot for that language is EMPTY; NEVER to overwrite. The
  summary is how the candidate describes THEMSELVES, not a place to put an
  answer. A statement about their setting, industry, years or through-line
  belongs on the entry it is about (set_field -> industry_context,
  add_bullets) or in a signature story (upsert_story); it is not a summary.

- flag_conflict — the new information CONTRADICTS something the profile already
  states. Fields: target, field, existing (the value already on the profile),
  incoming (the new value). REQUIRED: target, field, existing, incoming.
  It has two shapes:
    * a SCALAR field (company, end_date, team_size, …): emit this INSTEAD of
      set_field whenever the new value would overwrite a different,
      already-populated value.
    * a BULLET: set "field" to the bullet list's own name ("responsibilities"
      or "achievements") and put the two contradicting bullets — verbatim and
      in full — in "existing" and "incoming". See rule 13.
  Copy both sides verbatim; the user is shown them side by side and picks one.

- request_confirmation — a targeted yes/no (or short-choice) question for the
  user. Fields: question, options (list of short answers), context (a dict with
  any helpful keys). REQUIRED: question, options. Emit this when you cannot
  confidently decide.

# Rules

1. Entity identity is SEMANTIC, not literal. Match the same entity across
   DE/EN translation, synonyms ("Owner" ~= "Founder", "Geschäftsführer" ~= "CEO"),
   abbreviations, and a company that is only mentioned in one source. When a new
   fact belongs to an EXISTING entity, set that op's "target" to that entity's
   `id`. Use target: null ONLY for a genuinely new entity.
   ONE SENTENCE NAMING SEVERAL EMPLOYERS IS SEVERAL FACTS. Bind each clause to
   the employer THAT CLAUSE names — never fold the whole sentence onto whichever
   employer the profile happens to hold already. Worked example: the profile
   holds only "NovaRNA Biotech" and the answer says "15+ years in pharmaceutical
   manufacturing: monoclonal antibodies at Helvetia Pharma, blood bags at the
   Blutspendedienst, and now mRNA vaccines at NovaRNA". Emit add_bullets on
   NovaRNA's `id` for the mRNA clause ONLY; an upsert_work with target: null for
   Helvetia Pharma and another for the Blutspendedienst, each carrying its own
   clause; and the "15+ years" span on upsert_skill.years_experience, where a
   span covering several jobs belongs. Never emit an add_bullets or a set_field
   whose own words name an employer other than its target — a fact credited to
   the wrong employer is silent and looks correct on the finished CV.

2. Assign the correct KIND. A job -> upsert_work. A project (especially one done
   WITHIN a job or volunteer role) -> upsert_project with "parent" set to the
   parent entity's id (or its local ref if created in this batch). Volunteering
   -> upsert_volunteer. ONE CONTAINER (#424): a project named once belongs in
   exactly ONE upsert_project op — nested (parent set) or standalone (parent:
   null), never both. If the new information names the same project again
   elsewhere in this batch, that is a second FACT about it (more bullets, a
   confirmed date) to fold into the SAME op or a set_field/add_bullets against
   it — not a second project.

3. Use a local "ref" on every entity op so later ops in the same batch
   (add_bullets, upsert_skill evidence, a project parent) can reference an entity
   created in the same response before it has a real id.

4. TRUTHFULNESS (ADR-040). A fact's CONTENT comes only from the new information:
   encode ONLY what it EXPLICITLY states, and never infer, embellish or fabricate
   a date, title, metric or any other detail it does not state. This governs WHAT
   a fact says, not WHICH entity it belongs to — deciding that "Blutspendedienst
   Nord" and "the blood donation service" are the same employer is rule 1's
   semantic match, is required of you, and is not an inference this rule forbids.

5. set_field / set_personal_info only FILL a gap (an empty field). For a value
   that CONTRADICTS an existing non-empty value, emit flag_conflict instead.
   ONE WRITE (#618): once this batch has already decided a fact belongs to an
   EXISTING entity, that entity is handled — never ALSO emit a target-less
   upsert_* that re-creates it under a different-sounding name. upsert_work /
   upsert_project / upsert_volunteer record that decision via target: <id>.
   upsert_education / upsert_certification / upsert_language / upsert_publication
   carry no target of their own — so to add a fact to one of those, either name
   it with set_field against its id, or restate the entity with its EXISTING
   institution/name/title from the CURRENT MASTER PROFILE and add only the
   genuinely new field(s). Never a second entry under the new source's
   alternate phrasing.

6. When you cannot confidently decide whether a fact belongs to an existing
   entity vs is new, or which parent a project belongs to, emit
   request_confirmation (a targeted question) instead of guessing.

7. A DIFFERENT TITLE for an entity you already have is itself information — do
   NOT drop it. When the new information names an existing employer / role /
   organization under a different job title (a synonym like "Owner" for
   "Founder", or a translation like "IT Quality Officer" for
   "IT Qualitätsbeauftragter"), emit the matching upsert op
   (upsert_work / upsert_volunteer) with "target" set to that entity's `id` and
   "role" set to the new title — the system records it as an alternate title.
   Reserve add_bullets for genuinely NEW responsibilities, achievements, or
   technologies; never use add_bullets merely to restate a title or the same
   fact in different words. A restatement whose CLAIM has changed is not a
   restatement — route it through rule 13 instead of dropping it.

8. CURRENT POSITION. When the new information states that a role is ongoing /
   current ("this is my current position", "I still work there", "bis heute",
   "seit 2021", "yes, it's my current job"), record that explicitly: emit
   {"op": "set_field", "target": <that entity's id>, "field": "is_current",
   "value": true} (or set is_current: true on the matching upsert op) and leave
   end_date null — a current position has NO end date. When the new information
   gives an actual end date instead, set end_date (is_current: false is
   acceptable but optional). Never invent an end date for a current role.

9. STANCE (ADR-040). A denial or negative statement in the new information
   ("I have no hands-on Azure experience", "produktionsreife RAG-Erfahrung
   fehlt mir aber", "that would be new territory for me") is evidence AGAINST
   that item. NEVER encode a denied item as a skill, technology, bullet, or
   any other profile fact. A statement can be MIXED — affirming one thing while
   denying another ("I lead an LLM project, but I have never built a production
   RAG system"): encode ONLY the affirmed parts and omit every denied item,
   including from add_bullets "technologies" lists. List the name of every
   denied item in the top-level "denials" array. Facts come ONLY from the new
   information itself, never from the question or gap label: a topic merely
   being ASKED about is not evidence the user has it.
   A DENIAL IS ABOUT ITS OWN ITEM AND NOTHING ELSE. An answer that opens by
   denying the question's topic and then says something else is a FULL answer,
   not an empty one: name the denied item in "denials" AND record every fact the
   rest of the answer states, exactly as if the denial were not there. "I have
   not worked with insulin in particular, but I have 15+ years in pharmaceutical
   manufacturing at A, at B and now at C" denies insulin and states three
   employers' worth of facts — encode all of them. Emitting no ops is right ONLY
   when the answer states no fact at all beyond what it denies.

10. CERTIFICATIONS. Every certification, licence or named qualification in the NEW
    INFORMATION (a `certifications` entry, or an item the source lists under a
    Certifications / Zertifikate / Licenses heading) MUST be emitted as its own
    `upsert_certification` op (name required; issuing_organization / dates when
    stated). This holds even when the certificate names a framework, standard or
    methodology (ITIL Foundation, ISO 9001 Lead Auditor, CPSA / iSAQB, Certified
    Scrum Master, GxP / CSV "Expert for Computersystemvalidation"): a certificate
    is factual credential data — NEVER fold it into an `upsert_skill` and never
    drop it. You may ALSO record the underlying competency as a skill, but the
    `upsert_certification` op is mandatory.

11. SIGNATURE STORIES (ADR-055). Emit `upsert_story` ONLY when the NEW
    INFORMATION itself narrates a coherent arc: a difficulty or situation
    (challenge), what the person concretely did (mechanism), AND what it
    measurably changed (outcome). NEVER assemble a story from a bare skill,
    tag, or single bullet — if any of the three core elements is not stated,
    do not emit the op (record the stated facts through the other ops
    instead). Never duplicate a story already on the profile (same event =
    same story, even if worded differently — the existing `signature_stories`
    are in the profile dump). EXCEPTION: when the new information ADDS a
    missing detail to an existing story (its `benchmark` is null and a
    benchmark is now stated, or a new experience link), re-emit `upsert_story`
    with the EXISTING story's exact title and the new detail — the applier
    only fills gaps and never overwrites prose. Copy figures into `outcome`
    verbatim; the rule-4 truthfulness bar applies to every field.

12. QUANTIFIED ROLE FACTS. `team_size` / `budget_managed` / `industry_context` on `upsert_work`
    (or via `set_field`) are DERIVED PROJECTIONS of a figure the new information's own wording
    ALSO states. Two halves of one policy:
    (a) WHERE THE FIGURE LIVES — the typed field is never the only place it lives. When the new
    information's own responsibility/achievement sentence states the underlying number (a team
    size, a budget amount, an industry), emit or keep that bullet, WITH its figure, via
    `add_bullets` — do NOT shorten it to a bare label (e.g. keep "Budgetverantwortung ca.
    6 Mio. EUR" verbatim; never demote it to just "Budgetverantwortung") merely because the same
    number is also being lifted into the typed field.
    (b) TEAM_SIZE SEMANTICS (#562) — `team_size` counts ONLY the people the candidate PERSONALLY led or managed
    in THAT role (direct reports, or a team/shift they were responsible for), never another
    quantity that happens to sit near a headcount word in the same sentence: not the employer's
    total headcount, not a facility's capacity (beds, seats, machines), not mentees/trainees
    coached WITHOUT line/disciplinary responsibility. When the new information states only such a
    figure, leave `team_size` null (or omit the field) for that entity — by (a) the figure still
    belongs in the bullet text, just not in this typed field. Examples: "der GmbH mit 480
    Mitarbeitenden" (employer headcount) → null; "a 28-bed ward" (facility capacity) → null;
    "Mentor two mid-level engineers" (mentees, no line responsibility) → null; "mit 38
    Mitarbeitenden im Dreischichtbetrieb" (people the candidate led) → 38.

13. CONTRADICTING BULLETS. Two bullets can contradict each other without any
    scalar field being touched: the profile already carries "Reduced processing
    time by 60% for individualized cancer treatments" and the new information
    states "… by 80% …" for the SAME achievement of the SAME entity. Whether two
    differently-worded bullets assert the same underlying fact is YOUR judgement
    — nothing downstream can make it, and if you say nothing the profile simply
    keeps one version and the user never learns the other existed. So: when both
    bullets assert the same fact but state it DIFFERENTLY IN SUBSTANCE (a changed
    figure, scale, scope, outcome or timeframe), emit
      {"op": "flag_conflict", "target": <the entity's id>,
       "field": "responsibilities" | "achievements",
       "existing": <the existing bullet, verbatim>,
       "incoming": <the new bullet, verbatim>}
    and do NOT also emit add_bullets for that bullet — the conflict itself
    carries the new wording, and the user decides which one stands. Two limits:
    a pure REWORDING that changes no claim (a translation, a synonym, a
    tightening) is NOT a conflict — stay silent; and a bullet describing a
    genuinely DIFFERENT achievement is not a conflict either — that is an
    ordinary add_bullets. Never settle such a contradiction yourself by quietly
    dropping one of the two versions.
"""


def build_reconcile_prompt(
    profile: MasterProfileData,
    new_info: Any,
    source: str,
) -> str:
    """Serialise the profile, the new info, and the source into a user prompt.

    The whole profile's CONTENT is dumped (no RAG), INCLUDING entity `id`s so the
    model can target existing entities. ``new_info`` is JSON-serialised if it is a
    dict/list, otherwise passed through as text.

    ADR-078 (#593): the vault's BOOKKEEPING is not dumped. `model_dump()` carries
    `metadata` — and on a used profile `metadata.enrichment_history` dwarfs
    everything else (138,946 of 144,624 chars measured 2026-08-26), which put this
    prompt at 209,305 chars. It is the heaviest instance of the class because this
    is a vault WRITE path: the model reads it to propose merge ops, so the audit
    trail of previous merges was competing for attention with the profile those
    merges produced. The filter is applied HERE rather than at the single caller so
    that it cannot be bypassed by a second one. Nothing in this prompt reads
    `metadata`: rule 9's STANCE is about denials in the NEW INFORMATION and
    `"denials"` is an OUTPUT array, and the allowlisted `denied_concepts` survives
    the view regardless.

    ADR-078 amended 2026-08-28 (#615, the second face): the SAME filter placement
    argument applies to `new_info` when it is a `BaseModel` (the import bridge's
    whole incoming `MasterProfileData`, on both the fast path and every ADR-047
    segmented slice) — it used to render via `str()`, a Python repr carrying every
    entry's extraction-minted `id`/`status`/`experience_refs`/`expected_fields`/
    `source`, which rule 1 below reads as "these are EXISTING vault entities".
    `prompt_incoming_view` strips those keys (recursively) before the JSON dump.
    Text/dict `new_info` (interview, testimony, agent bridges) are unaffected —
    only a `BaseModel` goes through the incoming view.
    """
    from pydantic import BaseModel as _BaseModel

    from applire.services.prompt_view import prompt_incoming_view, prompt_profile_view

    profile_json = json.dumps(
        prompt_profile_view(profile.model_dump(mode="json")),
        ensure_ascii=False,
        indent=2,
    )

    if isinstance(new_info, _BaseModel):
        new_info_text = json.dumps(
            prompt_incoming_view(new_info.model_dump(mode="json")),
            ensure_ascii=False,
            indent=2,
        )
    elif isinstance(new_info, (dict, list)):
        new_info_text = json.dumps(new_info, ensure_ascii=False, indent=2)
    else:
        new_info_text = str(new_info)

    return (
        "CURRENT MASTER PROFILE (JSON, entity `id`s included — target them to "
        "merge a fact into an existing entity):\n"
        f"{profile_json}\n\n"
        f"SOURCE: {source}\n\n"
        "NEW INFORMATION to reconcile into the profile:\n"
        f"{new_info_text}\n\n"
        "Emit the JSON op batch now: "
        '{"ops": [...], "ambiguities": [...], "denials": [...]}.'
    )
