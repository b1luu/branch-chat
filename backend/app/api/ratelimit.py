"""Per-IP token-bucket rate limiting for the endpoints that call the LLM (D3).

Only `POST /api/messages/{id}/reply` and `POST /api/branches/{id}/merge` are
limited: they are the ones that cost money/GPU time. Reads are free.

Token bucket: each client IP has a bucket holding up to `burst` tokens that
refills continuously at `per_minute / 60` tokens per second. A limited request
spends one token; with none left it gets `429 Too Many Requests` and a
`Retry-After` header saying how many whole seconds until a token is back. This
allows short bursts (open a conversation, fire off a few questions) while
capping the sustained rate.

Implemented as plain ASGI middleware rather than Starlette's
`BaseHTTPMiddleware`, which wraps the response and can interfere with
streaming (SSE) bodies. Rejected requests never reach the route, so no user
node is persisted for them.

State is in-process memory: correct for a single API container. Behind the
web container's proxy every request comes from the proxy's IP, so set
`RATE_LIMIT_TRUST_PROXY=true` there to key on the last `X-Forwarded-For` hop
(the address our own proxy saw). Never trust that header when the API is
exposed directly, since clients can set it to anything.
"""

import json
import math
import os
import re
import time
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass, field
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

LIMITED_PATHS = (
    re.compile(r"^/api/messages/[^/]+/reply/?$"),
    re.compile(r"^/api/branches/[^/]+/merge/?$"),
)
MAX_TRACKED_CLIENTS = 10_000


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RateLimitConfig:
    enabled: bool = True
    per_minute: float = 20.0  # sustained refill rate
    burst: int = 10  # bucket capacity
    trust_proxy: bool = False

    @classmethod
    def from_env(cls) -> "RateLimitConfig":
        """`RATE_LIMIT_ENABLED`, `RATE_LIMIT_PER_MINUTE`, `RATE_LIMIT_BURST`, `RATE_LIMIT_TRUST_PROXY`."""
        return cls(
            enabled=_env_bool("RATE_LIMIT_ENABLED", True),
            per_minute=float(os.getenv("RATE_LIMIT_PER_MINUTE", "20")),
            burst=int(os.getenv("RATE_LIMIT_BURST", "10")),
            trust_proxy=_env_bool("RATE_LIMIT_TRUST_PROXY", False),
        )


@dataclass
class _Bucket:
    tokens: float
    updated: float


@dataclass
class TokenBucketLimiter:
    """Buckets keyed by client id. `clock` is injectable so tests don't sleep."""

    per_minute: float
    burst: int
    clock: Callable[[], float] = time.monotonic
    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    @property
    def rate(self) -> float:
        """Tokens per second."""
        return self.per_minute / 60.0

    def _refill(self, bucket: _Bucket, now: float) -> None:
        bucket.tokens = min(self.burst, bucket.tokens + (now - bucket.updated) * self.rate)
        bucket.updated = now

    def acquire(self, key: str) -> float | None:
        """Spend one token for `key`. Returns None if allowed, else seconds to wait."""
        now = self.clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            self._prune(now)
            bucket = self._buckets[key] = _Bucket(tokens=float(self.burst), updated=now)
        else:
            self._refill(bucket, now)
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return None
        if self.rate <= 0:
            return math.inf
        return (1.0 - bucket.tokens) / self.rate

    def _prune(self, now: float) -> None:
        """Bound memory: forget clients whose bucket has refilled completely.

        A full bucket is indistinguishable from a brand-new one, so dropping it
        changes nothing for that client.
        """
        if len(self._buckets) < MAX_TRACKED_CLIENTS:
            return
        for key, bucket in list(self._buckets.items()):
            self._refill(bucket, now)
            if bucket.tokens >= self.burst:
                del self._buckets[key]

    @property
    def tracked_clients(self) -> int:
        return len(self._buckets)


def client_key(scope: Scope, trust_proxy: bool) -> str:
    """The client IP for `scope`: the socket peer, or the last X-Forwarded-For hop."""
    if trust_proxy:
        for name, value in scope.get("headers", []):
            if name == b"x-forwarded-for":
                hops = [h.strip() for h in value.decode("latin-1").split(",") if h.strip()]
                if hops:
                    return hops[-1]
    client = scope.get("client")
    return client[0] if client else "unknown"


def is_limited(scope: Scope) -> bool:
    return (
        scope.get("type") == "http"
        and scope.get("method") == "POST"
        and any(p.match(scope.get("path", "")) for p in LIMITED_PATHS)
    )


class RateLimitMiddleware:
    def __init__(
        self, app: ASGIApp, limiter: TokenBucketLimiter, trust_proxy: bool = False
    ) -> None:
        self.app = app
        self.limiter = limiter
        self.trust_proxy = trust_proxy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not is_limited(scope):
            await self.app(scope, receive, send)
            return
        wait = self.limiter.acquire(client_key(scope, self.trust_proxy))
        if wait is None:
            await self.app(scope, receive, send)
            return
        retry_after = max(1, math.ceil(wait)) if math.isfinite(wait) else 3600
        body = json.dumps(
            {"detail": f"Rate limit exceeded. Try again in {retry_after} s."}
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"retry-after", str(retry_after).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
