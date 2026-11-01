from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time

import redis
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings

logger = logging.getLogger("market_assistant.http")
_redis_client: redis.Redis | None = None


def redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(
            settings.redis_url, socket_connect_timeout=0.2, socket_timeout=0.2
        )
    return _redis_client


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Best-effort Redis limiter; database-backed business operations remain available on degradation."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not request.url.path.startswith("/api/v1/"):
            return await call_next(request)
        if settings.persistence_backend == "memory":
            response = await call_next(request)
            response.headers["X-RateLimit-Mode"] = "disabled-dev"
            return response
        identity = request.headers.get("authorization") or ":".join(
            [
                request.headers.get("x-tenant-id", "unknown"),
                request.headers.get("x-user-id", "unknown"),
            ]
        )
        digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
        bucket = int(time.time() // 60)
        key = f"rate:{digest}:{bucket}"
        degraded = False
        try:
            count = await asyncio.to_thread(redis_client().incr, key)
            if count == 1:
                await asyncio.to_thread(redis_client().expire, key, 70)
            if count > 120:
                return JSONResponse(
                    status_code=429,
                    content={
                        "error_code": "RATE_LIMITED",
                        "message": "请求过于频繁，请稍后重试",
                        "request_id": request.headers.get("x-request-id", "unknown"),
                        "details": {},
                    },
                )
        except redis.RedisError:
            degraded = True
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-RateLimit-Mode"] = "degraded-open" if degraded else "redis"
        logger.info(
            json.dumps(
                {
                    "event": "http_request",
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "request_id": response.headers.get("X-Request-ID"),
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
                ensure_ascii=False,
            )
        )
        return response
