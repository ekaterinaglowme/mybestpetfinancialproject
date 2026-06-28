"""Таймаут-предохранитель для PetBank.

Использует asyncio.wait_for (Python 3.10+). Прерывает только на await-точках;
синхронный make_decision быстрый — это формальный предохранитель от будущих
зависаний на I/O, а не жёсткий killer текущего хендлера.
"""

import asyncio

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse


def install_request_timeout(app: FastAPI, *, seconds: float, counter,
                            paths: tuple[str, ...] = ("/applications",),
                            method: str = "POST") -> None:
    """Вешает на `app` middleware: ограничивает время method+paths, иначе 503."""

    @app.middleware("http")
    async def _timeout(request: Request, call_next):
        if request.method == method and request.url.path in paths:
            try:
                return await asyncio.wait_for(call_next(request), timeout=seconds)
            except asyncio.TimeoutError:
                counter.inc()
                return JSONResponse({"detail": "request timeout"}, status_code=503)
        return await call_next(request)
