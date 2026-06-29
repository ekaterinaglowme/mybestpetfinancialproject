"""US-1. Подача заявки на кредит v1 — POST /applications.

Чёрный ящик: проверяем контракт ответа и валидацию, НЕ конкретные пороги
бизнес-правил (возраст/страна меняются и живут в юнит-тестах).
"""

import uuid

import httpx
import pytest


def _payload(**over) -> dict:
    base = {
        "last_name": "Иванов",
        "first_name": "Иван",
        "middle_name": "Иванович",
        "phone": "+79991234567",
        "birth_date": "1995-05-15",
        "country": "Россия",
        "amount": 100000,
    }
    base.update(over)
    return base


@pytest.mark.blackbox
def test_otvet_sootvetstvuet_kontraktu(base_url):
    """Валидная заявка возвращает ответ заявленной формы.

    Дано: корректно заполненная заявка.
    Когда: POST /applications.
    Тогда: HTTP 200; в ответе есть application_id (валидный uuid), status из
           {approved, declined}, applicant с ФИО/возрастом/телефоном, reasons —
           список, received_at. ФИО и телефон совпадают с поданными. Какое именно
           решение — НЕ проверяем: это бизнес-правило.
    """
    p = _payload()
    r = httpx.post(f"{base_url}/applications", json=p, timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    uuid.UUID(body["application_id"])  # валидный uuid — иначе ValueError
    assert body["status"] in {"approved", "declined"}
    assert isinstance(body["reasons"], list)
    assert body["received_at"]
    applicant = body["applicant"]
    assert applicant["full_name"] == "Иванов Иван Иванович"
    assert applicant["phone"] == p["phone"]
    assert isinstance(applicant["age"], int)


@pytest.mark.blackbox
@pytest.mark.parametrize(
    "bad, why",
    [
        ({"phone": None}, "нет обязательного телефона"),
        ({"birth_date": "2999-01-01"}, "дата рождения в будущем"),
        ({"birth_date": "15-05-1995"}, "неверный формат даты"),
    ],
)
def test_krivaya_zayavka_otvergaetsya_422(base_url, bad, why):
    """Некорректная заявка отклоняется с понятной ошибкой, а не «проваливается».

    Дано: заявка с дефектом (по очереди: нет телефона / дата в будущем /
          неверный формат даты).
    Когда: POST /applications.
    Тогда: HTTP 422 (контракт валидации; от бизнес-правил не зависит).
    """
    r = httpx.post(f"{base_url}/applications", json=_payload(**bad), timeout=10)
    assert r.status_code == 422, f"{why}: {r.status_code} {r.text}"
