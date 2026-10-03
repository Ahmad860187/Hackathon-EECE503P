"""Local studio: paste a paper link + learning brief in a web form and watch the agent build the page.

Development / demo tool only (not part of the graded CLI). Runs on 127.0.0.1, reads OPENROUTER_API_KEY
from the environment or the gitignored .env, and calls `python agent.py` exactly as the CLI does.

    python scripts/studio.py            # then open http://127.0.0.1:8790
"""

from __future__ import annotations

import html
import json
import os
import subprocess
import threading
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "studio_runs"
PORT = int(os.environ.get("STUDIO_PORT", "8790"))
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"

sys.path.insert(0, str(ROOT / "scripts"))
from _common import load_dotenv_into  # noqa: E402  (loads .env values without printing them)

FORM = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Paper to Playground · Studio</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#0d1117;--mute:#5b6472;--line:#e3e6eb;--acc:#cc2a61}
@media (prefers-color-scheme:dark){:root{--bg:#0d0f13;--card:#161a20;--ink:#eef1f5;--mute:#9aa3b2;--line:#262c35;--acc:#e9508a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,"SF Pro Text","Segoe UI",system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:48px 20px}
.k{font:12px/1 ui-monospace,Menlo,Consolas,monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--mute)}
h1{font-size:40px;line-height:1.08;letter-spacing:-.02em;margin:10px 0 8px}
p.lead{color:var(--mute);margin:0 0 28px}
form,.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px}
label{display:block;font-weight:600;font-size:14px;margin:14px 0 6px}
input,textarea{width:100%;font:inherit;color:inherit;background:transparent;border:1px solid var(--line);border-radius:10px;padding:10px 12px}
textarea{min-height:120px;resize:vertical}
.hint{font-size:13px;color:var(--mute);margin-top:4px}
button{margin-top:18px;font:600 15px/1 inherit;padding:13px 18px;border:0;border-radius:10px;background:var(--ink);color:var(--bg);cursor:pointer}
button:active{transform:scale(.98)}
.busy{display:none;margin-top:14px;color:var(--mute)}form.wait .busy{display:block}form.wait button{opacity:.5;pointer-events:none}
table{width:100%;border-collapse:collapse;font-size:14px;margin-top:8px}td{padding:6px 4px;border-top:1px solid var(--line)}
a{color:var(--acc)}.runs{margin-top:28px}
</style></head><body><main>
<div class="k">Paper to Playground · studio</div>
<h1>Turn a paper into an interactive explanation</h1>
<p class="lead">Paste a paper link (arXiv abs/pdf/html, bare <code>arXiv:</code> id, DOI or PDF URL), say what to explain and for whom.
The agent runs exactly like <code>python agent.py --input case.json --output out --model MODEL</code>.</p>
<form method="post" action="/generate" onsubmit="this.classList.add('wait')">
<label for="u">Paper link (source_url)</label>
<input id="u" name="source_url" required placeholder="https://arxiv.org/abs/1706.03762">
<label for="c">Concept to explain (and section, if known)</label>
<input id="c" name="concept" required placeholder="Section 3.2.1: scaled dot-product attention">
<label for="ch">What the learner should change</label>
<input id="ch" name="change" placeholder="small Q, K and V matrices; scaling on/off">
<label for="sh">What to show</label>
<input id="sh" name="show" placeholder="similarity scores, normalized attention weights and the output">
<label for="ck">What must hold (checks)</label>
<input id="ck" name="check" placeholder="each row of weights sums to one; output equals the weighted sum of V">
<div class="hint">These four answers are combined into the learning brief (<code>focus</code>) in the same structure as the brief's examples. Only the concept is required.</div>
<label for="a">Audience</label>
<input id="a" name="audience" required value="engineering undergraduate">
<button type="submit">Generate playground</button>
<div class="busy">Generating… usually 20–60 seconds (one model call, free checks, targeted repairs).</div>
</form>
__RUNS__
</main></body></html>"""


PROGRESS = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Generating… · Paper to Playground</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#0d1117;--mute:#5b6472;--line:#e3e6eb;--acc:#cc2a61;--ok:#1a7f4b}
@media (prefers-color-scheme:dark){:root{--bg:#0d0f13;--card:#161a20;--ink:#eef1f5;--mute:#9aa3b2;--line:#262c35;--acc:#e9508a;--ok:#3fbf7f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,"SF Pro Text","Segoe UI",system-ui,sans-serif}
main{max-width:720px;margin:0 auto;padding:56px 20px}
.k{font:12px/1 ui-monospace,Menlo,Consolas,monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--mute)}
h1{font-size:34px;line-height:1.1;letter-spacing:-.02em;margin:10px 0 24px}
.bar{height:4px;background:var(--line);border-radius:4px;overflow:hidden;margin-bottom:28px}
.bar i{display:block;height:100%;width:3%;background:var(--acc);transition:width .4s ease}
ol{list-style:none;margin:0;padding:0;border:1px solid var(--line);border-radius:14px;background:var(--card)}
li{display:flex;gap:14px;align-items:flex-start;padding:16px 20px;border-top:1px solid var(--line);color:var(--mute)}
li:first-child{border-top:0}
li b{display:block;color:inherit;font-weight:600}
li small{display:block;font-size:13px;margin-top:2px}
.dot{flex:none;width:22px;height:22px;border-radius:50%;border:2px solid var(--line);margin-top:2px;display:grid;place-items:center;font-size:12px}
li.active{color:var(--ink)}li.active .dot{border-color:var(--acc);border-top-color:transparent;animation:spin .8s linear infinite}
li.done{color:var(--ink)}li.done .dot{border-color:var(--ok);background:var(--ok);color:#fff}
li.done .dot::after{content:"✓"}
@keyframes spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){li.active .dot{animation:none}}
.meta{display:flex;gap:24px;margin-top:20px;font:14px ui-monospace,Menlo,Consolas,monospace;color:var(--mute)}
.meta span b{color:var(--ink)}
a.btn{display:inline-block;margin-top:24px;padding:13px 18px;border-radius:10px;background:var(--ink);color:var(--bg);text-decoration:none;font-weight:600;visibility:hidden}
.err{color:var(--acc);margin-top:16px}
</style></head><body><main>
<div class="k">Paper to Playground · generating</div>
<h1 id="h">Building your playground…</h1>
<div class="bar"><i id="bar"></i></div>
<ol id="steps">
<li data-s="load"><span class="dot"></span><div><b>Reading the paper</b><small>Resolve the link and fetch the relevant section</small></div></li>
<li data-s="generate"><span class="dot"></span><div><b>Writing the explanation</b><small>One model call: content, calculations and visual</small></div></li>
<li data-s="check"><span class="dot"></span><div><b>Running the checks</b><small>Execute the page's JavaScript: controls, limits, invariants</small></div></li>
<li data-s="repair"><span class="dot"></span><div><b>Repairing what failed</b><small>Only the failing part is sent back</small></div></li>
<li data-s="audit"><span class="dot"></span><div><b>Fact-checking the text</b><small>Explanations compared with the computed numbers</small></div></li>
<li data-s="output"><span class="dot"></span><div><b>Done</b><small>Single offline page + trace</small></div></li>
</ol>
<div class="meta"><span>time <b id="t">0 s</b></span><span>model calls <b id="c">0</b></span><span>tokens <b id="tok">0</b></span><span>checks <b id="ck">–</b></span></div>
<div class="err" id="err"></div>
<a class="btn" id="open" href="#">Open the playground →</a>
</main>
<script>
const order=["load","generate","check","repair","audit","output"];
async function poll(){
  let s; try{ s=await (await fetch("/api/__RUN__")).json(); }catch(e){ return setTimeout(poll,1500); }
  const reached=new Set(s.stages); let last=-1;
  order.forEach((k,i)=>{ if(reached.has(k)) last=i; });
  document.querySelectorAll("#steps li").forEach((li,i)=>{
    const k=li.dataset.s; li.className = s.done ? (reached.has(k)||k==="output" ? "done":"") :
      (i<last ? "done" : (i===last ? "active" : ""));
    if(!s.done && k==="repair" && !reached.has("repair") && last>2) li.className="done";
  });
  document.getElementById("t").textContent=s.elapsed+" s";
  document.getElementById("c").textContent=s.calls;
  document.getElementById("tok").textContent=s.tokens;
  document.getElementById("ck").textContent=s.checks||"–";
  document.getElementById("bar").style.width=(s.done?100:Math.max(3,Math.min(95,(last+1)/order.length*100)))+"%";
  if(s.done){
    document.getElementById("h").textContent = s.exit===0 ? "Your playground is ready" : "Finished with check failures";
    if(s.exit!==0) document.getElementById("err").textContent="Some acceptance checks failed; the best page found is still shown.";
    const a=document.getElementById("open"); a.href="/runs/__RUN__/out/index.html"; a.style.visibility="visible";
    setTimeout(()=>{ location.href=a.href; }, 1500);
  } else setTimeout(poll,1000);
}
poll();
</script></body></html>"""


