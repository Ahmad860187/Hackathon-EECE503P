"""Deterministic SPEC normalisation (0 tokens): fixes shapes the shell relies on and
enforces grounding honesty before checks run. Every change is reported as a note."""

from __future__ import annotations

import copy
import math
import re

SUPPORTS = ("excerpt", "paper", "illustration")
CONTROL_TYPES = ("range", "number", "toggle", "select", "vector", "matrix")
_TYPE_ALIASES = {"slider": "range", "float": "range", "continuous": "range", "int": "number",
                 "integer": "number", "checkbox": "toggle", "bool": "toggle", "boolean": "toggle",
                 "switch": "toggle", "dropdown": "select", "choice": "select", "enum": "select",
                 "radio": "select", "array": "vector", "list": "vector"}
_IDENT = re.compile(r"^[A-Za-z_$][\w$]*$")


def _num(v, default=None):
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)) and math.isfinite(v):
        return v
    if isinstance(v, str):
        try:
            f = float(v.strip())
            if math.isfinite(f):
                return int(f) if f.is_integer() and "." not in v else f
        except ValueError:
            pass
    return default


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return ""
    return str(v).strip()


def option_values(c: dict) -> list:
    return [o.get("value") if isinstance(o, dict) and "value" in o else o for o in c.get("options", [])]


def _fix_support(obj: dict, origin: str, notes: list, where: str) -> None:
    sup = _s(obj.get("support")).lower()
    if sup not in SUPPORTS:
        if sup:
            notes.append(f"{where}: unknown support '{sup}' -> illustration")
        sup = "illustration"
    if sup == "excerpt" and origin == "none":
        notes.append(f"{where}: 'excerpt' label downgraded to 'paper' (no excerpt available)")
        sup = "paper"
    obj["support"] = sup


def _fix_control(c: dict, notes: list):
    cid = _s(c.get("id"))
    if not cid:
        return None
    if not _IDENT.match(cid):
        new = re.sub(r"\W", "_", cid)
        if not re.match(r"[A-Za-z_$]", new):
            new = "c_" + new
        notes.append(f"control id '{cid}' renamed '{new}'")
        cid = new
    c["id"] = cid
    t = _s(c.get("type")).lower()
    t = _TYPE_ALIASES.get(t, t)
    if t not in CONTROL_TYPES:
        if isinstance(c.get("default"), bool):
            t = "toggle"
        elif isinstance(c.get("options"), list):
            t = "select"
        elif isinstance(c.get("default"), list):
            t = "matrix" if c["default"] and isinstance(c["default"][0], list) else "vector"
        else:
            t = "range"
        notes.append(f"control '{cid}': type set to '{t}'")
    c["type"] = t
    c["label"] = _s(c.get("label")) or cid
    if t in ("range", "number"):
        d = _num(c.get("default"))
        lo, hi = _num(c.get("min")), _num(c.get("max"))
        if lo is None and hi is None:
            base = abs(d) if d not in (None, 0) else 1
            lo, hi = (0 if (d is None or d >= 0) else -2 * base), 2 * base
            notes.append(f"control '{cid}': missing min/max -> [{lo}, {hi}]")
        elif lo is None:
            lo = min(0, hi - 1)
        elif hi is None:
            hi = lo + (abs(lo) if lo else 1) * 2
        if lo > hi:
            lo, hi = hi, lo
        if lo == hi:
            hi = lo + 1
        c["min"], c["max"] = lo, hi
        step = _num(c.get("step"))
        if step is None or step <= 0:
            step = (hi - lo) / 100
            if all(isinstance(x, int) for x in (lo, hi)) and (hi - lo) <= 100:
                step = 1
            c["step"] = step
        if d is None:
            d = lo
            notes.append(f"control '{cid}': missing default -> min")
        if d < lo or d > hi:
            notes.append(f"control '{cid}': default {d} clamped to [{lo}, {hi}]")
            d = min(max(d, lo), hi)
        c["default"] = d
    elif t == "toggle":
        c["default"] = bool(c.get("default", False))
    elif t == "select":
        opts = c.get("options")
        if not isinstance(opts, list) or not opts:
            return None
        vals = option_values(c)
        if c.get("default") not in vals:
            if "default" in c:
                notes.append(f"control '{cid}': default not in options -> first option")
            c["default"] = vals[0]
    elif t == "vector":
        d = c.get("default")
        if not isinstance(d, list) or not d:
            n = int(_num(c.get("length"), 3) or 3)
            d = [0] * max(1, min(n, 12))
        d = [_num(x, 0) for x in d]
        c["default"] = d
        c["length"] = len(d)
    elif t == "matrix":
        d = c.get("default")
        if not isinstance(d, list) or not d or not all(isinstance(r, list) and r for r in d):
            rows = int(_num(c.get("rows"), 2) or 2)
            cols = int(_num(c.get("cols"), 2) or 2)
            d = [[0] * cols for _ in range(rows)]
        width = max(len(r) for r in d)
        d = [[_num(x, 0) for x in r] + [0] * (width - len(r)) for r in d]
        c["default"] = d
        c["rows"], c["cols"] = len(d), width
    return c


