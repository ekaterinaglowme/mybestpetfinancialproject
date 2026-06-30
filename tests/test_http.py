"""Интеграционные тесты HTTP-слоя (httpx + ASGITransport, БД — in-memory SQLite)."""

from datetime import date


def _adult_payload():
    born = date.today().replace(year=date.today().year - 30)
    return {
        "last_name": "Иванов",
        "first_name": "Иван",
        "phone": "+79991234567",
        "country": "Россия",
        "birth_date": born.isoformat(),
    }


async def test_health(async_client):
    resp = await async_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_root_help(async_client):
    resp = await async_client.get("/")
    assert resp.status_code == 200
    assert resp.json()["service"] == "PetBank"


async def test_application_approved(async_client):
    resp = await async_client.post("/applications", json=_adult_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["applicant"]["age"] == 30
    assert body["reasons"] == []


async def test_application_declined_minor(async_client):
    payload = _adult_payload()
    payload["birth_date"] = date.today().replace(year=date.today().year - 10).isoformat()
    resp = await async_client.post("/applications", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "declined"
    assert body["reasons"]


async def test_application_declined_blocked_country(async_client):
    payload = _adult_payload()
    payload["country"] = "Китай"
    resp = await async_client.post("/applications", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "declined"
    assert any("Китай" in reason for reason in body["reasons"])


async def test_application_validation_error(async_client):
    resp = await async_client.post("/applications", json={"first_name": "Иван"})
    assert resp.status_code == 422
    body = resp.json()
    assert "detail" in body
    fields_with_errors = [e["loc"][-1] for e in body["detail"]]
    assert "last_name" in fields_with_errors


async def test_application_invalid_birth_date_returns_422(async_client):
    payload = _adult_payload()
    payload["birth_date"] = "15.05.2000"
    resp = await async_client.post("/applications", json=payload)
    assert resp.status_code == 422
    body = resp.json()
    fields_with_errors = [e["loc"][-1] for e in body["detail"]]
    assert "birth_date" in fields_with_errors


async def test_application_valid_birth_date_ok(async_client):
    payload = _adult_payload()
    payload["birth_date"] = "2000-05-15"
    resp = await async_client.post("/applications", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body
    assert "applicant" in body


async def test_application_invalid_json(async_client):
    resp = await async_client.post(
        "/applications",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422


async def test_unknown_path_404(async_client):
    resp = await async_client.get("/nope")
    assert resp.status_code == 404


def _adult_payload_no_country():
    born = date.today().replace(year=date.today().year - 30)
    return {
        "last_name": "Иванов",
        "first_name": "Иван",
        "phone": "+79991234567",
        "birth_date": born.isoformat(),
    }


async def test_application_without_country_approved(async_client):
    # Обратная совместимость: старый клиент шлёт только базовые поля, без country.
    resp = await async_client.post("/applications", json=_adult_payload_no_country())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["reasons"] == []


async def test_country_known_but_optional_in_openapi(async_client):
    # country известно схеме API, но не обязательно.
    schema = (await async_client.get("/openapi.json")).json()
    model = schema["components"]["schemas"]["ApplicationRequest"]
    assert "country" in model["properties"]
    assert "country" not in model.get("required", [])
