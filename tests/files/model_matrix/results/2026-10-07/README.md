# Model-qualification matrix — 2026-10-07 (#688, Strawberry build 2)

The records and summaries behind the *Model qualification* section of
[`docs/llm-models.md`](../../../../../docs/llm-models.md), produced by
`scripts/model_matrix.py` on the reconcile seam (one interview turn → the ADR-046
reconciler → the deterministic applier, no database). Every turn is synthetic
(`tests/files/model_matrix/`, "Lena Fischer"); nothing here comes from a real profile.

| Folder | What it measured |
|---|---|
| `v4/` | Ruling V-4: the #715 reconcile prompt (`*-cur`) against the same tree with the #715 prompt hunks and the `MatchExisting` schema description reverted (`*-pre`, inputs `pre715.*`), on S6 + S9, n=10. `*-t180` re-ran S6 at `LLM_TIMEOUT=180`. |
| `v4/glm-S3-*` | The S3 follow-up after the matrix's single glm malformed turn on S3, both prompts, n=10. |
| `matrix/` | The five-model matrix on S1–S8, n=10: `gpt-5.6-luna` (S1–S5, S7, S8 here; S6 and S9 are the `v4/luna-cur` arm), `z-ai/glm-5.3-flash` (likewise, S6 in `v4/glm-cur*`), `mistralai/mistral-medium-3-5`, `anthropic/claude-haiku-4.5` with the shipped settings (`.schema-auto`, every call refused, #756) and with `LLM_STRUCTURED_OUTPUT=off` (`.schema-off`), `meta-llama/llama-3.2-3b-instruct` (its S10 turns are reported but never decide a verdict). |

Each `*.summary.json` names the prompt and response schema it measured
(`meta.prompt.*_sha256`), the settings, and the provider calls the run made
(`meta.llm_log`, counted from the LLM debug log — the stance guard's adjudication
calls included). Re-score any record file without a provider call:

```bash
PYTHONPATH=backend python3 scripts/model_matrix.py --score <file>.jsonl --out rescored.json
PYTHONPATH=backend python3 scripts/model_matrix.py --table <file>.summary.json [...]
```

A row is a statement about a model, a route (all OpenRouter here), a prompt and a
schema on this date — not about the model in general.
