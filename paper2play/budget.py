"""Per-case resource budget: wall time, API requests (incl. retries), completion tokens.

Every model request goes through `Budget`: `check_can_call` before the request,
`start_request` when it is sent, `record_usage` when it returns. Limits are never
exceeded: per-call `max_tokens` is clamped to the remaining completion budget and the
HTTP timeout to the remaining wall time minus a reserve for writing outputs.
"""

from __future__ import annotations

import time

WALL_LIMIT_S = 600.0          # 10 minutes per case
MAX_REQUESTS = 10             # incl. retries
MAX_COMPLETION_TOKENS = 30000  # total completion (incl. reasoning) tokens per case
RESERVE_S = 30.0              # kept free to assemble + write outputs


class BudgetExceeded(Exception):
    """Raised when a request would break one of the hard limits."""


class Budget:
    def __init__(self, wall_s: float = WALL_LIMIT_S, max_requests: int = MAX_REQUESTS,
                 max_completion: int = MAX_COMPLETION_TOKENS, reserve_s: float = RESERVE_S,
                 clock=time.monotonic):
        self.clock = clock
        self.t0 = clock()
        self.wall_s = float(wall_s)
        self.max_requests = int(max_requests)
        self.max_completion = int(max_completion)
        self.reserve_s = float(reserve_s)
        self.requests = 0
        self.completion_used = 0       # counted conservatively (requested max when usage unknown)
        self.completion_reported = 0   # only what the API reported
        self.prompt_reported = 0
        self.reasoning_reported = 0
        self.usage_unknown = False

    # ---- time ---------------------------------------------------------------
    def elapsed(self) -> float:
        return self.clock() - self.t0

    def remaining_time(self) -> float:
        return self.wall_s - self.elapsed()

    def usable_time(self) -> float:
        """Time that may still be spent on model calls (keeps the output reserve)."""
        return self.remaining_time() - self.reserve_s

    def http_timeout(self) -> float:
        return max(0.0, self.usable_time())

    # ---- requests / tokens --------------------------------------------------
    def remaining_requests(self) -> int:
        return self.max_requests - self.requests

    def remaining_completion(self) -> int:
        return max(0, self.max_completion - self.completion_used)

    def max_tokens(self, cap: int) -> int:
        return max(0, min(int(cap), self.remaining_completion()))

    def check_can_call(self, min_tokens: int = 512, min_time_s: float = 15.0):
        """Return (ok, reason). A call is allowed only if all three limits leave room."""
        if self.remaining_requests() <= 0:
            return False, f"request limit reached ({self.requests}/{self.max_requests})"
        if self.remaining_completion() < min_tokens:
            return False, (f"completion budget too small ({self.remaining_completion()} left,"
                           f" need >= {min_tokens})")
        if self.usable_time() < min_time_s:
            return False, f"time budget too small ({self.usable_time():.1f}s usable)"
        return True, ""

    def start_request(self) -> int:
        """Count a request about to be sent; returns its 1-based number."""
        if self.requests >= self.max_requests:
            raise BudgetExceeded("request limit reached")
        self.requests += 1
        return self.requests

    def record_usage(self, completion_tokens, prompt_tokens, requested_max: int,
                     reasoning_tokens=None) -> None:
        """Record usage of one request. Unknown completion usage is charged as the
        requested max_tokens so the 30k limit still holds."""
        if isinstance(completion_tokens, int):
            self.completion_used += completion_tokens
            self.completion_reported += completion_tokens
        else:
            self.usage_unknown = True
            self.completion_used += int(requested_max)
        if isinstance(prompt_tokens, int):
            self.prompt_reported += prompt_tokens
        else:
            self.usage_unknown = True
        if isinstance(reasoning_tokens, int):
            self.reasoning_reported += reasoning_tokens

    def totals(self) -> dict:
        unknown = "unknown"
        return {
            "calls": self.requests,
            "prompt_tokens": self.prompt_reported if not self.usage_unknown else unknown,
            "completion_tokens": self.completion_reported if not self.usage_unknown else unknown,
            "reasoning_tokens": self.reasoning_reported,
            "total_tokens": (self.prompt_reported + self.completion_reported)
            if not self.usage_unknown else unknown,
            "completion_charged": self.completion_used,
            "elapsed_s": round(self.elapsed(), 2),
        }
