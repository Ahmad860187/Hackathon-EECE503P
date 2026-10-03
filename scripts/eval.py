"""DEV-ONLY evaluation harness: run agent.py on eval cases and summarise traces.

Usage
  python scripts/eval.py --cases "*" --runs 2 --model MODEL_ID --parallel 3
  python scripts/eval.py --cases "attention,entropy,bench_*" --runs 1 --model MODEL_ID
  python scripts/eval.py --cases "*" --runs 1 --dry-run          # offline: fake agent, tests the harness
  python scripts/eval.py --summarize eval/runs/20261003-120000  # re-print a finished run

Each run gets a fresh directory eval/runs/<timestamp>/<case>/<run>/ containing
  input/case.json (+ input/excerpt.txt unless --no-excerpt)   <- reference.json is NEVER copied
  out/index.html, out/trace.jsonl                               <- written by agent.py
  agent.log                                                     <- stdout+stderr (API key redacted)
and eval/runs/<timestamp>/results.csv + summary.json.

OPENROUTER_API_KEY is taken from the environment, or else from the gitignored .env in the repo
root, and passed only to the child process. It is never printed.
"""
from __future__ import annotations

import argparse
import csv
import fnmatch
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import REPO, load_dotenv_into, redact, secret_values  # noqa: E402

CASES_DIR = REPO / "eval" / "cases"
RUNS_DIR = REPO / "eval" / "runs"
LIMITS = {"calls": 10, "completion_tokens": 30000, "wall_s": 600}
CHECK_ID = re.compile(r"^[A-Z]{1,2}\d{1,2}\b")
SUMMARY_ACTIONS = {"rerun_all", "run_all", "summary", "all", "acceptance"}

FIELDS = ["case", "run", "exit_code", "timed_out", "wall_s", "accept", "html_exists", "html_bytes",
          "calls", "prompt_tokens", "completion_tokens", "reasoning_tokens", "tokens_known",
          "checks_passed", "checks_failed", "failed_checks", "budget_ok", "budget_violations",
          "external_refs", "source_origin", "trace_events", "trace_errors", "run_dir", "error"]


# ----------------------------------------------------------------------------------------------
def select_cases(patterns: str) -> list:
    all_cases = sorted(p.name for p in CASES_DIR.iterdir() if (p / "case.json").is_file())
    pats = [s.strip() for s in patterns.split(",") if s.strip()] or ["*"]
    chosen = [c for c in all_cases if any(fnmatch.fnmatch(c, p) for p in pats)]
    return chosen


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse_trace(path: Path) -> dict:
    """Sum LLM usage and collect final check results from trace.jsonl (CONTRACT §6 shape)."""
    info = {"events": 0, "errors": 0, "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "reasoning_tokens": 0, "tokens_known": True, "checks": {}, "accept": None,
            "source_origin": "", "totals": None}
    if not path.is_file():
        info["tokens_known"] = False
        return info
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            info["errors"] += 1
            continue
        if not isinstance(ev, dict):
            continue
        info["events"] += 1
        stage, action, result = str(ev.get("stage", "")), str(ev.get("action", "")), str(ev.get("result", ""))
        if result == "error":
            info["errors"] += 1
        if action == "llm_call" or "call" in ev and ("prompt_tokens" in ev or "completion_tokens" in ev):
            info["calls"] += 1
            for k in ("prompt_tokens", "completion_tokens", "reasoning_tokens"):
                v = num(ev.get(k))
                if v is not None:
                    info[k] += v
                elif k != "reasoning_tokens" and k in ev:
                    info["tokens_known"] = False
        if stage == "load" and ev.get("origin"):
            info["source_origin"] = str(ev.get("origin"))
        if stage == "check":
            cid = str(ev.get("id") or action)
            if action not in SUMMARY_ACTIONS and result in ("pass", "fail", "skip", "error", "ok"):
                info["checks"][cid] = "pass" if result in ("pass", "ok") else result
        for k in ("accept", "accepted"):
            if isinstance(ev.get(k), bool) and stage != "check":  # check/summary 'accepted' is per candidate
                info["accept"] = ev[k]
        if isinstance(ev.get("checks"), dict) and ev["checks"]:  # final per-id results of the promoted page
            final_checks = {str(cid): ("pass" if str(r) in ("pass", "ok") else str(r))
                            for cid, r in ev["checks"].items()}
            info["final_checks"] = final_checks
        if isinstance(ev.get("totals"), dict):
            info["totals"] = ev["totals"]
    if info.get("final_checks"):
        info["checks"] = info.pop("final_checks")
    t = info["totals"]
    if t:  # prefer agent's own totals when present and numeric
        for k in ("calls", "prompt_tokens", "completion_tokens", "reasoning_tokens"):
            if num(t.get(k)) is not None:
                info[k] = t[k]
            elif t.get(k) == "unknown":
                info["tokens_known"] = False
        if isinstance(t.get("accept"), bool):
            info["accept"] = t["accept"]
    return info


