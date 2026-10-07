# Model-qualification matrix — 2026-10-07 (#688, Strawberry build 2)

The records and summaries behind the *Model qualification* section of
[`docs/llm-models.md`](../../../../../docs/llm-models.md), produced by
`scripts/model_matrix.py` on the reconcile seam (one interview turn → the ADR-046
reconciler → the deterministic applier, no database). Every turn is synthetic
(`tests/files/model_matrix/`, "Lena Fischer"); nothing here comes from a real profile.

| Folder | What it measured |
|---|---|
| `v4/` | Ruling V-4: the #715 reconcile prompt (`*-cur`) against the same tree with the #715 prompt hunks and the `MatchExisting` schema description reverted (`*-pre`, inputs `pre715.*`), on S6 + S9, n=10. `*-t180` re-ran S6 at `LLM_TIMEOUT=180`. |
| `matrix/` | The five-model matrix on S1–S8, n=10 (S10 turns in the llama file are reported but never decide a verdict). |

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
