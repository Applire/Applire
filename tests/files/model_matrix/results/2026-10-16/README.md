# Model matrix — 2026-10-16/17 (Hazelnut, #765 and #766)

Produced by `scripts/model_matrix.py`, OpenRouter, `--reasoning off`
(`openrouter_disable_thinking: true` in every summary's `meta.settings`), `LLM_TIMEOUT=180`.
All inputs are synthetic (the "Lena Fischer" reconcile fixtures; the E066 bench cells under
`tests/files/quality_bench/`). Nothing here comes from a real profile.

## `schema-765/` — reconcile response schema, op descriptions (#765)

Arm `before` = the tree's schema before #765 (`schema-before.json`, sha `222a0ceb…`,
11,885 chars as sent), passed with `--schema`; arm `after` = the tree's own (`schema-after.json`,
sha `084b7520…`). System prompt identical (`a741ec08…`). S1–S9, n=10.

| Model | before | after |
|---|---|---|
| `openai/gpt-5.6-luna` | qualified, every rate 0 %, 840,085 in / 19,385 out, $0.191 | qualified, every rate 0 %, 747,896 in / 20,572 out, $0.174 |
| `z-ai/glm-5.3-flash` | qualified, every rate 0 % | qualified, every rate 0 % |
| `mistralai/mistral-small-2603` | **inconclusive** — OpenRouter 429 on 50 calls (c=3) / 33 (c=1, `*.c1.*`) | **inconclusive** — 429 on 69 / 48 calls |

The mistral-small files without `.c1` ran at `--concurrency 3`, the `.c1` files at 1; both pairs are
dominated by `LLMRateLimitError` (see `meta.llm_log.error_kinds`) and are kept as the record of why
that model is unmeasured, not as a bar.

## `analysis-766/` — job-analysis and gap-analysis seams (#766)

`--seam jd|gap --cells same` (the four same-language bench cells), n=3 per cell = 12 runs per row.
Arms: `before` = prompts at `33d84615`; `after` = f076bb11 (writer + reviewer + corrector edits,
JD) / 2b746e2d (gap deletions); `after2` = the shipped JD state (bf6eaf25: reviewer rewording
reverted); `before2` = the `before` prompts re-run the next day (drift check).

| JD, review loop | before | before2 | after | after2 (shipped) |
|---|---|---|---|---|
| luna exhausted / 12 | 1 | — | 3 | 2 |
| ministral-8b exhausted / 12 | 6 | **10** | 10 | 11 |
| glm-5.3-flash reviewer call failed / 12 (verdict truncated at 2048 tokens) | 9 | — | 3 | — |
| glm invented CEFR level on the two DE postings stating none | 4 of 7 | — | 0 of 4 | — |

ministral's unchanged-prompt re-run moved 6 → 10 overnight: at n=12 the loop's exhaustion
rate is not distinguishable between arms. Gap: luna and ministral-8b unchanged on every metric;
glm-5.3-flash truncates in both arms (7 → 11 of 12, mean 41k completion tokens per run — the
model's own output length, not the prompt).
