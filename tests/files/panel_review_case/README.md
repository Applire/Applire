# Panel-Review Test Case — "Daniel Kovač → NovaPay Senior Backend Engineer (Payments)"

Canonical end-to-end **document-quality** test case: the full Applire process runs to
generated documents, and the run only counts as green when a **blind simulated hiring
panel** (HR screener + hiring manager subagents) decides to invite the candidate.
Process checks alone (import, gaps, interview, Oracle, ATS) verify *honesty*; this case
verifies the *outcome* — would the application actually earn an interview?

Procedure: `applire-journey-walkthrough` skill, §"Hiring-panel quality gate".
The panel briefing contract mirrors `applire-edge-uat` Phase 4.

## Files

| File | Purpose |
|---|---|
| `cv_daniel_kovac.md` | Primary CV (text — usable via UI paste or `import_cv(text=…)`) |
| `linkedin_daniel_kovac.md` | Second source (LinkedIn-style export) — exercises two-source merge/dedupe |
| `jd_novapay_senior_backend.md` | The job description (English; German market company) |
| `dossier_daniel_kovac.md` | **Off-CV dossier** — candidate facts NOT in either CV, keyed to the expected gaps. The tester answers interview questions ONLY from CV + dossier; the honest denial is in here too. |

## Designed shape

The CVs deliberately omit evidence the candidate genuinely has (dossier), so the
pre-interview match lands mid-band and the interview has real work to do:

| JD requirement | In CVs? | In dossier? | Expected classification |
|---|---|---|---|
| Python 5+ yrs, Django/Flask, PostgreSQL, AWS, CI/CD, mentoring, German, English | ✓ | — | direct (A) |
| FastAPI | partial (Flask/Django only) | ✓ migrated two services | B — uncaptured strength |
| Kafka / event-driven | ✗ | ✓ order-event pipeline | B — uncaptured strength |
| Kubernetes | ✗ | ✓ led 12-service migration (signature-story material) | B — uncaptured strength |
| Observability (Prometheus/Grafana) | ✗ | ✓ dashboards + SLO alerts | B — uncaptured strength |
| Payments domain / PSD2 | ✗ | partial (one Stripe PSP integration; **no PSD2**) | C — position honestly |
| Blockchain/crypto (nice-to-have) | ✗ | **explicit denial** ("no blockchain experience — honest gap") | C — must become `denial_recorded`, never claimable |

## Expected outcomes (acceptance)

1. **Pre-interview match: 0.55–0.80** (mid-band; LLM classification wobbles — the band, not a point, is the assertion).
2. **Interview**: questions target the gap clusters; tester answers strictly from the dossier; the blockchain denial returns an honest status (`denial_recorded` on the agent channel) and the concept is **never claimable** afterwards.
3. **Post-interview**: match increases; Kafka/Kubernetes/observability/FastAPI evidence lands in the vault with receipts; the Kubernetes migration arc is signature-story material.
4. **Documents**: CV + cover letter generate; Oracle reports grounded-dominated; no fabricated PSD2/blockchain claims; ATS headline honest about anything still missing.
5. **Panel (the new last check)**: blind HR screener → *pass-to-hiring-manager: yes*; blind hiring manager → **invite to interview: yes**. A "no" for *document-quality* reasons (evasive, inflated, key evidence missing from the documents although it's in the vault) is a **finding**; a "no" for genuine *fit* reasons should not occur with this case's design — if it does, either the design drifted or generation buried the evidence: investigate, don't shrug.

## Rules for the tester

- Answer interview questions **only** from the CVs + dossier — never invent.
- Deliver the denial verbatim when blockchain comes up.
- Run the panel **blind** (reviewers screen "an incoming application", never told a tool wrote it), subagents on **sonnet**, in parallel, with the fixed output contract from the skill.
- Verify content claims at the DB JSONB, not the preview.

Synthetic persona — no real personal data. Contact strings use example.com.
