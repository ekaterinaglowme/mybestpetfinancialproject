"""Тесты Prometheus-метрик: эндпоинт /metrics и бизнес-метрики."""

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


def test_metrics_endpoint_returns_prometheus_text(client):
    client.get("/health")  # сгенерировать немного трафика
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert not body.lstrip().startswith("{")  # это текст exposition, не JSON
    assert "http_request_duration_seconds" in body


def _post(client, **overrides):
    payload = _adult_payload()
    payload.update(overrides)
    return client.post("/applications", json=payload)


def test_decisions_counter_has_status_label(client):
    _post(client)  # approved (возраст 30)
    minor = date.today().replace(year=date.today().year - 10).isoformat()
    _post(client, birth_date=minor)  # declined (несовершеннолетний)
    body = client.get("/metrics").text
    assert "petbank_decisions_total{" in body
    assert 'status="approved"' in body
    assert 'status="declined"' in body


def test_rejection_reason_category_counter(client):
    minor = date.today().replace(year=date.today().year - 10).isoformat()
    _post(client, birth_date=minor)  # причина: age_below_min
    body = client.get("/metrics").text
    assert 'petbank_rejection_reasons_total{reason="age_below_min"}' in body


def test_application_amount_histogram_present(client):
    _post(client, amount=100000)
    body = client.get("/metrics").text
    assert "petbank_application_amount_rub_bucket" in body
    assert "petbank_application_amount_rub_count" in body


def test_red_metrics_present(client):
    # RED отдаёт instrumentator по умолчанию — фиксируем тестом, чтобы не пропало.
    client.get("/health")  # 2xx
    client.get("/nope")    # 4xx
    body = client.get("/metrics").text
    requests_lines = [l for l in body.splitlines() if l.startswith("http_requests_total{")]
    assert requests_lines, "ожидали http_requests_total (Rate + Errors по status)"
    assert any('status="2xx"' in l for l in requests_lines)  # Errors: лейбл status есть
    assert "http_request_duration_seconds_bucket" in body    # Duration: гистограмма


def test_app_info_metric_exposes_version_and_commit(client):
    body = client.get("/metrics").text
    info_lines = [l for l in body.splitlines() if l.startswith("petbank_app_info{")]
    assert info_lines, "ожидали метрику petbank_app_info"
    assert "version=" in info_lines[0]
    assert "commit=" in info_lines[0]
