# Model-qualification matrix — 2026-10-10 (#688, reasoning off)

The records behind the *Model qualification (2026-10-10)* section of
[`docs/llm-models.md`](../../../../../docs/llm-models.md), produced by
`scripts/model_matrix.py` on the reconcile seam (one interview turn → the ADR-046
reconciler → the deterministic applier, no database). Every turn is synthetic
(`tests/files/model_matrix/`, "Lena Fischer"); nothing here comes from a real profile.

Why it exists: the 2026-10-07 matrix (`../2026-10-07/`) ran at the code default of that
date, reasoning **on**, while every delivery run and blind panel of the release ran with
reasoning off. This run measures the shipped configuration of 0.43.0 (ADR-009 amended
2026-10-10: `OPENROUTER_DISABLE_THINKING` / `REQUESTY_DISABLE_THINKING` default `true`).

| File | Model | Settings |
|---|---|---|
| `matrix/openai-gpt-5.6-luna.reasoning-off.*` | `openai/gpt-5.6-luna` | `--reasoning off`, S1–S9, n=10 |
| `matrix/mistralai-mistral-medium-3-5.reasoning-off.*` | `mistralai/mistral-medium-3-5` | same |
| `matrix/z-ai-glm-5.3-flash.reasoning-off.*` | `z-ai/glm-5.3-flash` | same |
| `matrix/anthropic-claude-haiku-4.5.schema-auto-post756.*` | `anthropic/claude-haiku-4.5` | same, `LLM_STRUCTURED_OUTPUT=auto` with the #756 fallback |

All runs: OpenRouter, `LLM_TIMEOUT=180`, prompt sha256 `a741ec08…` (in each summary's
`meta.prompt`). Each summary's `meta.settings` records the reasoning value that reached
the provider (`openrouter_disable_thinking: true`). Re-score without a provider call:

```bash
PYTHONPATH=backend python3 scripts/model_matrix.py --score <file>.jsonl --out rescored.json
PYTHONPATH=backend python3 scripts/model_matrix.py --table matrix/*.summary.json
```
