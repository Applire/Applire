# Render-budget overrun fixture — Stefan Brandt (inflated)

Synthetic, designed overflow case for the `RENDER_BUDGET_ITERATION` instrument
(ADR-076 Amendment 3 §3) and the condense loop. Derived from the
`panel_review_case/operations_marcus_de` family (same synthetic persona,
example.com contact); pair it with that case's JD
(`../panel_review_case/operations_marcus_de/jd_rheinwerk_leiter_operations.md`).

Designed shape (why this CV overflows a 1-page target):

- **6 roles (1994–today)** — structural length.
- **2 projects with bullets attached to the top role** — deliberately opens the
  cap-vs-condense counting seam: the bullet cap counts role bullets only
  (`cv.py` `_cap_bullets`), while condensation counts role + project bullets.
  Reconcile that seam in the #540 build before relying on either count.
- **11 continuing-education entries** — structural overflow that survives
  bullet-capping.

Exercised for real on 2026-08-19-era dev stack (fresh DB, OpenRouter Luna,
`POST /api/cv/generate` with `target_pages=1` — the legitimate channel override
below the UI floor, E042 precedent): both flags fired per terminal round
(`condense_fired=True` at iteration 1, `condensation_exhausted=True` on the
WARNING else-branch at iteration 2). Durable gate record: comment on
Applire/Applire#540.

Import note: Luna's extraction once failed this file with `invalid_document`
when project roles were implicit — the project role lines below are explicit for
that reason.
