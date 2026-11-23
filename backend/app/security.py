from __future__ import annotations

import re
import time
from collections import defaultdict

from fastapi import HTTPException, Request

from .config import settings

INJECTION_PATTERNS = [
    re.compile(r"ignore (all )?(previous|prior|above) (instructions|prompts)", re.I),
    re.compile(r"disregard (the )?(system|developer) (prompt|message|instructions)", re.I),
    re.compile(r"you are now (a|an) ", re.I),
    re.compile(r"reveal (your|the) (system prompt|api key|secret)", re.I),
    re.compile(r"(execute|run) (this|the following) (code|command)", re.I),
    re.compile(r"</?(system|assistant|developer)>", re.I),
]


def detect_prompt_injection(text: str) -> list[str]:
    """Return human-readable flags for instruction-like content in untrusted data."""
    flags: list[str] = []
    for pattern in INJECTION_PATTERNS:
        match = pattern.search(text or "")
        if match:
            snippet = (text[max(0, match.start() - 40):match.end() + 60] or "").replace("\n", " ")
            flags.append(f"Instruction-like text treated as data: “{snippet.strip()[:120]}”")
    return flags[:5]


class RateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, tuple[float, int]] = defaultdict(lambda: (0.0, 0))

    def check(self, key: str) -> None:
        now = time.time()
        window_start, count = self._buckets[key]
        if now - window_start > settings.rate_limit_window_seconds:
            window_start, count = now, 0
        count += 1
        self._buckets[key] = (window_start, count)
        if count > settings.rate_limit_requests:
            retry_after = int(settings.rate_limit_window_seconds - (now - window_start)) + 1
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please wait before retrying.",
                headers={"Retry-After": str(max(1, retry_after))},
            )
        # Bounded memory for single-node deployments.
        if len(self._buckets) > 5000:
            for stale in [k for k, (start, _) in self._buckets.items() if now - start > 3600][:1000]:
                self._buckets.pop(stale, None)


rate_limiter = RateLimiter()


def rate_limit(request: Request) -> None:
    user_id = getattr(request.state, "user_id", None) or (request.client.host if request.client else "anonymous")
    rate_limiter.check(f"{user_id}:{request.url.path}")
