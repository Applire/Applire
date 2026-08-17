# Panel-Review Test Cases — the document-quality gate's canonical inputs

End-to-end **document-quality** test cases: the full Applire process runs to generated
documents, and a run only counts as green when a **blind simulated hiring panel**
(HR screener + hiring manager subagents) decides to **invite the candidate**.
Process checks (import, gaps, interview, Oracle, ATS) verify *honesty*; these cases
verify the *outcome* — would the application actually earn an interview?

Procedure: `applire-journey-walkthrough` skill, §"Hiring-panel quality gate"
(panel contract shared with `applire-edge-uat` Phase 4).

## Case index (persona archetypes are industry-agnostic — the family deliberately spans occupations)

| Case folder | Persona archetype | Occupation | Language | Distinct stress |
|---|---|---|---|---|
| `it_backend_daniel/` | Marcus | IT / backend engineering | EN | Baseline; tech-skill gaps, PSD2 honest partial, blockchain denial |
| `operations_marcus_de/` | Marcus | Manufacturing / operations | **DE** (same-language DE run) | Non-IT vocabulary (Lean/MES/ISO), leadership-span honesty, IFS/BRC denial, German Anrede/letter norms |
| `nursing_priya_relocator/` | Priya | Healthcare / nursing | EN → DACH employer | Relocator process-states (Anerkennung, language level) that must never be rounded up; ECMO no-overclaim; paediatric denial |
| `controlling_emma_de/` | Emma (occupation stand-in) | Finance / controlling | **DE** | Certification-vs-responsibility precision (IFRS "not lead-responsible"), soft-skill leadership honesty, US-GAAP denial |

## Shared design shape (every case)

1. **Two CV sources** (CV + LinkedIn/XING-style export) — exercises two-source merge and dedupe.
2. **A JD engineered for a pre-interview match of 0.55–0.80** (the band, not a point, is the assertion — LLM classification wobbles). Per-case exception: `operations_marcus_de` is recalibrated to 0.40–0.55 (#554) — its JD's sub-concept density makes the extraction denominator structurally exceed this band's assumption; see that case's README.
3. **An off-CV dossier** (`dossier_*.md`) — facts the candidate genuinely has that appear in NEITHER CV, keyed to the designed gaps. The tester answers interview questions **only** from the CVs + dossier, first person, 2–5 sentences, never inventing.
4. **One verbatim explicit denial** per case — must yield an honest status (`denial_recorded` on the agent channel) and the concept must **never become claimable** afterwards (ADR-059).
5. **At least one no-overclaim trap** — a partial the documents must state precisely (assisted-not-independent, in-progress-not-done, course-not-responsibility). An unbounded claim in any generated document is a truthfulness finding.
6. **Expected panel outcome: HR pass = yes, hiring manager invite = yes.** Full designed-shape table and per-case expectations in each case's `README.md`.

## Interpreting panel results

- **Quality-"no"** (evidence exists in the vault but the documents bury it; impression "evasive"/"inflated") → a **finding** on generation.
- **Fit-"no"** on these cases → the run drifted or generation buried the evidence — investigate, don't shrug (the cases are designed to be invitable once the dossier evidence lands in the vault).
- Match reviewers to the market: DE cases get German-speaking reviewers judging DACH conventions (photo/address, MM/YYYY, Anschreiben norms); the nursing case gets international-programme reviewers.

Synthetic personas — no real personal data; contact strings use example.com.
