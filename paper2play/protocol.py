"""Parse the delimited model output (CONTRACT §1).

Robust to: leading prose, markdown code fences, missing ===END===, marker spacing/case,
JSON with comments or trailing commas, and (as a last resort) outputs with no markers at all.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

SECTIONS = ("SPEC", "COMPUTE", "RENDER")
_MARKER = re.compile(r"^[ \t>*#`]*={2,}\s*(SPEC|COMPUTE|RENDER|END)\s*={2,}[ \t`*]*$", re.I | re.M)
_FENCE_OPEN = re.compile(r"^\s*```[\w+\-]*[ \t]*\n")
_FENCE_CLOSE = re.compile(r"\n?[ \t]*```[ \t]*$")


@dataclass
class Parsed:
    spec: dict | None = None
    spec_raw: str | None = None
    spec_error: str | None = None
    compute: str | None = None
    render: str | None = None
    found: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    has_end: bool = False


def strip_fences(s: str) -> str:
    s = s.strip("\n")
    s2 = _FENCE_OPEN.sub("", s, count=1)
    if s2 != s:
        s = _FENCE_CLOSE.sub("", s2)
    else:
        s = _FENCE_CLOSE.sub("", s)
    # a stray fence line anywhere at the edges
    lines = s.split("\n")
    while lines and lines[0].strip().startswith("```"):
        lines.pop(0)
    while lines and lines[-1].strip().startswith("```"):
        lines.pop()
    return "\n".join(lines).strip()


def split_sections(text: str) -> tuple:
    """Return ({name: content}, has_end). Later non-empty occurrences win."""
    out = {}
    has_end = False
    marks = list(_MARKER.finditer(text or ""))
    for i, m in enumerate(marks):
        name = m.group(1).upper()
        if name == "END":
            has_end = True
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = strip_fences(text[m.end():end])
        if body.strip():
            out[name] = body
        elif name not in out:
            out[name] = ""
    return out, has_end


# ---------------------------------------------------------------------------- JSON
def _strip_json_comments_and_commas(s: str) -> str:
    out = []
    i, n = 0, len(s)
    in_str = False
    quote = ""
    while i < n:
        c = s[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(s[i + 1])
                i += 2
                continue
            if c == quote:
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str, quote = True, c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and s[i + 1] == "/":
            j = s.find("\n", i)
            i = n if j == -1 else j
            continue
        if c == "/" and i + 1 < n and s[i + 1] == "*":
            j = s.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if c == ",":
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j < n and s[j] in "}]":
                i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def _balanced_object(s: str, start: int):
    """Return the first brace-balanced {...} starting at `start` (string-aware), or None."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def _close_truncated(s: str) -> str:
    """Append closers for a JSON object cut off mid-way (best effort)."""
    stack = []
    in_str = False
    esc = False
    for c in s:
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            stack.append("}" if c == "{" else "]")
        elif c in "}]" and stack:
            stack.pop()
    t = s
    if in_str:
        t += '"'
    t = re.sub(r",\s*$", "", t.rstrip())
    t = re.sub(r",?\s*\"[^\"]*\"\s*:\s*$", "", t)  # dangling key
    return t + "".join(reversed(stack))


# LaTeX commands whose leading backslash JSON would silently decode as a control character
# (backslash-f + 'rac' in a frac command, backslash-t + 'heta', ...): double those backslashes.
_LATEX_ESC = re.compile(r"(?<!\\)\\(?=(?:frac|theta|tau|times|text|tilde|top|beta|bar|boldsymbol|bigl|bigr|"
                        r"nu|nabla|neq|ne|rho|right|rightarrow|rangle|forall|binom|bmod|nmid|rm|bf|bm)(?![A-Za-z]))")
_BAD_ESC = re.compile(r'(?<!\\)\\(?!["\\/bfnrtu])')


def parse_json_lenient(s: str):
    """Return (obj, error_or_None, repaired_flag)."""
    if s is None:
        return None, "empty", False
    s = strip_fences(s)
    s = _LATEX_ESC.sub(r"\\\\", s)
    start = s.find("{")
    if start == -1:
        return None, "no JSON object found", False
    body = _balanced_object(s, start)
    if body is None:
        end = s.rfind("}")
        body = s[start:end + 1] if end > start else s[start:]
    try:
        obj = json.loads(body, strict=False)
        if isinstance(obj, dict):
            return obj, None, False
    except ValueError as e:
        first_err = f"{e.msg} at line {e.lineno} col {e.colno}"
    else:
        return None, "JSON is not an object", False
    clean = _strip_json_comments_and_commas(_strip_json_comments_and_commas(body))
    tail = _strip_json_comments_and_commas(_strip_json_comments_and_commas(s[start:]))
    for candidate in (clean, _close_truncated(tail), _BAD_ESC.sub(r"\\\\", clean),
                      _close_truncated(_BAD_ESC.sub(r"\\\\", tail))):
        try:
            obj = json.loads(candidate, strict=False)
            if isinstance(obj, dict):
                return obj, None, True
        except ValueError:
            continue
    return None, f"invalid JSON: {first_err}", False


# ---------------------------------------------------------------------------- fallback extraction
def extract_function(text: str, name: str):
    """Find `function name(...) {...}` (or const name = ...) with brace matching that skips
    strings, template literals and comments. Returns source or None."""
    m = re.search(r"(?m)^[ \t]*(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(", text or "")
    if not m:
        m = re.search(r"(?m)^[ \t]*(?:const|let|var)\s+" + re.escape(name) + r"\s*=", text or "")
    if not m:
        return None
    i = text.find("{", m.end())
    if i == -1:
        return None
    depth = 0
    j = i
    n = len(text)
    while j < n:
        c = text[j]
        if c in "\"'`":
            q = c
            j += 1
            while j < n and text[j] != q:
                if text[j] == "\\":
                    j += 1
                j += 1
        elif c == "/" and j + 1 < n and text[j + 1] == "/":
            k = text.find("\n", j)
            j = n if k == -1 else k
            continue
        elif c == "/" and j + 1 < n and text[j + 1] == "*":
            k = text.find("*/", j + 2)
            j = n if k == -1 else k + 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[m.start():j + 1].strip()
        j += 1
    return None


def parse_output(text: str) -> Parsed:
    p = Parsed()
    sections, p.has_end = split_sections(text or "")
    if not sections:
        p.notes.append("no section markers; used fallback extraction")
        spec_obj, err, _ = parse_json_lenient(text or "")
        if spec_obj is not None:
            sections["SPEC"] = json.dumps(spec_obj)
        c = extract_function(text, "compute")
        r = extract_function(text, "render")
        if c:
            sections["COMPUTE"] = c
        if r:
            sections["RENDER"] = r
    p.found = [s for s in SECTIONS if sections.get(s, "").strip()]
    if not p.has_end and sections:
        p.notes.append("missing ===END=== (possibly truncated)")
    raw_spec = sections.get("SPEC")
    p.spec_raw = raw_spec
    if raw_spec and raw_spec.strip():
        obj, err, repaired = parse_json_lenient(raw_spec)
        p.spec, p.spec_error = obj, err
        if repaired:
            p.notes.append("SPEC JSON repaired (comments/trailing commas/truncation)")
    else:
        p.spec_error = "SPEC section missing"
    comp = sections.get("COMPUTE", "").strip() or None
    rend = sections.get("RENDER", "").strip() or None
    p.compute, p.render = comp, rend
    return p
