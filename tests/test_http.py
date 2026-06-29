"""Тесты служебных ручек: liveness /health, readiness /ready, справка /, 404.

Заявочные тесты живут в test_http_v2.py — здесь только то, что не про бизнес.
"""


async def test_health(async_client):
    resp = await async_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_ready_ok(async_client):
    # БД отвечает на SELECT 1 → инстанс готов принимать трафик.
    resp = await async_client.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


async def test_ready_503_when_db_query_fails(async_client, monkeypatch):
    # БД недоступна (запрос падает) → readiness отдаёт 503, балансировщик уводит трафик.
    import db

    class _BoomSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, *args, **kwargs):
            raise RuntimeError("DB недоступна")

    monkeypatch.setattr(db, "AsyncSessionLocal", lambda: _BoomSession())
    resp = await async_client.get("/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "not ready"


async def test_root_help(async_client):
    resp = await async_client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "PetBank"
    assert "GET /ready" in body["endpoints"]


async def test_unknown_path_404(async_client):
    resp = await async_client.get("/nope")
    assert resp.status_code == 404
