"""Тесты JSON-логирования: форматтер, middleware, корреляция request_id."""

import json
import logging
from datetime import date

import pytest
from fastapi.testclient import TestClient

from logging_setup import JsonFormatter
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


def _record(name="server", level=logging.INFO, msg="сообщение", **extra):
    record = logging.LogRecord(name, level, __file__, 1, msg, (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formatter_outputs_valid_json_with_base_fields():
    out = JsonFormatter().format(_record(msg="Заявка одобрена"))
    data = json.loads(out)
    assert data["level"] == "INFO"
    assert data["logger"] == "server"
    assert data["message"] == "Заявка одобрена"
    assert "timestamp" in data
    # Кириллица не экранируется
    assert "Заявка одобрена" in out
    # request_id пуст → поле опущено
    assert "request_id" not in data


def test_formatter_includes_extra_fields():
    out = JsonFormatter().format(
        _record(name="petbank.access", event="http_request", status_code=200)
    )
    data = json.loads(out)
    assert data["event"] == "http_request"
    assert data["status_code"] == 200


def test_formatter_includes_request_id_when_set():
    data = json.loads(JsonFormatter().format(_record(request_id="abc123")))
    assert data["request_id"] == "abc123"


def test_decision_log_has_structured_fields(client, caplog):
    with caplog.at_level(logging.INFO, logger="server"):
        client.post("/applications", json=_adult_payload())
    finals = [r for r in caplog.records if getattr(r, "status", None) == "approved"]
    assert finals, "ожидали итоговый лог решения со status=approved"
    assert all(getattr(r, "application_id", None) for r in finals)


def _access_records(caplog):
    return [r for r in caplog.records if getattr(r, "event", None) == "http_request"]


def test_request_metadata_logged_and_header_set(client, caplog):
    with caplog.at_level(logging.INFO, logger="petbank.access"):
        resp = client.post("/applications", json=_adult_payload())
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID")
    records = _access_records(caplog)
    assert len(records) == 1
    rec = records[0]
    assert rec.method == "POST"
    assert rec.path == "/applications"
    assert rec.status_code == 200
    assert isinstance(rec.duration_ms, float)


def test_full_request_body_logged(client, caplog):
    payload = _adult_payload()
    with caplog.at_level(logging.INFO, logger="petbank.access"):
        client.post("/applications", json=payload)
    rec = _access_records(caplog)[0]
    assert rec.body["phone"] == payload["phone"]
    assert rec.body["last_name"] == payload["last_name"]


def test_invalid_json_body_logged_as_text(client, caplog):
    with caplog.at_level(logging.INFO, logger="petbank.access"):
        resp = client.post(
            "/applications",
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code == 422
    rec = _access_records(caplog)[0]
    assert rec.body == "{not json"


def test_incoming_request_id_is_reused(client, caplog):
    with caplog.at_level(logging.INFO, logger="petbank.access"):
        resp = client.get("/health", headers={"X-Request-ID": "test-123"})
    assert resp.headers["X-Request-ID"] == "test-123"
    assert _access_records(caplog)[0].request_id == "test-123"


def test_request_id_correlates_business_and_access_logs(client, caplog):
    with caplog.at_level(logging.INFO):
        client.post("/applications", json=_adult_payload())
    access = _access_records(caplog)[0]
    business = [r for r in caplog.records if r.name == "server"]
    assert access.request_id
    assert business
    assert all(getattr(r, "request_id", "") == access.request_id for r in business)
