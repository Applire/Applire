# Choosing an LLM for Applire

Applire is **bring-your-own-key**. You choose the provider and the model, and no provider is
privileged. This page answers one question: **which model should I pick?**

The answer comes from measurement, not from benchmarks or reputation. Every model named below
was run through the same Applire tasks; a model that was never measured is not listed.
How we measure, and every individual run, is in the [measurement log](llm-models-measurements.md).

## Models measured to work

State as of **2026-09-16**. Every row was measured on this release's prompts, between 2026-09-14 and
2026-09-16.

| Model | Set `LLM_PROVIDER` to | Your profile stays correct¹ | A full application gets you invited² | Cost per interview turn³ | Worth knowing |
|---|---|---|---|---|---|
| `openai/gpt-5.6-luna` | `openrouter` | **yes** | **yes** — both reviewers invited | ≈ $0.002 | The model we develop and test Applire on. US-hosted. |
| `openai/gpt-5.6-luna` | `requesty` | **yes** | not measured on this route | billed by Requesty, not measured | Requesty's gateway is in the EU (Frankfurt). Whether the model itself runs in the EU depends on the model id you choose there. |
| `anthropic/claude-haiku-4.5` | `openrouter` | **yes** | not measured yet | ≈ $0.004 | About twice the cost of `gpt-5.6-luna`. US-hosted. |
| `mistralai/ministral-8b-2512` | `openrouter` | yes, with rare slips (1 in 10 on two of the tested situations) | yes, with flaws — the documents left out one required skill and repeated one bullet | ≈ $0.001 | The cheapest model that has completed a full application. |
| `mistralai/mistral-small-2603` | `openrouter` | yes, with rare slips (1 in 10 facts filed under the wrong employer, in one tested situation) | not measured yet | ≈ $0.001 | |

¹ **Your profile stays correct:** after an interview answer, the model records what you said,
under the right employer, without losing the answer. This is the step where a mistake changes
your stored profile rather than one document.
² **A full application gets you invited:** Applire produced a CV and a cover letter for a
candidate who fits the job well, and an HR screener and a hiring manager who did not know a tool
was involved decided whether to invite. Tested on one German-language case.
³ From the most recent measurement run through OpenRouter (2026-09-14 or 2026-09-16), divided by
the number of test turns. One complete job application uses roughly 90–105 model calls, most of
them larger than an interview turn, so an application costs many times this figure.

## Models measured not to work

| Model | Set `LLM_PROVIDER` to | What went wrong | Measured |
|---|---|---|---|
| `glm-5.3-flash` | `requesty` | Recorded nothing for 30–50 % of interview answers that begin with "I have not done X, but…". On a real install, imports and quality checks also ran past the model's output limit and failed. | 2026-09-09, 2026-09-16 |
| `z-ai/glm-5.3-flash` | `openrouter` | Keeps the profile correct, but could not finish an application: gap analysis, interview and CV generation timed out or returned broken output. | 2026-09-14 |
| `cohere/command-r7b-12-2024` | `openrouter` | Recorded nothing for every interview answer. | 2026-09-14 |
| `openai/gpt-5-nano` | `openrouter` | Lost up to 44 % of interview answers. | 2026-09-14 |
| `qwen/qwen3.8-flash` | `openrouter` | Lost answers and produced invalid output; very slow. | 2026-09-14 |
| `deepseek/deepseek-v4-flash-0731` | `openrouter` | 10–30 % of calls failed. | 2026-09-14 |
| `nvidia/nemotron-3-super-120b-a12b:free` | `openrouter` | 10–20 % of calls failed. | 2026-09-14 |
| `mistralai/ministral-3b-2512` | `openrouter` | 40 % invalid output in one tested situation. | 2026-09-14 |

**The route matters as much as the model.** The same `glm-5.3-flash` behaves differently through
OpenRouter and through Requesty, because the two gateways run it with different settings. A result
is only valid for the route it was measured on.

## Your model isn't on either list

Then it is unmeasured, which is not the same as bad. You have three options:

