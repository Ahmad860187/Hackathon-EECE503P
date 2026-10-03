"""Small shared helpers for the dev-only evaluation scripts (stdlib only)."""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_dotenv_into(env: dict, path: Path | None = None, keys=("OPENROUTER_API_KEY",)) -> list:
    """Copy selected KEY=VALUE pairs from the repo's gitignored .env into `env` when not already set.

    Returns the names that were loaded (never the values). Values are never printed or logged.
    """
    path = path or (REPO / ".env")
    loaded = []
    if not path.is_file():
        return loaded
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return loaded
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k.startswith("export "):
            k = k[len("export "):].strip()
        v = v.strip().strip('"').strip("'")
        if k in keys and v and not env.get(k):
            env[k] = v
            loaded.append(k)
    return loaded


def secret_values(env: dict, keys=("OPENROUTER_API_KEY",)) -> list:
    return [env[k] for k in keys if env.get(k)]


def redact(text: str, secrets) -> str:
    for s in secrets:
        if s and len(s) >= 6:
            text = text.replace(s, "[redacted]")
    return text


def api_key() -> str:
    env = dict(os.environ)
    load_dotenv_into(env)
    return env.get("OPENROUTER_API_KEY", "")
