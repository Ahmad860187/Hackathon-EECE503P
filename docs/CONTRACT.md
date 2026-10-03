# Interface contract (all modules must follow this)

Pipeline: `agent.py` → load case + source → **1 LLM call** returns 3 delimited sections → Python assembles a
single self-contained HTML from the fixed shell + kit + generated code → checks run the JS in **quickjs**
(Python, `quickjs==1.19.4`) → targeted repair calls (max 2) → write `out/index.html` + `out/trace.jsonl`.

## 1. LLM output protocol (exact delimiters, each on its own line)

```
===SPEC===
{ ...JSON, schema in §2... }
===COMPUTE===
function compute(p) { ... return r; }
===RENDER===
function render(r, p, kit, el) { ... }
===END===
```

- COMPUTE is pure: no DOM; randomness only via `kit.rng(seed)`-style seeded PRNG written inside it; must be
  deterministic for the same `p`. Returns a plain object `r`.
- RENDER draws into `el` (an empty `<div>` the shell clears before every call) using `kit.*` (preferred) or
  raw SVG via `kit.svg(...)`. It may call `kit.*` several times (multiple panels).
- Both may define helper functions inside their own section. No `import`, no `fetch`, no timers except via `kit.timeline`.

## 2. SPEC schema (JSON)

```json
{
  "title": "short page title",
  "paper": {"title": "", "authors": "", "year": "", "url": "", "section": "e.g. §3.2.1, Eq. (1) — or \"\" if unknown"},
  "idea": "2-4 sentences, plain language for the audience",
  "why": "1-3 sentences why it matters",
  "symbols": [{"sym": "d_k", "meaning": "key dimension"}],
  "equations": [{"label": "Scaled dot-product attention", "expr": "Attention(Q,K,V) = softmax(QKᵀ/√d_k)·V",
                 "live": "optional template with {key} placeholders from r or p, e.g. 'H = {H} bits'",
                 "support": "excerpt|paper|illustration"}],
  "controls": [
    {"id": "temp", "label": "Temperature T", "type": "range", "min": 0.1, "max": 5, "step": 0.1, "default": 1, "help": "what it means"},
    {"id": "scale", "label": "Scale by 1/√d_k", "type": "toggle", "default": true},
    {"id": "n", "label": "Outcomes", "type": "select", "options": [2,3,4,5,6], "default": 4},
    {"id": "Q", "label": "Queries Q", "type": "matrix", "rows": 2, "cols": 3, "default": [[1,0,1],[0,1,0]], "step": 0.1},
    {"id": "probs", "label": "Probabilities", "type": "vector", "length": 4, "default": [0.25,0.25,0.25,0.25], "min": 0, "max": 1, "step": 0.01}
  ],
  "intermediates": [{"key": "weights", "label": "Attention weights", "fmt": 3, "unit": ""}],
  "visual": {"caption": "One sentence: what you are looking at", "how_to_read": "One sentence: how to read it (axes, colours)"},
  "explorations": [
    {"title": "", "predict": "Question asking the learner to predict", "preset": {"temp": 0.2},
     "watch": "key in r to highlight", "observe": "what they should see", "why": "the mechanism explaining it"}
  ],
  "limitation": {"kind": "limitation|assumption|misconception", "text": ""},
  "claims": [{"text": "", "support": "excerpt|paper|illustration"}],
  "invariants": [{"label": "Each row of weights sums to 1", "expr": "r.weights.every(row => Math.abs(row.reduce((a,b)=>a+b,0)-1) < 1e-6)"}],
  "tests": [{"name": "uniform 4 outcomes gives 2 bits", "params": {"n": 4}, "expr": "Math.abs(r.H - 2) < 1e-9"}]
}
```

- Exactly 2 explorations, ≥2 controls, ≥1 invariant, ≥2 tests.
- `preset` / `tests[].params` are partial: missing ids take their `default`.
- `expr` strings are JS boolean expressions over `r` and `p`.
- Matrix/vector values in `p` are arrays of numbers; toggle → boolean; select → the option value; range/number → number.

## 3. Shell runtime (paper2play/shell/*) — globals available to generated code

The assembled page contains, in order: `style.css` inline, then `<script>` with `const SPEC = …; const BUILD = …;`,
then `kit.js`, then COMPUTE, then RENDER, then `app.js` (builds the page from SPEC and wires everything).

`app.js`: builds header, sections (Idea & why · Symbols · Playground [visual | controls + intermediates + live
equations] · Explore ×2 [predict → "Try it" button applies preset + highlights `watch` → reveal observe/why] ·
Limitation · Source & grounding (paper, section, claims with badges 📄 excerpt / 🧠 paper-not-verified / 🧪
illustration, plus `BUILD.source_origin` note) · Checks (live invariants ✓/✗ + `BUILD.checks` summary)).
On every control change: `r = compute(p)`; clear `el`; `render(r, p, kit, el)`; update intermediates table
(flash changed cells), live equations, invariants. Errors are caught and shown inline in the visual area,
never a blank page.