TOP_LEVEL_KEYS = ("title", "paper", "idea", "why", "symbols", "equations", "controls", "intermediates",
                  "visual", "explorations", "limitation", "claims", "invariants", "tests")
# Only structural keys that cannot legitimately appear nested (e.g. never hoist explorations[0].why).
HOISTABLE_KEYS = ("symbols", "equations", "controls", "intermediates", "explorations", "limitation", "map",
                  "claims", "invariants", "tests")


def _find_nested(obj, key, path="", depth=0):
    """Breadth-first search for a dict (below the top level) holding `key`; returns (dict, path)."""
    queue = [(v, f"{k}") for k, v in obj.items() if isinstance(v, (dict, list))]
    while queue:
        node, where = queue.pop(0)
        if isinstance(node, dict):
            if key in node:
                return node, where
            queue.extend((v, f"{where}.{k}") for k, v in node.items() if isinstance(v, (dict, list)))
        else:
            queue.extend((v, f"{where}[{i}]") for i, v in enumerate(node) if isinstance(v, (dict, list)))
    return None


def normalize_spec(spec: dict, case: dict, origin: str) -> tuple:
    """Return (normalised copy, notes)."""
    notes = []
    s = copy.deepcopy(spec) if isinstance(spec, dict) else {}
    # Models sometimes nest top-level keys inside a sibling object (seen: "explorations" inside
    # "visual"). Hoist any missing top-level key found one level down.
    for key in HOISTABLE_KEYS:
        if key in s:
            continue
        found = _find_nested(s, key)
        if found:
            holder, where = found
            s[key] = holder.pop(key)
            notes.append(f"'{key}' was nested inside '{where}'; moved to top level")
    s["title"] = _s(s.get("title")) or _s(case.get("focus"))[:80] or "Interactive explainer"
    paper = s.get("paper") if isinstance(s.get("paper"), dict) else {}
    for k in ("title", "authors", "year", "url", "section"):
        paper[k] = _s(paper.get(k))
    src = _s(case.get("source_url"))
    if re.match(r"(?i)^https?://", src) and paper["url"] != src:
        if paper["url"]:
            notes.append("paper.url replaced by the case source_url")
        paper["url"] = src
    elif paper["url"] and not re.match(r"(?i)^https?://", paper["url"]):
        paper["url"] = ""
    s["paper"] = paper
    for k in ("idea", "why"):
        s[k] = _s(s.get(k))

    s["symbols"] = [x for x in (s.get("symbols") or []) if isinstance(x, dict)] \
        if isinstance(s.get("symbols"), list) else []
    eqs = []
    for i, e in enumerate(s.get("equations") or [] if isinstance(s.get("equations"), list) else []):
        if isinstance(e, dict) and _s(e.get("expr")):
            _fix_support(e, origin, notes, f"equations[{i}]")
            eqs.append(e)
    s["equations"] = eqs

    controls, seen = [], set()
    raw_controls = s.get("controls") if isinstance(s.get("controls"), list) else []
    for c in raw_controls:
        if not isinstance(c, dict):
            continue
        fc = _fix_control(c, notes)
        if fc is None:
            notes.append("dropped an invalid control")
            continue
        if fc["id"] in seen:
            notes.append(f"dropped duplicate control '{fc['id']}'")
            continue
        seen.add(fc["id"])
        controls.append(fc)
    s["controls"] = controls

    inter = []
    for x in s.get("intermediates") or [] if isinstance(s.get("intermediates"), list) else []:
        if isinstance(x, str):
            x = {"key": x, "label": x}
        if isinstance(x, dict) and _s(x.get("key")):
            x["key"] = _s(x["key"])
            x["label"] = _s(x.get("label")) or x["key"]
            inter.append(x)
    s["intermediates"] = inter

    vis = s.get("visual") if isinstance(s.get("visual"), dict) else {}
    s["visual"] = {"caption": _s(vis.get("caption")), "how_to_read": _s(vis.get("how_to_read"))}

    exps = []
    for e in s.get("explorations") or [] if isinstance(s.get("explorations"), list) else []:
        if not isinstance(e, dict):
            continue
        if not isinstance(e.get("preset"), dict):
            e["preset"] = {}
        for k in ("title", "predict", "watch", "observe", "why"):
            e[k] = _s(e.get(k))
        exps.append(e)
    if len(exps) > 2:
        notes.append(f"trimmed explorations {len(exps)} -> 2")
        exps = exps[:2]
    s["explorations"] = exps

    lim = s.get("limitation")
    if isinstance(lim, str):
        lim = {"kind": "limitation", "text": lim}
    if not isinstance(lim, dict):
        lim = {"kind": "limitation", "text": ""}
    kind = _s(lim.get("kind")).lower()
    lim["kind"] = kind if kind in ("limitation", "assumption", "misconception") else "limitation"
    lim["text"] = _s(lim.get("text"))
    s["limitation"] = lim

    claims = []
    for i, c in enumerate(s.get("claims") or [] if isinstance(s.get("claims"), list) else []):
        if isinstance(c, str):
            c = {"text": c}
        if isinstance(c, dict) and _s(c.get("text")):
            _fix_support(c, origin, notes, f"claims[{i}]")
            claims.append(c)
    s["claims"] = claims

    def _exprs(key, name_key):
        out = []
        for x in s.get(key) or [] if isinstance(s.get(key), list) else []:
            if isinstance(x, dict):
                expr = _s(x.get("expr") or x.get("assert"))
                if expr:
                    x["expr"] = expr
                    x[name_key] = _s(x.get(name_key)) or expr[:60]
                    if key == "tests" and not isinstance(x.get("params"), dict):
                        x["params"] = {}
                    out.append(x)
        return out

    s["invariants"] = _exprs("invariants", "label")
    s["tests"] = _exprs("tests", "name")
    _snap_steps(s, notes)
    _clean_map(s, notes)
    return s, notes


