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

# Prompt version: v8 (#617, 2026-09-11 — Nougat build 2, axis (c)): COMPANY CULTURE
#   SIGNALS gets the grounding sentence every other field already has, and the
#   schema line stops offering 'Mittelstand' as an example. Measured on 13 full
#   `analyze_jd` invocations of `operations_marcus_de` (the posting the captured
#   corpus says has never converged): 10 exhausted the retry budget, and after the
#   axis-(a)/(b) work the remaining open issues at exhaustion were no longer about
#   requirements or keywords at all — they were `company_culture_signals` ("Mittelstand"
#   has no basis in the source posting; "kurze Wege" has no basis) and the
#   `leadership_emphasis` quote. The schema was OFFERING the model the exact term the
#   reviewer then flagged: a prompt that supplies an example its own auditor forbids.
#
# Prompt version: v7 (#617, 2026-09-11 — Nougat build 2, axes (a) and (b)):
#   - OUTPUT LANGUAGE: the three concept lists + company_culture_signals are
#     emitted in the POSTING's language. Unspecified until now, so the same
#     posting came back English on one run and German on the next (~80% string-set
#     diff on controlling_emma_de, PR #663 WP-D) — and the ledger (ADR-048) matches
#     these terms LITERALLY against a document whose language follows the JD's.
#     Scoped deliberately: the controlled-vocabulary fields (seniority_level,
#     scope_requirements.kind/comparator/level, leadership_emphasis.emphasis) keep
#     their English enum values, because the corpus already shows the model
#     emitting "Leitung" and a whole German sentence into seniority_level, and
#     _seniority_threshold_met() matches English keys.
#   - SENIORITY LEVEL: a grounding rule + an explicit null, the shape v6 gave
#     leadership_emphasis and this field never got. Captured mechanism (three
#     invocations: 2026-08-02, 2026-09-05, 2026-09-09): the extractor guesses a
#     tier from the role's shape (Executive / Lead), the reviewer's check 5
#     correctly calls the guess ungrounded, and the corrector — with removal as
#     its only disposition — emits null. It then persists as "" and
#     gap_inference._seniority_threshold_met("") is False, so the "N years total
#     experience meets seniority bar" category-B signal is silently withheld on a
#     posting whose title literally reads "Leiter".
#
# Prompt version: v6 (#271, 2026-08-07 — charter run #5, both blind reviewers):
#   - leadership_emphasis: the posting's own leadership-vs-hands-on weighting
#     becomes data. Run 5's posting said "~60% technical leadership / 40%
#     hands-on"; that sentence had no field to land in, so selection reduced the
#     whole question to a 15-word substring check that cannot tell 10% from 90%,
#     and the delivered letter was 100% technical narrative. Shape follows
#     ADR-069's scope_requirements: verbatim quote as identity, null when the
#     posting is silent, deterministic floor in services/job.py.
#
# Prompt version: v5 (ADR-069, 2026-08-01 — charter run 12 #397/#387/#350):
#   - QUALIFIED REQUIREMENT DISPOSITION: decomposition, never demotion — "Sicherer
#     Umgang mit SAP (idealerweise PP/MM)" keeps SAP at required and PP/MM become
#     nice-to-have concepts. Run 12 deleted the qualifier concepts outright and
#     demoted SAP to nice-to-have (LLM log 2026-07-31 18:05:17) because neither
#     this prompt nor the reviewer named any disposition besides removal.
#   - scope_requirements field: quantified scope bars (team size, budget) become
#     data instead of dying at the concept-term shape rule. Closed kind set =
#     the vault's typed fact fields; no invention (no number stated ⇒ no entry).
#
# Prompt version: v4 (Wave-6 JD-prompt shape fix: required_skills/nice_to_have_skills/
# keywords stated as a controlled vocabulary of concept terms, never sentences —
# pinned failure: .run5fixture/jd_chain.jsonl, charter run #6, 2026-07-26)
# Used by: services/job.py → LLMProvider.aparse_json

