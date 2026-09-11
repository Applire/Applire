# Model-matrix fixtures (US311 / #688)

Input fixtures for the opt-in model-qualification harness `scripts/model_matrix.py`.
They pin one interview turn — a vault, a gap, a question and the candidate's answer —
so the same measurement can be repeated on any model, any time.

## The data is synthetic

"Lena Fischer" does not exist. The vault was written by hand for the 2026-09-06
summary-seed spike: the address is `lena.fischer@example.com`, and every employer
(NovaRNA Biotech GmbH, Blutspendedienst Nord gGmbH, Helvetia Pharma AG, Rhein
Consulting GmbH) is invented. No content comes from a real profile, and none ever
may — E060 §3.1's architecture boundary, and `tests/unit/test_model_matrix_harness.py`
asserts it (`test_the_vault_fixture_is_synthetic`).

## Layout

| Path | What it is |
|---|---|
| `shapes.json` | the ten input shapes S1–S10, the station map, and which profile each shape uses |
| `profiles/lena_full.json` | three stations (NovaRNA current, Blutspendedienst, Helvetia) |
| `profiles/lena_no_bsd.json` | the same vault with the Blutspendedienst station **absent** |
| `profiles/lena_current_only.json` | only the current station — the smallest vault |
| `profiles/lena_big.json` | five stations, DE+EN summary, nine skills — closer to a used vault |
| `profiles/lena_bullets.json` | `lena_full` with one extra `w-nova` achievement bullet, for the S9 bullet-conflict shape |
| `golden/*.user_prompt.txt` | the rendered reconcile user prompt for S6, S7, S9 and S10, UUIDs canonicalised |

Profiles are `MasterProfileData.model_dump(mode="json")` dumps, so they load with
`model_validate` and carry their entity ids (`w-nova`, `w-bsd`, `w-helv`) verbatim —
the harness's station attribution reads exactly those ids.

## The shapes

| Shape | Vault | Turn |
|---|---|---|
| S1 | full | EN gap question, EN answer naming all three stations |
| S2 | no BSD | same answer against a vault missing one named station |
| S3 | full | DE question, DE answer |
| S4 | full | the same three stations compressed into ONE sentence |
| S5 | no BSD | S4's sentence against the vault missing one station |
| S6 | full | the incident shape: the answer opens with a denial ("not insulin"), then names three stations |
| S7 | current only | the incident shape against the smallest vault — the shape `glm-5.3-flash` lost 11/20 times |
| S8 | big | the incident shape against a five-station vault |
| S9 | bullets | bullet-vs-bullet contradiction: the vault already has one figure for a NovaRNA achievement, the answer restates it with a changed figure — measures whether reconcile rule 13 (CONTRADICTING BULLETS) fires as `flag_conflict` instead of a silent overwrite or an ordinary `add_bullets` |
| S10 | full | a restated fact, nothing new: the answer repeats a `w-nova` responsibility the vault already carries verbatim, so the CORRECT output is no ops at all. `expected_stations` is deliberately `[]` — `station_coverage` stays `None` for this shape and it must never be folded into a model's `no_write` qualification verdict, whose bar would misread the correct "wrote nothing" outcome as a lost turn |

## Provenance and the golden prompts

Generated from `Documents/Runs/Nougat/summary-seed-spike-2026-09-06/replay_multistation.py`
(the spike behind #684/#688). The goldens under `golden/` were rendered from that
script's own profile builders, not from these fixtures, and the smoke test asserts
the fixtures reproduce them **byte for byte**.

S9 and S10 (added in Nougat build 2, WP-P2) have no spike counterpart — there was
no 2026-09-06 script run for a bullet-conflict or a restated-fact turn. Their
goldens were rendered straight from these fixtures via the harness's own
`Fixtures` / `render_prompts` / `canonical_prompt`, and the smoke test that pins
them is a **drift pin**: it proves the fixture keeps rendering the same prompt it
did the day it was added, not a comparison against an external spike.

One caveat is baked into that claim: the spike minted a fresh random UUID for every
education entry and every skill on every invocation, so its prompt was never byte-stable
across two runs of its own. The fixtures freeze those ids, and both sides of the
comparison are canonicalised (`<uuid-0>`, `<uuid-1>`, … in first-appearance order) before
the diff. Everything a rule can read — the entity ids the model targets, the ordering,
the wording, the JSON key order of the turn — is identical.

## Changing a fixture

Any edit here invalidates every published matrix row measured on the old fixture.
Add a shape rather than editing one, and re-generate the goldens in the same commit.
