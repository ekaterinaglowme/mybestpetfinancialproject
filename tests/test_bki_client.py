"""Клиент БКИ: ретрай с паузой, fail-open, сырой ответ при сбое разбора."""

import httpx
import pytest

import bki
from bki_parse import BkiFeatures

CLEAN_XML = (
    '<?xml version="1.0" encoding="windows-1251"?>'
    '<КредитныйОтчетОтвет><Служебная><КодРезультата>0</КодРезультата></Служебная>'
    "<Скоринг><Балл>702</Балл></Скоринг></КредитныйОтчетОтвет>"
).encode("windows-1251")

NO_HISTORY_XML = (
    '<?xml version="1.0" encoding="windows-1251"?>'
    "<КредитныйОтчетОтвет><Служебная><КодРезультата>3</КодРезультата></Служебная>"
    "</КредитныйОтчетОтвет>"
).encode("windows-1251")

RETRY_XML = (
    '<?xml version="1.0" encoding="windows-1251"?>'
    "<КредитныйОтчетОтвет><Служебная><КодРезультата>9</КодРезультата></Служебная>"
    "</КредитныйОтчетОтвет>"
).encode("windows-1251")


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    """Тесты не должны спать 10 секунд между попытками."""
    monkeypatch.setattr(bki, "BKI_RETRY_DELAY_SECONDS", 0)


@pytest.fixture
def bki_client():
    """Фабрика: поднимает клиент над httpx.MockTransport и гасит после теста."""
    def _make(handler):
        transport = httpx.MockTransport(handler)
        bki.configure(httpx.AsyncClient(transport=transport, base_url="http://bki.test"))
    yield _make
    # dispose — async; здесь достаточно сбросить модульное состояние.
    bki._client = None


@pytest.mark.asyncio
async def test_ok_first_try(bki_client):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=CLEAN_XML)

    bki_client(handler)
    outcome = await bki.get_report_with_retry("1234567890")
    assert outcome.status == "ok"
    assert isinstance(outcome.features, BkiFeatures)
    assert outcome.features.score == 702
    assert "КодРезультата" in outcome.raw_xml
    assert len(calls) == 1
    # Запрос ушёл в нужную ручку нужным методом.
    assert calls[0].method == "POST" and calls[0].url.path == "/report"


@pytest.mark.asyncio
async def test_no_history(bki_client):
    bki_client(lambda request: httpx.Response(200, content=NO_HISTORY_XML))
    outcome = await bki.get_report_with_retry("6516841025")
    assert outcome.status == "no_history"
    assert outcome.features is None
    assert outcome.raw_xml is not None


@pytest.mark.asyncio
async def test_retry_after_code_9_then_success(bki_client):
    responses = [
        httpx.Response(200, content=RETRY_XML),
        httpx.Response(200, content=CLEAN_XML),
    ]
    bki_client(lambda request: responses.pop(0))
    outcome = await bki.get_report_with_retry("1234567890")
    assert outcome.status == "ok"
    assert not responses  # обе заготовки израсходованы — ретрай был


@pytest.mark.asyncio
async def test_unavailable_after_two_failures_keeps_last_raw(bki_client):
    bki_client(lambda request: httpx.Response(200, content=RETRY_XML))
    outcome = await bki.get_report_with_retry("1234567890")
    assert outcome.status == "unavailable"
    assert outcome.features is None
    # Ответ был получен (пусть и «повторите позже») — он сохранён для отладки.
    assert "КодРезультата" in outcome.raw_xml


@pytest.mark.asyncio
async def test_unavailable_on_network_error_raw_is_none(bki_client):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    bki_client(handler)
    outcome = await bki.get_report_with_retry("1234567890")
    assert outcome.status == "unavailable"
    assert outcome.raw_xml is None


@pytest.mark.asyncio
async def test_http_403_is_unavailable(bki_client):
    bki_client(lambda request: httpx.Response(403, text="wrong partner"))
    outcome = await bki.get_report_with_retry("1234567890")
    assert outcome.status == "unavailable"
