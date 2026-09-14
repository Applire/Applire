# Extraction-parity fixtures (M5.1.2 / M5.1.3, Nougat build 2)

Two synthetic CV texts used to measure prompt rules on the two CV-ingestion
doors (`prompts/cv_extraction.py`, the web-UI door; `prompts/profile_extraction.py`,
the agent/paste door). They are NOT pytest fixtures — CI never calls a provider.
The measurement driver is `scripts/extraction_probe.py` (opt-in, real provider).

## The data is synthetic

"Jonas Weiler" does not exist. `jonas.weiler@example.com`, Kaltenbach
Kunststofftechnik GmbH and Rheinstahl Umformtechnik GmbH are invented. No
content comes from a real profile and none ever may (E060 §3.1's architecture
boundary; `Documents/testdata/RealProfiles/` is off limits).

## `multi_employer_kenntnisse.txt` — the #407 shape

Two employers. `SAP PP` appears exactly twice: in the **Rheinstahl** role's own
bullet, and in the general `KENNTNISSE` section. It appears NOWHERE in the
Kaltenbach role's own text.

* **Correct:** `SAP PP` is in Rheinstahl's `technologies` and in the top-level
  skills list; Kaltenbach's `technologies` does not name it (it is not evidence
  about that role). `MS Excel` behaves the same way.
* **The defect it measures:** a general skills-section item backfilled onto the
  most recent / current role — #407's run-12 evidence.
* **Second trap, same file:** "zeitweise als stellvertretender Werkleiter" is a
  sub-role inside a bullet. `work_history` must hold exactly TWO entries, both
  with a non-empty company — never a third, company-less shell entry (the
  VALID ENTRIES ONLY rule).

## `certification_heavy.txt` — the invented-issuer shape

Five items under a `ZERTIFIKATE UND QUALIFIKATIONEN` heading. Only two state an
issuer (`IHK Koblenz`, `TUEV Rheinland`).

* **Correct:** five `certifications` entries; `issuing_organization` is present
  ONLY on the two that state one, and `null`/absent on the other three.
* **The defect it measures:** "Herstellerschulung Spritzgiessmaschinen" invites
  an issuer invented out of the German compound's own first morpheme
  ("Hersteller" = manufacturer). A fabricated issuer is a vault write the
  candidate never made.

## Captured extractions (E-1, 2026-09-13)

`multi_employer_kenntnisse_hard.violating.json` and `.clean.json` are two real
extractions of `multi_employer_kenntnisse_hard.txt`, taken verbatim from the
build-2 measurement records (`ministral-8b-2512`, runs 2 and 1). They exist so the
REVIEWER can be measured without re-extracting: the positive case is guaranteed to
carry the #407 defect (`SAP PP` / `MS Excel` backfilled onto the Kaltenbach role,
whose own passage names neither) and the negative case is guaranteed not to
(`Proficy` / `Grafana`, both named in its own bullet). Re-extracting instead would
have left the measurement at the mercy of whether that run happened to reproduce
the defect at all.

They are also honest about themselves: BOTH carry unrelated defects the reviewer
correctly blocks on (`team_size: 38` and a `budget_managed` the source never
states), which is why "did the reviewer block?" is not the metric — "did it raise a
blocking issue naming a misattributed tool?" is. Used by
`scripts/extraction_probe.py --probe review_per_entry_tech`.
