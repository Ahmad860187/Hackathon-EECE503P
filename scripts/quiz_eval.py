"""DEV-ONLY PaperQuiz-style teaching check (method from Paper2Poster, arXiv 2505.21497).

A separate model sees ONLY the text of a generated page and answers 5 multiple-choice questions about the
concept. Questions come from eval/cases/<case>/reference.json ("quiz", written from its must-mention facts).
If a reference has no quiz, 5 questions are generated from its must_mention list with one extra call.
Every question gets an extra option "E: the page does not say", and the model is told to use it when the
page text does not support an answer, so prior knowledge is penalised rather than rewarded.

Usage
  python scripts/quiz_eval.py --page eval/runs/<ts>/attention/1/out/index.html --case attention --model MODEL
  python scripts/quiz_eval.py --runs-dir eval/runs/<ts> --model MODEL        # every page in a run -> quiz.csv
  python scripts/quiz_eval.py --page ... --case attention --dry-run         # offline: show extracted text + prompt
  add --baseline to also ask with an empty page (measures how much the model answers from prior knowledge)

Text extraction uses stdlib html.parser: visible text (no script/style), plus, because the shell builds most
of the page at runtime from `const SPEC = {...}`, the human-readable SPEC fields (the same strings app.js
renders). Use --no-spec to score static visible text only.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import REPO, load_dotenv_into  # noqa: E402

CASES_DIR = REPO / "eval" / "cases"
URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_PAGE_CHARS = 24000
SPEC_SKIP_KEYS = {"id", "type", "support", "fmt", "format", "watch", "url", "tests", "default", "preset",
                  "params", "options", "scale", "step", "min", "max", "rows", "cols", "length", "live"}


# ----------------------------------------------------------------------------------------------
class _Visible(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "head"}
    BLOCK = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "br", "table", "ul", "ol",
             "details", "summary", "figcaption", "label", "button"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if not self.skip:
            a = dict(attrs)
            for k in ("aria-label", "alt", "title"):
                if a.get(k):
                    self.parts.append(f" {a[k]} ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.scripts, self._in = [], False

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self._in = True
            self.scripts.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self._in = False

    def handle_data(self, data):
        if self._in:
            self.scripts[-1] += data


def _spec_strings(obj, out, key=""):
    """Collect the human-readable strings of SPEC (what app.js renders); skip ids, JS and presets."""
    if isinstance(obj, str):
        t = obj.strip()
        if t and not (key == "expr" and ("=>" in t or "r." in t or "Math." in t or "&&" in t)):
            out.append(t)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k not in SPEC_SKIP_KEYS:
                _spec_strings(v, out, k)
    elif isinstance(obj, list):
        for v in obj:
            _spec_strings(v, out, key)


def extract_text(html: str, use_spec: bool = True) -> dict:
    v = _Visible()
    v.feed(html)
    visible = re.sub(r"[ \t\r\f\v]+", " ", "".join(v.parts))
    visible = re.sub(r"\n\s*\n+", "\n", visible).strip()
    spec_txt = ""
    if use_spec:
        s = _Scripts()
        s.feed(html)
        for code in s.scripts:
            m = re.search(r"\b(?:const|let|var)\s+SPEC\s*=\s*", code)
            if not m:
                continue
            try:
                obj, _ = json.JSONDecoder().raw_decode(code[m.end():])
            except json.JSONDecodeError:
                continue
            out = []
            _spec_strings(obj, out)
            seen, uniq = set(), []
            for t in out:
                if t not in seen:
                    seen.add(t)
                    uniq.append(t)
            spec_txt = "\n".join(uniq)
            break
    combined = visible + ("\n\n[Text rendered from the page's SPEC]\n" + spec_txt if spec_txt else "")
    return {"visible": visible, "spec": spec_txt, "combined": combined[:MAX_PAGE_CHARS]}


# ----------------------------------------------------------------------------------------------
def call_openrouter(key: str, model: str, messages: list, max_tokens: int = 600, timeout: float = 120) -> dict:
    body = {"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
    req = urllib.request.Request(URL, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "X-Title": "paper2play-dev-quiz"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            return {"text": msg.get("content") or "", "usage": data.get("usage") or {}}
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code not in (429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = type(e).__name__
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"OpenRouter call failed: {last}")


def parse_answers(text: str, n: int) -> list:
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            ans = obj.get("answers")
            if isinstance(ans, list):
                return [str(a).strip().upper()[:1] for a in ans][:n] + ["?"] * max(0, n - len(ans))
        except json.JSONDecodeError:
            pass
    found = re.findall(r"\b([A-E])\b", text.upper())
    return (found + ["?"] * n)[:n]


def quiz_prompt(page_text: str, quiz: list) -> list:
    lines = []
    for i, qq in enumerate(quiz, 1):
        lines.append(f"Q{i}. {qq['q']}")
        for k, v in qq["options"].items():
            lines.append(f"   {k}) {v}")
        lines.append("   E) The page does not say")
    sys_msg = ("You are a student who may ONLY use the web page text given below. Do not use outside knowledge. "
               "For each question choose the option the page supports. If the page text does not contain enough "
               "information to answer, choose E. Reply with JSON only: {\"answers\": [\"A\", ...]} in question order.")
    user = f"PAGE TEXT START\n{page_text if page_text.strip() else '(empty page)'}\nPAGE TEXT END\n\n" + "\n".join(lines)
    return [{"role": "system", "content": sys_msg}, {"role": "user", "content": user}]


def generate_quiz(key: str, model: str, ref: dict) -> list:
    facts = "\n".join(f"{i}. {f}" for i, f in enumerate(ref.get("must_mention", [])))
    msgs = [{"role": "system", "content": "You write fair multiple-choice questions. Reply with JSON only."},
            {"role": "user", "content": "Write exactly 5 multiple-choice questions, each testing one of these facts. "
             "Each has options A-D with exactly one correct. JSON: {\"quiz\": [{\"q\":..., \"options\": {\"A\":...,"
             "\"B\":...,\"C\":...,\"D\":...}, \"answer\": \"A\", \"fact\": 0}]}\nFacts:\n" + facts}]
    out = call_openrouter(key, model, msgs, max_tokens=1500)
    m = re.search(r"\{.*\}", out["text"], re.S)
    return json.loads(m.group(0))["quiz"][:5]


def score_page(html_path: Path, ref: dict, model: str, key: str, dry_run=False, baseline=False, use_spec=True) -> dict:
    html = html_path.read_text(encoding="utf-8", errors="replace")
    ex = extract_text(html, use_spec)
    quiz = ref.get("quiz") or []
    if dry_run:
        msgs = quiz_prompt(ex["combined"], quiz or [{"q": "(would be generated from must_mention)", "options":
                                                       {"A": "", "B": "", "C": "", "D": ""}}])
        return {"dry_run": True, "visible_chars": len(ex["visible"]), "spec_chars": len(ex["spec"]),
                "prompt_chars": sum(len(m["content"]) for m in msgs), "prompt": msgs[1]["content"]}
    if not quiz:
        quiz = generate_quiz(key, model, ref)
    res = call_openrouter(key, model, quiz_prompt(ex["combined"], quiz))
    ans = parse_answers(res["text"], len(quiz))
    correct = [a == qq["answer"] for a, qq in zip(ans, quiz)]
    out = {"score": sum(correct), "n": len(quiz), "not_stated": sum(a == "E" for a in ans), "answers": ans,
           "key": [qq["answer"] for qq in quiz], "missed_facts": [ref["must_mention"][qq["fact"]] for qq, c in
                                                                  zip(quiz, correct) if not c and "fact" in qq
                                                                  and qq["fact"] < len(ref.get("must_mention", []))],
           "visible_chars": len(ex["visible"]), "spec_chars": len(ex["spec"]), "usage": res["usage"]}
    if baseline:
        b = call_openrouter(key, model, quiz_prompt("", quiz))
        bans = parse_answers(b["text"], len(quiz))
        out["baseline_score"] = sum(a == qq["answer"] for a, qq in zip(bans, quiz))
    return out


def load_ref(case: str) -> dict:
    p = CASES_DIR / case / "reference.json"
    return json.loads(p.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PaperQuiz-style page check (dev only).")
    ap.add_argument("--page", help="path to a generated index.html")
    ap.add_argument("--case", help="case slug (eval/cases/<slug>/reference.json)")
    ap.add_argument("--reference", help="explicit reference.json path (overrides --case)")
    ap.add_argument("--runs-dir", help="score every <case>/<run>/out/index.html under an eval run folder")
    ap.add_argument("--model", default=os.environ.get("QUIZ_MODEL", os.environ.get("MODEL_ID", "")),
                    help="OpenRouter model id for the quiz taker (or env QUIZ_MODEL / MODEL_ID)")
    ap.add_argument("--baseline", action="store_true", help="also ask with an empty page (prior-knowledge leak)")
    ap.add_argument("--no-spec", action="store_true", help="use static visible text only")
    ap.add_argument("--dry-run", action="store_true", help="no API call; print extracted text stats and prompt")
    ap.add_argument("--json-out", help="write the result JSON here")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    env = dict(os.environ)
    load_dotenv_into(env)
    key = env.get("OPENROUTER_API_KEY", "")
    if not a.dry_run and (not key or not a.model):
        print("need OPENROUTER_API_KEY (env or .env) and --model, or use --dry-run", file=sys.stderr)
        return 2

    if a.runs_dir:
        root = Path(a.runs_dir)
        rows = []
        for page in sorted(root.glob("*/*/out/index.html")):
            case, run = page.parts[-4], page.parts[-3]
            try:
                r = score_page(page, load_ref(case), a.model, key, a.dry_run, a.baseline, not a.no_spec)
            except Exception as e:
                r = {"error": str(e)[:200]}
            row = {"case": case, "run": run, "score": r.get("score", ""), "n": r.get("n", ""),
                   "not_stated": r.get("not_stated", ""), "baseline_score": r.get("baseline_score", ""),
                   "visible_chars": r.get("visible_chars", ""), "spec_chars": r.get("spec_chars", ""),
                   "missed_facts": " | ".join(r.get("missed_facts", [])), "error": r.get("error", "")}
            rows.append(row)
            print(f"  {case:28s} #{run}: {row['score']}/{row['n']}  not-stated={row['not_stated']} "
                  f"text={row['visible_chars']}+{row['spec_chars']} chars {row['error']}")
        if rows and not a.dry_run:
            with open(root / "quiz.csv", "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            scored = [r for r in rows if isinstance(r["score"], int)]
            if scored:
                print(f"mean quiz score: {sum(r['score'] for r in scored) / len(scored):.2f} / 5 over {len(scored)} pages"
                      f" -> {root / 'quiz.csv'}")
        return 0

    if not a.page or not (a.case or a.reference):
        ap.error("--page with --case/--reference, or --runs-dir, is required")
    ref = json.loads(Path(a.reference).read_text(encoding="utf-8")) if a.reference else load_ref(a.case)
    r = score_page(Path(a.page), ref, a.model, key, a.dry_run, a.baseline, not a.no_spec)
    if a.dry_run:
        print(f"visible chars={r['visible_chars']} spec chars={r['spec_chars']} prompt chars={r['prompt_chars']}")
        print(r["prompt"][:4000])
    else:
        print(f"quiz score: {r['score']}/{r['n']} (not stated: {r['not_stated']})"
              + (f" | baseline (no page): {r['baseline_score']}/{r['n']}" if "baseline_score" in r else ""))
        for f in r["missed_facts"]:
            print(f"  missed: {f}")
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(r, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
