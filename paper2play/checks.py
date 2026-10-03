"""Zero-token checks (PLAN §5): structure, execution (quickjs), science, grounding.

Each check returns {"id", "name", "result": "pass"|"fail"|"skip", "detail"}.
Acceptance = S1 S2 S3 X1 X2 X3 X4 X5 G1 have no "fail".
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .spec import default_params, merged_params, option_values

SHELL_DIR = Path(__file__).resolve().parent / "shell"
ACCEPT_IDS = ("S1", "S2", "S3", "X1", "X2", "X3", "X4", "X5", "G1")
CHECK_NAMES = {
    "S1": "required_fields", "S2": "self_contained", "S3": "protocol_parsed",
    "X1": "js_parses", "X2": "default_run", "X3": "boundaries", "X4": "controls_matter",
    "X5": "render_smoke", "X6": "time_limits", "C1": "model_tests", "C2": "exploration_presets",
    "C3": "invariants", "C4": "live_keys_resolve", "G1": "grounding_honesty",
}
ORDER = ("S1", "S2", "S3", "X1", "X2", "X3", "X4", "X5", "X6", "C1", "C2", "C3", "C4", "G1")
EVAL_TIME_LIMIT_S = 1
MEMORY_LIMIT = 128 * 1024 * 1024
CHECKS_TIME_BUDGET_S = 25.0

try:  # quickjs is a pinned requirement; degrade to "skip" (never "pass") if unavailable
    import quickjs  # type: ignore
except Exception:  # pragma: no cover
    quickjs = None

_HELPERS = r"""
var __p2p = (function(){
  function get(o, path){ var parts = String(path).split('.'); var v = o;
    for (var i=0;i<parts.length;i++){ if (v===null||v===undefined) return undefined; v = v[parts[i]]; } return v; }
  function find(x, bad, path, depth){
    if (depth > 6) return null;
    if (typeof x === 'number') return bad(x) ? path + '=' + String(x) : null;
    if (x && typeof x === 'object') {
      var keys = Array.isArray(x) ? null : Object.keys(x);
      var n = keys ? keys.length : x.length;
      for (var i=0; i<n && i<5000; i++){
        var k = keys ? keys[i] : i;
        var f = find(x[k], bad, path + (keys ? '.' + k : '[' + k + ']'), depth+1);
        if (f) return f;
      }
    }
    return null;
  }
  function isnan(v){ return v !== v; }
  function nonfinite(v){ return !isFinite(v); }
  function errstr(e){ var s = (e && e.message) ? (e.name ? e.name + ': ' : '') + e.message : String(e); return s.slice(0, 300); }
  function run(pjson, keysjson){
    var p = JSON.parse(pjson), keys = JSON.parse(keysjson), r;
    try { r = compute(p); } catch (e) { return {error: errstr(e)}; }
    if (r === null || typeof r !== 'object' || Array.isArray(r))
      return {error: 'compute(p) must return a plain object, got ' + (Array.isArray(r) ? 'array' : (r === null ? 'null' : typeof r))};
    var out = {nan: find(r, isnan, 'r', 0), missing: [], nonfinite: [], keys: Object.keys(r).slice(0, 40)};
    for (var i=0;i<keys.length;i++){
      var v = get(r, keys[i]);
      if (v === undefined) out.missing.push(keys[i]);  // null = "not applicable" (e.g. no mixed equilibrium)
      else { var b = find(v, nonfinite, keys[i], 0); if (b) out.nonfinite.push(b); }
    }
    try { out.sig = JSON.stringify(r); } catch (e) { return {error: 'result not JSON-serialisable: ' + errstr(e)}; }
    return out;
  }
  function watch(pjson, path){
    var r = compute(JSON.parse(pjson)); var v = get(r, path);
    return {present: v !== undefined, sig: v === undefined ? null : JSON.stringify(v)};
  }
  function expr(pjson, src){
    var p = JSON.parse(pjson); var r;
    try { r = compute(p); } catch (e) { return {error: 'compute threw: ' + errstr(e)}; }
    var f;
    try { f = new Function('r', 'p', 'return (' + src + ');'); } catch (e) { return {error: 'expr does not parse: ' + errstr(e)}; }
    try { var v = f(r, p); return {value: !!v}; } catch (e) { return {error: 'expr threw: ' + errstr(e)}; }
  }
  function draw(pjson){
    var p = JSON.parse(pjson); var r;
    try { r = compute(p); } catch (e) { return {error: 'compute threw: ' + errstr(e)}; }
    var el = document.createElement('div');
    try { render(r, p, kit, el); } catch (e) { return {error: errstr(e)}; }
    return {nodes: __stubCount(el)};
  }
  function kitprobe(){
    if (typeof kit !== 'object' || kit === null) return {error: 'global kit not defined'};
    try {
      var el = document.createElement('div');
      if (typeof kit.plot === 'function') kit.plot(el, {title: 't', x: {label: 'x'}, y: {label: 'y'}, series: [{name: 's', points: [[0,0],[1,1]]}]});
      return {nodes: __stubCount(el)};
    } catch (e) { return {error: errstr(e)}; }
  }
  return {run: run, watch: watch, expr: expr, draw: draw, kitprobe: kitprobe};
})();
"""


def _res(cid, result, detail=""):
    return {"id": cid, "name": CHECK_NAMES.get(cid, cid), "result": result, "detail": str(detail)[:600]}


class JSRunner:
    """quickjs context with per-eval time limit and a global deadline for all checks."""

    def __init__(self, deadline: float):
        self.deadline = deadline
        self.timeouts = 0
        self.ctx = quickjs.Context()
        self.ctx.set_time_limit(EVAL_TIME_LIMIT_S)
        self.ctx.set_memory_limit(MEMORY_LIMIT)
        try:
            self.ctx.set_max_stack_size(4 * 1024 * 1024)
        except Exception:
            pass

    def out_of_time(self) -> bool:
        return time.monotonic() > self.deadline

    def exec(self, code: str):
        """Run code; return (ok, error_message)."""
        if self.out_of_time():
            return False, "checks time budget exhausted"
        try:
            self.ctx.eval(code)
            return True, ""
        except Exception as e:  # quickjs.JSException and friends
            msg = str(e).strip().split("\n")[0][:300]
            if "interrupted" in msg:
                self.timeouts += 1
                msg = f"timed out (> {EVAL_TIME_LIMIT_S}s): {msg}"
            return False, msg

    def call(self, fn: str, *args):
        """Call __p2p.<fn>(...) with string args; returns (dict|None, error)."""
        if self.out_of_time():
            return None, "checks time budget exhausted"
        code = "JSON.stringify(__p2p.%s(%s))" % (fn, ",".join(json.dumps(a) for a in args))
        try:
            raw = self.ctx.eval(code)
        except Exception as e:
            msg = str(e).strip().split("\n")[0][:300]
            if "interrupted" in msg:
                self.timeouts += 1
                msg = f"timed out (> {EVAL_TIME_LIMIT_S}s)"
            return None, msg
        try:
            return json.loads(raw), ""
        except (TypeError, ValueError):
            return None, "unexpected non-JSON result"


# --------------------------------------------------------------------------- structural
def check_s1(spec) -> dict:
    if not isinstance(spec, dict):
        return _res("S1", "fail", "no valid SPEC")
    probs, warns = [], []
    if not spec.get("title"):
        probs.append("title missing")
    if not spec.get("idea"):
        probs.append("idea missing")
    if not (spec.get("paper") or {}).get("title"):
        probs.append("paper.title (citation) missing")
    if len(spec.get("controls", [])) < 2:
        probs.append(f"need >=2 valid controls (have {len(spec.get('controls', []))})")
    exps = spec.get("explorations", [])
    if len(exps) != 2:
        probs.append(f"need exactly 2 explorations (have {len(exps)})")
    for i, e in enumerate(exps):
        miss = [k for k in ("predict", "watch", "observe", "why") if not e.get(k)]
        if not e.get("preset"):
            miss.append("preset")
        if miss:
            probs.append(f"explorations[{i}] missing {', '.join(miss)}")
    if not (spec.get("limitation") or {}).get("text"):
        probs.append("limitation.text missing")
    # Counts of tests/invariants are enforced by C1/C3 (not acceptance-critical): the brief does not
    # require them, and unverifiable ones may be pruned from the final page.
    if len(spec.get("invariants", [])) < 1:
        warns.append("no invariants")
    if len(spec.get("tests", [])) < 2:
        warns.append(f"{len(spec.get('tests', []))} tests")
    if not spec.get("intermediates"):
        probs.append("need >=1 intermediate")
    for k in ("why", "symbols", "equations", "claims"):
        if not spec.get(k):
            warns.append(f"{k} empty")
    if not (spec.get("visual") or {}).get("caption"):
        warns.append("visual.caption empty")
    detail = "; ".join(probs) if probs else "ok"
    if warns:
        detail += " (warnings: " + ", ".join(warns) + ")"
    return _res("S1", "fail" if probs else "pass", detail)


_S2_PATTERNS = [
    (re.compile(r"(?is)<script[^>]*\bsrc\s*="), "external <script src>"),
    (re.compile(r"(?is)<link\b[^>]*\bhref\s*=\s*[\"']?(?:https?:)?//"), "external <link href>"),
    (re.compile(r"(?is)\bsrc\s*=\s*[\"']?(?:https?:)?//"), "remote src= attribute"),
    (re.compile(r"(?i)@import\b"), "CSS @import"),
    (re.compile(r"(?i)url\(\s*[\"']?(?:https?:)?//"), "remote url()"),
    (re.compile(r"\bfetch\s*\("), "fetch() call"),
    (re.compile(r"\bXMLHttpRequest\b"), "XMLHttpRequest"),
    (re.compile(r"\bnew\s+WebSocket\b"), "WebSocket"),
    (re.compile(r"\bimport\s*\(\s*[\"'`]"), "dynamic import()"),
    (re.compile(r"sk-or-v1-[0-9a-f]{16,}"), "API key-like string"),
]


def check_s2(html, paper_url: str = "", secrets=()) -> dict:
    if not html:
        return _res("S2", "fail", "no assembled page (shell missing?)")
    probs = []
    for rx, what in _S2_PATTERNS:
        if rx.search(html):
            probs.append(what)
    for m in re.finditer(r"(?is)\bhref\s*=\s*[\"']?((?:https?:)?//[^\"'\s>]+)", html):
        if paper_url and m.group(1).rstrip("/") == paper_url.rstrip("/"):
            continue
        probs.append(f"remote href {m.group(1)[:80]}")
        break
    for s in secrets:
        if s and s in html:
            probs.append("credential found in page")
    return _res("S2", "fail" if probs else "pass", "; ".join(probs) or "ok: no external resources")


def check_s3(parsed) -> dict:
    probs = []
    if parsed is None:
        return _res("S3", "fail", "no model output")
    for sec in ("SPEC", "COMPUTE", "RENDER"):
        if sec not in parsed.found:
            probs.append(f"{sec} section missing")
    if parsed.spec_error and "SPEC section missing" not in parsed.spec_error:
        probs.append(f"SPEC: {parsed.spec_error}")
    detail = "; ".join(probs) or "ok"
    if parsed.notes:
        detail += " (" + "; ".join(parsed.notes) + ")"
    return _res("S3", "fail" if probs else "pass", detail)


def check_g1(spec, origin: str, norm_notes=()) -> dict:
    if not isinstance(spec, dict):
        return _res("G1", "skip", "no SPEC")
    bad = []
    for key in ("claims", "equations"):
        for i, x in enumerate(spec.get(key, [])):
            if x.get("support") == "excerpt" and origin == "none":
                bad.append(f"{key}[{i}]")
    fixes = [n for n in norm_notes if "downgraded" in n]
    if bad:
        return _res("G1", "fail", f"'excerpt' support without an excerpt: {', '.join(bad)}")
    n_claims = len(spec.get("claims", []))
    detail = f"origin={origin}; {n_claims} claims labelled"
    if fixes:
        detail += f"; auto-downgraded {len(fixes)} excerpt label(s)"
    return _res("G1", "pass", detail)


# --------------------------------------------------------------------------- execution
def _boundary_values(c: dict) -> list:
    t = c["type"]
    if t in ("range", "number"):
        vals = [c["min"], c["max"]]
        if c["min"] < 0 < c["max"]:
            vals.append(0)
        return vals
    if t == "toggle":
        return [not c["default"]]
    if t == "select":
        return [v for v in option_values(c) if v != c["default"]]
    lo, hi = c.get("min"), c.get("max")
    out = []
    for bound in (lo, hi):
        if isinstance(bound, (int, float)) and not isinstance(bound, bool):
            if t == "vector":
                out.append([bound] * len(c["default"]))
            else:
                out.append([[bound] * len(r) for r in c["default"]])
    return out


def _alt_values(c: dict) -> list:
    """Values to try for 'does this control matter' (X4)."""
    t = c["type"]
    d = c["default"]
    if t in ("range", "number"):
        mid = (c["min"] + c["max"]) / 2
        return [v for v in (c["max"], c["min"], mid) if v != d]
    if t in ("toggle", "select"):
        return _boundary_values(c)
    step = c.get("step") if isinstance(c.get("step"), (int, float)) and c.get("step") else None
    lo = c.get("min") if isinstance(c.get("min"), (int, float)) else None
    hi = c.get("max") if isinstance(c.get("max"), (int, float)) else None

    def bump(x):
        delta = step or (abs(x) * 0.5 if x else 1)
        delta = max(delta, ((hi - lo) / 4) if (lo is not None and hi is not None) else 0)
        y = x + delta
        if hi is not None and y > hi:
            y = x - delta
        if lo is not None and y < lo:
            y = lo if x != lo else hi if hi is not None else x + 1
        return y

    out = []
    if t == "vector":
        for idx in {0, len(d) - 1}:
            v = list(d)
            v[idx] = bump(v[idx])
            out.append(v)
    else:
        for (i, j) in {(0, 0), (len(d) - 1, len(d[-1]) - 1)}:
            m = [list(r) for r in d]
            m[i][j] = bump(m[i][j])
            out.append(m)
    return out


def _fmt(v) -> str:
    s = json.dumps(v)
    return s if len(s) < 40 else s[:37] + "..."


def run_checks(spec, compute_js, render_js, html, origin: str, parsed=None, norm_notes=(),
               shell_dir=None, kit_js=None, time_budget_s: float = CHECKS_TIME_BUDGET_S,
               secrets=()) -> list:
    """Run every check. `spec` must already be normalised (spec.normalize_spec)."""
    results = {}
    results["S1"] = check_s1(spec)
    paper_url = ((spec or {}).get("paper") or {}).get("url", "") if isinstance(spec, dict) else ""
    results["S2"] = check_s2(html, paper_url, secrets)
    results["S3"] = check_s3(parsed) if parsed is not None else _res("S3", "skip", "not parsed here")
    results["G1"] = check_g1(spec, origin, norm_notes)

    x_ids = ("X1", "X2", "X3", "X4", "X5", "X6", "C1", "C2", "C3", "C4")
    if quickjs is None:
        for cid in x_ids:
            results[cid] = _res(cid, "skip", "quickjs not installed: JS not executed")
        return [results[k] for k in ORDER]

    js = JSRunner(time.monotonic() + time_budget_s)
    shell = Path(shell_dir) if shell_dir else SHELL_DIR
    stub_path = SHELL_DIR / "dom_stub.js"
    stub_ok, stub_err = js.exec(stub_path.read_text(encoding="utf-8")) if stub_path.is_file() else (False, "dom_stub.js missing")
    js.exec(_HELPERS)
    # kit (optional)
    if kit_js is None:
        kp = shell / "kit.js"
        kit_js = kp.read_text(encoding="utf-8") if kp.is_file() else ""
    kit_state = "absent"
    if kit_js and stub_ok:
        ok, err = js.exec(kit_js)
        kit_state = "ok" if ok else f"kit.js failed to load: {err}"
        if ok:
            probe, perr = js.call("kitprobe")
            if probe is None or probe.get("error"):
                kit_state = f"kit.plot unusable in DOM stub: {(probe or {}).get('error') or perr}"
    elif not stub_ok:
        kit_state = f"DOM stub failed: {stub_err}"

    # X1 parse + define
    x1 = []
    have_compute = have_render = False
    if not compute_js:
        x1.append("COMPUTE missing")
    else:
        ok, err = js.exec(compute_js)
        if not ok:
            x1.append(f"COMPUTE: {err}")
        else:
            have_compute = js.ctx.eval("typeof compute") == "function"
            if not have_compute:
                x1.append("COMPUTE does not define function compute(p)")
    if not render_js:
        x1.append("RENDER missing")
    else:
        ok, err = js.exec(render_js)
        if not ok:
            x1.append(f"RENDER: {err}")
        else:
            have_render = js.ctx.eval("typeof render") == "function"
            if not have_render:
                x1.append("RENDER does not define function render(r, p, kit, el)")
    results["X1"] = _res("X1", "fail" if x1 else "pass", "; ".join(x1) or "compute and render compile")

    if not isinstance(spec, dict) or not spec.get("controls"):
        for cid in ("X2", "X3", "X4", "X5", "C1", "C2", "C3", "C4"):
            results[cid] = _res(cid, "skip", "no valid SPEC/controls")
        results["X6"] = _res("X6", "fail" if js.timeouts else "pass", f"{js.timeouts} timeouts")
        return [results[k] for k in ORDER]
    if not have_compute:
        for cid in ("X2", "X3", "X4", "X5", "C1", "C2", "C3", "C4"):
            results[cid] = _res(cid, "skip", "compute() unavailable (see X1)")
        results["X6"] = _res("X6", "fail" if js.timeouts else "pass", f"{js.timeouts} timeouts")
        return [results[k] for k in ORDER]

    keys = [x["key"] for x in spec.get("intermediates", [])]
    keys_json = json.dumps(keys)
    defaults = default_params(spec)

    def run(p):
        return js.call("run", json.dumps(p), keys_json)

    # X2 default run (+ determinism)
    base, err = run(defaults)
    base_sig = None
    if base is None or base.get("error"):
        results["X2"] = _res("X2", "fail", f"compute(defaults) failed: {(base or {}).get('error') or err}")
    else:
        probs = []
        if base.get("nan"):
            probs.append(f"NaN in result: {base['nan']}")
        if base.get("missing"):
            probs.append(f"intermediates missing from r: {', '.join(base['missing'])} (r has: {', '.join(base.get('keys', []))})")
        if base.get("nonfinite"):
            probs.append(f"non-finite intermediate: {', '.join(base['nonfinite'][:3])}")
        again, _ = run(defaults)
        if again is not None and again.get("sig") != base.get("sig"):
            probs.append("compute is not deterministic (two runs with the same p differ)")
        base_sig = base.get("sig")
        results["X2"] = _res("X2", "fail" if probs else "pass", "; ".join(probs) or
                             f"finite values for {len(keys)} intermediates; deterministic")

    # X3 boundaries
    probs, n = [], 0
    for c in spec["controls"]:
        for v in _boundary_values(c):
            p = dict(defaults)
            p[c["id"]] = v
            out, err = run(p)
            n += 1
            if out is None or out.get("error"):
                probs.append(f"{c['id']}={_fmt(v)}: {(out or {}).get('error') or err}")
            elif out.get("nan") or out.get("nonfinite"):
                probs.append(f"{c['id']}={_fmt(v)}: {out.get('nan') or out['nonfinite'][0]}")
    results["X3"] = _res("X3", "fail" if probs else "pass",
                         "; ".join(probs[:4]) or f"{n} boundary runs without NaN/Infinity/exceptions")

    # X4 controls matter
    if base_sig is None:
        results["X4"] = _res("X4", "skip", "default run failed")
    else:
        # A control may legitimately matter only under another select/toggle setting
        # (e.g. custom probabilities when shape = "custom"), so also try those contexts.
        contexts = [dict(defaults)]
        # select/toggle settings first, then other controls' alternative values (e.g. a "uniform"
        # toggle only matters once the probabilities are no longer uniform)
        ordered = sorted(spec["controls"], key=lambda o: o["type"] not in ("select", "toggle"))
        for o in ordered:
            for v in _alt_values(o)[:3]:
                ctx = dict(defaults)
                ctx[o["id"]] = v
                contexts.append(ctx)
        contexts = contexts[:40]
        dead, conditional = [], []
        for c in spec["controls"]:
            changed_at = None
            for ci, ctx in enumerate(contexts):
                if ctx is not contexts[0] and ctx.get(c["id"]) != defaults.get(c["id"]):
                    continue  # this context varies the control itself
                ref, _ = run(ctx) if ci else ({"sig": base_sig}, None)
                if ref is None or ref.get("error"):
                    continue
                for v in _alt_values(c):
                    p = dict(ctx)
                    p[c["id"]] = v
                    out, _ = run(p)
                    if out is not None and not out.get("error") and out.get("sig") != ref.get("sig"):
                        changed_at = ci
                        break
                if changed_at is not None:
                    break
            if changed_at is None and c["type"] in ("toggle", "select") and render_js and re.search(
                    r"\bp\s*(?:\.\s*" + re.escape(c["id"]) + r"\b|\[\s*['\"]" + re.escape(c["id"]) + r"['\"]\s*\])",
                    render_js):
                conditional.append(c["id"] + " (visual only)")  # display toggle: render reads it
            elif changed_at is None:
                dead.append(c["id"])
            elif changed_at:
                conditional.append(c["id"])
        detail = (f"control(s) change nothing in compute(p): {', '.join(dead)}" if dead
                  else f"all {len(spec['controls'])} controls change the output")
        if conditional and not dead:
            detail += f" ({', '.join(conditional)} only under another select/toggle setting)"
        results["X4"] = _res("X4", "fail" if dead else "pass", detail)

    # X5 render smoke
    if kit_state != "ok":
        results["X5"] = _res("X5", "skip", kit_state if kit_state != "absent" else "kit.js not present: render not smoke-tested")
    elif not have_render:
        results["X5"] = _res("X5", "fail", "render() unavailable (see X1)")
    else:
        probs = []
        out, err = js.call("draw", json.dumps(defaults))
        if out is None or out.get("error"):
            probs.append(f"render at defaults threw: {(out or {}).get('error') or err}")
        elif not out.get("nodes"):
            probs.append("render at defaults drew nothing into el")
        for i, e in enumerate(spec.get("explorations", [])):
            out, err = js.call("draw", json.dumps(merged_params(spec, e.get("preset"))))
            if out is None or out.get("error"):
                probs.append(f"render at exploration {i + 1} preset threw: {(out or {}).get('error') or err}")
        results["X5"] = _res("X5", "fail" if probs else "pass", "; ".join(probs) or "render ok at defaults and presets")

    # C1 model tests
    probs = []
    ids = {c["id"] for c in spec["controls"]}
    for t in spec.get("tests", []):
        unknown = [k for k in t.get("params", {}) if k not in ids]
        out, err = js.call("expr", json.dumps(merged_params(spec, t.get("params"))), t["expr"])
        name = t.get("name", "")[:60]
        if out is None or out.get("error"):
            probs.append(f"'{name}': {(out or {}).get('error') or err}")
        elif not out.get("value"):
            probs.append(f"'{name}' is false: {t['expr'][:120]}" + _actual_values(js, merged_params(spec, t.get("params")), t["expr"])
                         + (f" (unknown params {unknown})" if unknown else ""))
        elif unknown:
            probs.append(f"'{name}': params {unknown} are not control ids")
    nt = len(spec.get("tests", []))
    if 0 < nt < 2 and not probs:
        probs.append(f"only {nt} test (need >=2)")
    results["C1"] = _res("C1", "fail" if probs or nt == 0 else "pass",
                         "; ".join(probs[:8]) or (f"{nt} tests pass" if nt else "no tests"))

    # C2 exploration presets change the watched value
    probs = []
    for i, e in enumerate(spec.get("explorations", [])):
        preset = e.get("preset") or {}
        unknown = [k for k in preset if k not in ids]
        if unknown:
            probs.append(f"exploration {i + 1}: preset keys {unknown} are not control ids")
            continue
        watch = e.get("watch", "")
        if not watch:
            probs.append(f"exploration {i + 1}: no watch key")
            continue
        a, err_a = js.call("watch", json.dumps(defaults), watch)
        b, err_b = js.call("watch", json.dumps(merged_params(spec, preset)), watch)
        if a is None or b is None:
            probs.append(f"exploration {i + 1}: {err_a or err_b}")
        elif not b.get("present"):
            probs.append(f"exploration {i + 1}: watch '{watch}' is not a key of r")
        elif a.get("sig") == b.get("sig"):
            probs.append(f"exploration {i + 1}: preset {_fmt(preset)} does not change r.{watch}")
    results["C2"] = _res("C2", "fail" if probs else "pass", "; ".join(probs) or "both presets change their watched value")

    # C3 invariants at defaults and at each preset
    probs = []
    settings = [("defaults", defaults)] + [(f"preset {i + 1}", merged_params(spec, e.get("preset")))
                                          for i, e in enumerate(spec.get("explorations", []))]
    invs = spec.get("invariants", [])
    for inv in invs:
        for where, p in settings:
            out, err = js.call("expr", json.dumps(p), inv["expr"])
            if out is None or out.get("error"):
                probs.append(f"'{inv.get('label', '')[:50]}' at {where}: {(out or {}).get('error') or err}")
                break
            if not out.get("value"):
                probs.append(f"'{inv.get('label', '')[:50]}' false at {where}: {inv['expr'][:100]}"
                             + _actual_values(js, p, inv["expr"]))
                break
    results["C3"] = _res("C3", "fail" if probs or not invs else "pass",
                         "; ".join(probs[:8]) or (f"{len(invs)} invariants hold at defaults + presets" if invs else "no invariants"))

    # C4 every {key} in live equations and every intermediates key resolves in r (or p) at defaults,
    # otherwise the page shows "?" / "—" placeholders instead of numbers
    probs = []
    keys = [(f"intermediate '{it.get('key')}'", str(it.get("key", ""))) for it in spec.get("intermediates", [])]
    keys += [(f"map node '{n.get('id')}' key", str(n.get("key", ""))) for n in (spec.get("map") or {}).get("nodes", [])]
    for eq in spec.get("equations", []):
        for k in re.findall(r"\{\s*([A-Za-z_$][\w$]*(?:\.[\w$]+|\[\d+\])*)\s*(?::\s*\d+)?\s*\}", str(eq.get("live") or "")):
            keys.append((f"live equation '{str(eq.get('label', ''))[:30]}' key {{{k}}}", k))
    getter = ("(function(o,s){var a=s.replace(/\\[(\\d+)\\]/g,'.$1').split('.').filter(Boolean);"
              "for(var i=0;i<a.length;i++){if(o===null||o===undefined)return undefined;o=o[a[i]];}return o;})")
    for where, k in keys:
        if not k:
            continue
        out, err = js.call("expr", json.dumps(defaults),
                           f"{getter}(r,{json.dumps(k)})!==undefined||{getter}(p,{json.dumps(k)})!==undefined")
        if out is None or out.get("error") or not out.get("value"):
            probs.append(f"{where} is not returned by compute()")
    results["C4"] = _res("C4", "fail" if probs else "pass",
                         "; ".join(probs[:4]) or f"{len(keys)} live keys resolve")

    results["X6"] = _res("X6", "fail" if js.timeouts else "pass",
                         f"{js.timeouts} evaluation(s) hit the {EVAL_TIME_LIMIT_S}s limit" if js.timeouts
                         else f"all JS evaluations under {EVAL_TIME_LIMIT_S}s")
    return [results[k] for k in ORDER]


def unverified_expectations(spec, compute_js) -> tuple:
    """Model-written tests/invariants that are false for the final compute(): returns
    (failing test names, failing invariant labels). Used to drop unverifiable claims from the
    page after repairs are exhausted; the trace keeps the record."""
    if quickjs is None or not isinstance(spec, dict) or not compute_js:
        return [], []

    def holds(p, expr):
        ctx = quickjs.Context()
        ctx.set_time_limit(EVAL_TIME_LIMIT_S)
        ctx.set_memory_limit(MEMORY_LIMIT)
        try:
            ctx.eval(compute_js)
            return bool(ctx.eval(f"(function(){{var p={json.dumps(p)};var r=compute(p);return !!({expr});}})()"))
        except Exception:  # noqa: BLE001 - any JS error means "does not hold"
            return False

    bad_t = [t.get("name", "") for t in spec.get("tests", [])
             if not holds(merged_params(spec, t.get("params")), t.get("expr", "false"))]
    settings = [default_params(spec)] + [merged_params(spec, e.get("preset")) for e in spec.get("explorations", [])]
    bad_i = [i.get("label", "") for i in spec.get("invariants", [])
             if not all(holds(p, i.get("expr", "false")) for p in settings)]
    return bad_t, bad_i


def _actual_values(js, params, expr: str) -> str:
    """' [actual: r.K=0.38, r.P=1.2]' for the r.<key> values an expression references, so a repair can
    tell whether the expectation or the computation is wrong."""
    keys = list(dict.fromkeys(re.findall(r"\br\.([A-Za-z_$][\w$]*)", expr or "")))[:4]
    vals = []
    for k in keys:
        out, _ = js.call("watch", json.dumps(params), k)
        if out and out.get("present"):
            vals.append(f"r.{k}={str(out.get('sig'))[:60]}")
    return f" [actual: {', '.join(vals)}]" if vals else ""


def exploration_facts(spec, compute_js) -> list:
    """Actual watched values at defaults vs each exploration preset (for the fidelity audit)."""
    if quickjs is None or not isinstance(spec, dict) or not compute_js:
        return []
    out = []
    for e in spec.get("explorations", []):
        w = e.get("watch", "")
        vals = []
        for p in (default_params(spec), merged_params(spec, e.get("preset"))):
            ctx = quickjs.Context()
            ctx.set_time_limit(EVAL_TIME_LIMIT_S)
            ctx.set_memory_limit(MEMORY_LIMIT)
            try:
                ctx.eval(compute_js)
                vals.append(ctx.eval(f"JSON.stringify(compute({json.dumps(p)})[{json.dumps(w)}])"))
            except Exception as ex:  # noqa: BLE001
                vals.append(f"error: {str(ex)[:60]}")
        out.append({"title": e.get("title", ""), "preset": e.get("preset"), "watch": w,
                    "at_defaults": str(vals[0])[:200], "at_preset": str(vals[1])[:200]})
    return out


def accepted(results) -> bool:
    return all(r["result"] != "fail" for r in results if r["id"] in ACCEPT_IDS)


def score(results) -> tuple:
    return (1 if accepted(results) else 0, sum(1 for r in results if r["result"] == "pass"))


def failing(results) -> list:
    return [r for r in results if r["result"] == "fail"]
