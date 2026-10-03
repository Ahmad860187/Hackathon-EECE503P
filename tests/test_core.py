"""Core tests without any API call. Run: `python tests/test_core.py` (or `pytest tests`)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper2play import checks as chk  # noqa: E402
from paper2play.assemble import assemble, sanitize_code  # noqa: E402
from paper2play.budget import Budget  # noqa: E402
from paper2play.llm import BudgetStop, OpenRouterClient  # noqa: E402
from paper2play.pipeline import Run  # noqa: E402
from paper2play.protocol import parse_json_lenient, parse_output  # noqa: E402
from paper2play.spec import normalize_spec  # noqa: E402
from paper2play.trace import Trace  # noqa: E402

# ----------------------------------------------------------------------------- fixtures (generic)
CASE = {"source_url": "https://example.org/paper", "focus": "logistic population growth", "audience": "first-year students"}

SPEC = {
    "title": "Logistic growth",
    "paper": {"title": "A toy population model", "authors": "", "year": "", "url": "", "section": ""},
    "idea": "Growth slows as the population approaches the carrying capacity K.",
    "why": "It explains saturation.",
    "symbols": [{"sym": "r", "meaning": "growth rate"}, {"sym": "K", "meaning": "carrying capacity"}],
    "equations": [{"label": "update", "expr": "N' = N + rN(1 - N/K)", "support": "excerpt"}],
    "controls": [
        {"id": "r", "label": "rate r", "type": "range", "min": 0.05, "max": 1, "step": 0.05, "default": 0.3},
        {"id": "K", "label": "capacity K", "type": "range", "min": 10, "max": 200, "step": 1, "default": 100},
        {"id": "N0", "label": "start N0", "type": "range", "min": 1, "max": 50, "step": 1, "default": 5},
    ],
    "intermediates": [{"key": "N_final", "label": "N after 50 steps"}, {"key": "growth0", "label": "first-step growth"}],
    "visual": {"caption": "Population over time", "how_to_read": "x is time, y is N"},
    "explorations": [
        {"title": "Faster", "predict": "What if r doubles?", "preset": {"r": 0.9}, "watch": "growth0",
         "observe": "first step grows more", "why": "growth is proportional to r"},
        {"title": "Small K", "predict": "Lower K?", "preset": {"K": 20}, "watch": "N_final",
         "observe": "plateau drops", "why": "N saturates at K"},
    ],
    "limitation": {"kind": "assumption", "text": "Discrete steps, no noise."},
    "claims": [{"text": "Population saturates at K", "support": "excerpt"}],
    "invariants": [{"label": "0 < N <= K", "expr": "r.N_final > 0 && r.N_final <= p.K + 1e-9"}],
    "tests": [
        {"name": "equilibrium at K", "params": {"N0": 50, "K": 50}, "expr": "Math.abs(r.N_final - 50) < 1e-9"},
        {"name": "first step", "params": {"N0": 50, "K": 100, "r": 0.5}, "expr": "Math.abs(r.growth0 - 12.5) < 1e-9"},
    ],
}

COMPUTE = """function compute(p) {
  var N = p.N0, pts = [[0, N]];
  for (var t = 1; t <= 50; t++) { N = N + p.r * N * (1 - N / p.K); pts.push([t, N]); }
  return { N_final: N, growth0: p.r * p.N0 * (1 - p.N0 / p.K), series: pts };
}"""

COMPUTE_DEAD_K = COMPUTE.replace("(1 - N / p.K)", "(1 - N / 100)").replace("(1 - p.N0 / p.K)", "(1 - p.N0 / 100)")

RENDER = """function render(r, p, kit, el) {
  kit.plot(el, {title: 'Population over time', x: {label: 't'}, y: {label: 'N', min: 0},
    series: [{name: 'N(t)', points: r.series, highlight: true}], hlines: [{y: p.K, label: 'K'}]});
}"""

FAKE_KIT = """var kit = { plot: function (el, o) {
  var s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  (o.series || []).forEach(function (x) { if (!Array.isArray(x.points)) throw new Error('points must be an array'); });
  el.appendChild(s); return s; } };"""

SHELL_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>/*@STYLE@*/</style></head><body><div id="app"></div>
<script>/*@DATA@*/</script><script>/*@KIT@*/</script><script>/*@COMPUTE@*/</script><script>/*@RENDER@*/</script><script>/*@APP@*/</script></body></html>"""