- **Measure it yourself** with the script described in [How we measure](#how-we-measure). It
  spends a few cents of your own credit.
- **Try it and watch for the symptoms** in the table [below](#symptoms-and-what-they-mean).
- **Check it against the minimum** in the next section. That rules models out; it does not
  prove they work. Several models above meet every item and still failed.

### The minimum a model needs

- **Output limit of at least 4,000 tokens per answer**, ideally 8,000. The output limit is often
  far smaller than the context window a model advertises, and it is the one that matters:
  Applire's profiles and job ads are short, but a whole CV is a long answer. Below the limit,
  Applire splits the work into several smaller calls, so a document still completes — it just
  takes more calls.
- **Reliable JSON.** Applire asks for structured answers. Very small models (well under 7B
  parameters) produce broken structure too often.
- **Reasoning ("thinking") you can limit or switch off** makes life easier. Reasoning tokens come
  out of the same output limit as the answer. Some models reason on every call and cannot be
  stopped — Google Gemini Flash / Pro, `deepseek/deepseek-v3.1-terminus` and
  `stepfun/step-3.5-flash:free` among them (as of mid-2026). Applire copes with them, but they
  are slower and more likely to cut an answer short.

## Choosing by what matters to you

**Your data should stay in the EU.** Use Requesty's EU gateway (`LLM_PROVIDER=requesty`) with an
EU-region model id — it reaches the large US models through their EU deployments. Mistral's own
API (`LLM_PROVIDER=mistral`) is EU-hosted, but has not been through the measurement above yet.

**Lowest cost.** `mistralai/ministral-8b-2512` and `mistralai/mistral-small-2603` cost about half
as much per call as `openai/gpt-5.6-luna`. Only ministral-8b has completed a full application so
far, with the flaws noted in the table; luna made no mistakes.

**Nothing leaves your machine.** Run a local model with Ollama (`LLM_PROVIDER=ollama`). Pick a
larger instruct model, set its context size (`num_ctx`) explicitly because Ollama's default is
small, and raise `LLM_TIMEOUT` (for example to `600`) if it runs on a CPU. No local model has been
measured yet.

**Trying several models.** OpenRouter (`LLM_PROVIDER=openrouter`) gives you one key for all the
models in the tables.

**Claude or GPT directly.** `LLM_PROVIDER=anthropic` and `LLM_PROVIDER=openai` need an API key
from the provider's developer console. A ChatGPT or Claude subscription does not work in other
applications. `LLM_PROVIDER=openai` also covers local servers such as LM Studio or vLLM through
`OPENAI_BASE_URL`.

## Settings that make a difference

| Setting | Shipped value | When to change it |
|---|---|---|
| `LLM_TIMEOUT` | `180` seconds per call | Raise it for slow models, a slow gateway or a CPU-only local model. `llm_timeout` in the backend log is the sign. |
| `OPENROUTER_REASONING_EFFORT` / `REQUESTY_REASONING_EFFORT` | unset (model's own default) | Set `low` to limit a model that reasons heavily. |
| `OPENROUTER_DISABLE_THINKING` / `REQUESTY_DISABLE_THINKING` | `false` | A request, not a switch: some models refuse it and keep reasoning, and some routes never reason whatever you set. Applire handles a refusal by itself. |
| `LLM_STRUCTURED_OUTPUT` | `auto` | Leave it. It hands the model the exact answer format for the profile-writing step, which made weaker models noticeably more accurate. It costs about 2,300 extra input tokens per interview turn; `off` removes that. If your model does not support it, Applire notices and falls back by itself. |

## Symptoms and what they mean

| You see… | Likely cause | What to do |
|---|---|---|
| A CV import says *"We couldn't finish reading this CV in time — nothing was changed"* | Either the model or its gateway is too slow (`llm_timeout` in the backend log), or the model wrote past its output limit (`llm_truncated`) | For `llm_timeout`, raise `LLM_TIMEOUT`. If it keeps happening, or the log says `llm_truncated`, switch to a model from the first table |
| Something you said in the interview never appears in your profile | The model recorded nothing for that answer | Switch to a model from the first table; this is exactly what the measurement looks for |
| Generation takes several steps or is slow | The model has a small output limit, or reasons heavily | Nothing is broken. For fewer steps, choose a model with a larger output limit or set the reasoning effort to `low` |
| A generated document is cut off | A model that always reasons spent its output limit on reasoning | Set the reasoning effort to `low`, or choose a model whose reasoning can be switched off |
| Frequent broken output from a small local model | The model is too small for structured answers | Use a larger instruct model |

When a quality check or a writing step gets an answer it cannot read, Applire does not stop: the
document is built from the last valid version, and the review report marks that step
`review_malformed`.

## How we measure

Every model is run through two tests. It has to pass both to appear in the first table.

1. **Does your profile stay correct?** The model gets a test profile and one interview answer
   that names several employers, and we check what it records. This is repeated 10 times for each
   of several situations (one, three and five employers; answers that start with a denial). A model
   fails if more than 5 % of answers are lost, more than 10 % of its output is invalid, more than
   10 % of facts land under the wrong employer, or more than 10 % of calls get no answer.
2. **Do the documents get you invited?** For each model that passes step 1, Applire runs a full
   application for a candidate who fits the job well, and two reviewers decide blind whether to
   invite.

The test data is synthetic and part of the repository (`tests/files/model_matrix/`). To measure a
model yourself:

```bash
# see what the model will be asked, without spending anything
PYTHONPATH=backend python3 scripts/model_matrix.py --dry-run

# measure one model (spends real credit on your key)
PYTHONPATH=backend python3 scripts/model_matrix.py \
  --provider openrouter --model <model-id> --n 10 --shapes S6,S7,S8 \
  --out matrix.jsonl
```

Results describe a model, a route and a prompt version on a date. Providers change models under
the same name, and Applire's prompts change between releases, so a result can go stale. Every run
so far, including the ones that changed a model's verdict, is in the
[measurement log](llm-models-measurements.md).

## See also

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — ADR-009 (how providers plug in) and ADR-047 (how Applire
  splits large answers so that small output limits still work).
- `.env.example` — every provider's settings.
