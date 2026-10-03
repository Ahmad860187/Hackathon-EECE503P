# Paper to Playground

An agent that turns a research-paper reference and a focused learning brief into **one self-contained,
offline, interactive HTML page** that teaches the mechanism to an engineering undergraduate. Learners
change inputs and watch the effect.

**Team:** _TEAM MEMBERS_

## Run

```bash
python -m pip install -r requirements.txt
python agent.py --input case.json --output out --model deepseek/deepseek-v4.1-flash
```

- `MODEL_ID` used for development: **`deepseek/deepseek-v4.1-flash`** (OpenRouter). Any OpenRouter chat model ID works.
- Requires `OPENROUTER_API_KEY` in the environment. Nothing else: no browser, GPU, or system packages.
- Writes `out/index.html` (single file, no CDN/fonts/remote assets) and `out/trace.jsonl`.
- Exit code 0 = the page passed every acceptance check. Nonzero = failure, but the best usable page and the trace are still written.

`case.json` holds three required non-empty strings: `source_url`, `focus` (concept and learning outcomes) and `audience`.

## Architecture

```
case.json ─► LOAD ─► GENERATE (1 LLM call) ─► ASSEMBLE ─► CHECK (0 tokens) ─► pass ─► write page + trace
                                                             │
                                                             └─ fail ─► REPAIR (only the failing section,
                                                                        ≤2 calls) ─► re-check everything,
                                                                        keep the best candidate
```

- **One main model call.** The model returns three delimited sections: `SPEC` (JSON content: idea, symbols,
  equations, controls, explorations, limitation, grounded claims, invariants, tests), `COMPUTE` (a pure JS
  `compute(p)`) and `RENDER` (JS that draws with our visual kit). Code is never embedded in JSON.
- **Fixed generic shell** (`paper2play/shell/`). This is the page layout, teaching structure and interaction
  (controls, "Try it" exploration presets, predict-then-reveal, live equations with current numbers,
  grounding badges, a live invariant checklist) plus a **visual kit**: plot, bars, matrix, pipeline,
  timeline, grid, graph, vec2d, flow (cause→effect), compare. Visual design follows multimedia-learning
  principles: an establishing caption, a ghost of the previous state for continuity, one focal accent,
  direct labels, and consistent colour meaning. The model writes no CSS or layout, so output tokens go to
  content and logic only.
- **Real checks, no tokens** (`paper2play/checks.py`). The generated JavaScript is executed with
  [quickjs](https://pypi.org/project/quickjs/) against a DOM stub:
  - **Structure:** S1 required fields, S2 self-contained, S3 protocol.
  - **Execution:** X1 parses, X2 finite defaults, X3 control min/max, X4 every control matters,
    X5 render smoke, X6 time limits.
  - **Science and teaching:** C1 the model's reference tests, C2 exploration presets change the watched
    value, C3 invariants, C4 live values resolve.
  - **Grounding honesty:** G1.

  Each result is logged in the trace, and failures trigger targeted repairs.
- **Budget guard** (`paper2play/budget.py`). This enforces 10 min, ≤10 requests incl. retries and ≤30k
  completion tokens per case. Hidden reasoning is disabled by default because it counts as completion
  tokens and dominated latency in our measurements. If a reply is empty because reasoning used up the
  budget, the agent retries with reasoning off.
- **Source loading** (`paper2play/source.py`). The source is resolved in this order: an excerpt given
  inline (explicitly marked), a local file, a short fetch (arXiv abs/pdf/html, mirrors, DOIs and bare
  IDs), or none. The origin is recorded, and the page labels each claim **📄 from the excerpt**,
  **🧠 from the paper (not verified against an excerpt)** or **🧪 our illustration**. Section and equation
  numbers are never invented.

## Trace

`out/trace.jsonl` holds one JSON event per line: stage, action and result, per-call prompt/completion
(and reasoning) tokens from the API `usage` field, elapsed seconds, every check result, failures,
repairs and totals. Credentials, headers and reasoning text are never logged.

## Evaluation (development only)

- 19 practice cases in `eval/cases/` cover ML, information theory, epidemiology, biochemistry, ecology,
  genetics, economics, finance, sociology and signal processing. They also include 7 cases derived from
  **SciCode** and **TheoremExplainBench** (physics, chemistry, materials, electronics). Independently
  computed reference values were used for grading during development and are deliberately not part of
  the repository or the agent.
- `scripts/eval.py` runs cases × runs into fresh directories and reports accept rate, tokens, latency
  and failing checks. `--no-excerpt` simulates OpenRouter-only network access.
- `scripts/quiz_eval.py` measures teaching clarity: a separate model answers multiple-choice questions
  using only the page text. This follows the PaperQuiz method from Paper2Poster.
- `eval/RUBRIC.md` is a scoring sheet that mirrors the assessment criteria.

## Example

`examples/attention/` contains the input `case.json` and a generated `out/index.html` plus `out/trace.jsonl`.

## Credits and reuse

- [quickjs](https://pypi.org/project/quickjs/) (MIT): JavaScript engine for checks. [requests](https://pypi.org/project/requests/) (Apache-2.0).
- Development cases derived from [SciCode](https://github.com/scicode-bench/SciCode) (Apache-2.0) and
  [TheoremExplainBench](https://arxiv.org/abs/2502.19400) (MIT). The quiz evaluation method is from
  [Paper2Poster](https://arxiv.org/abs/2505.21497). See `eval/BENCHMARKS.md`.
- Built with the help of an AI coding assistant (Claude Code), as permitted by the hackathon rules.
  All code was reviewed and tested by the team.