def _clean_map(s: dict, notes: list) -> None:
    """Optional mechanism map (drawn by the shell with kit.flow). Keep only well-formed parts."""
    m = s.get("map")
    if not isinstance(m, dict):
        s.pop("map", None)
        return
    nodes, seen = [], set()
    for n in m.get("nodes") or []:
        if isinstance(n, dict) and _s(n.get("id")) and _s(n.get("id")) not in seen:
            seen.add(_s(n["id"]))
            nodes.append({"id": _s(n["id"]), "label": _s(n.get("label")) or _s(n["id"]), "key": _s(n.get("key"))})
    edges = [{"from": _s(e.get("from")), "to": _s(e.get("to")), "label": _s(e.get("label"))}
             for e in (m.get("edges") or []) if isinstance(e, dict)
             and _s(e.get("from")) in seen and _s(e.get("to")) in seen]
    if len(nodes) < 2:
        s.pop("map", None)
        notes.append("map dropped (fewer than 2 valid nodes)")
        return
    s["map"] = {"nodes": nodes[:8], "edges": edges[:12]}


def _snap_steps(s: dict, notes: list) -> None:
    """A range/number slider can only land on min + k*step. If the default, a preset or a test value
    is off that grid, the page would silently show and compute a different value, so refine the step."""
    for c in s.get("controls", []):
        if c.get("type") not in ("range", "number") or not isinstance(c.get("step"), (int, float)):
            continue
        vals = [c.get("default")]
        vals += [(e.get("preset") or {}).get(c["id"]) for e in s.get("explorations", [])]
        vals += [(t.get("params") or {}).get(c["id"]) for t in s.get("tests", [])]
        vals = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        lo, step = c.get("min", 0), c["step"]

        def on_grid(st):
            return all(abs((v - lo) / st - round((v - lo) / st)) < 1e-6 for v in vals)

        orig = step
        for _ in range(4):
            if on_grid(step):
                break
            step = step / 10
        if not on_grid(step):
            step = (c.get("max", lo + 1) - lo) / 10000 or orig
        if step != orig:
            c["step"] = float(f"{step:.12g}")
            notes.append(f"control '{c['id']}' step {orig} -> {c['step']} so its default/preset/test values are reachable")


def default_params(spec: dict) -> dict:
    return {c["id"]: copy.deepcopy(c.get("default")) for c in spec.get("controls", [])}


def merged_params(spec: dict, partial) -> dict:
    p = default_params(spec)
    ranges = {c.get("id"): c for c in spec.get("controls", []) if c.get("type") in ("range", "number")}
    if isinstance(partial, dict):
        for k, v in partial.items():
            c = ranges.get(k)
            if c and isinstance(v, (int, float)) and not isinstance(v, bool)                     and isinstance(c.get("min"), (int, float)) and isinstance(c.get("max"), (int, float)):
                v = min(max(v, c["min"]), c["max"])   # the page's slider cannot go outside its range
            p[k] = copy.deepcopy(v)
    return p
