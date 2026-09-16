# Cross-language two-source import — the #707 case

Synthetic. The persona is the `panel_review_case/it_backend_daniel/` engineer (Daniel Kovač,
example.com contact strings); this folder adds the **German first source** the incident needs.

## Design shape

| Import | File | Language | Role in the case |
|---|---|---|---|
| 1st (creates the profile) | `cv_daniel_kovac_de.md` (this folder) | **DE** | Every entry in German surface form: roles, degree, skill names, language names |
| 2nd (merges) | `../panel_review_case/it_backend_daniel/linkedin_daniel_kovac.md` | **EN** | A pure RESTATEMENT of the first source — same three positions, same degree, same two certifications, same skills and languages, no fact that the German CV lacks |

Pairs the second import restates in another surface form (what the reconciler must recognise as
the SAME entity, ADR-046 rule 1):

| German (in the vault) | English (incoming) | Kind |
|---|---|---|
| Deutsch / Englisch | German / English | language names |
| B.Sc. Informatik | B.Sc. Computer Science | degree |
| Senior Backend-Entwickler / Backend-Entwickler / Werkstudent Softwareentwicklung | Senior Backend Engineer / Backend Engineer / Working Student Software Engineering | role titles (same employers) |
| Störfallmanagement | Incident Management | skill |
| Mentoring von Nachwuchskräften | Mentoring | skill |
| REST-Schnittstellenentwicklung | REST APIs | skill |
| Vertragsbasierte Tests | Contract Testing | skill |
| Python · Django · Flask · PostgreSQL · SQL · GitLab CI/CD · Amazon Web Services (AWS) | identical | control group — carried by the witness's exact-key arm regardless |

## Expected outcome

After the second import: **no new skill, language, education or certification row** (the vault stays
at the German entries — the EN names are restatements), and the import's `not_applied` receipt
(`EnrichmentRecord.not_applied`, `ImportNotApplied`) is **empty**, because every translated entry is
bound to its German counterpart by a `match_existing` op (ADR-046 amended 2026-09-16) which the
import witness (`reconcile/import_witness.py`, arm (c) sub-clause 3) reads as carried.

Before that amendment the same run listed the translated pairs as `no_op_carried_entry` — the false
"Not carried over from your import: English, German" callout of Bug #707. That is the measured
"before" arm this case exists to reproduce.

Read the outcome from the PERSISTED receipt (`GET /api/profile/changes` → `enrichment_history`,
the head record's `not_applied` and its `changes` with `rationale_key == "reconcile_matched"`),
never from the overlay.
