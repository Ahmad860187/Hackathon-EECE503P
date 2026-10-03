# Paper to Playground: Build Plan (v2, merged)

The authoritative source is the brief PDF. Where the brief is ambiguous, this plan says so and chooses the most robust option.
User correction: `case.json` has **3** required string fields: `source_url`, `focus`, `audience`.

---

## 0. Decisions (from the ECC council review: Architect, Skeptic, Pragmatist, Critic)

| # | Decision | Chosen | Why |
|---|---|---|---|
| D1 | Model calls per run | **1 generate call + at most 2 targeted repairs** (worst case 3 of 10) | All 3 outside voices: a planner+builder pair doubles prompt tokens, latency and hidden-reasoning spend. Token and latency scores are relative to the best team. A second stage is added **only if E3 (below) shows ≥ +5/85 quality.** |
| D2 | Visuals | **Composable primitive kit + custom-render escape hatch** | Not a closed list of category templates. Papers come from any field, so a fixed menu will misfit. Tested helpers make broken or blank pages rare, and the model can still compose them or draw raw SVG/Canvas. |
| D3 | Source text | **Layered loader, honest labels** (see §2) | Assessment network is OpenRouter-only, and each hidden case "contains an excerpt". We must work whether the excerpt arrives inline, as a file, as a URL, or not at all. |
| D4 | Output protocol | **Delimited sections, not JSON-embedded code** | JS inside JSON breaks on escaping and is the top cause of parse failures. Only the small `SPEC` block is JSON. |
| D5 | Checks | **Really execute the page's JS in Python** (pip JS engine + DOM stub) | Gives real evidence for the 10 autonomy points and catches dead controls, the most common failure. |
| D6 | Never zero | **Deterministic fallback page** + best-candidate preservation | "Runs producing no usable page receive zero." |

Strongest dissent, kept on record: *a primitive kit could cap the visual score at "generic"*. Mitigation: the escape hatch, plus per-paper composition with labelled, annotated visuals.

---

## 1. Pipeline (one process, budget-aware)

```
case.json
  │
  ▼
[1] LOAD  ── validate 3 non-empty strings · resolve source (§2) · start budget clock
  │
  ▼
[2] GENERATE (call 1) ── compact system prompt + kit API cheat-sheet + case + excerpt
  │      returns  ===SPEC=== (JSON)  ===COMPUTE=== (JS)  ===RENDER=== (JS)
  ▼
[3] ASSEMBLE ── inject into fixed shell → candidate HTML (fully inline)
  │
  ▼
[4] CHECK (0 tokens) ── structure · execution · science · interaction (§5)
  │        pass ─────────────────────────────────────────────┐
  │        fail → [5] REPAIR (call 2..3): failing check names │
  │                 + only the broken section → re-assemble   │
  │                 → re-check ALL (catch regressions)        │
  ▼                                                           ▼
[6] PROMOTE best candidate (highest check score, no regressions) → out/index.html
[7] TRACE flush → out/trace.jsonl · exit 0 if acceptance checks pass, else 1 (page still written)
```

**Budget guard** (checked before every call):
- Stop if calls ≥ 10, or completion tokens used ≥ 30k, or elapsed time ≥ 9:00.
- `max_tokens` = min(per-call cap, remaining completion budget).
- HTTP timeout = remaining time − 30 s reserve for saving.
- Retries on 429/5xx count against the 10 requests.

**Reasoning models:** cap `max_tokens` (the target is about 8k on the main call). Send `reasoning: {effort: "low"}` when the model supports it, and test this in E1. If a response is truncated, follow the repair path rather than resending everything.

---

## 2. Source loader (`load_source(case) -> {text, title, section, origin}`)

Try these in order. Record which step succeeded (`origin`) in the trace and on the page:
1. **Inline excerpt.** `focus` (or `source_url`) already contains the excerpt text, detected by length or quotes.
2. **Local file.** `source_url` is a path that exists, resolved relative to `case.json`.
3. **Network fetch,** ≤ 3 s timeout. arXiv `abs` gives title and abstract; `html` gives the section text, sliced around keywords taken from `focus`. Use the stdlib (`urllib`) only.
4. **None.** The model works from `focus` and its own knowledge of the paper.

The model's grounding labels depend on `origin`:
- 📄 **From the source excerpt.** Allowed only when step 1, 2 or 3 succeeded.
- 🧠 **From the cited paper, not verified against an excerpt.** Used when step 4 was reached.
- 🧪 **Our illustration or simplification.**

Never invent section or equation numbers. If unsure, cite the paper and the concept name only.