## 4. kit API (global `kit`)

All drawing functions append an SVG (responsive via viewBox, width 100%) to `el` and return it.
Colours come from CSS variables (light/dark). Visual principles are implemented **inside the kit**:
smooth 250 ms transitions, a faint **ghost of the previous state** on plots/bars (continuity: shows what
changed), accent colour reserved for the highlighted / watched element, direct labels instead of legends
where possible, readable font ≥ 12px, `prefers-reduced-motion` respected.

| Function | Options |
|---|---|
| `kit.plot(el, o)` | `o = {title, x:{label,min?,max?,log?}, y:{label,min?,max?}, series:[{name, points:[[x,y],…], dashed?, area?, highlight?}], markers:[{x,y,label}], vlines:[{x,label}], hlines:[{y,label}], note?}` |
| `kit.bars(el, o)` | `o = {title, labels:[], values:[], unit?, max?, highlight:[idx], fmt?, secondary?:{name, values:[]}}` (secondary = second bar set side-by-side) |
| `kit.matrix(el, o)` | `o = {title, data:[[...]], rowLabels?, colLabels?, fmt?, scale:'seq'|'div', highlight:[[i,j]]}` numbers drawn in cells |
| `kit.pipeline(el, o)` | `o = {stages:[{title, kind:'matrix'|'vector'|'scalar'|'text', data, caption?}], ops:['×Kᵀ','÷√d','softmax','×V']}` stages left→right (wraps on narrow screens) with op arrows |
| `kit.timeline(el, o)` | `o = {length, t0?, fps?, draw:(t, sub)=>void}` adds play/pause/step/reset + scrubber; calls `draw(t, sub)` with a cleared sub-div each frame |
| `kit.grid(el, o)` | `o = {title, cells:[[v,…]], palette?:{value:cssColor}, labels?:{value:text}, cellSize?}` |
| `kit.graph(el, o)` | `o = {title, nodes:[{id,label,x,y,value?}], edges:[{from,to,weight?,label?}], highlight:[id]}` (x,y in 0..1) |
| `kit.vec2d(el, o)` | `o = {title, range, vectors:[{x,y,label,from?:[x,y]}], handles:[{id, x, y}]}` dragging handle `id` calls `kit.set(id+'_x', v)` and `kit.set(id+'_y', v)` |
| `kit.callout(el, text, kind?)` | annotation box (`kind`: 'info'|'warn'|'key') |
| `kit.svg(el, w, h)` | raw SVG root (viewBox 0 0 w h) for custom drawing; `kit.svgEl(tag, attrs, parent)` helper |
| `kit.set(id, value)` | programmatically set a control (updates UI + reruns) |
| `kit.fmt(x, d=3)` | number/array/matrix → string |
| `kit.color(i)` / `kit.accent` | categorical palette / accent colour |
| `kit.rng(seed)` | deterministic PRNG → function returning [0,1) |

Only these DOM APIs may be used by kit.js and RENDER code (the Python render-smoke check stubs exactly these):
`document.createElement`, `document.createElementNS`, `el.setAttribute`, `el.appendChild`, `el.append`,
`el.replaceChildren`, `el.textContent`, `el.innerHTML` (kit only), `el.classList.add/remove/toggle`, `el.style.*`,
`el.addEventListener`, `el.getBoundingClientRect` (may return zeros), `requestAnimationFrame` (kit only).
kit.js must not touch the DOM at load time (only inside function calls), so it can be loaded in quickjs.

## 5. Assembly (paper2play/assemble.py)

`assemble(spec: dict, compute_js: str, render_js: str, build: dict) -> str` — reads shell/shell.html,
replaces `/*@STYLE@*/`, `/*@DATA@*/`, `/*@KIT@*/`, `/*@COMPUTE@*/`, `/*@RENDER@*/`, `/*@APP@*/` placeholders.
JSON is embedded with `</` escaped as `<\/`. Result must contain no external URLs except `SPEC.paper.url`
as plain link text/href.

`BUILD = {source_origin: "inline|file|fetched|none", model, checks:[{id, result, detail}], generated_at}`

## 6. Trace event shape (one JSON per line)

`{"t": seconds_since_start, "stage": "...", "action": "...", "result": "ok|fail|pass|skip|error", ...extra}`
LLM calls add: `call, model, prompt_tokens, completion_tokens, reasoning_tokens?, elapsed_s, finish_reason`.
Never log keys, headers, or reasoning text.
