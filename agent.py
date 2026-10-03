#!/usr/bin/env python3
"""Paper to Playground agent.

Usage: python agent.py --input case.json --output out --model MODEL_ID

Writes out/index.html (single self-contained page) and out/trace.jsonl.
Exit code 0 when the acceptance checks pass, nonzero otherwise (a page and trace are
always written). The OpenRouter key is read only from the OPENROUTER_API_KEY env var.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path

from paper2play.budget import Budget
from paper2play.fallback import build_fallback
from paper2play.pipeline import Run
from paper2play.trace import Trace

REQUIRED_FIELDS = ("source_url", "focus", "audience")
EXIT_INVALID_INPUT = 2


class CaseError(Exception):
    pass


def load_case(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise CaseError(f"cannot read input file {path}: {e.strerror or e}") from None
    try:
        case = json.loads(raw)
    except ValueError as e:
        raise CaseError(f"input is not valid JSON: {e}") from None
    if not isinstance(case, dict):
        raise CaseError("input JSON must be an object with fields: " + ", ".join(REQUIRED_FIELDS))
    problems = []
    for k in REQUIRED_FIELDS:
        v = case.get(k)
        if k not in case:
            problems.append(f"'{k}' is missing")
        elif not isinstance(v, str):
            problems.append(f"'{k}' must be a string (got {type(v).__name__})")
        elif not v.strip():
            problems.append(f"'{k}' must be non-empty")
    if problems:
        raise CaseError("invalid case.json: " + "; ".join(problems))
    for k in REQUIRED_FIELDS:
        case[k] = case[k].strip()
    return case


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Turn one paper mechanism into an interactive teaching page.")
    ap.add_argument("--input", required=True, help="path to case.json")
    ap.add_argument("--output", required=True, help="output directory")
    ap.add_argument("--model", required=True, help="OpenRouter model id")
    args = ap.parse_args(argv)

    budget = Budget()
    out_dir = Path(args.output)
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    trace = Trace(out_dir / "trace.jsonl", clock=budget.clock, t0=budget.t0, secrets=[api_key])
    trace.event("start", "run", "ok", model=args.model, python=platform.python_version(),
                limits={"wall_s": budget.wall_s, "requests": budget.max_requests,
                        "completion_tokens": budget.max_completion})

    input_path = Path(args.input)
    try:
        case = load_case(input_path)
        trace.event("load", "validate_case", "ok", fields=list(case.keys()))
    except CaseError as e:
        print(f"error: {e}", file=sys.stderr)
        trace.event("load", "validate_case", "fail", detail=str(e))
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "index.html").write_text(
                build_fallback({}, None, reason=str(e), model=args.model), encoding="utf-8")
            trace.event("output", "write_index_html", "ok", kind="fallback")
        except OSError:
            pass
        trace.event("end", "exit", "fail", code=EXIT_INVALID_INPUT)
        trace.close()
        return EXIT_INVALID_INPUT

    code = 1
    run = None
    try:
        run = Run(case, input_path.resolve().parent, out_dir, args.model, api_key, trace, budget)
        code = run.execute()
    except Exception as e:  # last-resort guard: never leave without a page and a trace
        trace.event("error", "unhandled", "error", detail=f"{type(e).__name__}: {str(e)[:300]}")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "index.html").write_text(
                build_fallback(case, None, reason="internal error", model=args.model,
                               origin=run.src["origin"] if run else "none"), encoding="utf-8")
        except OSError:
            pass
        code = 1
    trace.event("end", "exit", "ok" if code == 0 else "fail", code=code,
                elapsed_s=round(budget.elapsed(), 2))
    trace.close()
    totals = budget.totals()
    print(f"{'accepted' if code == 0 else 'NOT accepted'}: calls={totals['calls']} "
          f"prompt_tokens={totals['prompt_tokens']} completion_tokens={totals['completion_tokens']} "
          f"elapsed={totals['elapsed_s']}s -> {out_dir / 'index.html'}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