**Open question for the instructor (ask at the start):** *Where does the excerpt come from, given that the input has only 3 fields and assessment network access is OpenRouter-only?*

---

## 3. Model output protocol (call 1)

```
===SPEC===
{ "title","paper","authors_year","section_or_eq","source_origin",
  "idea","why_it_matters","symbols":[{"sym","meaning","unit"}],
  "equations":[{"tex_like","plain","source":"excerpt|paper|illustration"}],
  "controls":[{"id","label","type":"slider|number|select|toggle|matrix","min","max","step","default","meaning"}],
  "intermediates":[{"key","label","format"}],
  "explorations":[{"title","preset":{...},"change","observe","why"} x2],
  "limitation":{"kind":"limitation|assumption|misconception","text"},
  "claims":[{"text","support":"excerpt|paper|illustration"}],
  "tests":[{"name","params":{...},"assert":"JS boolean expr over result"}],
  "visual_plan":"one line: what is drawn and what moves"
}
===COMPUTE===
function compute(p) { /* pure, deterministic, no DOM; returns {…intermediates, series…} */ }
===RENDER===
function render(r, p, kit) { /* uses kit.* or raw SVG; called on every change */ }
```

- The model writes **no CSS and no layout.** The shell owns presentation, so output tokens go only to content and logic.
- `tests` are written by the model. They are **consistency checks, not independent proof**, and the trace says so.

---

## 4. Fixed shell and primitive kit (written once, generic, nothing paper-specific)

**Shell section order** follows the rubric:
1. Idea and why it matters
2. Symbols table
3. Playground: visual, controls, intermediates
4. Exploration 1 and exploration 2
5. Limitation
6. Source grounding
7. Live checks

Uses system fonts and inline CSS, has light and dark themes, and fits in one screen width.

**Kit (vanilla JS, ~15 KB, tested on the public examples):**
- `kit.plot`: axes, lines, points, shaded regions, a movable marker, auto-scaling and labels.
- `kit.bars`: categorical bars with values and an optional stacked "contribution" view.
- `kit.matrix`: an editable or read-only heatmap with numbers in the cells and row/column labels.
- `kit.pipeline`: a stage-by-stage flow (A → op → B → op → C) where each stage is a matrix, vector or scalar view.
- `kit.timeline`: a time-stepped simulation with play, pause, step and reset buttons (handles ODEs and iterative algorithms).
- `kit.grid`: a cellular or agent grid (e.g. Schelling, diffusion, game of life).
- `kit.graph`: nodes and edges with weights, plus step highlighting (Markov chains, PageRank, message passing).
- `kit.vec2d`: a 2D canvas with draggable handles (vectors, projections, geometry).
- `kit.eq`: shows the equation **with the live numbers substituted in** (e.g. H = −Σ p log p = 1.52 bits).
- `kit.values`: a table of intermediate values with a short highlight on each changed cell.

**Teaching features built into the shell** (zero tokens per run; the creative edge):
- **"Try it" buttons** for each exploration. They apply the preset, scroll to the visual and highlight the value to watch.
- **Predict, then reveal:** each exploration asks "What do you expect?" before showing the "why".
- **Live check panel:** the model's invariants run in the browser (e.g. "each row of weights sums to 1 ✓"), so learners and the assessor can see that the calculations hold.
- **Grounding badges** (📄 / 🧠 / 🧪) on every claim and equation.
- **Change highlighting:** the values affected by the last control move flash briefly, showing cause and effect.
- Multimedia-learning principles: labels placed next to what they describe, one colour meaning one thing, explanations revealed step by step.

---

## 5. Checks (0 tokens; each logged by name with pass/fail and evidence)

| ID | Check | How |
|---|---|---|
| S1 | Required sections and fields present | parse SPEC; ≥2 controls, 2 explorations, a limitation, a citation |
| S2 | Self-contained | no `http(s)://` in `src`/`href`/`@import`/`url()`, apart from the citation link text; no key strings |
| S3 | Protocol parsed | all 3 sections present; SPEC is valid JSON |
| X1 | JS parses | JS engine compiles COMPUTE and RENDER |
| X2 | Default run | `compute(defaults)` returns finite numbers for every intermediate |
| X3 | Boundaries | `compute` at each control's min and max (and 0 where relevant) gives no NaN, Infinity or exception |
| X4 | Controls matter | changing each control alone changes at least one output (catches dead controls) |
| X5 | Render smoke | `render(...)` runs against a DOM/SVG stub without throwing |
| X6 | Timeouts | every JS execution is capped at 1 s |
| C1 | Model's tests | all `tests[].assert` pass |
| C2 | Exploration presets | each preset runs, and its observed key changes in the direction the text claims |
| C3 | Generic science properties | probabilities in [0,1] summing to 1; counts are integers; units shown |
| G1 | Grounding honesty | `source_origin` ≠ excerpt means no 📄 badges |