def make_shell(d: Path, kit=FAKE_KIT) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "shell.html").write_text(SHELL_HTML, encoding="utf-8")
    (d / "style.css").write_text("body{margin:0}", encoding="utf-8")
    (d / "kit.js").write_text(kit, encoding="utf-8")
    (d / "app.js").write_text("/* app */", encoding="utf-8")
    return d


def model_output(spec=SPEC, compute=COMPUTE, render=RENDER, prose=True, fences=False):
    js = json.dumps(spec, indent=1)
    if fences:
        js = "```json\n" + js + "\n```"
        compute = "```js\n" + compute + "\n```"
    pre = "Sure! Here is the playground.\n" if prose else ""
    return f"{pre}===SPEC===\n{js}\n===COMPUTE===\n{compute}\n===RENDER===\n{render}\n===END===\n"


def norm(spec=SPEC, origin="inline"):
    return normalize_spec(spec, CASE, origin)


# ----------------------------------------------------------------------------- protocol
def test_protocol_basic_and_fences():
    p = parse_output(model_output(fences=True))
    assert p.found == ["SPEC", "COMPUTE", "RENDER"], p.found
    assert p.spec["title"] == "Logistic growth"
    assert p.compute.startswith("function compute") and "```" not in p.compute
    assert p.has_end


def test_protocol_missing_end_and_trailing_commas():
    text = '===SPEC===\n{"title": "x", "controls": [1, 2,], // comment\n}\n===COMPUTE===\nfunction compute(p){return {a:1}}\n'
    p = parse_output(text)
    assert p.spec == {"title": "x", "controls": [1, 2]}, p.spec
    assert p.compute and not p.render and not p.has_end
    assert any("END" in n for n in p.notes)


def test_protocol_no_markers_fallback():
    text = 'Here:\n```json\n{"title": "y"}\n```\n```js\nfunction compute(p) { var s = "}"; return {v: 1}; }\n' \
           'function render(r, p, kit, el) { kit.plot(el, {}); }\n```'
    p = parse_output(text)
    assert p.spec == {"title": "y"}
    assert p.compute.endswith("return {v: 1}; }"), p.compute
    assert p.render.startswith("function render")


def test_json_truncated_repair():
    obj, err, repaired = parse_json_lenient('{"a": [1, 2, {"b": "c"')
    assert obj == {"a": [1, 2, {"b": "c"}]} and repaired, (obj, err)


# ----------------------------------------------------------------------------- assembly
def test_assembly_escaping_and_sanitising():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        shell = make_shell(Path(td) / "shell")
        spec = dict(SPEC, idea="evil </script><script>alert(1)</script> <img src=http://x.org/a.png>")
        render = RENDER + "\nvar u = 'https://cdn.example.com/lib.js'; var s = '</script>'; var ns = 'http://www.w3.org/2000/svg';"
        html = assemble(spec, COMPUTE, render, {"source_origin": "none", "checks": []}, shell)
        assert "/*@" not in html
        # only the shell's own 5 closing script tags remain
        assert html.lower().count("</script>") == 5, html.lower().count("</script>")
        assert "cdn.example.com" not in html
        assert "http://www.w3.org/2000/svg" in html
        assert "<\\/script" in html
        assert "const SPEC = " in html and "const BUILD = " in html
    code, notes = sanitize_code("fetch('https://evil.example.com/x')")
    assert "evil" not in code and notes


# ----------------------------------------------------------------------------- budget
def test_budget_enforcement():
    now = [0.0]
    b = Budget(clock=lambda: now[0])
    assert b.max_tokens(14000) == 14000
    b.record_usage(20000, 1000, 14000)
    assert b.max_tokens(14000) == 10000
    b.record_usage(None, None, 9000)       # unknown usage is charged at requested max
    assert b.remaining_completion() == 1000 and b.usage_unknown
    ok, why = b.check_can_call(min_tokens=2000)
    assert not ok and "completion" in why
    b2 = Budget(clock=lambda: now[0])
    for _ in range(10):
        b2.start_request()
    assert not b2.check_can_call()[0]
    try:
        b2.start_request()
        raise AssertionError("expected BudgetExceeded")
    except Exception as e:
        assert "limit" in str(e)
    b3 = Budget(clock=lambda: now[0])
    now[0] = 560.0
    assert not b3.check_can_call()[0]           # 40 s left - 30 s reserve < 15 s
    assert abs(b3.http_timeout() - 10.0) < 1e-9


