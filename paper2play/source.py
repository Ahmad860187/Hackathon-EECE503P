"""Layered source loader (PLAN §2).

Order: inline excerpt (in focus / source_url / optional extra case keys) → local file
relative to case.json → short stdlib network fetch (arXiv abs|pdf → html page) → none.
Returns {"text", "title", "origin": "inline|file|fetched|none", "detail"}.
"""

from __future__ import annotations

import html
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

EXCERPT_CHARS = 6000
FETCH_TIMEOUT_S = 3.0
FETCH_TOTAL_S = 4.5
INLINE_MIN_CHARS = 400

_STOP = set("""a an the and or of to in on for with by from as at is are was were be been this that these
those it its into how why what which when where does do using use show shows explain explains about
between their them they we our you your paper section equation figure model method approach based
over under than then also can may such each per via role effect effects""".split())

_EXCERPT_MARKERS = re.compile(r"(?is)\b(excerpt|source text|quote|passage)\s*[:\-]\s*(.+)$")


def _clean_ws(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    s = re.sub(r"[ \t\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    return s.strip()


def keywords(focus: str) -> list:
    words = re.findall(r"[A-Za-z][A-Za-z\-]{2,}", focus or "")
    out = []
    for w in words:
        lw = w.lower()
        if lw not in _STOP and lw not in out:
            out.append(lw)
    return out[:20]


def slice_around(text: str, focus: str, n: int = EXCERPT_CHARS) -> str:
    """Pick the n-char window with the most focus-keyword hits."""
    text = _clean_ws(text)
    if len(text) <= n:
        return text
    kws = keywords(focus)
    if not kws:
        return text[:n]
    low = text.lower()
    hits = []
    for kw in kws:
        for m in re.finditer(re.escape(kw), low):
            hits.append((m.start(), kw))
    if not hits:
        return text[:n]
    hits.sort()
    best_start, best_score = 0, -1
    step = max(200, n // 10)
    for start in range(0, max(1, len(text) - n + step), step):
        end = start + n
        seen = {}
        for pos, kw in hits:
            if start <= pos < end:
                seen[kw] = seen.get(kw, 0) + 1
        # distinct keywords weigh more than repeats
        score = 10 * len(seen) + sum(min(c, 5) for c in seen.values())
        if score > best_score:
            best_start, best_score = start, score
    start = best_start
    # snap to a paragraph / sentence boundary when cheap
    if start > 0:
        nl = text.rfind("\n", max(0, start - 300), start)
        if nl != -1:
            start = nl + 1
    return text[start:start + n].strip()


def html_to_text(raw: str) -> tuple:
    title = ""
    m = re.search(r"(?is)<h1[^>]*class=\"[^\"]*ltx_title[^\"]*\"[^>]*>(.*?)</h1>", raw)
    if not m:
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    if m:
        title = _clean_ws(html.unescape(re.sub(r"(?s)<[^>]+>", " ", m.group(1))))
    body = re.sub(r"(?is)<(script|style|noscript|svg|head|nav|footer)[^>]*>.*?</\1>", " ", raw)
    # keep math alttext (arXiv/LaTeXML) instead of dropping formulas
    body = re.sub(r"(?is)<math[^>]*alttext=\"([^\"]*)\"[^>]*>.*?</math>", r" \1 ", body)
    body = re.sub(r"(?i)<br\s*/?>|</p>|</h\d>|</li>|</div>|</section>", "\n", body)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    return title, _clean_ws(html.unescape(body))


_ARXIV_ID = r"([0-9]{4}\.[0-9]{4,5}(?:v\d+)?|[a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)"


def _arxiv_id(url: str):
    url = url.strip()
    m = re.search(r"arxiv\.org/(?:abs|pdf|html)/" + _ARXIV_ID, url, re.I)
    if not m:  # mirrors and aggregators: ar5iv, alphaxiv, huggingface papers, DOI 10.48550/arXiv.X, "arXiv:X", bare id
        m = (re.search(r"(?:ar5iv|alphaxiv|huggingface\.co/papers)[^\s]*?/" + _ARXIV_ID, url, re.I)
             or re.search(r"(?:10\.48550/arXiv\.|arXiv:\s*)" + _ARXIV_ID, url, re.I)
             or re.fullmatch(_ARXIV_ID, url, re.I))
    if not m:
        return None
    aid = m.group(1)
    return aid[:-4] if aid.lower().endswith(".pdf") else aid


def _fetch_blocking(url: str, timeout: float) -> tuple:
    req = urllib.request.Request(url, headers={"User-Agent": "paper2play/0.1 (research teaching tool)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (http/https only)
        ctype = resp.headers.get("Content-Type", "")
        data = resp.read(3_000_000)
    return ctype, data


def _fetch(url: str, timeout: float) -> tuple:
    """Fetch with a hard wall-clock limit (DNS lookups ignore socket timeouts, so the
    request runs in a daemon thread that is abandoned if it overruns)."""
    box = {}

    def work():
        try:
            box["ok"] = _fetch_blocking(url, timeout)
        except BaseException as e:  # noqa: BLE001 - surfaced to the caller below
            box["err"] = e

    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(timeout + 0.2)
    if "ok" in box:
        return box["ok"]
    if "err" in box:
        err = box["err"]
        raise err if isinstance(err, (urllib.error.URLError, OSError, ValueError)) else OSError(str(err))
    raise TimeoutError(f"no response within {timeout:.1f}s")


def _inline_excerpt(case: dict):
    for key in ("excerpt", "source_text", "text", "context"):
        v = case.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip(), f"case['{key}']"
    focus = case.get("focus", "")
    m = _EXCERPT_MARKERS.search(focus)
    if m and len(m.group(2).strip()) >= 120:
        return m.group(2).strip(), "focus (marked excerpt)"
    # A long focus is still a learning brief, not source text: only explicit markers count.
    src = case.get("source_url", "")
    if (not re.match(r"(?i)^\s*(https?://|www\.)", src) and len(src) >= INLINE_MIN_CHARS
            and src.count(" ") >= 40):
        return src.strip(), "source_url (text)"
    return None, ""


def load_source(case: dict, case_dir=None, allow_network: bool = True) -> dict:
    focus = case.get("focus", "")
    src = (case.get("source_url") or "").strip()
    notes = []

    # 1. inline excerpt
    text, where = _inline_excerpt(case)
    if text:
        return {"text": slice_around(text, focus), "title": "", "origin": "inline", "detail": where}

    # 2. local file (relative to case.json)
    if src and not re.match(r"(?i)^(https?|ftp)://", src):
        cands = []
        p = Path(src.replace("file://", ""))
        if p.is_absolute():
            cands.append(p)
        if case_dir is not None:
            cands.append(Path(case_dir) / p)
        cands.append(p)
        for c in cands:
            try:
                if c.is_file():
                    if c.suffix.lower() == ".pdf":
                        notes.append(f"file {c.name} is a PDF (not parsed)")
                        break
                    raw = c.read_text(encoding="utf-8", errors="replace")
                    title = ""
                    if c.suffix.lower() in (".html", ".htm") or "<html" in raw[:2000].lower():
                        title, raw = html_to_text(raw)
                    return {"text": slice_around(raw, focus), "title": title, "origin": "file",
                            "detail": f"read {c.name}"}
            except OSError as e:
                notes.append(f"file error {type(e).__name__}")
    # a sibling excerpt file next to case.json
    if case_dir is not None:
        for name in ("excerpt.txt", "excerpt.md", "source.txt"):
            c = Path(case_dir) / name
            if c.is_file():
                try:
                    raw = c.read_text(encoding="utf-8", errors="replace")
                    if raw.strip():
                        return {"text": slice_around(raw, focus), "title": "", "origin": "file",
                                "detail": f"read sibling {name}"}
                except OSError:
                    pass

    # 3. network fetch (short, stdlib only)
    if allow_network and (re.match(r"(?i)^https?://", src) or _arxiv_id(src)):
        t_end = time.monotonic() + FETCH_TOTAL_S
        urls = []
        aid = _arxiv_id(src)
        if aid:
            urls = [f"https://arxiv.org/html/{aid}", f"https://arxiv.org/abs/{aid}"]
        elif not src.lower().endswith(".pdf"):
            urls = [src]
        for u in urls:
            left = t_end - time.monotonic()
            if left < 0.5:
                notes.append("fetch time budget exhausted")
                break
            try:
                ctype, data = _fetch(u, min(FETCH_TIMEOUT_S, left))
                if "pdf" in ctype.lower():
                    notes.append(f"{u}: pdf not parsed")
                    continue
                raw = data.decode("utf-8", errors="replace")
                title, body = html_to_text(raw) if "<" in raw[:5000] else ("", raw)
                if len(body) < 300:
                    notes.append(f"{u}: too little text")
                    continue
                if "/abs/" in u:
                    m = re.search(r"(?is)Abstract:?(.{200,3000}?)(Comments:|Subjects:|Submission history)", body)
                    if m:
                        body = "Abstract: " + m.group(1)
                title = re.sub(r"^\[[^\]]+\]\s*", "", title)
                return {"text": slice_around(body, focus), "title": title, "origin": "fetched",
                        "detail": f"fetched {u}" + (f"; {'; '.join(notes)}" if notes else "")}
            except (urllib.error.URLError, OSError, ValueError) as e:
                notes.append(f"{u}: {type(e).__name__}")

    return {"text": "", "title": "", "origin": "none", "detail": "; ".join(notes) or "no excerpt available"}
