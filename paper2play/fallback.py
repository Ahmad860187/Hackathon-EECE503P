"""Deterministic, self-contained fallback page (no model output required).

Used when no interactive page could be assembled. It shows whatever SPEC text exists (or
just the case) and is clearly labelled as incomplete. No scripts, no external resources.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone

_CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1f;--muted:#5d5d66;--card:#fff;--line:#e3e1da;--warn:#9a5b00;--warnbg:#fff4df}
@media (prefers-color-scheme:dark){:root{--bg:#141417;--fg:#ececf1;--muted:#a3a3ad;--card:#1d1d22;--line:#33333b;--warn:#ffcf7a;--warnbg:#2c2414}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px 48px}h1{font-size:1.6rem;margin:.2em 0}h2{font-size:1.1rem;margin:1.6em 0 .4em}
.banner{background:var(--warnbg);color:var(--warn);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:12px 0 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:8px 0}
.muted{color:var(--muted)}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
code{font-family:ui-monospace,Consolas,monospace;font-size:.92em}.badge{font-size:.8em;padding:1px 6px;border-radius:6px;border:1px solid var(--line);white-space:nowrap}
"""

_BADGE = {"excerpt": "📄 excerpt", "paper": "🧠 paper, not verified", "illustration": "🧪 illustration"}


def _e(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def build_fallback(case: dict | None, spec: dict | None = None, reason: str = "",
                   origin: str = "none", model: str = "", checks=None) -> str:
    case = case or {}
    spec = spec if isinstance(spec, dict) else {}
    title = spec.get("title") or case.get("focus") or "Paper to Playground"
    paper = spec.get("paper") if isinstance(spec.get("paper"), dict) else {}
    url = paper.get("url") or case.get("source_url") or ""
    parts = [f"<h1>{_e(title)}</h1>"]
    parts.append('<div class="banner" role="status"><strong>Incomplete page.</strong> The interactive '
                 'playground could not be generated for this run, so only the explanatory text that was '
                 f'available is shown.{(" Reason: " + _e(reason)) if reason else ""}</div>')
    meta = []
    if case.get("focus"):
        meta.append(f"<div><strong>Focus:</strong> {_e(case.get('focus'))}</div>")
    if case.get("audience"):
        meta.append(f"<div><strong>Audience:</strong> {_e(case.get('audience'))}</div>")
    if meta:
        parts.append('<div class="card">' + "".join(meta) + "</div>")
    if spec.get("idea") or spec.get("why"):
        parts.append("<h2>Idea and why it matters</h2>")
        if spec.get("idea"):
            parts.append(f"<p>{_e(spec['idea'])}</p>")
        if spec.get("why"):
            parts.append(f"<p class=\"muted\">{_e(spec['why'])}</p>")
    syms = [s for s in spec.get("symbols", []) if isinstance(s, dict)] if isinstance(spec.get("symbols"), list) else []
    if syms:
        rows = "".join(f"<tr><td><code>{_e(s.get('sym'))}</code></td><td>{_e(s.get('meaning'))}</td></tr>" for s in syms)
        parts.append(f"<h2>Symbols</h2><table>{rows}</table>")
    eqs = [q for q in spec.get("equations", []) if isinstance(q, dict)] if isinstance(spec.get("equations"), list) else []
    if eqs:
        parts.append("<h2>Equations</h2>")
        for q in eqs:
            badge = _BADGE.get(q.get("support"), "")
            parts.append(f'<div class="card"><div class="muted">{_e(q.get("label"))} '
                         f'<span class="badge">{_e(badge)}</span></div><code>{_e(q.get("expr"))}</code></div>')
    ctrls = [c for c in spec.get("controls", []) if isinstance(c, dict)] if isinstance(spec.get("controls"), list) else []
    if ctrls:
        rows = "".join(f"<tr><td>{_e(c.get('label') or c.get('id'))}</td><td><code>{_e(c.get('default'))}</code></td>"
                       f"<td class=\"muted\">{_e(c.get('help'))}</td></tr>" for c in ctrls)
        parts.append(f"<h2>Parameters (not interactive in this fallback)</h2><table>{rows}</table>")
    exps = [x for x in spec.get("explorations", []) if isinstance(x, dict)] if isinstance(spec.get("explorations"), list) else []
    if exps:
        parts.append("<h2>Things to explore</h2>")
        for x in exps:
            parts.append(f'<div class="card"><strong>{_e(x.get("title"))}</strong><p>{_e(x.get("predict"))}</p>'
                         f'<p>{_e(x.get("observe"))}</p><p class="muted">{_e(x.get("why"))}</p></div>')
    lim = spec.get("limitation") if isinstance(spec.get("limitation"), dict) else None
    if lim and lim.get("text"):
        parts.append(f"<h2>{_e((lim.get('kind') or 'limitation').capitalize())}</h2><p>{_e(lim['text'])}</p>")
    parts.append("<h2>Source</h2>")
    cite = " · ".join(x for x in (paper.get("title"), paper.get("authors"), paper.get("year"), paper.get("section")) if x)
    if cite:
        parts.append(f"<p>{_e(cite)}</p>")
    if url.startswith(("http://", "https://")):
        parts.append(f'<p><a href="{_e(url)}">{_e(url)}</a></p>')
    elif url:
        parts.append(f"<p><code>{_e(url)}</code></p>")
    claims = [c for c in spec.get("claims", []) if isinstance(c, dict)] if isinstance(spec.get("claims"), list) else []
    if claims:
        parts.append("<ul>" + "".join(f'<li>{_e(c.get("text"))} <span class="badge">{_e(_BADGE.get(c.get("support"), ""))}</span></li>'
                                      for c in claims) + "</ul>")
    note = {"inline": "Grounded in an excerpt supplied with the case.",
            "file": "Grounded in an excerpt read from a local file.",
            "fetched": "Grounded in text fetched from the source URL.",
            "none": "No source excerpt was available; statements come from the cited paper and are not verified against it."}
    parts.append(f'<p class="muted">{_e(note.get(origin, note["none"]))}</p>')
    if checks:
        rows = "".join(f"<tr><td><code>{_e(c.get('id'))}</code></td><td>{_e(c.get('result'))}</td>"
                       f"<td class=\"muted\">{_e(c.get('detail'))}</td></tr>" for c in checks)
        parts.append(f"<h2>Checks</h2><table>{rows}</table>")
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts.append(f'<p class="muted">Generated {gen}{(" · model " + _e(model)) if model else ""} · fallback page</p>')
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{_e(title)} (incomplete)</title><style>{_CSS}</style></head>"
            f"<body><main>{''.join(parts)}</main></body></html>")
