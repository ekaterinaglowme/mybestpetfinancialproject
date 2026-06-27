"""Интеграционные тесты HTTP-слоя.

fastapi.testclient.TestClient гоняет ASGI-приложение in-process через тот же
стек, что обработал бы настоящий HTTP-запрос — без поднятия реального сокета.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from server import app


@pytest.fixture()
def client():
    return TestClient(app)


def _adult_payload():
    born = date.today().replace(year=date.today().year - 30)
    return {
        "last_name": "Иванов",
        "first_name": "Иван",
        "phone": "+79991234567",
        "country": "Россия",
        "birth_date": born.isoformat(),
    }


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_root_help(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["service"] == "PetBank"


def test_application_approved(client):
    resp = client.post("/applications", json=_adult_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["applicant"]["age"] == 30
    assert body["reasons"] == []


def test_application_declined_minor(client):
    payload = _adult_payload()
    payload["birth_date"] = date.today().replace(year=date.today().year - 10).isoformat()
    resp = client.post("/applications", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "declined"
    assert body["reasons"]


def test_application_declined_blocked_country(client):
    payload = _adult_payload()
    payload["country"] = "Китай"
    resp = client.post("/applications", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "declined"
    assert any("Китай" in reason for reason in body["reasons"])


def test_application_validation_error(client):
    resp = client.post("/applications", json={"first_name": "Иван"})
    assert resp.status_code == 422
    body = resp.json()
    assert "detail" in body
    fields_with_errors = [e["loc"][-1] for e in body["detail"]]
    assert "last_name" in fields_with_errors


def test_application_invalid_birth_date_returns_422(client):
    payload = _adult_payload()
    payload["birth_date"] = "15.05.2000"
    resp = client.post("/applications", json=payload)
    assert resp.status_code == 422
    body = resp.json()
    fields_with_errors = [e["loc"][-1] for e in body["detail"]]
    assert "birth_date" in fields_with_errors


def test_application_valid_birth_date_ok(client):
    payload = _adult_payload()
    payload["birth_date"] = "2000-05-15"
    resp = client.post("/applications", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body
    assert "applicant" in body


def test_application_invalid_json(client):
    resp = client.post(
        "/applications",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422


def test_unknown_path_404(client):
    resp = client.get("/nope")
    assert resp.status_code == 404