**Acceptance** = S1–S3, X1–X5 and G1 all pass. C1–C3 failures trigger a repair while budget remains.

**JS engine:** `mini-racer` (V8 wheels), with `quickjs` as the fallback. **E0 must confirm one of them installs from pip alone on Linux and Windows with Python 3.11.** If neither does, the X checks are recorded as "not run" (never as "pass").

---

## 6. Trace (`out/trace.jsonl`, one JSON object per line)

```json
{"t":0.00,"stage":"load","action":"resolve_source","result":"ok","origin":"none","detail":"fetch timeout 3s"}
{"t":0.41,"stage":"generate","action":"llm_call","call":1,"model":"…","prompt_tokens":3120,"completion_tokens":5480,"elapsed_s":21.3,"result":"ok"}
{"t":21.9,"stage":"check","action":"X4_controls_matter","result":"fail","detail":"control 'scale' changes nothing"}
{"t":22.0,"stage":"repair","action":"llm_call","call":2,"targets":["COMPUTE"],"prompt_tokens":1400,"completion_tokens":900,"elapsed_s":6.2,"result":"ok"}
{"t":28.4,"stage":"check","action":"rerun_all","result":"pass","passed":13,"failed":0}
{"t":28.5,"stage":"output","action":"write","result":"ok","totals":{"calls":2,"prompt_tokens":4520,"completion_tokens":6380,"elapsed_s":28.5}}
```

Token usage comes from the API `usage` field. If it's missing, record `"unknown"`, never an estimate presented as fact. No key, no headers and no hidden reasoning are logged.

---

## 7. Research and evaluation plan (ECC `eval-harness` style)

**Practice set:** 12 cases, deliberately **not just math or CS**. We write them ourselves in the 3-field format, each with a short excerpt file for the "excerpt available" condition.

| # | Field | Paper / section | Visual pattern exercised |
|---|---|---|---|
| 1 | ML | Vaswani et al. 2017, §3.2.1 scaled dot-product attention (public) | pipeline + matrix |
| 2 | Info theory | Shannon 1948, §6 entropy (public) | bars + eq |
| 3 | Epidemiology | Kermack & McKendrick 1927, SIR model | timeline + plot |
| 4 | Biochemistry | Michaelis & Menten 1913, rate vs substrate | plot + marker |
| 5 | Ecology | Lotka–Volterra predator–prey (Volterra 1926) | timeline + phase plot |
| 6 | Genetics | Hardy 1908, genotype equilibrium | bars |
| 7 | Economics | Akerlof 1970, "Market for Lemons" adverse selection | plot + bars (qualitative mechanism) |
| 8 | Finance | Black & Scholes 1973, option price vs volatility | plot |
| 9 | Sociology | Schelling 1971, segregation dynamics | grid (stochastic) |
| 10 | Signals | Nyquist 1928, sampling and aliasing | plot (two series) |
| 11 | ML optimisation | Kingma & Ba 2014 (Adam), bias correction | timeline + plot |
| 12 | Generative ML | Ho et al. 2020 (DDPM), closed-form forward noising | plot + slider over t |

Each case gets **independently derived reference values** (computed by us, not the model) for 2–3 parameter settings. These score accuracy during development only and never ship in the repo's templates.

**Harness:** `scripts/eval.py`
- Runs every case × 2 in fresh output directories.
- Writes `eval/results.csv` with: case, run, calls, prompt and completion tokens, latency, check pass/fail by ID, accept, exit code.
- Then a **browser assessment pass** in the built-in browser. Claude acts as the assessor: it opens each page, operates every control, clicks the "Try it" buttons, compares against the reference values, and scores the 5 quality criteria (0–85) using the rubric text from the brief. Results go into `eval/scores.csv`.

**Experiments.** Each one changes one variable. Metrics: mean quality (out of 85), acceptance rate, mean total tokens, mean latency.