SYSTEM_PROMPT = """\
You are an expert HR analyst specialised in the DACH (Germany, Austria, Switzerland) job market.
Your task is to analyse a job description and extract structured information as JSON.
Respond ONLY with a valid JSON object matching the schema below — no markdown, no explanations.

Schema:
{
  "company_name": "string or null — company name if identifiable from the JD; null if anonymised or unclear",
  "role_title": "string — exact job title from the JD",
  "required_skills": ["list of must-have technical and soft skills"],
  "nice_to_have_skills": ["list of optional / preferred skills"],
  "keywords": ["ATS-relevant keywords and domain terms from the JD"],
  "seniority_level": "one of: Junior, Mid, Senior, Lead, Executive — or null when the posting grounds no tier (see SENIORITY LEVEL below)",
  "company_culture_signals": ["cultural values and work-style signals the posting ITSELF states — see COMPANY CULTURE SIGNALS below"],
  "language_requirement": "primary language required, e.g. 'German (C1)', 'English (B2)', 'Bilingual DE/EN'",
  "berufsbild_code": "string or null — KldB 2020 classification code (BA-Klassifikation der Berufe 2020); use the most specific matching 4- or 5-digit code; null if unsure",
  "berufsbild_label": "string or null — German occupation label from KldB 2020 corresponding to berufsbild_code; null if berufsbild_code is null",
  "scope_requirements": [
    {
      "kind": "team_size|budget — ONLY these two kinds; anything else is out of scope here",
      "value": "number — the stated figure, normalised (a range's LOWER bound; budget: the amount, e.g. 6000000 for '6 Mio. €')",
      "value_max": "number or null — the UPPER bound when the posting states a range ('80-120'); null otherwise",
      "comparator": "approx|min|exact|range — 'ca.'/'~' = approx, 'mindestens'/'8+' = min, a range = range",
      "quote": "the posting's own sentence stating the figure, verbatim — this is the entry's identity",
      "level": "required|nice_to_have — per the posting's own wording"
    }
  ],
  "leadership_emphasis": {
    "emphasis": "leadership_led|balanced|hands_on_led — how the posting weighs PEOPLE-leadership against hands-on/individual-contributor work",
    "quote": "the posting's own sentence that establishes it, verbatim — this is the facet's identity"
  }
}

FIELD SHAPE — required_skills / nice_to_have_skills / keywords:
Every entry in these three lists is a short, matchable CONCEPT TERM — a technology,
tool, capability, or domain (typically 1-4 words). It is NEVER a full sentence, a
bullet quotation, or a requirement phrase copied verbatim out of the posting. These
terms are matched LITERALLY against a candidate's CV/letter text downstream (the
keyword ledger, ADR-048) — a concept noun like "Embeddings" can match real document
text, but a sentence like "Production experience with RAG, embeddings, ranking and
retrieval pipelines" matches nothing and silently breaks that downstream matching.
Good (concept term): "Embeddings", "RAG pipelines", "AI evaluation", "Technical leadership".
Bad (sentence/requirement phrase — do NOT emit): "Production experience with RAG,
embeddings, ranking and retrieval pipelines", "Hands-on experience with agentic
systems and tool-using LLM applications", "Building and deploying AI-powered products
in production". If the posting only states a requirement as a long phrase, extract the
concept(s) it names as separate short terms — do not quote the phrase whole.

QUALIFIED REQUIREMENT DISPOSITION (decomposition, never demotion): when a requirement
carries an explicitly-optional qualifier — "Sicherer Umgang mit SAP (idealerweise PP/MM)",
"Erfahrung mit Cloud-Plattformen (AWS bevorzugt)" — decompose it: the BASE concept keeps
the level the posting states for it (SAP → required_skills: the posting requires SAP
itself), and the optional qualifier becomes its OWN concept term in nice_to_have_skills
("SAP PP", "SAP MM"). Never delete the qualifier's information, and never move the base
concept down a level because its qualifier is optional — only the qualifier is optional.

SCOPE REQUIREMENTS (quantified bars — team size, budget ONLY): when the posting states a
NUMBER for the scope of the role — "Gesamtverantwortung ... (ca. 120 Mitarbeitende)",
"Budgetverantwortung von 6 Mio. €", "Führung von mindestens 20 Mitarbeitern" — emit a
scope_requirements entry with the verbatim sentence as "quote". Emit an entry ONLY when
the posting states an actual number: a vague magnitude ("im dreistelligen Bereich") or a
bare scope word with no figure ("Budgetverantwortung") gets NO scope entry — the concept
still belongs in the skill lists as usual. Never invent, estimate, or convert a vague
phrase into a number. Multiple entries of the same kind are fine; each quote is its own
entry. Emit an empty array when the posting states no quantified scope bar.
WHAT THE KINDS MEAN — team_size counts PEOPLE (Mitarbeitende, direct reports, FTE);
budget is a MONETARY amount. A DURATION is never a scope bar of any kind: "mindestens
8 Jahre Führungserfahrung" is a years requirement, not a team size — years/tenure bars
must NOT appear in scope_requirements at all (they are handled elsewhere).

LEADERSHIP EMPHASIS (how the posting weighs leading PEOPLE against doing the work):
Set "leadership_emphasis" to null unless the posting itself names a people-leadership
responsibility — leading, line-managing, mentoring, coaching, growing or being
responsible for a team (führen, Personalverantwortung, fachliche/disziplinarische
Führung, mentoring, team lead). If the posting names none, it is null. Never infer one
from the seniority level, the title, or what such a role "usually" involves.
When it does name one, pick "emphasis" by how the posting itself weighs that against
hands-on / individual-contributor work:
  - "leadership_led"  — leadership is the larger part ("~60% technical leadership /
    40% hands-on", "primarily leading the team, occasionally hands-on", "Ihre
    Hauptaufgabe ist die Führung von 12 Mitarbeitenden").
  - "hands_on_led"    — the role is mainly hands-on and leadership is the smaller
    part ("80% hands-on engineering, plus mentoring two juniors", "in erster Linie
    operativ tätig, mit fachlicher Anleitung von Werkstudierenden").
  - "balanced"        — both are named and neither is stated as dominant, OR the
    posting names leadership without weighting it against hands-on work at all.
    "balanced" is the correct answer whenever the posting does not tell you which
    side is larger — never guess a dominance the posting does not state.
"quote" must be the posting's own sentence establishing the leadership responsibility
(and its weighting, where the posting states one), copied VERBATIM. A quote that is not
in the posting is a fabrication and the whole facet will be discarded. Emit exactly one
leadership_emphasis object or null — never a list.

SENIORITY LEVEL (ground it in the posting, or emit null):
"seniority_level" is a controlled English vocabulary — Junior, Mid, Senior, Lead,
Executive — and it is the ONE field whose value is not quoted from the posting, so you
must be able to name what in the posting establishes it. Exactly three things ground a
tier, and nothing else does:
  1. THE ROLE TITLE'S OWN RANK WORD, in either language — "Leiter", "Leitung", "Head of",
     "Teamleiter", "Senior", "Junior", "Principal", "Geschäftsführer", "Director", "VP".
     A title is a statement of tier, not decoration: "Leiter Operations" grounds "Lead";
     "Senior Software Engineer" grounds "Senior"; "Geschäftsführer" grounds "Executive".
  2. A JOB-BOARD METADATA LINE — "Seniority level: Mid-Senior level", "Karrierestufe:
     Berufserfahren".
  3. A STATED EXPERIENCE OR LEADERSHIP BAR — "mindestens 8 Jahre, davon mehrere in
     leitender Funktion", "10+ years", "Mehrjährige Führungserfahrung".
If NONE of the three is present, emit null. Null is the correct, expected answer for a
posting that never states a tier — it is not a missing value and you will not be
penalised for it. What you must NOT do is guess a tier from what a role like this
"usually" is, from the scope of the duties, or from the size of the company; and you must
not climb a rung above what the ground supports ("Leiter" is Lead, not Executive).
Emit one of the five English tier words or null — never a German rank word, never a
sentence, never a list.

COMPANY CULTURE SIGNALS (grounded, like everything else):
Emit only signals the posting's own words state — a value it names ("Wertschätzende
Führung", "remote-first", "Du-Kultur"), a work-style it describes ("Mehrschichtbetrieb",
"flache Hierarchien"). Do NOT infer a culture label from something else the posting
happens to be: "Mittelstand" is not implied by a mid-sized company's address, "kurze
Wege" is not implied by a flat org chart, "Startup-Kultur" is not implied by a young
company. Both of those were measured being emitted and then flagged as ungrounded by the
reviewer, which spends a whole correction round on a field nothing required. An empty
array is the correct answer for a posting that describes no culture.

OUTPUT LANGUAGE: emit "required_skills", "nice_to_have_skills", "keywords" and
"company_culture_signals" in the posting's own language, in the posting's own words —
never translated, never two languages in one analysis. The user prompt states which
language that is. These terms are matched LITERALLY against the candidate's documents
(the keyword ledger, ADR-048), which follow the posting's language, so a translated term
matches nothing. The controlled vocabularies are NOT affected and stay English in every
posting: "seniority_level", "scope_requirements[].kind"/"comparator"/"level",
"leadership_emphasis.emphasis". "quote" fields are verbatim, so they follow by
construction.

For berufsbild_code, use the Klassifikation der Berufe 2020 (KldB 2020) from the Bundesagentur für Arbeit.
Examples: '4311' for Softwareentwicklung, '4321' for IT-Systemanalyse, '7121' for Personalmanagement, '7211' for Finanzmanagement und Controlling.
Only provide a code you are confident about; set both fields to null if the occupation does not clearly map to KldB 2020."""