EXT_REF = re.compile(r"""(?:\bsrc|\bhref)\s*=\s*["']\s*(https?:)?//|@import\s+(?:url\()?\s*["']?https?://|url\(\s*["']?https?://""",
                     re.I)


def page_facts(html_path: Path) -> dict:
    if not html_path.is_file():
        return {"html_exists": False, "html_bytes": 0, "external_refs": 0}
    txt = html_path.read_text(encoding="utf-8", errors="replace")
    # external loads only (a single citation <a href> to the paper is allowed by the contract)
    refs = [m for m in EXT_REF.finditer(txt)]
    ext = 0
    for m in refs:
        start = txt.rfind("<", 0, m.start())
        tag = txt[start:start + 3].lower() if start >= 0 else ""
        if tag.startswith("<a"):
            continue
        ext += 1
    return {"html_exists": True, "html_bytes": len(txt.encode("utf-8")), "external_refs": ext}


# ----------------------------------------------------------------------------------------------
def run_one(case: str, run: int, root: Path, args, env: dict, secrets: list, lock) -> dict:
    run_dir = root / case / str(run)
    inp, out = run_dir / "input", run_dir / "out"
    inp.mkdir(parents=True, exist_ok=True)
    src = CASES_DIR / case
    (inp / "case.json").write_bytes((src / "case.json").read_bytes())
    if not args.no_excerpt and (src / "excerpt.txt").is_file():
        (inp / "excerpt.txt").write_bytes((src / "excerpt.txt").read_bytes())

    if args.dry_run:
        cmd = [sys.executable, str(Path(__file__).resolve()), "--_fake-agent", "--input", str(inp / "case.json"),
               "--output", str(out), "--model", args.model or "dry-run"]
    else:
        cmd = [sys.executable, str(REPO / args.agent), "--input", str(inp / "case.json"), "--output", str(out),
               "--model", args.model] + (args.agent_args or [])
    t0 = time.monotonic()
    timed_out, code, err = False, None, ""
    try:
        proc = subprocess.run(cmd, cwd=str(REPO), env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=args.timeout)
        code = proc.returncode
        log = (proc.stdout or "") + "\n--- stderr ---\n" + (proc.stderr or "")
    except subprocess.TimeoutExpired as e:
        timed_out, code = True, None
        log = f"TIMEOUT after {args.timeout}s\n" + str(e.stdout or "") + "\n--- stderr ---\n" + str(e.stderr or "")
        err = "timeout"
    except OSError as e:
        log, err = f"failed to start: {e}", f"spawn: {e}"
    wall = time.monotonic() - t0
    (run_dir / "agent.log").write_text(redact(log, secrets), encoding="utf-8")

    tr = parse_trace(out / "trace.jsonl")
    pf = page_facts(out / "index.html")
    failed = sorted(k for k, v in tr["checks"].items() if v in ("fail", "error"))
    passed = sum(1 for v in tr["checks"].values() if v == "pass")
    accept = tr["accept"]
    if accept is None:
        accept = (code == 0) and pf["html_exists"]
    viol = []
    if tr["calls"] > LIMITS["calls"]:
        viol.append("calls")
    if tr["completion_tokens"] > LIMITS["completion_tokens"]:
        viol.append("completion_tokens")
    if wall > LIMITS["wall_s"]:
        viol.append("wall_time")
    if not pf["html_exists"]:
        viol.append("no_page")
    if not err and code not in (0, None) and not pf["html_exists"]:
        tail = [ln for ln in log.strip().splitlines() if ln.strip()][-1:] or [""]
        err = redact(tail[0], secrets)[:200]
    row = {"case": case, "run": run, "exit_code": code, "timed_out": timed_out, "wall_s": round(wall, 2),
           "accept": bool(accept), "html_exists": pf["html_exists"], "html_bytes": pf["html_bytes"],
           "calls": tr["calls"], "prompt_tokens": tr["prompt_tokens"], "completion_tokens": tr["completion_tokens"],
           "reasoning_tokens": tr["reasoning_tokens"], "tokens_known": tr["tokens_known"],
           "checks_passed": passed, "checks_failed": len(failed), "failed_checks": ";".join(failed),
           "budget_ok": not viol, "budget_violations": ";".join(viol), "external_refs": pf["external_refs"],
           "source_origin": tr["source_origin"], "trace_events": tr["events"], "trace_errors": tr["errors"],
           "run_dir": str(run_dir.relative_to(REPO)), "error": err}
    with lock:
        mark = "ACCEPT" if row["accept"] else "reject"
        print(f"  [{case} #{run}] {mark} exit={code} {wall:.1f}s calls={row['calls']} "
              f"completion={row['completion_tokens']} failed=[{row['failed_checks']}]", flush=True)
    return row