def _run_agent(run: Path, model: str) -> None:
    t0 = time.time()
    proc = subprocess.run([sys.executable, str(ROOT / "agent.py"), "--input", str(run / "case.json"),
                           "--output", str(run / "out"), "--model", model],
                          cwd=str(ROOT), capture_output=True, text=True, timeout=700)
    (run / "done.json").write_text(json.dumps({"exit": proc.returncode, "seconds": round(time.time() - t0, 1)}),
                                   encoding="utf-8")


def _status(run: Path) -> dict:
    stages, calls, tokens, checks = [], 0, 0, ""
    trace = run / "out" / "trace.jsonl"
    if trace.is_file():
        for line in trace.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            st = e.get("stage")
            if st == "promote" or st == "normalize" or st == "assemble" or st == "parse":
                st = None
            if st == "finalize":
                st = "audit"
            if st and st not in stages:
                stages.append(st)
            if e.get("action") == "llm_call" and e.get("result") == "ok":
                calls += 1
                tokens += sum(v for v in (e.get("prompt_tokens"), e.get("completion_tokens")) if isinstance(v, int))
            if e.get("stage") == "check" and e.get("action") == "summary":
                checks = f"{e.get('passed', '?')}/{(e.get('passed') or 0) + (e.get('failed') or 0) + (e.get('skipped') or 0)}"
    done = run / "done.json"
    d = json.loads(done.read_text(encoding="utf-8")) if done.is_file() else None
    started = (run / "case.json").stat().st_mtime
    return {"stages": stages, "calls": calls, "tokens": tokens, "checks": checks, "done": d is not None,
            "exit": d["exit"] if d else None, "elapsed": round((d or {}).get("seconds", time.time() - started), 1)}


