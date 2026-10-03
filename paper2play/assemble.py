"""Assemble one self-contained HTML page from the fixed shell + generated code (CONTRACT §5)."""

from __future__ import annotations

import json
import re
from pathlib import Path

SHELL_DIR = Path(__file__).resolve().parent / "shell"
PLACEHOLDERS = ("STYLE", "DATA", "KIT", "COMPUTE", "RENDER", "APP")
_PH_RE = re.compile(r"/\*@(STYLE|DATA|KIT|COMPUTE|RENDER|APP)@\*/")

# URLs allowed inside code: XML namespaces are identifiers, not fetches.
_URL_RE = re.compile(r"(?i)\bhttps?://(?!www\.w3\.org/)[a-z0-9][\w.\-]*\.[a-z]{2,}[^\s'\"`)<>]*")
_SCRIPT_TAG_RE = re.compile(r"(?is)<\s*script\b[^>]*>|<\s*/\s*script\s*>")
_TAG_IN_TEXT_RE = re.compile(
    r"(?is)<\s*/?\s*(script|img|iframe|link|style|object|embed|frame|meta|base|video|audio|source|form|input)\b[^>]*>")


class ShellMissing(Exception):
    pass


def shell_available(shell_dir=None) -> bool:
    d = Path(shell_dir) if shell_dir else SHELL_DIR
    return (d / "shell.html").is_file()


def escape_script(code: str) -> str:
    """Make arbitrary JS safe inside an inline <script>: no `</script` or `<!--`."""
    code = re.sub(r"(?i)</(script)", r"<\\/\1", code)
    code = code.replace("<!--", "<\\!--")
    return code


def json_embed(obj) -> str:
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    s = s.replace("</", "<\\/").replace("<!--", "<\\u0021--")
    s = s.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return s


def sanitize_code(js: str) -> tuple:
    """Strip <script> tags and remote URLs from generated JS. Returns (code, notes)."""
    notes = []
    if not js:
        return "", notes
    new = _SCRIPT_TAG_RE.sub("", js)
    if new != js:
        notes.append("removed <script> tag text")
    js = new
    urls = _URL_RE.findall(js)
    if urls:
        js = _URL_RE.sub("", js)
        notes.append(f"removed {len(urls)} remote URL(s)")
    return js, notes


def sanitize_spec_text(obj, keep_url: str = ""):
    """Remove HTML tags that could load resources from any string in SPEC."""
    if isinstance(obj, str):
        return _TAG_IN_TEXT_RE.sub("", obj)
    if isinstance(obj, list):
        return [sanitize_spec_text(v, keep_url) for v in obj]
    if isinstance(obj, dict):
        return {k: sanitize_spec_text(v, keep_url) for k, v in obj.items()}
    return obj


def _read(d: Path, name: str) -> str:
    p = d / name
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def assemble(spec: dict, compute_js: str, render_js: str, build: dict, shell_dir=None) -> str:
    d = Path(shell_dir) if shell_dir else SHELL_DIR
    shell_path = d / "shell.html"
    if not shell_path.is_file():
        raise ShellMissing(f"{shell_path} not found")
    shell = shell_path.read_text(encoding="utf-8")
    style = _read(d, "style.css").replace("</style", "<\\/style")
    kit = _read(d, "kit.js")
    app = _read(d, "app.js")
    compute_js, _ = sanitize_code(compute_js or "")
    render_js, _ = sanitize_code(render_js or "")
    data = f"const SPEC = {json_embed(spec)};\nconst BUILD = {json_embed(build)};\n"
    parts = {
        "STYLE": style,
        "DATA": data,
        "KIT": escape_script(kit),
        "COMPUTE": escape_script(compute_js or "function compute(p){ return {}; }"),
        "RENDER": escape_script(render_js or "function render(r, p, kit, el){}"),
        "APP": escape_script(app),
    }
    # single pass: inserted content is never re-scanned for placeholders
    return _PH_RE.sub(lambda m: parts[m.group(1)], shell)


def missing_placeholders(shell_dir=None) -> list:
    d = Path(shell_dir) if shell_dir else SHELL_DIR
    p = d / "shell.html"
    if not p.is_file():
        return list(PLACEHOLDERS)
    s = p.read_text(encoding="utf-8")
    return [ph for ph in PLACEHOLDERS if f"/*@{ph}@*/" not in s]