# ----------------------------------------------------------------------------------------------
def mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return statistics.fmean(xs) if xs else float("nan")


def summarize(rows: list) -> dict:
    by_case = {}
    for r in rows:
        by_case.setdefault(r["case"], []).append(r)
    hist = Counter()
    for r in rows:
        for c in filter(None, str(r["failed_checks"]).split(";")):
            hist[c] += 1
    total_tok = lambda r: float(r["prompt_tokens"]) + float(r["completion_tokens"])
    per_case = {}
    for c, rs in sorted(by_case.items()):
        per_case[c] = {"runs": len(rs), "accept_rate": mean([1.0 if truthy(r["accept"]) else 0.0 for r in rs]),
                       "mean_total_tokens": mean([total_tok(r) for r in rs]),
                       "mean_completion_tokens": mean([float(r["completion_tokens"]) for r in rs]),
                       "mean_wall_s": mean([float(r["wall_s"]) for r in rs]),
                       "mean_calls": mean([float(r["calls"]) for r in rs]),
                       "budget_ok_rate": mean([1.0 if truthy(r["budget_ok"]) else 0.0 for r in rs])}
    overall = {"runs": len(rows), "accept_rate": mean([1.0 if truthy(r["accept"]) else 0.0 for r in rows]),
               "mean_total_tokens": mean([total_tok(r) for r in rows]),
               "mean_completion_tokens": mean([float(r["completion_tokens"]) for r in rows]),
               "mean_wall_s": mean([float(r["wall_s"]) for r in rows]),
               "max_wall_s": max([float(r["wall_s"]) for r in rows], default=float("nan")),
               "mean_calls": mean([float(r["calls"]) for r in rows]),
               "budget_ok_rate": mean([1.0 if truthy(r["budget_ok"]) else 0.0 for r in rows])}
    return {"overall": overall, "per_case": per_case, "failing_checks": dict(hist.most_common())}


def truthy(v):
    return v is True or str(v).lower() in ("true", "1", "yes")