def build_focus(form: dict) -> str:
    """Compose the learning brief in the structure used by the brief's public examples:
    concept → what to change → what to show → what to check (+ standard scope guidance)."""
    concept = form.get("concept", "").strip().rstrip(".")
    parts = [f"Explain {concept} using a small, self-contained example."]
    if form.get("change", "").strip():
        parts.append(f"Let the learner change {form['change'].strip().rstrip('.')}.")
    if form.get("show", "").strip():
        parts.append(f"Show {form['show'].strip().rstrip('.')}.")
    parts.append("Guide them through two contrasting scenarios and state one limitation or common misconception.")
    if form.get("check", "").strip():
        parts.append(f"Check that {form['check'].strip().rstrip('.')}.")
    return " ".join(parts)


def _runs_table() -> str:
    rows = []
    for d in sorted(RUNS.glob("*/"), reverse=True)[:12]:
        summary = d / "summary.json"
        if not summary.is_file():
            continue
        s = json.loads(summary.read_text(encoding="utf-8"))
        rows.append(f"<tr><td><a href='/runs/{d.name}/out/index.html'>{html.escape(s.get('title') or d.name)}</a></td>"
                    f"<td>{'accepted' if s.get('exit') == 0 else 'check failures'}</td>"
                    f"<td>{s.get('tokens', '?')} tokens</td><td>{s.get('seconds', '?')} s</td></tr>")
    if not rows:
        return ""
    return "<div class='card runs'><div class='k'>Recent runs</div><table>" + "".join(rows) + "</table></div>"


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            page = FORM.replace("__RUNS__", "")
            return self._send(200, page.encode("utf-8"))
        if path.startswith("/progress/"):
            name = path[len("/progress/"):]
            if (RUNS / name).is_dir():
                return self._send(200, PROGRESS.replace("__RUN__", html.escape(name)).encode("utf-8"))
        if path.startswith("/api/"):
            name = path[len("/api/"):]
            if (RUNS / name).is_dir():
                return self._send(200, json.dumps(_status(RUNS / name)).encode("utf-8"),
                                  "application/json; charset=utf-8")
        if path.startswith("/runs/"):
            target = (RUNS / path[len("/runs/"):]).resolve()
            if RUNS.resolve() in target.parents and target.is_file():
                ctype = "text/html; charset=utf-8" if target.suffix == ".html" else "application/json; charset=utf-8"
                return self._send(200, target.read_bytes(), ctype)
        self._send(404, b"not found", "text/plain")

    def do_POST(self):  # noqa: N802
        if urllib.parse.urlparse(self.path).path != "/generate":
            return self._send(404, b"not found", "text/plain")
        length = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0].strip() for k, v in urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8")).items()}
        case = {"source_url": form.get("source_url", ""), "focus": build_focus(form),
                "audience": form.get("audience") or "engineering undergraduate"}
        run = RUNS / time.strftime("%Y%m%d-%H%M%S")
        run.mkdir(parents=True, exist_ok=True)
        (run / "case.json").write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
        threading.Thread(target=_run_agent, args=(run, DEFAULT_MODEL), daemon=True).start()
        self.send_response(303)
        self.send_header("Location", f"/progress/{run.name}")
        self.end_headers()

    def log_message(self, fmt, *args):  # keep the console quiet
        pass


def main():
    load_dotenv_into(os.environ)
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("warning: OPENROUTER_API_KEY is not set (put it in the environment or .env)")
    RUNS.mkdir(exist_ok=True)
    print(f"Paper to Playground studio on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
