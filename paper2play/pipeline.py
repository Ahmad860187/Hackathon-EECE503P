"""End-to-end pipeline: load → generate → parse → assemble → check → targeted repair → write.

One main generation call; at most 2 targeted repairs that resend only the failing section(s)
plus the check messages. The best candidate (acceptance first, then #passing checks, never a
regression) is promoted. Outputs are always written (fallback page if needed).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import checks as chk
from .assemble import ShellMissing, assemble, missing_placeholders, sanitize_spec_text, shell_available
from .budget import Budget
from .fallback import build_fallback
from .llm import BudgetStop, LLMError, NoAPIKey, OpenRouterClient
from .protocol import Parsed, parse_json_lenient, parse_output, split_sections, strip_fences
from .source import load_source
from .spec import normalize_spec

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
GEN_CAP = 14000
REPAIR_CAP = 7000
MAX_REPAIRS = 2

_ORIGIN_DESC = {
    "inline": "excerpt supplied with the case (you may label statements found in it 'excerpt')",
    "file": "excerpt read from a local file (you may label statements found in it 'excerpt')",
    "fetched": "text fetched from the source URL (you may label statements found in it 'excerpt')",
    "none": "no excerpt available: use your knowledge of the cited paper; never label anything 'excerpt'",
}


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8").strip()


def system_prompt() -> str:
    return load_prompt("system.md").replace("{{KIT}}", load_prompt("kit.md"))


def build_user_message(case: dict, src: dict) -> str:
    lines = [f"FOCUS: {case['focus']}", f"AUDIENCE: {case['audience']}", f"SOURCE: {case['source_url']}",
             f"SOURCE ORIGIN: {src['origin']} — {_ORIGIN_DESC.get(src['origin'], _ORIGIN_DESC['none'])}"]
    if src.get("title"):
        lines.append(f"DETECTED TITLE: {src['title']}")
    dup = next((k for k in ("focus", "source_url") if src.get("detail", "").startswith(k)
                and len(case.get(k, "")) <= 6000), None)
    if src.get("text") and dup:
        lines.append(f"EXCERPT: the {dup.upper()} text above is the excerpt")
    elif src.get("text"):
        lines.append('EXCERPT:\n"""\n' + src["text"].strip() + '\n"""')
    else:
        lines.append("EXCERPT: none")
    return "\n".join(lines)


@dataclass
class Candidate:
    label: str
    spec_in: dict | None = None        # as returned by the model (parsed JSON)
    spec_error: str | None = None
    compute: str | None = None
    render: str | None = None
    parsed: Parsed | None = None
    spec: dict | None = None           # normalised
    norm_notes: list = field(default_factory=list)
    html: str | None = None
    checks: list = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return bool(self.checks) and chk.accepted(self.checks)

    @property
    def score(self) -> tuple:
        return chk.score(self.checks) if self.checks else (0, 0)


class Run:
    def __init__(self, case, case_dir, out_dir, model, api_key, trace, budget: Budget,
                 session=None, shell_dir=None, kit_js=None, allow_network=True, sleep=None):
        self.case = case
        self.case_dir = case_dir
        self.out_dir = Path(out_dir)
        self.model = model
        self.api_key = api_key
        self.trace = trace
        self.budget = budget
        self.session = session
        self.shell_dir = shell_dir
        self.kit_js = kit_js
        self.allow_network = allow_network
        self.sleep = sleep
        self.secrets = [api_key] if api_key and len(api_key) >= 8 else []
        self.src = {"text": "", "title": "", "origin": "none", "detail": ""}
        self.revisions = 0

    # ------------------------------------------------------------------ helpers
    def build(self, checks=()) -> dict:
        return {"source_origin": self.src["origin"], "model": self.model,
                "checks": [{"id": c["id"], "name": c.get("name", ""), "result": c["result"],
                            "detail": c.get("detail", "")} for c in checks],
                "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    def evaluate(self, cand: Candidate) -> Candidate:
        origin = self.src["origin"]
        if isinstance(cand.spec_in, dict):
            spec, notes = normalize_spec(cand.spec_in, self.case, origin)
            cand.spec = sanitize_spec_text(spec)
            cand.norm_notes = notes
            if notes:
                self.trace.event("normalize", "spec_fixes", "ok", candidate=cand.label,
                                 fixes=notes[:12], n=len(notes))
        if cand.spec is not None and shell_available(self.shell_dir):
            try:
                cand.html = assemble(cand.spec, cand.compute, cand.render, self.build(), self.shell_dir)
                miss = missing_placeholders(self.shell_dir)
                self.trace.event("assemble", "inline_shell", "ok" if not miss else "fail",
                                 candidate=cand.label, bytes=len(cand.html),
                                 **({"detail": f"shell placeholders missing: {miss}"} if miss else {}))
            except (ShellMissing, OSError) as e:
                cand.html = None
                self.trace.event("assemble", "inline_shell", "fail", candidate=cand.label, detail=str(e))
        elif cand.spec is not None:
            self.trace.event("assemble", "inline_shell", "fail", candidate=cand.label,
                             detail="shell/shell.html not found")
        t_budget = max(3.0, min(chk.CHECKS_TIME_BUDGET_S, self.budget.remaining_time() - 15.0))
        cand.checks = chk.run_checks(cand.spec, cand.compute, cand.render, cand.html,
                                     origin, parsed=cand.parsed, norm_notes=cand.norm_notes,
                                     shell_dir=self.shell_dir, kit_js=self.kit_js,
                                     time_budget_s=t_budget, secrets=self.secrets)
        shell_d = Path(self.shell_dir) if self.shell_dir else Path(__file__).resolve().parent / "shell"
        absent = [f for f in ("style.css", "kit.js", "app.js") if not (shell_d / f).is_file()]
        if absent and cand.html is not None:
            for c in cand.checks:
                if c["id"] == "S2":
                    c["result"] = "fail"
                    c["detail"] = f"shell incomplete: missing {', '.join(absent)}; " + c["detail"]
        for c in cand.checks:
            self.trace.event("check", f"{c['id']}_{c['name']}", c["result"], candidate=cand.label,
                             detail=c["detail"])
        n_pass = sum(c["result"] == "pass" for c in cand.checks)
        n_fail = sum(c["result"] == "fail" for c in cand.checks)
        self.trace.event("check", "summary", "pass" if cand.accepted else "fail", candidate=cand.label,
                         accepted=cand.accepted, passed=n_pass, failed=n_fail,
                         skipped=len(cand.checks) - n_pass - n_fail,
                         failing=[c["id"] for c in cand.checks if c["result"] == "fail"])
        return cand

    def candidate_from_text(self, text: str, label: str) -> Candidate:
        parsed = parse_output(text)
        self.trace.event("parse", "protocol", "ok" if len(parsed.found) == 3 and parsed.spec else "fail",
                         candidate=label, found=parsed.found, notes=parsed.notes,
                         spec_error=parsed.spec_error)
        return Candidate(label=label, spec_in=parsed.spec, spec_error=parsed.spec_error,
                         compute=parsed.compute, render=parsed.render, parsed=parsed)

    # ------------------------------------------------------------------ repair
    @staticmethod
    def repair_targets(cand: Candidate):
        """Map failing checks to (required sections, optional sections)."""
        req, opt = set(), set()
        for c in chk.failing(cand.checks):
            cid, d = c["id"], c["detail"]
            if cid == "S3":
                for sec in ("SPEC", "COMPUTE", "RENDER"):
                    if f"{sec} section missing" in d or (sec == "SPEC" and "SPEC:" in d):
                        req.add(sec)
            elif cid in ("S1", "G1"):
                req.add("SPEC")
            elif cid == "X1":
                if "COMPUTE" in d:
                    req.add("COMPUTE")
                if "RENDER" in d:
                    req.add("RENDER")
            elif cid in ("X2", "X3", "X4", "X6", "C1", "C3"):
                req.add("COMPUTE")
                opt.add("SPEC")
            elif cid == "X5":
                req.add("RENDER")
            elif cid == "C2":
                if any(f["id"] == "X4" for f in chk.failing(cand.checks)):
                    req.add("COMPUTE")       # a dead control explains the unchanged watch value
                    opt.add("SPEC")
                else:
                    req.add("SPEC")
                    opt.add("COMPUTE")
            elif cid == "S2" and "no assembled page" not in d and "shell incomplete" not in d:
                req.add("RENDER")
        if cand.spec is None:
            req.add("SPEC")
        return req, opt - req

    def repair_message(self, cand: Candidate, req: set, opt: set) -> str:
        fails = chk.failing(cand.checks)
        lines = [f"FOCUS: {self.case['focus']}", f"AUDIENCE: {self.case['audience']}",
                 f"SOURCE ORIGIN: {self.src['origin']}", "", "FAILED CHECKS:"]
        lines += [f"- {c['id']} {c['name']}: {c['detail']}" for c in fails]
        order = [s for s in ("SPEC", "COMPUTE", "RENDER") if s in req]
        fix = "FIX: " + ", ".join(order)
        if opt:
            fix += f" (you may also return {', '.join(sorted(opt))} if the fix needs it)"
        lines += ["", fix]
        if cand.spec_in is not None and ("SPEC" in req or "SPEC" in opt):
            lines.append("A returned ===SPEC=== may contain only the top-level keys you change; "
                         "each replaces the old value (e.g. {\"tests\":[...]}).")
        if "COMPUTE" in req and "RENDER" not in req:
            lines.append("Keep the keys of r that RENDER uses.")
        full_spec = "SPEC" in req
        # read-only context (compact)
        ctx = []
        if not full_spec and cand.spec is not None:
            compact = {k: cand.spec.get(k) for k in ("controls", "intermediates", "invariants", "tests")}
            compact["explorations"] = [{"preset": e.get("preset"), "watch": e.get("watch")}
                                       for e in cand.spec.get("explorations", [])]
            ctx.append("===SPEC=== (excerpt)\n" + json.dumps(compact, ensure_ascii=False, separators=(",", ":")))
        if "RENDER" in req and "COMPUTE" not in req and cand.compute:
            ctx.append("===COMPUTE===\n" + cand.compute)
        if ctx:
            lines += ["", "CONTEXT:"] + ctx
        lines += ["", "CURRENT (return corrected versions):"]
        for sec in order + sorted(s for s in opt if s != "SPEC"):
            if sec == "SPEC":
                if cand.spec_in is not None:
                    body = json.dumps(cand.spec_in, ensure_ascii=False, separators=(",", ":"))
                else:
                    raw = (cand.parsed.spec_raw if cand.parsed else None) or ""
                    body = raw[:8000] if raw.strip() else "(missing: write the full SPEC)"
            elif sec == "COMPUTE":
                body = cand.compute or "(missing: write it)"
            else:
                body = cand.render or "(missing: write it)"
            lines.append(f"==={sec}===\n{body}")
        return "\n".join(lines)

    def apply_repair(self, base: Candidate, text: str, label: str) -> Candidate:
        sections, _ = split_sections(text)
        if not sections:
            p = parse_output(text)  # fallback extraction
            sections = {}
            if p.spec is not None:
                sections["SPEC"] = json.dumps(p.spec)
            if p.compute:
                sections["COMPUTE"] = p.compute
            if p.render:
                sections["RENDER"] = p.render
        new = Candidate(label=label, spec_in=base.spec_in, spec_error=base.spec_error,
                        compute=base.compute, render=base.render, parsed=None)
        replaced = []
        if sections.get("SPEC", "").strip():
            obj, err, _ = parse_json_lenient(sections["SPEC"])
            if obj is not None:
                if isinstance(base.spec_in, dict):   # patch: returned top-level keys replace old ones
                    merged_spec = dict(base.spec_in)
                    merged_spec.update(obj)
                    obj = merged_spec
                new.spec_in, new.spec_error = obj, None
                replaced.append("SPEC")
            else:
                self.trace.event("repair", "apply", "fail", candidate=label, detail=f"returned SPEC invalid: {err}")
        if sections.get("COMPUTE", "").strip():
            new.compute = strip_fences(sections["COMPUTE"])
            replaced.append("COMPUTE")
        if sections.get("RENDER", "").strip():
            new.render = strip_fences(sections["RENDER"])
            replaced.append("RENDER")
        # synthesise protocol status for S3 from the merged candidate
        merged = Parsed(spec=new.spec_in, spec_error=new.spec_error, compute=new.compute, render=new.render)
        merged.found = [s for s, v in (("SPEC", new.spec_in), ("COMPUTE", new.compute), ("RENDER", new.render)) if v]
        merged.has_end = True
        new.parsed = merged
        self.trace.event("repair", "apply", "ok" if replaced else "fail", candidate=label, replaced=replaced)
        return new

    @staticmethod
    def better(new: Candidate, old: Candidate) -> bool:
        if new.score <= old.score:
            return False
        if new.accepted and not old.accepted:
            return True
        old_pass = {c["id"] for c in old.checks if c["result"] == "pass"}
        regressed = [c["id"] for c in new.checks if c["id"] in old_pass and c["result"] == "fail"]
        return not regressed

    # ------------------------------------------------------------------ main
    def execute(self) -> int:
        tr = self.trace
        self.src = load_source(self.case, self.case_dir, allow_network=self.allow_network)
        tr.event("load", "resolve_source", "ok" if self.src["origin"] != "none" else "skip",
                 origin=self.src["origin"], chars=len(self.src["text"]), detail=self.src["detail"][:300])
        best = None
        reason = ""
        try:
            client = OpenRouterClient(self.api_key, self.model, self.budget, tr, session=self.session,
                                      **({"sleep": self.sleep} if self.sleep else {}))
        except NoAPIKey as e:
            client = None
            reason = str(e)
            tr.event("generate", "llm_call", "error", detail=reason)

        if client is not None:
            sys_msg = system_prompt()
            user_msg = build_user_message(self.case, self.src)
            try:
                res = client.chat([{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}],
                                  GEN_CAP, stage="generate", purpose="main", min_tokens=2000)
                best = self.evaluate(self.candidate_from_text(res.text, "gen"))
            except (BudgetStop, LLMError) as e:
                reason = f"generation failed: {e}"
                tr.event("generate", "give_up", "error", detail=str(e)[:300])

            n_rep = 0
            while best is not None and n_rep < MAX_REPAIRS and chk.failing(best.checks):
                req, opt = self.repair_targets(best)
                if not req:
                    tr.event("repair", "plan", "skip", detail="failures not repairable by the model",
                             failing=[c["id"] for c in chk.failing(best.checks)])
                    break
                n_rep += 1
                label = f"repair{n_rep}"
                nothing = best.spec_in is None and not best.compute and not best.render
                tr.event("repair", "plan", "ok", candidate=label, targets=sorted(req), optional=sorted(opt),
                         mode="regenerate" if nothing else "targeted")
                try:
                    if nothing:
                        msgs = [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}]
                        res = client.chat(msgs, GEN_CAP, stage="repair", purpose=label, min_tokens=2000)
                        cand = self.candidate_from_text(res.text, label)
                    else:
                        rsys = load_prompt("repair.md")
                        if "RENDER" in req or "RENDER" in opt:
                            rsys += "\n\n" + load_prompt("kit.md")
                        msgs = [{"role": "system", "content": rsys},
                                {"role": "user", "content": self.repair_message(best, req, opt)}]
                        res = client.chat(msgs, REPAIR_CAP, stage="repair", purpose=label, min_tokens=800)
                        cand = self.apply_repair(best, res.text, label)
                except BudgetStop as e:
                    tr.event("repair", "stop", "skip", detail=str(e)[:300])
                    break
                except LLMError as e:
                    tr.event("repair", "give_up", "error", detail=str(e)[:300])
                    break
                self.revisions += 1
                cand = self.evaluate(cand)
                if self.better(cand, best):
                    tr.event("promote", "select_best", "ok", candidate=cand.label, previous=best.label,
                             score=list(cand.score), previous_score=list(best.score))
                    best = cand
                else:
                    tr.event("promote", "select_best", "skip", candidate=cand.label, kept=best.label,
                             score=list(cand.score), kept_score=list(best.score),
                             detail="no improvement or a previously passing check regressed")

        return self.write_outputs(best, reason)

    def write_outputs(self, best, reason: str) -> int:
        tr = self.trace
        out_html = self.out_dir / "index.html"
        page, kind = None, "fallback"
        if best is not None and best.spec is not None and best.html is not None:
            try:
                page = assemble(best.spec, best.compute, best.render, self.build(best.checks), self.shell_dir)
                kind = "assembled"
            except (ShellMissing, OSError) as e:
                reason = reason or f"assembly failed: {e}"
        if page is None:
            if best is not None and not reason:
                reason = "no valid page could be assembled (" + ", ".join(
                    c["id"] for c in chk.failing(best.checks)) + " failed)"
            page = build_fallback(self.case, best.spec if best else None, reason=reason,
                                  origin=self.src["origin"], model=self.model,
                                  checks=best.checks if best else None)
        for s in self.secrets:
            if s and s in page:
                page = page.replace(s, "[redacted]")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        out_html.write_text(page, encoding="utf-8", newline="\n")
        ok = best is not None and kind == "assembled" and best.accepted
        tr.event("output", "write_index_html", "ok", kind=kind, bytes=len(page),
                 candidate=best.label if best else None, accepted=ok)
        totals = self.budget.totals()
        totals["revisions"] = self.revisions
        tr.event("output", "totals", "pass" if ok else "fail", totals=totals,
                 checks={c["id"]: c["result"] for c in (best.checks if best else [])},
                 failures=[f"{c['id']}: {c['detail'][:160]}" for c in (chk.failing(best.checks) if best else [])],
                 reason=reason or None)
        return 0 if ok else 1
