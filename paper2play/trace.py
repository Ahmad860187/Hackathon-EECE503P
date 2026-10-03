"""JSONL trace writer (CONTRACT §6). One event per line, flushed immediately.

Never logs credentials, headers or reasoning text: such keys are dropped and any
registered secret value is redacted from every string.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

_FORBIDDEN_KEYS = {"authorization", "api_key", "apikey", "key", "headers", "reasoning",
                   "reasoning_content", "reasoning_details", "token", "secret"}


class Trace:
    def __init__(self, path, clock=time.monotonic, t0=None, secrets=()):
        self.path = Path(path)
        self.clock = clock
        self.t0 = clock() if t0 is None else t0
        self.secrets = [s for s in secrets if s and len(s) >= 8]
        self.events = []
        self._fh = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self.path, "w", encoding="utf-8", newline="\n")
        except OSError:
            self._fh = None

    def add_secret(self, value: str) -> None:
        if value and len(value) >= 8:
            self.secrets.append(value)

    def _clean(self, value):
        if isinstance(value, str):
            for s in self.secrets:
                if s and s in value:
                    value = value.replace(s, "[redacted]")
            return value
        if isinstance(value, dict):
            return {k: self._clean(v) for k, v in value.items()
                    if str(k).lower() not in _FORBIDDEN_KEYS}
        if isinstance(value, (list, tuple)):
            return [self._clean(v) for v in value]
        if isinstance(value, float):
            return round(value, 4)
        return value

    def event(self, stage: str, action: str, result: str, **extra) -> dict:
        ev = {"t": round(self.clock() - self.t0, 3), "stage": stage, "action": action,
              "result": result}
        ev.update(self._clean(extra))
        self.events.append(ev)
        if self._fh:
            try:
                self._fh.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
                self._fh.flush()
            except (OSError, ValueError):
                pass
        return ev

    def close(self) -> None:
        if self._fh:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
