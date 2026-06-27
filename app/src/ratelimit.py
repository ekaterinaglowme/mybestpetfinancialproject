"""Глобальный rate limiter для PetBank (token bucket, in-memory)."""

import time
from collections.abc import Callable

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse


class TokenBucket:
    """Token bucket: ёмкость `capacity`, пополнение `rps` токенов/с.

    Один async-процесс → проверка/списание синхронны и без локов.
    `now` инжектируется для детерминированных тестов.
    """

    def __init__(self, rps: float, capacity: float,
                 now: Callable[[], float] = time.monotonic):
        self.rps = rps
        self.capacity = capacity
        self._now = now
        self.tokens = float(capacity)
        self.updated = now()

    def _refill(self) -> None:
        t = self._now()
        self.tokens = min(self.capacity, self.tokens + (t - self.updated) * self.rps)
        self.updated = t

    def allow(self) -> bool:
        """Списывает 1 токен, если есть. True — пропустить, False — отказать."""
        self._refill()
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False

    def seconds_until_token(self) -> float:
        """Секунды до появления хотя бы 1 токена (для Retry-After)."""
        self._refill()
        if self.tokens >= 1 or self.rps <= 0:
            return 0.0
        return (1 - self.tokens) / self.rps


def install_rate_limiter(app: FastAPI, *, bucket: TokenBucket, counter,
                         path: str = "/applications", method: str = "POST") -> None:
    """Вешает на `app` middleware: лимитирует method+path, иначе 429."""

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):
        if request.method == method and request.url.path == path:
            if not bucket.allow():
                counter.inc()
                retry = max(1, round(bucket.seconds_until_token()))
                return JSONResponse(
                    {"detail": "rate limit exceeded"},
                    status_code=429,
                    headers={"Retry-After": str(retry)},
                )
        return await call_next(request)
