# Replay fixtures — real captured LLM output, committed

## What this is

Fixtures in this directory are **verbatim output the model actually produced**, lifted from
the captured LLM corpus at `backend/logs/llm/*.jsonl` (not committed — 211 MB, and it contains
real profile content from founder-acceptance runs).

They exist because of a specific, repeated failure: **a synthetic fixture can fail to reach the
code it claims to guard, and nothing goes red.** ADR-072 clause 1 is the worked example —
deleting the behaviour the clause exists for passed all 3991 tests, *including the test whose
name claimed to guard it*, because that test's hand-written "second carrier" matched neither
surface form. German compounding diverges at `Verpackung[s|en]`, and a human writing a fixture
does not naturally produce that divergence. The model does, constantly.

So: the assertions here are ours; **the inputs are the model's.**

## The provenance contract — read before adding a fixture

The corpus spans 2026-07-06 → 2026-08-02 and is **not uniformly safe to commit from.**
Founder-acceptance runs 1–6 used a real posting and a real profile. Charter runs 10–16 used the
synthetic cases in `tests/files/panel_review_case/`, whose README states: *"Synthetic personas —
no real personal data; contact strings use example.com."*

**A committed fixture may only draw from records whose run used a `panel_review_case` case.**
In practice that means the 2026-08-01 and 2026-08-02 slices, and it must be checked per record,
not per day. `test_replay_corpus_provenance.py` enforces this mechanically — it fails if a
fixture in this directory contains an identity that is not on the synthetic allowlist.

That test is the control. It is not documentation, and it is not a convention: if you add a
fixture carrying a real name, the build goes red.

**Never** paste corpus content into an issue, a comment or a non-fixture file. The provenance
guard only covers this directory.

## Refreshing a fixture when a prompt changes

A recorded response corresponds to the prompt that produced it. When a prompt changes materially,
the recorded response is stale evidence about what today's model would say — but it remains
perfectly valid evidence about *the shape of real model output*, which is what these fixtures are
for. So:

- **Fixtures used for input realism** (compound morphology, phrasing variance, figure formatting)
  do not need refreshing on a prompt change. Their value is that a human did not write them.
- **Fixtures used to assert what the model returns for a given prompt** must be re-captured, and
  should carry the prompt hash. None currently do, because none make that claim.

Each fixture records its own `provenance` block: stage, review_role, capture dates and case family.
Keep it accurate — it is how the next reader knows which of the two kinds they are holding.
