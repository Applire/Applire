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

## Which models work — measured (2026-09-08)

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
| **zero-op** | the model returned no operations at all | your answer is gone, with nothing on screen to say so |
| **malformed-op** | it emitted an operation the schema rejects | the same loss one layer down; Applire logs a warning and drops it |
| **wrong-slot** | a fact landed on a different employer than the one the answer named | your CV would credit the wrong job |
| **error** | the call failed (timeout, unparseable output, truncation) | visible and retried, so the least severe |

### The bar *(proposed — pending sign-off)*

Rates are measured per input shape; the **worst** shape decides the model's verdict.

| Verdict | Meaning |
|---|---|
| **qualified** | every shape clean: no lost turn, no malformed operation, no wrong slot |
| **usable, with caveats** | losses occurred but stayed under every bar below |
| **sub-par** | any bar crossed on any shape |

Bars: **zero-op > 5 %** · **malformed-op > 10 %** · **wrong-slot > 10 %** · **error > 10 %**.

zero-op carries the tightest bar because it is the one failure the product cannot show you.

### Results

<!-- Rows are filled from a real run of scripts/model_matrix.py; never from a datasheet,
     a benchmark score, or a reputation. An unmeasured model has no row. -->

| Model | Provider | n | shapes | zero-op | malformed | wrong-slot | error | Verdict | Cost/run | Measured |
|---|---|---|---|---|---|---|---|---|---|---|
| _(pending — first run 2026-09-08)_ | | | | | | | | | | |

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
