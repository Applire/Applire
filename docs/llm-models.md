# Choosing an LLM for Applire

Applire is **bring-your-own-key**: you pick the model, set `LLM_PROVIDER` (and the matching
API key), and Applire runs against it. No provider is privileged. This guide helps you
choose a model that gives the smoothest experience — especially for the two heaviest
operations, **CV tailoring** and **multi-CV profile merging**.

> **You are not locked out by a "weak" model.** Applire generates large documents in
> segments so that no single model call needs a big output (see
> [ADR-047 in ARCHITECTURE.md](ARCHITECTURE.md#adr-047--llm-output-robustness-segmentation-first)).
> That means even models with a small output ceiling, or a local Ollama, will produce a
> complete CV. The recommendations below are about *quality and smoothness*, not
> hard requirements. Where the limit is discoverable (OpenRouter, Ollama) Applire detects
> it and segments pre-emptively; everywhere else it falls back to segmenting on demand.

## What actually matters

LLMs have **two different limits**, and the one that bites is usually the smaller one:

| Limit | What it is | Why it matters |
|---|---|---|
| **Context window** | How much text the model can *read* (input + output combined) | Rarely the constraint for Applire — profiles and JDs are small. |
| **Max output tokens** | How much the model can *write* in one response | **This is the one that matters.** It is often far below the context window (e.g. a 128k-context model that only emits ~8k tokens), and a whole CV or a rich two-CV merge is a large output. |

A second, subtler factor is **reasoning ("thinking") behaviour**. On reasoning models the
"thinking" tokens come out of the *same* output budget as the answer, so a model that
reasons heavily can starve the actual output. Applire manages this for you
(`disable_thinking` for short calls, `OPENROUTER_REASONING_EFFORT` to bound it — see
ARCHITECTURE.md), but a model that lets you *bound or disable* reasoning is easier to run.

## Minimum-capability floor

A model at or above this floor will run every Applire flow comfortably:

- **Output budget:** ideally **≥ 8k** tokens per response; **≥ 4k** works fine with
  segmentation. (Below ~4k, segmentation still completes but uses more calls.)
- **Instruction-following for JSON:** Applire asks for structured JSON. Native
  structured-output / JSON mode is a plus but not required — Applire validates and repairs.
  Very small models (well under ~7B parameters) tend to struggle with structured output
  and produce more retries.
- **Reasoning:** any behaviour works. Models that let you bound/disable reasoning
  (most OpenRouter models, OpenAI/Anthropic) give the cleanest results; reasoning-mandatory
  models are handled automatically but spend more tokens.

## Which models work — measured (2026-09-09)

The capability floor above is a *specification*. This section is a **measurement**: the
same interview turn, replayed on each model, counted. It exists because a model can meet
every stated requirement — big context, JSON mode, bounded reasoning — and still quietly
drop what you told Applire about yourself.

> **This does not introduce a default model.** Applire ships no recommendation and
> privileges no provider; the table says what was measured, and the choice stays yours.

### What is measured, and why this seam

The **reconcile seam** — the single LLM call (ADR-046) that turns one interview answer
into a batch of typed operations against your vault. It is the sharpest test in the
product for three reasons: it is a *write* path (a mistake changes your stored profile,
not a document you can regenerate), it demands a large structured output against a
14-rule prompt, and its failures are **silent** — an empty operation batch looks exactly
like "nothing new in that answer".

Each measured turn feeds the model a synthetic vault plus one gap answer that names three
different employers, and reads back what it emitted. Four failure classes are counted:

| Rate | What went wrong | Why it matters |
|---|---|---|
| **lost turn** | nothing the model emitted wrote anything to your vault — an empty batch, or only a question handed back to you | your answer is gone, with nothing on screen to say so |
| **malformed-op** | it emitted an operation the schema rejects | the same loss one layer down; Applire logs a warning and drops it |
| **wrong-slot** | a fact landed on a different employer than the one the answer named | your CV would credit the wrong job |
| **no response** | the call never came back (timeout, transport error) | not the model's judgement, so it is counted separately and excluded from the rates above |

"Lost turn" is deliberately stricter than "returned nothing". One model in the first
round answered every turn with a single *question back to the candidate* and no vault
operation at all — a perfectly well-formed response that lost the answer just as
completely as silence. And "no response" is separated out because Applire's reconciler
turns a failed call into an empty result: without splitting them, a model that timed out
would be published as a model that chose to say nothing.

### The bar — two tiers

A model has to clear **both** to be called good enough.

**Tier 1 — does your vault survive the interview?** The rates above, measured per input
shape; the **worst** shape decides.

| Verdict | Meaning |
|---|---|
| **qualified** | every shape clean: no lost turn, no malformed operation, no wrong slot |
| **usable, with caveats** | losses occurred but stayed under every bar below |
| **sub-par** | any bar crossed on any shape |

Bars: **lost turn > 5 %** · **malformed-op > 10 %** · **wrong-slot > 10 %** · **no response > 10 %**.

The lost-turn bar is the tightest because it is the one failure the product cannot show you.

**Tier 2 — do the documents get you invited?** A vault that survives is not the whole job.
For each model that clears tier 1, Applire generates a CV and a cover letter for a case
whose data is a *very good* match for the posting, and a blind hiring panel — an HR
screener and a hiring manager who never see which model wrote the documents — decide
whether to invite. A model that produces documents this panel will not invite is **sub-par
too**, however clean its operation batches were. A model that is already sub-par on tier 1
is not put through tier 2.

### Results

<!-- Rows come from a real run of scripts/model_matrix.py; never from a datasheet, a
     benchmark score, or a reputation. An unmeasured model has no row.
     Rates are per shape, written S6 / S7 / S8:
       S6 = three-employer vault  ·  S7 = one-employer vault  ·  S8 = five-employer vault
     "reasoning" is the effective setting for the run, and it changes results a lot —
     see the note under the table. -->

| Model | Gateway | Reasoning | n | lost turn S6/S7/S8 | malformed | wrong-slot | no response | Tier 1 | Panel (tier 2) | tokens in/out | ≈ cost/run |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `mistralai/ministral-3b-2512` | OpenRouter | on (model default) | 10 | 0% / 0% / 0% | 0% / 0% / 0% | 0% / 70% / 10% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 186,541 / 9,575 | $0.0196 |
| `mistralai/ministral-8b-2512` | OpenRouter | on (model default) | 10 | 0% / 0% / 10% | 0% / 20% / 20% | 0% / 10% / 0% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 224,180 / 16,813 | $0.0361 |
| `openai/gpt-5.6-luna` | OpenRouter | on (model default) | 10 | 0% / 20% / 0% | 0% / 0% / 0% | 0% / 30% / 0% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 155,934 / 20,359 | $0.0251 |
| `z-ai/glm-5.3-flash` | OpenRouter | on (model default) | 10 | 20% / 0% / 0% | 0% / 40% / 0% | 0% / 10% / 0% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 169,933 / 152,569 | $0.0509 |
| `glm-5.3-flash` | **Requesty** (an operator's actual route, run inside that install's container) | off (route default) | 10 | 20% / **70%** / 30% | 10% / 20% / 20% | 0% / 10% / 0% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 158,497 / 4,824 | ≈ $0.013 |
| `openai/gpt-5-nano` | OpenRouter | on (model default) | 10 | 10% / 20% / 40% | 0% / 0% / 0% | 0% / 60% / 10% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 161,562 / 116,042 | $0.0545 |
| `nvidia/nemotron-3-super-120b-a12b:free` | OpenRouter | on (model default) | 10 | 60% / 60% / 60% | 0% / 0% / 0% | 0% / 20% / 0% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 166,462 / 51,340 | $0 (free tier) |
| `deepseek/deepseek-v4-flash-0731` | OpenRouter | on (model default) | 10 | 30% / 14% / 89% | 0% / 0% / 0% | 0% / 0% / 0% | 4/30 | **sub-par** | n/a (sub-par on tier 1) | 150,002 / 223,345 | $0.0432 |
| `qwen/qwen3.8-flash` | OpenRouter | on (model default) | 10 | 17% / 0% / 43% | 0% / 0% / 0% | 0% / 0% / 0% | 17/30 | **sub-par** | n/a (sub-par on tier 1) | 83,609 / 103,556 | $0.0612 |
| `qwen/qwen3.8-flash` (600 s timeout) | OpenRouter | on (model default) | 10 | 40% / 20% / 40% | 0% / 20% / 0% | 0% / 0% / 0% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 184,360 / 341,405 | $0.1881 |
| `anthropic/claude-haiku-4.5` | OpenRouter | on (model default) | 10 | 100% / 0% / 0% | 0% / 0% / 0% | 0% / 100% / 0% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 172,478 / 7,060 | $0.2078 |
| `mistralai/mistral-small-2603` | OpenRouter | on (model default) | 10 | 100% / 100% / 30% | 0% / 0% / 0% | 0% / 0% / 0% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 163,150 / 3,009 | $0.0263 |
| `cohere/command-r7b-12-2024` | OpenRouter | on (model default) | 10 | 100% / 100% / 10% | 0% / 0% / 0% | 0% / 0% / 0% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 163,044 / 4,692 | $0.0068 |

All rows measured 2026-09-09 through OpenRouter from a clean checkout with no `.env`, so
every knob sat at its code default — in particular reasoning was **left on**, which is what
an operator who never touches the setting gets. `n` = 10 turns per shape, 30 per model.
Rates are over the turns the model actually answered; the "no response" column says how many
it did not.

**Nothing in this table is qualified.** That is the finding, not a formatting accident. On
these three shapes every model measured — the cheapest and the most expensive alike — either
loses turns, mis-attributes facts, or emits operations the schema rejects. Two consequences:
the tier-2 panel has nothing to run on yet, and the next piece of work is the prompt, not the
model list.

Read the columns as different diseases, not one score:

- **Silence.** `mistralai/mistral-small-2603` and `cohere/command-r7b-12-2024` lost every
  single one-employer turn. The answers they were given open with a denial ("*while I have
  not worked with insulin in particular…*") and then name three employers; both models
  stopped at the denial. r7b did it by asking the question back instead of recording anything.
- **Mis-attribution.** `mistralai/ministral-3b-2512` never lost a turn and never emitted an
  invalid operation — and put a fact on the wrong employer in 7 of 10 one-employer turns.
  `anthropic/claude-haiku-4.5` credited a whole fifteen-year cross-employer career to the
  candidate's current job, in every one of those turns. A confident wrong answer is worse for
  a candidate than a blank.
- **Schema drift.** `z-ai/glm-5.3-flash` and `mistralai/ministral-8b-2512` are the only two
  that emitted operations the schema rejected; in glm's case by creating an employer the
  answer named but gave no job title for, which the schema currently requires.
- **Not finishing.** `qwen/qwen3.8-flash` did not return at all on 17 of 30 calls at the
  180 s client timeout this run used (`LLM_TIMEOUT`). Re-measured with a 600 s timeout it does
  return — and then loses 40 % / 20 % / 40 % of turns anyway, so the timeout was hiding a
  second problem rather than being the only one. `deepseek/deepseek-v4-flash-0731` spent the
  entire 32k output budget on 4 turns and emitted nothing usable.

Cost is not the deciding factor: the spread across this table is about 30× in price and the
outcomes do not follow it.


**The same model measures differently on a different setting or gateway.** Reasoning left
on, reasoning disabled, and one gateway versus another are not cosmetic differences here:
the first model measured moved from 60 % to 20 % zero-op on the same shapes when the run
came through a different gateway with reasoning enabled. Read a row together with its
`reasoning` and `gateway` cells, and re-measure before trusting a row for a configuration
it was not measured on.

Tier-1 rows were measured against the operation schema as of `main` @ `24ee8cd6`. The
harness stores the exact validation error per rejected operation, so every row can be
re-scored (`--score`, no new provider calls) when that schema changes — a malformed-op rate
is a statement about a model *and* a schema, not about the model alone.

### After the prompt was fixed (2026-09-09, prompt v2)

The table above measures the prompt as it stood when the eleven models were run. The
prompt was then **changed twice**, and both changes were measured before and after on the
same shapes, the same fixture set and the same `n`. Two of the three models re-measured
moved verdict. The rows below are the state this release ships.

**What changed in the prompt.** (1) The rule about denials said that an answer which does
not address the question contributes nothing about the question's topic, and stated its
"emit nothing" escape a second time. An answer like *"I have not worked with insulin in
particular, but I have 15+ years at A, at B and now at C"* therefore gave the model two
explicit reasons to record nothing. It now says a denial is about its own item and nothing
else, and the escape is stated once. (2) The rule about entity identity said nothing about
a sentence naming several employers; it now says each clause binds to the employer that
clause names, with a worked example.

| Model | Gateway | Reasoning | n | lost turn S6/S7/S8 | malformed | wrong-slot | Tier 1 **before** | Tier 1 **after** | tokens in/out |
|---|---|---|---|---|---|---|---|---|---|
| `openai/gpt-5.6-luna` | OpenRouter | on (model default) | 10 | 0% / 0% / 0% | 0% / 0% / 0% | 0% / 0% / 0% | sub-par | **qualified** | 185,460 / 29,947 |
| `z-ai/glm-5.3-flash` | OpenRouter | on (model default) | 10 | 0% / 0% / 0% | 0% / 0% / 0% | 0% / 0% / 0% | sub-par | **qualified** | 190,311 / 42,364 |
| `mistralai/ministral-8b-2512` | OpenRouter | on (model default) | 10 | 0% / 0% / 0% | 10% / 0% / 0% | 0% / 0% / 0% | sub-par | **usable, with caveats** | 226,323 / 18,836 |

`openai/gpt-5.6-luna`'s move happened in two steps and only one of them is the prompt
review: the schema change that made a job title optional (a model that correctly refuses to
invent one could not create a station at all) already took it to a clean sheet, and the
prompt changes held it there. `z-ai/glm-5.3-flash` moved on the denial rule alone — **and
spent 48 % fewer output tokens doing it** (109,253 → 57,202 for the same 30 turns, and
again to 42,364 after the second change), with its median turn falling from 45 s to 6 s.
The model had been spending its reasoning budget deliberating a contradiction in the
instructions; removing the contradiction removed the deliberation. Better and cheaper on
the same call.

The remaining nine models in the table above have **not** been re-measured against the new
prompt. A row that says sub-par there is a statement about the old prompt; re-run the
harness before trusting it.

### One more thing about "turn reasoning off"

`OPENROUTER_DISABLE_THINKING` / `REQUESTY_DISABLE_THINKING` are a **request**, not a
switch. Two things were measured on 2026-09-09 that are worth knowing before you set them:

- **Some models refuse.** `z-ai/glm-5.3-flash` on OpenRouter answers *"Reasoning is
  mandatory for this endpoint and cannot be disabled"* (HTTP 400). Applire retries
  correctly with reasoning left on, so the model still works — but the setting saved you
  nothing, and until this release it re-sent the rejected request on **every** call, which
  doubled the round-trips and made timeouts more likely. It is now remembered per process.
- **Some routes never reason anyway.** The same model through a different gateway produced
  ~124 output tokens per call against ~2,724 on OpenRouter, with the setting untouched, and
  asking that route explicitly for `medium` reasoning effort did not change it. Whether a
  model reasons is a property of the model *and* the gateway, not of this setting.

If the goal is to spend less: the largest saving measured on this seam did not come from
the reasoning switch at all. It came from fixing the prompt.

### Reproduce it yourself

The harness is in the repository and opt-in — it never runs in CI, and it needs your own
API key:

```bash
# what the model is asked to read, without spending anything
PYTHONPATH=backend python3 scripts/model_matrix.py --dry-run

# measure one model (this spends real credit on your key)
PYTHONPATH=backend python3 scripts/model_matrix.py \
  --provider openrouter --model <model-id> --n 10 --shapes S6,S7,S8 \
  --out matrix.jsonl
```

Inputs are synthetic and committed (`tests/files/model_matrix/`), so two runs of the same
model on the same fixtures are comparable. The numbers describe *these* shapes on *this*
seam at the date given — a model that fails here may be perfectly good elsewhere, and
model behaviour changes under the same name over time.

## Models to watch: forced ("mandatory") reasoning

A few models — mostly seen on OpenRouter — are *forced* to "think" before every answer, and
you cannot turn it off. Because reasoning tokens are billed from the **same output budget**
as the answer (OpenRouter: *"reasoning tokens are considered output tokens"*), a model that
over-thinks can spend its budget reasoning and cut a document short, or simply run much
slower. Applire detects and works around this automatically — it raises budgets, segments
(ADR-047), and, when a model rejects "thinking off", retries with reasoning left on — so
these models still *work*. They are just a rougher ride. Given a choice, prefer a model
whose reasoning can be bounded or disabled.

Examples of forced-reasoning models (mid-2026 — treat the names as examples; the *behaviour*
is the durable signal):

- **Google Gemini Flash / Pro** — reasoning is flagged `mandatory` and cannot be disabled;
  this is the classic cause of a CV that truncates or a noticeably slow interview.
- **`deepseek/deepseek-v3.1-terminus`** — reasoning on by default, no way to turn it off.
- **`stepfun/step-3.5-flash:free`** — rejects any request that disables reasoning (HTTP 400).
- Any "reasoning-only" model with no non-thinking mode (o-series / R1-style) for the short
  interview calls.

**If you want to run one anyway**, set `OPENROUTER_REASONING_EFFORT=low` to cap the thinking
(OpenRouter only), and give it a generous output budget. Models like **Mistral**, **Claude**,
**GPT**, or a **Qwen3** *non-thinking* variant sidestep the issue entirely.

## Picking by priority

**If EU data residency matters** (the typical DACH case):

- **Mistral** (`LLM_PROVIDER=mistral`) — EU-hosted, strong German. The shipped fallback.
  Note some Mistral models have a modest output cap; segmentation covers this, and Applire
  runs fine on them.
- **Requesty** (`LLM_PROVIDER=requesty`) — EU endpoint (Frankfurt, zero-retention) that
  also routes to frontier models (Claude / GPT / Gemini) via their EU deployments. The
  EU-resident way to use a top-tier model.
- **Ollama** (`LLM_PROVIDER=ollama`) — fully offline, no key, no cloud. Pick a model with a
  generous context and set `num_ctx` explicitly (Ollama defaults it low). Larger instruct
  models (e.g. a capable ~8B+ instruct model) handle the structured calls best.

**If you just want the smoothest quality** and residency is not a constraint:

- **OpenRouter** (`LLM_PROVIDER=openrouter`) — one key, many models; lets you pick a model
  with a high output ceiling and bounded reasoning. Convenient for trying models.
- **Anthropic** (`LLM_PROVIDER=anthropic`) — Claude via a Console **API key** (a Claude
  Pro/Max/Team *subscription* cannot be used in third-party apps). US-hosted.
- **OpenAI / OpenAI-compatible** (`LLM_PROVIDER=openai`) — also covers LM Studio, vLLM, and
  other local servers via `OPENAI_BASE_URL`. Output-ceiling discovery isn't available on
  generic endpoints, so Applire relies on segmentation here — which is exactly what it's for.

## Symptoms and what they mean

| You see… | Likely cause | What to do |
|---|---|---|
| Generation retries or takes several steps | Model has a small output ceiling — segmentation is doing its job | Nothing required; pick a higher-output model for fewer steps |
| Occasional "couldn't finish — try again" | Transient provider error or a reasoning-heavy model | Retry; consider bounding reasoning (`OPENROUTER_REASONING_EFFORT=low`) |
| Generation is much slower than expected, or a document cuts off | A forced-reasoning model spending the budget on "thinking" (see above) | `OPENROUTER_REASONING_EFFORT=low`, or switch to a model whose reasoning can be disabled |
| Frequent malformed output on a tiny local model | Model too small for reliable structured output | Use a larger instruct model |

> Model names and limits change quickly — treat any specific model mentioned here as an
> example, not a pinned recommendation. The **capability floor** above is the durable guide.

## See also

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — ADR-009 (provider abstraction) and ADR-047 (output
  robustness) for how Applire keeps generation stable across models.
- `.env.example` — the environment variables for each provider.