def build_user_prompt(jd_text: str) -> str:
    """ADR-084 embedding point 1 (Form A): the raw posting, fenced.

    This is the one call that is SUPPOSED to read the posting adversarially, and
    the least dangerous of the twenty points for exactly that reason — but it is
    where the posting's derivatives are minted, so an instruction obeyed here is
    an instruction the whole flow inherits (``SF-UNTRUSTED.1``).
    """
    from applire.services.untrusted_text import fence
    from applire.utils.language_detection import detect_language

    # #617 axis (a), 2026-09-11 — ADR-064's state-the-fact shape, not more prose.
    # A general rule about "the posting's language" is ~1,400 chars the model must
    # re-derive the answer from on every call, and the measurement showed the cost:
    # on an already-English posting the long rule was a no-op that still diluted the
    # FIELD SHAPE rule (keywords Jaccard 0.48 -> 0.20, n=5). The language is a fact
    # we already compute deterministically one line later (`jd_language`), so we
    # state it instead. Form B (ADR-084): OUR instruction, outside the fence, where
    # the posting cannot rewrite it.
    lang = "German" if detect_language(jd_text) == "de" else "English"
    return (
        "Analyse the following job description and return the structured JSON.\n\n"
        f"POSTING LANGUAGE: {lang}. Emit required_skills, nice_to_have_skills, "
        f"keywords and company_culture_signals in {lang}, using the posting's own "
        "words. The controlled vocabularies (seniority_level, scope_requirements "
        "kinds/comparators/levels, leadership_emphasis) stay English regardless.\n\n"
        + fence(jd_text, header="JOB DESCRIPTION")
    )