# ----------------------------------------------------------------------------- checks
def _run_checks(spec=SPEC, compute=COMPUTE, render=RENDER, origin="inline", kit=FAKE_KIT, html="<html></html>"):
    s, notes = norm(spec, origin)
    return {c["id"]: c for c in chk.run_checks(s, compute, render, html, origin, norm_notes=notes, kit_js=kit)}


def test_checks_all_pass_on_generic_spec():
    res = _run_checks()
    fails = {k: v["detail"] for k, v in res.items() if v["result"] != "pass" and k != "S3"}
    assert not fails, fails
    assert chk.accepted(list(res.values()))


def test_checks_detect_problems():
    res = _run_checks(compute=COMPUTE_DEAD_K)
    assert res["X4"]["result"] == "fail" and "K" in res["X4"]["detail"], res["X4"]
    # division by r with r allowed to be 0 -> Infinity/NaN at the boundary
    spec = json.loads(json.dumps(SPEC))
    spec["controls"][0]["min"] = 0
    res = _run_checks(spec=spec, compute=COMPUTE.replace("return {", "return { doubling: Math.log(2) / p.r, "),
                      )
    spec["intermediates"].append({"key": "doubling", "label": "doubling"})
    res = _run_checks(spec=spec, compute=COMPUTE.replace("return {", "return { doubling: Math.log(2) / p.r, "))
    assert res["X3"]["result"] == "fail" and "r=0" in res["X3"]["detail"], res["X3"]
    # wrong test expectation, preset that does not change its watch key, syntax error, render error
    bad = json.loads(json.dumps(SPEC))
    bad["tests"][1]["expr"] = "Math.abs(r.growth0 - 99) < 1e-9"
    bad["explorations"][1]["preset"] = {"N0": 5}
    res = _run_checks(spec=bad)
    assert res["C1"]["result"] == "fail" and res["C2"]["result"] == "fail"
    res = _run_checks(compute="function compute(p) { return {", render="function render(r,p,kit,el){ kit.nope(el); }")
    assert res["X1"]["result"] == "fail" and res["X2"]["result"] == "skip"
    res = _run_checks(render="function render(r,p,kit,el){ kit.plot(el, {series:[{points: 3}]}); }")
    assert res["X5"]["result"] == "fail", res["X5"]
    res = _run_checks(compute="function compute(p){ while(true){} }")
    assert res["X2"]["result"] == "fail" and res["X6"]["result"] == "fail"


def test_grounding_and_kit_absent():
    res = _run_checks(origin="none", kit="")
    assert res["G1"]["result"] == "pass" and "downgraded" in res["G1"]["detail"]
    assert res["X5"]["result"] == "skip"
    s, notes = norm(origin="none")
    assert all(c["support"] != "excerpt" for c in s["claims"] + s["equations"])
    assert s["paper"]["url"] == CASE["source_url"]


def test_real_kit_smoke_if_present():
    kit_path = ROOT / "paper2play" / "shell" / "kit.js"
    if not kit_path.is_file():
        return
    res = _run_checks(kit=kit_path.read_text(encoding="utf-8"))
    assert res["X5"]["result"] in ("pass", "skip"), res["X5"]


# ----------------------------------------------------------------------------- llm client + pipeline (fake HTTP)
class FakeResp:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._raw = json.dumps(body).encode()

    def iter_content(self, chunk_size=1):
        yield self._raw

    def close(self):
        pass


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.bodies = []
        self.headers = []

    def post(self, url, headers=None, data=None, timeout=None, stream=False):
        assert url == "https://openrouter.ai/api/v1/chat/completions"
        self.bodies.append(json.loads(data))
        self.headers.append(headers)
        r = self.responses.pop(0)
        return r(json.loads(data)) if callable(r) else r


def ok_body(text, ct=1000, pt=2000, rt=100):
    return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": pt, "completion_tokens": ct,
                      "completion_tokens_details": {"reasoning_tokens": rt}}}


def test_llm_retries_and_reasoning_fallback():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        b = Budget()
        tr = Trace(Path(td) / "t.jsonl", secrets=["sk-secret"])
        sess = FakeSession([
            FakeResp(429, {"error": {"message": "rate limited"}}, {"Retry-After": "0"}),
            FakeResp(400, {"error": {"message": "reasoning is not supported for this model"}}),
            FakeResp(200, ok_body("hello")),
        ])
        c = OpenRouterClient("sk-secret", "m", b, tr, session=sess, sleep=lambda s: None)
        res = c.chat([{"role": "user", "content": "hi"}], 5000, stage="generate")
        assert res.text == "hello" and res.attempts == 3 and b.requests == 3
        assert "reasoning" in sess.bodies[0] and "reasoning" not in sess.bodies[2]
        assert sess.bodies[0]["usage"] == {"include": True}
        assert sess.headers[0]["Authorization"] == "Bearer sk-secret"
        tr.close()
        log = (Path(td) / "t.jsonl").read_text(encoding="utf-8")
        assert "sk-secret" not in log and "Authorization" not in log
        assert '"reasoning_tokens": 100' in log
        b.requests = 10
        try:
            c.chat([{"role": "user", "content": "hi"}], 5000, stage="generate")
            raise AssertionError("expected BudgetStop")
        except BudgetStop:
            pass