def print_summary(s: dict) -> None:
    hdr = f"{'case':28s} {'runs':>4s} {'accept':>7s} {'tokens':>8s} {'compl':>7s} {'calls':>5s} {'wall_s':>7s} {'budget':>6s}"
    print("\n" + hdr)
    print("-" * len(hdr))
    def line(name, d):
        print(f"{name:28s} {d['runs']:>4d} {d['accept_rate']*100:>6.0f}% {d['mean_total_tokens']:>8.0f} "
              f"{d['mean_completion_tokens']:>7.0f} {d['mean_calls']:>5.1f} {d['mean_wall_s']:>7.1f} "
              f"{d['budget_ok_rate']*100:>5.0f}%")
    for c, d in s["per_case"].items():
        line(c, d)
    print("-" * len(hdr))
    line("ALL", s["overall"])
    print(f"max wall time: {s['overall']['max_wall_s']:.1f}s (limit {LIMITS['wall_s']}s)")
    print("\nFailing checks histogram (final state per run):")
    if not s["failing_checks"]:
        print("  (none)")
    width = max([len(k) for k in s["failing_checks"]] + [4])
    for k, n in s["failing_checks"].items():
        print(f"  {k:{width}s} {n:>3d} {'#' * n}")


def write_results(root: Path, rows: list) -> dict:
    rows.sort(key=lambda r: (r["case"], int(r["run"])))
    with open(root / "results.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    s = summarize(rows)
    (root / "summary.json").write_text(json.dumps(s, indent=2), encoding="utf-8")
    return s


def load_rows(root: Path) -> list:
    with open(root / "results.csv", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ----------------------------------------------------------------------------------------------
def fake_agent(argv) -> int:
    """Stand-in for agent.py used by --dry-run: writes a tiny page + a CONTRACT-shaped trace."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--input")
    ap.add_argument("--output")
    ap.add_argument("--model")
    a = ap.parse_args(argv)
    case = json.loads(Path(a.input).read_text(encoding="utf-8"))
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    seed = sum(map(ord, case["focus"])) % 7
    has_excerpt = (Path(a.input).parent / "excerpt.txt").is_file()
    t = 0.0
    evs = [{"t": t, "stage": "load", "action": "resolve_source", "result": "ok",
            "origin": "file" if has_excerpt else "none"},
           {"t": 0.4, "stage": "generate", "action": "llm_call", "call": 1, "model": a.model, "prompt_tokens": 3000 + seed,
            "completion_tokens": 5000 + 100 * seed, "elapsed_s": 20.0, "finish_reason": "stop", "result": "ok"}]
    for cid in ("S1", "S2", "S3", "X1", "X2", "X3", "X4", "X5", "C1", "C2", "G1"):
        res = "fail" if (cid == "X4" and seed % 3 == 0) or (cid == "C2" and seed % 2 == 0) else "pass"
        evs.append({"t": 21.0, "stage": "check", "action": cid, "result": res})
    calls = 1
    if seed % 3 == 0:
        evs.append({"t": 22.0, "stage": "repair", "action": "llm_call", "call": 2, "prompt_tokens": 1400,
                    "completion_tokens": 900, "elapsed_s": 6.0, "result": "ok"})
        evs.append({"t": 28.0, "stage": "check", "action": "X4", "result": "pass"})
        calls = 2
    evs.append({"t": 28.5, "stage": "output", "action": "write", "result": "ok",
                "totals": {"calls": calls, "prompt_tokens": sum(e.get("prompt_tokens", 0) for e in evs),
                           "completion_tokens": sum(e.get("completion_tokens", 0) for e in evs), "elapsed_s": 28.5}})
    with open(out / "trace.jsonl", "w", encoding="utf-8") as fh:
        for e in evs:
            fh.write(json.dumps(e) + "\n")
    (out / "index.html").write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>dry run</title></head><body>"
        f"<h1>Dry run</h1><p>{case['focus'][:200]}</p><a href='{case['source_url']}'>source</a>"
        "<script>const SPEC={\"title\":\"dry run\",\"idea\":\"placeholder\"};</script></body></html>",
        encoding="utf-8")
    print("fake agent done")
    return 0


# ----------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--_fake-agent":
        return fake_agent(argv[1:])
    ap = argparse.ArgumentParser(description="Run agent.py over eval cases and summarise traces (dev only).")
    ap.add_argument("--cases", default="*", help="comma-separated glob(s) over case folder names (default: *)")
    ap.add_argument("--runs", type=int, default=2, help="runs per case (default 2)")
    ap.add_argument("--model", default=os.environ.get("MODEL_ID", ""), help="OpenRouter model id (or env MODEL_ID)")
    ap.add_argument("--parallel", type=int, default=1, help="concurrent agent processes (default 1)")
    ap.add_argument("--timeout", type=float, default=660.0, help="hard kill per run, seconds (default 660)")
    ap.add_argument("--no-excerpt", action="store_true", help="do not copy excerpt.txt (tests the no-excerpt path)")
    ap.add_argument("--agent", default="agent.py", help="agent entry point relative to repo root")
    ap.add_argument("--agent-args", nargs=argparse.REMAINDER, help="extra args passed through to agent.py (put last)")
    ap.add_argument("--dry-run", action="store_true", help="use a built-in fake agent (no API calls)")
    ap.add_argument("--tag", default="", help="suffix for the run folder name")
    ap.add_argument("--summarize", metavar="RUN_DIR", help="re-print the summary of an existing run folder")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.summarize:
        root = Path(args.summarize)
        rows = load_rows(root)
        print_summary(write_results(root, rows))
        return 0

    cases = select_cases(args.cases)
    if not cases:
        print(f"no cases match {args.cases!r} in {CASES_DIR}", file=sys.stderr)
        return 2
    if not args.dry_run:
        if not args.model:
            print("--model is required (or set MODEL_ID) unless --dry-run", file=sys.stderr)
            return 2
        if not (REPO / args.agent).is_file():
            print(f"agent entry point not found: {REPO / args.agent}", file=sys.stderr)
            return 2

    env = dict(os.environ)
    loaded = load_dotenv_into(env)
    env["PYTHONIOENCODING"] = "utf-8"
    secrets = secret_values(env)
    if not args.dry_run and not env.get("OPENROUTER_API_KEY"):
        print("warning: OPENROUTER_API_KEY not set in environment or .env; agent will likely fail", file=sys.stderr)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + (f"-{args.tag}" if args.tag else "") + ("-dry" if args.dry_run else "")
    root = RUNS_DIR / stamp
    root.mkdir(parents=True, exist_ok=False)
    meta = {"started": datetime.now().isoformat(timespec="seconds"), "model": args.model or "dry-run",
            "cases": cases, "runs": args.runs, "parallel": args.parallel, "no_excerpt": args.no_excerpt,
            "dry_run": args.dry_run, "timeout_s": args.timeout, "key_from_dotenv": bool(loaded)}
    (root / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"eval -> {root.relative_to(REPO)} | {len(cases)} cases x {args.runs} runs | model={meta['model']} "
          f"| parallel={args.parallel}{' | key from .env' if loaded else ''}")

    jobs = [(c, r) for c in cases for r in range(1, args.runs + 1)]
    lock = threading.Lock()
    rows = []
    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as ex:
        futs = {ex.submit(run_one, c, r, root, args, env, secrets, lock): (c, r) for c, r in jobs}
        for f in as_completed(futs):
            try:
                rows.append(f.result())
            except Exception as e:  # harness bug: record, keep going
                c, r = futs[f]
                rows.append({"case": c, "run": r, "accept": False, "error": f"harness: {e!r}", "wall_s": 0,
                             "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "budget_ok": False,
                             "failed_checks": ""})
    s = write_results(root, rows)
    print_summary(s)
    print(f"\nresults: {(root / 'results.csv').relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
