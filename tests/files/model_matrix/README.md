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
| `shapes.json` | the eight input shapes S1–S8, the station map, and which profile each shape uses |
| `profiles/lena_full.json` | three stations (NovaRNA current, Blutspendedienst, Helvetia) |
| `profiles/lena_no_bsd.json` | the same vault with the Blutspendedienst station **absent** |
| `profiles/lena_current_only.json` | only the current station — the smallest vault |
| `profiles/lena_big.json` | five stations, DE+EN summary, nine skills — closer to a used vault |
| `golden/*.user_prompt.txt` | the rendered reconcile user prompt for S6 and S7, UUIDs canonicalised |

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

## Provenance and the golden prompts

Generated from `Documents/Runs/Nougat/summary-seed-spike-2026-09-06/replay_multistation.py`
(the spike behind #684/#688). The goldens under `golden/` were rendered from that
script's own profile builders, not from these fixtures, and the smoke test asserts
the fixtures reproduce them **byte for byte**.

One caveat is baked into that claim: the spike minted a fresh random UUID for every
education entry and every skill on every invocation, so its prompt was never byte-stable
across two runs of its own. The fixtures freeze those ids, and both sides of the
comparison are canonicalised (`<uuid-0>`, `<uuid-1>`, … in first-appearance order) before
the diff. Everything a rule can read — the entity ids the model targets, the ordering,
the wording, the JSON key order of the turn — is identical.

## Changing a fixture

Any edit here invalidates every published matrix row measured on the old fixture.
Add a shape rather than editing one, and re-generate the goldens in the same commit.
