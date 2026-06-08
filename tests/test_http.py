"""Интеграционные тесты HTTP-слоя.

Поднимаем настоящий сервер на свободном порту в фоновом потоке и дёргаем
его через urllib — проверяем коды ответов и тело, как увидит реальный клиент.
"""

import json
import threading
from datetime import date
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from server import Handler


@pytest.fixture()
def base_url():
    # Порт 0 — ОС сама выдаёт свободный, тесты не конфликтуют между собой.
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _post(url, body):
    data = json.dumps(body).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except HTTPError as e:
        return e.code, json.loads(e.read())


def _get(url):
    try:
        with urlopen(url) as resp:
            return resp.status, json.loads(resp.read())
    except HTTPError as e:
        return e.code, json.loads(e.read())


def _adult_payload():
    born = date.today().replace(year=date.today().year - 30)
    return {
        "last_name": "Иванов",
        "first_name": "Иван",
        "phone": "+79991234567",
        "birth_date": born.isoformat(),
    }


def test_health(base_url):
    status, body = _get(f"{base_url}/health")
    assert status == 200
    assert body == {"status": "ok"}


def test_root_help(base_url):
    status, body = _get(f"{base_url}/")
    assert status == 200
    assert body["service"] == "PetBank"


def test_application_approved(base_url):
    status, body = _post(f"{base_url}/applications", _adult_payload())
    assert status == 200
    assert body["status"] == "approved"
    assert body["applicant"]["age"] == 30
    assert body["reasons"] == []


def test_application_declined_minor(base_url):
    payload = _adult_payload()
    payload["birth_date"] = date.today().replace(year=date.today().year - 10).isoformat()
    status, body = _post(f"{base_url}/applications", payload)
    assert status == 200
    assert body["status"] == "declined"
    assert body["reasons"]


def test_application_validation_error(base_url):
    status, body = _post(f"{base_url}/applications", {"first_name": "Иван"})
    assert status == 400
    assert body["error"] == "validation_error"
    assert body["details"]


def test_application_invalid_json(base_url):
    req = Request(
        f"{base_url}/applications",
        data=b"{not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req) as resp:
            status = resp.status
    except HTTPError as e:
        status = e.code
    assert status == 400


def test_unknown_path_404(base_url):
    status, _ = _get(f"{base_url}/nope")
    assert status == 404
