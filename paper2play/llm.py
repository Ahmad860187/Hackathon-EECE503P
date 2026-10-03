"""OpenRouter chat-completions client with budget enforcement and usage logging.

- Bearer auth from a key passed in by agent.py (read only from os.environ there).
- Retries on 408/429/5xx/network errors with backoff; every attempt counts as a request.
- Sends `reasoning: {"effort": "low"}`; if the model/provider rejects it, retries once without.
- Hard wall-clock deadline per request (response is read in chunks and aborted past it).
- Logs per-attempt usage (prompt / completion / reasoning tokens) — never the key, headers
  or reasoning text.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import requests

from .budget import Budget

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529}
MAX_ATTEMPTS = 4


class LLMError(Exception):
    pass


class NoAPIKey(LLMError):
    pass


class BudgetStop(LLMError):
    pass


@dataclass
class LLMResult:
    text: str
    finish_reason: str
    prompt_tokens: object = "unknown"
    completion_tokens: object = "unknown"
    reasoning_tokens: object = None
    elapsed_s: float = 0.0
    attempts: int = 1
    call_numbers: list = field(default_factory=list)


class _HTTPFailure(Exception):
    def __init__(self, status, message, retryable, retry_after=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after


def reasoning_setting() -> dict:
    """Hidden reasoning counts as completion tokens and dominates latency. Measured on
    deepseek-v4.1-flash: effort "low" still spent ~12k reasoning tokens per generation,
    while enabled=false spent 0. P2P_REASONING=<n> grants a capped budget instead."""
    import os
    v = os.environ.get("P2P_REASONING", "off").strip().lower()
    if v.isdigit() and int(v) > 0:
        return {"max_tokens": int(v), "exclude": True}
    return {"enabled": False, "exclude": True}


def _short(s, n=300):
    s = str(s)
    return s if len(s) <= n else s[:n] + "..."


def _content_text(message) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # list of parts
        out = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                out.append(part["text"])
            elif isinstance(part, str):
                out.append(part)
        return "".join(out)
    return ""


class OpenRouterClient:
    def __init__(self, api_key, model: str, budget: Budget, trace, temperature: float = 0.2,
                 session=None, sleep=time.sleep):
        if not api_key:
            raise NoAPIKey("OPENROUTER_API_KEY is not set")
        self._api_key = api_key
        self.model = model
        self.budget = budget
        self.trace = trace
        self.temperature = temperature
        self.session = session or requests.Session()
        self.sleep = sleep
        self.send_reasoning = True
        self.reasoning_override = None

    # ------------------------------------------------------------------ HTTP
    def _post(self, body: dict, deadline_s: float):
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-Title": "paper2play",
        }
        start = time.monotonic()
        try:
            resp = self.session.post(OPENROUTER_URL, headers=headers, data=json.dumps(body),
                                     timeout=(min(10.0, max(1.0, deadline_s)), max(1.0, deadline_s)),
                                     stream=True)
        except requests.exceptions.Timeout as e:
            raise _HTTPFailure(None, f"timeout: {type(e).__name__}", True) from None
        except requests.exceptions.RequestException as e:
            raise _HTTPFailure(None, f"network error: {type(e).__name__}", True) from None
        try:
            chunks = []
            for chunk in resp.iter_content(chunk_size=16384):
                if chunk:
                    chunks.append(chunk)
                if time.monotonic() - start > deadline_s:
                    raise _HTTPFailure(None, "deadline exceeded while reading response", True)
            raw = b"".join(chunks)
        except requests.exceptions.RequestException as e:
            raise _HTTPFailure(None, f"read error: {type(e).__name__}", True) from None
        finally:
            try:
                resp.close()
            except Exception:
                pass
        status = resp.status_code
        try:
            data = json.loads(raw.decode("utf-8", errors="replace").strip() or "{}")
        except ValueError:
            data = None
        if status != 200:
            msg = ""
            if isinstance(data, dict) and isinstance(data.get("error"), dict):
                msg = str(data["error"].get("message", ""))
            retry_after = None
            try:
                retry_after = float(resp.headers.get("Retry-After"))
            except (TypeError, ValueError):
                pass
            raise _HTTPFailure(status, f"HTTP {status}: {_short(msg)}",
                               status in RETRYABLE_STATUS, retry_after)
        if not isinstance(data, dict):
            raise _HTTPFailure(status, "invalid JSON body", True)
        if isinstance(data.get("error"), dict):  # error delivered with HTTP 200
            err = data["error"]
            code = err.get("code")
            try:
                code = int(code)
            except (TypeError, ValueError):
                code = None
            raise _HTTPFailure(code, f"API error {code}: {_short(err.get('message', ''))}",
                               code is None or code in RETRYABLE_STATUS)
        return data

    # ------------------------------------------------------------------ chat
    def chat(self, messages, max_tokens_cap: int, stage: str, purpose: str = "",
             min_tokens: int = 512) -> LLMResult:
        """One logical call (may take several attempts). Raises BudgetStop / LLMError."""
        attempts = 0
        reasoning_retry_used = False
        last_err = "no attempt"
        calls = []
        while attempts < MAX_ATTEMPTS:
            ok, reason = self.budget.check_can_call(min_tokens=min_tokens)
            if not ok:
                self.trace.event(stage, "llm_call", "skip", purpose=purpose, detail=reason)
                raise BudgetStop(reason if attempts == 0 else f"{reason}; last error: {last_err}")
            max_tokens = self.budget.max_tokens(max_tokens_cap)
            timeout = self.budget.http_timeout()
            body = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": max_tokens,
                "usage": {"include": True},
            }
            if self.send_reasoning:
                body["reasoning"] = self.reasoning_override or reasoning_setting()
            call_no = self.budget.start_request()
            calls.append(call_no)
            attempts += 1
            t_start = time.monotonic()
            try:
                data = self._post(body, timeout)
            except _HTTPFailure as f:
                elapsed = time.monotonic() - t_start
                # An HTTP error status means nothing was generated; a timeout / dropped
                # connection may still be billed, so charge it conservatively (requested max).
                if f.status:
                    self.budget.record_usage(0, 0, max_tokens)
                else:
                    self.budget.record_usage(None, None, max_tokens)
                last_err = f.message
                # Some providers reject the `reasoning` field with a generic 400; retry once without it.
                rejected_reasoning = self.send_reasoning and f.status in (400, 422)
                self.trace.event(stage, "llm_call", "error", call=call_no, model=self.model,
                                 purpose=purpose, attempt=attempts, http_status=f.status,
                                 elapsed_s=round(elapsed, 2), max_tokens=max_tokens,
                                 detail=f.message, prompt_tokens="unknown",
                                 completion_tokens="unknown")
                if rejected_reasoning and not reasoning_retry_used:
                    reasoning_retry_used = True
                    self.send_reasoning = False
                    continue
                if not f.retryable:
                    raise LLMError(f.message)
                wait = f.retry_after if f.retry_after is not None else min(8.0, 1.5 * (2 ** (attempts - 1)))
                wait = min(wait, max(0.0, self.budget.usable_time() - 20.0))
                if wait > 0:
                    self.sleep(wait)
                continue

            elapsed = time.monotonic() - t_start
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            pt = usage.get("prompt_tokens")
            ct = usage.get("completion_tokens")
            details = usage.get("completion_tokens_details") or {}
            rt = details.get("reasoning_tokens") if isinstance(details, dict) else None
            pt = pt if isinstance(pt, int) else "unknown"
            ct = ct if isinstance(ct, int) else "unknown"
            rt = rt if isinstance(rt, int) else None
            self.budget.record_usage(ct if isinstance(ct, int) else None,
                                     pt if isinstance(pt, int) else None, max_tokens, rt)
            choices = data.get("choices") or []
            choice = choices[0] if choices and isinstance(choices[0], dict) else {}
            text = _content_text(choice.get("message"))
            finish = str(choice.get("finish_reason") or choice.get("native_finish_reason") or "unknown")
            ev = dict(call=call_no, model=self.model, purpose=purpose, attempt=attempts,
                      prompt_tokens=pt, completion_tokens=ct, elapsed_s=round(elapsed, 2),
                      finish_reason=finish, max_tokens=max_tokens, chars=len(text))
            if rt is not None:
                ev["reasoning_tokens"] = rt
            if isinstance(usage.get("cost"), (int, float)):
                ev["cost_usd"] = usage["cost"]
            if not text.strip():
                last_err = f"empty content (finish_reason={finish})"
                self.trace.event(stage, "llm_call", "fail", detail=last_err, **ev)
                if finish == "length":
                    # All tokens went to hidden reasoning. Retrying the same way won't help, so
                    # retry once with reasoning switched off (the rest of the run keeps it off).
                    if (self.send_reasoning and self.reasoning_override is None
                            and reasoning_setting().get("enabled", True) is not False):
                        self.reasoning_override = {"enabled": False, "exclude": True}
                        self.trace.event(stage, "llm_call", "retry", purpose=purpose,
                                         detail="reasoning consumed the budget; retrying with reasoning disabled")
                        continue
                    raise LLMError(last_err)
                continue
            self.trace.event(stage, "llm_call", "ok", **ev)
            return LLMResult(text=text, finish_reason=finish, prompt_tokens=pt, completion_tokens=ct,
                             reasoning_tokens=rt, elapsed_s=elapsed, attempts=attempts,
                             call_numbers=calls)
        raise LLMError(f"gave up after {attempts} attempts: {last_err}")
