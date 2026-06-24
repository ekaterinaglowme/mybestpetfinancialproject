import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from request_timeout import install_request_timeout


class FakeCounter:
    def __init__(self):
        self.n = 0

    def inc(self, amount: float = 1) -> None:
        self.n += 1


def _mini_app(seconds, counter):
    app = FastAPI()
    install_request_timeout(app, seconds=seconds, counter=counter)

    @app.post("/applications")
    async def slow():
        await asyncio.sleep(0.5)
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"ok": True}

    return TestClient(app)


def test_timeout_returns_503():
    counter = FakeCounter()
    client = _mini_app(0.05, counter)
    resp = client.post("/applications")
    assert resp.status_code == 503
    assert resp.json() == {"detail": "request timeout"}
    assert counter.n == 1


def test_fast_request_passes():
    counter = FakeCounter()
    client = _mini_app(0.05, counter)
    assert client.get("/health").status_code == 200
    assert counter.n == 0
