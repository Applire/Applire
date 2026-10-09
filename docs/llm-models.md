# Choosing an LLM for Applire

Applire is **bring-your-own-key**. You choose the provider and the model, and no provider is
privileged. This page answers one question: **which model should I pick?**

The answer comes from measurement, not from benchmarks or reputation. Every model named below
was run through the same Applire tasks; a model that was never measured is not listed.
How we measure, and every individual run, is in the [measurement log](llm-models-measurements.md).

## Models measured to work

State as of **2026-10-07**. The rows were measured between 2026-09-14 and 2026-10-07; the
[qualification section](#model-qualification-2026-10-07) below has this release's numbers. One row
per model; the route a result was measured through is named, and where a gateway changed the
result, the row says so.

| Model | Measured through | Your profile stays correct¹ | A full application gets you invited² | Cost per interview turn³ | Worth knowing |
|---|---|---|---|---|---|
| **`gpt-5.6-luna`** (OpenAI) | OpenRouter, Requesty | **yes** — clean on all eight test situations (re-measured 2026-10-07) | **yes** — both reviewers invited | ≈ $0.003 | **Our recommendation for price versus performance.** The full application was measured through OpenRouter; through Requesty, the profile step was measured and was equally clean. Model id `openai/gpt-5.6-luna` on both. |
| `mistral-medium-3.5` (Mistral) | OpenRouter | **yes** — clean on all eight test situations (2026-10-07) | not measured yet | ≈ $0.012 | About four times the cost of `gpt-5.6-luna` per turn. Model id `mistralai/mistral-medium-3-5`. |
| `ministral-8b` (Mistral) | OpenRouter | yes, with rare slips (1 in 10 on two of the tested situations) | yes, with flaws — the documents left out one required skill and repeated one bullet | ≈ $0.001 | The cheapest model that has completed a full application. Model id `mistralai/ministral-8b-2512`. |
| `mistral-small` (Mistral) | OpenRouter | yes, with rare slips (1 in 10 facts filed under the wrong employer, in one tested situation) | not measured yet | ≈ $0.001 | Model id `mistralai/mistral-small-2603`. |

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

| Model | Measured through | What went wrong | Measured |
|---|---|---|---|
| `glm-5.3-flash` (Z.ai) | OpenRouter, Requesty | **Through OpenRouter** (`z-ai/glm-5.3-flash`) it is too slow: 10–20 % of profile updates got no answer within 180 seconds (2026-10-07), and a whole application could not finish — gap analysis, interview and CV generation timed out or returned broken output. **Through Requesty** (`glm-5.3-flash`) it recorded nothing for 30–50 % of interview answers that begin with "I have not done X, but…", and on a real install imports and quality checks ran past its output limit and failed. | 2026-09-09 – 2026-10-07 |
| `claude-haiku-4.5` (Anthropic) | OpenRouter | **Thins your profile.** Every server OpenRouter routes this model to refuses the answer format Applire sends with the profile step; from 0.43.0 Applire falls back to its plain format on its own ([#756](https://github.com/Applire/Applire/issues/756); before that, every interview answer was lost). On the plain format it passes the profile step's bars, but when an answer opens with a denial it records the three employers' facts only as one skill, not under each employer. | 2026-10-07 |
| `llama-3.2-3b` (Meta) | OpenRouter | Lost half of the answers that name three employers in one sentence (it asked a question back instead), and filed other employers' facts under the current job in every single-job profile. | 2026-10-07 |
| `command-r7b` (Cohere) | OpenRouter | Recorded nothing for every interview answer. | 2026-09-14 |
| `gpt-5-nano` (OpenAI) | OpenRouter | Lost up to 44 % of interview answers. | 2026-09-14 |
| `qwen3.8-flash` (Qwen) | OpenRouter | Lost answers and produced invalid output; very slow. | 2026-09-14 |
| `deepseek-v4-flash` (DeepSeek) | OpenRouter | 10–30 % of calls failed. | 2026-09-14 |
| `nemotron-3-super-120b` free tier (NVIDIA) | OpenRouter | 10–20 % of calls failed. | 2026-09-14 |
| `ministral-3b` (Mistral) | OpenRouter | 40 % invalid output in one tested situation. | 2026-09-14 |

**The gateway can change a result.** A gateway decides some settings for you — whether the model
reasons before answering, for example — and may serve the model from a different backend. That is
why `glm-5.3-flash` failed in two different ways above, while `gpt-5.6-luna` measured the same
through both gateways we tried. If a model behaves differently for you than in this table, the
gateway is the first thing to suspect.

## Model qualification (2026-10-07)

**Seam:** the profile step — one interview answer is turned into changes to your stored profile
(the reconciler, ADR-046). **Inputs:** the eight committed test situations S1–S8
(`tests/files/model_matrix/`: one, two, three or five employers; English and German; answers that
open with "I have not done X, but…"), **10 runs each**. **Route:** OpenRouter, with
`LLM_TIMEOUT=180` and every other setting at its shipped value (`LLM_STRUCTURED_OUTPUT=auto`)
unless the row says otherwise. **Prompt:** this release's reconcile prompt (20,320 characters) and
response format. The bars are the ones in [How we measure](#how-we-measure); the worst situation
decides. "Facts per employer" is the share of the employers an answer named that got a fact of
their own (1.0 = all of them) — reported, not a bar.

| Model | Answers lost | Invalid output | Wrong employer | No answer | Facts per employer (lowest) | Verdict | Cost per turn |
|---|---|---|---|---|---|---|---|
| `openai/gpt-5.6-luna` | 0 % | 0 % | 0 % | 0 % | 1.0 | **qualified** | ≈ $0.003 |
| `mistralai/mistral-medium-3-5` | 0 % | 0 % | 0 % | 0 % | 1.0 | **qualified** | ≈ $0.012 |
| `anthropic/claude-haiku-4.5`, **shipped settings** | — | — | — | **100 %** — every call refused (stopped after 75 turns) | — | **sub-par** before the [#756](https://github.com/Applire/Applire/issues/756) fix; from 0.43.0 Applire falls back to the row below | — |
| `anthropic/claude-haiku-4.5`, `LLM_STRUCTURED_OUTPUT=off` | 0 % | 0 % | 0 % | 0 % | **0.0** (S6, S7) | passes the bars, see below | ≈ $0.009 |
| `z-ai/glm-5.3-flash` | 0 % | up to 11 % (S3, 1 of 9) | 0 % | **10–20 %** | 0.9 | **sub-par** (no answer) | ≈ $0.005 |
| `meta-llama/llama-3.2-3b-instruct` | **50 %** (S4) | 0 % | **100 %** (S7) | 0 % | 0.65 | **sub-par** | ≈ $0.0006 |

**Read this table with four notes.**

- **`claude-haiku-4.5` is refused, not weak.** With the shipped settings, every server OpenRouter
  routes it to rejected the answer format Applire sends with this step (first because of how one
  field is declared, then because the format as a whole is too large for their checker), and
  Applire did not fall back to its plain format, so every answer was lost. That was a defect on
  Applire's side, fixed in 0.43.0 ([#756](https://github.com/Applire/Applire/issues/756)): Applire
  now retries without the format and keeps using the plain format for that process. Only
  `claude-haiku-4.5` was measured; other Claude models on the same hosts are likely to be refused
  the same way.
- **With the format switched off, `claude-haiku-4.5` passes every bar and still thins the
  profile.** On the two situations whose answer opens with a denial, it recorded one skill
  ("pharmaceutical manufacturing IT", backed by all three jobs) and nothing under any of the
  three employers — the details of what you did where were not kept.
- **`glm-5.3-flash` is slow through OpenRouter.** It answered every situation correctly when it
  answered, but 10–20 % of turns got no answer within 180 seconds (40–60 % at the 120 seconds
  built into the code), and one German turn out of nine carried an invalid operation (0 of 9 on a
  repeat). Its earlier failure to finish a whole application (table above) is the same latency in
  the longer steps.
- **A weaker model makes more calls, not fewer.** When a model records a skill the answer only
  implies, Applire checks it with a second short call. Per turn: 1.5 calls for `gpt-5.6-luna`,
  1.4 for `mistral-medium-3-5`, 1.8 for `claude-haiku-4.5`, 1.9 for `glm-5.3-flash`, 2.8 for
  `llama-3.2-3b`.

Every number above comes from the committed records in
`tests/files/model_matrix/results/2026-10-07/`, which you can re-score without spending anything
(`scripts/model_matrix.py --score <file>`).

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
API (`LLM_PROVIDER=mistral`) is EU-hosted, but has not been through the measurement above yet;
`mistral-medium-3-5` qualified on the profile step through OpenRouter (2026-10-07), which says the
model can do the job, not that Mistral's own endpoint behaves the same.

**Lowest cost.** `ministral-8b` and `mistral-small` cost about half as much per call as
`gpt-5.6-luna`. Only ministral-8b has completed a full application so
far, with the flaws noted in the table; luna made no mistakes.

**Nothing leaves your machine.** Run a local model with Ollama (`LLM_PROVIDER=ollama`). Pick a
larger instruct model, set its context size (`num_ctx`) explicitly because Ollama's default is
small, and raise `LLM_TIMEOUT` (for example to `600`) if it runs on a CPU. The small model
`.env.example` names for Ollama, `llama3.2` (3B), was measured through OpenRouter as
`meta-llama/llama-3.2-3b-instruct` and does **not** keep the profile correct (see
[qualification](#model-qualification-2026-10-07)); pick a larger model. No model has been measured
through Ollama itself yet.

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

# measure one model (spends real credit on your key) — the qualification run:
# eight situations, 10 runs each, and a hard stop at 250 model calls
PYTHONPATH=backend python3 scripts/model_matrix.py \
  --provider openrouter --model <model-id> --n 10 --shapes S1,S2,S3,S4,S5,S6,S7,S8 \
  --timeout 180 --out matrix.jsonl --llm-log-dir /tmp/matrix-log --max-calls 250
```

A run makes **more model calls than turns**: when the model records a skill the answer only
implies, Applire asks the model a second, short question to confirm it. A strong model triggers
that about once per turn on the situations with a denial; a weak one more often (`llama-3.2-3b`
made 250 calls for 90 turns). `--max-calls` stops starting new turns at that count, and
`--llm-log-dir` writes the log the count is read from — it holds the full test prompts, so keep it
out of anything you share.

Results describe a model, a route and a prompt version on a date. Providers change models under
the same name, and Applire's prompts change between releases, so a result can go stale. Every run
so far, including the ones that changed a model's verdict, is in the
[measurement log](llm-models-measurements.md).

## See also

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — ADR-009 (how providers plug in) and ADR-047 (how Applire
  splits large answers so that small output limits still work).
- `.env.example` — every provider's settings.
