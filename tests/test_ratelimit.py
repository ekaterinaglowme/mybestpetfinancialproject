from fastapi import FastAPI
from fastapi.testclient import TestClient

from ratelimit import TokenBucket, install_rate_limiter
from server import app as real_app


def test_bucket_allows_up_to_capacity():
    clock = [0.0]
    b = TokenBucket(rps=100, capacity=3, now=lambda: clock[0])
    assert b.allow() is True
    assert b.allow() is True
    assert b.allow() is True
    assert b.allow() is False  # ёмкость исчерпана, время не шло


def test_bucket_refills_over_time():
    clock = [0.0]
    b = TokenBucket(rps=10, capacity=1, now=lambda: clock[0])
    assert b.allow() is True
    assert b.allow() is False
    clock[0] = 0.1  # 0.1с * 10 rps = 1 токен
    assert b.allow() is True


def test_seconds_until_token():
    clock = [0.0]
    b = TokenBucket(rps=2, capacity=1, now=lambda: clock[0])
    assert b.allow() is True
    assert b.seconds_until_token() == 0.5  # 1 токен / 2 rps


def test_seconds_until_token_zero_when_available():
    b = TokenBucket(rps=100, capacity=5, now=lambda: 0.0)
    assert b.seconds_until_token() == 0.0


class FakeCounter:
    def __init__(self):
        self.n = 0

    def inc(self, amount: float = 1) -> None:
        self.n += 1


def _mini_app(bucket, counter):
    app = FastAPI()
    install_rate_limiter(app, bucket=bucket, counter=counter)

    @app.post("/applications")
    def apply():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"ok": True}

    return TestClient(app)


def test_429_after_capacity_exhausted():
    counter = FakeCounter()
    # rps=0 → не пополняется: после 1 запроса bucket пуст
    client = _mini_app(TokenBucket(rps=0, capacity=1, now=lambda: 0.0), counter)
    assert client.post("/applications").status_code == 200
    resp = client.post("/applications")
    assert resp.status_code == 429
    assert resp.json() == {"detail": "rate limit exceeded"}
    assert "retry-after" in {k.lower() for k in resp.headers}
    assert counter.n == 1


def test_health_not_limited():
    counter = FakeCounter()
    client = _mini_app(TokenBucket(rps=0, capacity=1, now=lambda: 0.0), counter)
    for _ in range(5):
        assert client.get("/health").status_code == 200
    assert counter.n == 0


def test_real_app_applications_ok_with_request_id():
    client = TestClient(real_app)
    body = {
        "last_name": "Иванов", "first_name": "Иван", "phone": "+79991234567",
        "birth_date": "2000-05-15", "country": "Россия", "amount": 100000,
    }
    resp = client.post("/applications", json=body)
    assert resp.status_code == 200
    assert resp.json()["status"] in ("approved", "declined")
    assert "X-Request-ID" in resp.headers  # log_requests остался внешним
