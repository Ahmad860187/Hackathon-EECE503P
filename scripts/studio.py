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
<label for="f">What to explain (focus)</label>
<textarea id="f" name="focus" required placeholder="Section 3.2.1. Explain scaled dot-product attention with small editable Q, K, V matrices. Show scores, weights and output. Check that each row of weights sums to one."></textarea>
<div class="hint">Name the concept, the learning outcomes, what the learner should change, and what to check.</div>
<label for="a">Audience</label>
<input id="a" name="audience" required value="engineering undergraduate">
<label for="m">Model (OpenRouter id)</label>
<input id="m" name="model" value="__MODEL__">
<button type="submit">Generate playground</button>
<div class="busy">Generating… usually 20–60 seconds (one model call, free checks, targeted repairs).</div>
</form>
__RUNS__
</main></body></html>"""


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
            page = FORM.replace("__MODEL__", html.escape(DEFAULT_MODEL)).replace("__RUNS__", _runs_table())
            return self._send(200, page.encode("utf-8"))
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
        case = {k: form.get(k, "") for k in ("source_url", "focus", "audience")}
        model = form.get("model") or DEFAULT_MODEL
        run = RUNS / time.strftime("%Y%m%d-%H%M%S")
        run.mkdir(parents=True, exist_ok=True)
        (run / "case.json").write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
        t0 = time.time()
        proc = subprocess.run([sys.executable, str(ROOT / "agent.py"), "--input", str(run / "case.json"),
                               "--output", str(run / "out"), "--model", model],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=700)
        secs = round(time.time() - t0, 1)
        tokens, title = "?", case["focus"][:60]
        try:
            for line in (run / "out" / "trace.jsonl").read_text(encoding="utf-8").splitlines():
                e = json.loads(line)
                if e.get("action") == "totals":
                    tokens = e["totals"].get("total_tokens", tokens)
            page = (run / "out" / "index.html").read_text(encoding="utf-8")
            if "<title>" in page:
                title = html.unescape(page.split("<title>", 1)[1].split("</title>", 1)[0]).split(" · ")[0]
        except Exception:  # noqa: BLE001
            pass
        (run / "summary.json").write_text(json.dumps({"title": title, "exit": proc.returncode, "tokens": tokens,
                                                      "seconds": secs}), encoding="utf-8")
        self.send_response(303)
        self.send_header("Location", f"/runs/{run.name}/out/index.html")
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