def test_pipeline_end_to_end_with_targeted_repair():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        td = Path(td)
        shell = make_shell(td / "shell")
        out = td / "out"
        fixed = "===COMPUTE===\n" + COMPUTE + "\n===END==="
        sess = FakeSession([FakeResp(200, ok_body(model_output(compute=COMPUTE_DEAD_K))),
                            FakeResp(200, ok_body(fixed, ct=300, pt=900))])
        b = Budget()
        tr = Trace(out / "trace.jsonl", secrets=["sk-test"])
        run = Run(CASE, td, out, "test/model", "sk-test", tr, b, session=sess, shell_dir=shell,
                  allow_network=False, sleep=lambda s: None)
        code = run.execute()
        tr.close()
        assert code == 0, [e for e in tr.events if e.get("result") == "fail"]
        assert b.requests == 2 and run.revisions == 1
        repair_user = sess.bodies[1]["messages"][1]["content"]
        assert "X4" in repair_user and "FIX: COMPUTE" in repair_user
        assert "function render" not in repair_user          # only the failing section is resent
        page = (out / "index.html").read_text(encoding="utf-8")
        assert "(1 - N / p.K)" in page and "sk-test" not in page
        lines = [json.loads(x) for x in (out / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
        tot = [e for e in lines if e["action"] == "totals"][0]["totals"]
        assert tot["prompt_tokens"] == 2900 and tot["completion_tokens"] == 1300 and tot["revisions"] == 1
        assert any(e["action"] == "select_best" and e["result"] == "ok" for e in lines)


def test_pipeline_truncated_output_repairs_missing_render():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        td = Path(td)
        shell = make_shell(td / "shell")
        out = td / "out"
        truncated = model_output().split("===RENDER===")[0]
        body = ok_body(truncated)
        body["choices"][0]["finish_reason"] = "length"
        sess = FakeSession([FakeResp(200, body), FakeResp(200, ok_body("===RENDER===\n" + RENDER + "\n===END==="))])
        tr = Trace(out / "trace.jsonl")
        run = Run(CASE, td, out, "m", "k", tr, Budget(), session=sess, shell_dir=shell, allow_network=False)
        assert run.execute() == 0
        assert "FIX: RENDER" in sess.bodies[1]["messages"][1]["content"]


def test_cli_invalid_input_and_missing_key():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        td = Path(td)
        env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
        (td / "bad.json").write_text(json.dumps({"source_url": "x", "focus": ""}), encoding="utf-8")
        p = subprocess.run([sys.executable, str(ROOT / "agent.py"), "--input", str(td / "bad.json"),
                            "--output", str(td / "o1"), "--model", "m"], capture_output=True, text=True, env=env)
        assert p.returncode == 2 and "focus" in p.stderr and "audience" in p.stderr, p.stderr
        assert (td / "o1" / "trace.jsonl").is_file() and (td / "o1" / "index.html").is_file()
        (td / "case.json").write_text(json.dumps({"source_url": "notes.txt", "focus": "logistic growth",
                                                  "audience": "students"}), encoding="utf-8")
        (td / "notes.txt").write_text("Logistic growth: dN/dt = rN(1-N/K).", encoding="utf-8")
        p = subprocess.run([sys.executable, str(ROOT / "agent.py"), "--input", str(td / "case.json"),
                            "--output", str(td / "o2"), "--model", "m"], capture_output=True, text=True, env=env)
        assert p.returncode == 1
        ev = [json.loads(x) for x in (td / "o2" / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
        assert any(e["action"] == "resolve_source" and e["origin"] == "file" for e in ev)
        assert "Incomplete page" in (td / "o2" / "index.html").read_text(encoding="utf-8")


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                import traceback
                traceback.print_exc()
                print(f"FAIL {name}: {e}")
    print("all passed" if not failed else f"{failed} failed")
    sys.exit(1 if failed else 0)