| ID | When | Question | Decision rule |
|---|---|---|---|
| E0 | H1 | Does `mini-racer` or `quickjs` install from pip alone (py3.11, Linux + Windows)? | pick the first that works; otherwise the X checks are "not run" |
| E1 | H1 | Reasoning-token behaviour of the dev model and of one reasoning model; is `reasoning.effort` honoured? | set the default `max_tokens` and effort |
| E2 | H3 | Kit cheat-sheet: full docs vs a compact list of function signatures | keep the smaller one unless quality drops by >3 |
| E3 | H3 | 1-call vs planner+builder (2-call) on cases 1, 3, 7, 9 × 2 | adopt 2-call only if quality rises ≥ +5 and acceptance doesn't fall |
| E4 | H4 | Repair on vs off (cases with injected failures + natural ones) | keep repair if it rescues ≥1 in 4 failures |
| E5 | H5 | Robustness: network blocked except OpenRouter; varied audiences; vague `focus` | ≥ 90% acceptance and no run below 50/85 |
| E6 | H5 | Run-to-run variance (temperature 0 vs 0.3) | pick the lower-variance setting |

**Public benchmarks (development testing only; nothing from them ships inside the agent):**

None of these matches our task exactly, so each is used for the part it is good at:

| Benchmark | What it contains | How we use it |
|---|---|---|
| [TheoremExplainBench](https://arxiv.org/html/2502.19400v2) (ACL 2025) | 240 theorems across STEM fields, used for agent-made visual explanations, with 5 automated metrics | Sample about 15 theorems from fields we haven't covered, turn each into a `case.json`, and score our pages using its criteria (accuracy, visual relevance, logical flow). This tests generalisation to unseen topics. |
| [SciCode](https://arxiv.org/abs/2407.13168v1) (NeurIPS 2024) | 80 research problems in 16 natural-science subfields, with scientist-written background text, gold solutions and test cases | Use the background text as the "excerpt" and the gold solutions as **independent reference values** to check our `compute()` numbers. This is the strongest accuracy test available. |
| [Paper2Poster](https://arxiv.org/html/2505.21497v2) (2025), *PaperQuiz* method | Measures whether a generated page conveys the paper's core content, by having a model answer quiz questions using only that page | Borrow the method: a separate model sees only our page and answers 5 questions on the concept. This gives a teaching-clarity score we can compare between versions. |

Each is credited in the README if used. Run a subset at H3 (generalisation) and at H5 (final regression).

**Failure log:** every failed run gets one line in `eval/failures.md` with its cause, the fix and a re-test. Generalisation fixes go into the prompt or kit, **never** into paper-specific code.

---

## 8. Six-hour schedule

| Time | Deliverables | ECC tools |
|---|---|---|
| **H1** | repo skeleton, CLI and argument validation, source loader, OpenRouter client with budget guard and usage logging, trace writer, E0 and E1, first end-to-end page for case 1 | `/ecc:project-init`, `ecc:tdd-guide` |
| **H2** | shell + kit (plot, bars, matrix, pipeline, eq, values, Try-it buttons, badges, live checks); cases 1–2 pass acceptance; check suite S/X | `ecc:frontend-design-direction`, `ecc:a11y-architect` |
| **H3** | cases 3–9 (fields outside math/CS); add timeline, grid, graph, vec2d; E2 and E3 | `ecc:eval-harness` |
| **H4** | repair loop, regression re-check, best-candidate preservation, fallback page, timeouts; E4 | `ecc:silent-failure-hunter` |
| **H5** | full eval (12 cases × 2), browser assessment, E5 and E6, fix generalisation failures | built-in browser, `ecc:e2e-runner` |
| **H6** | fresh-venv install rehearsal; README (team, architecture, MODEL_ID, credits); example input and output; key leak scan; push; check repo access; submit URL + full SHA | `ecc:security-reviewer`, `ecc:code-reviewer`, `/ecc:quality-gate` |

**Freeze rule:** no new features after H5:00. Only bug fixes and the README.

---

## 9. Repo layout

```
agent.py                 # CLI entry: load → generate → check → repair → write
requirements.txt         # pinned: requests, mini-racer (or quickjs)
paper2play/
  source.py  llm.py  budget.py  trace.py  protocol.py  assemble.py  checks.py  fallback.py
  prompts/system.md  prompts/repair.md  prompts/kit_cheatsheet.md
  shell/shell.html  shell/kit.js  shell/style.css   # generic, inlined at assembly
examples/attention/{case.json, excerpt.txt, out/}  # one showcase pair
scripts/eval.py   eval/cases/*   (dev only)
README.md
```

---

## 10. Guardrails

- No paper-specific answers or pages in templates or prompts. This is a brief rule.
- Repository and page text never address the assessor. The brief treats that as reward hacking.
- Keys come only from `OPENROUTER_API_KEY`. `.env` is in `.gitignore`. Run a secret scan before every push.
- Credit everything we reuse in the README.
- Before the hackathon starts, confirm with the instructor whether generic code may be written ahead of time.
