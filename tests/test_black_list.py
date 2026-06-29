import httpx
import pytest
from prometheus_client import REGISTRY

import black_list


@pytest.fixture(autouse=True)
async def _reset_client():
    # После каждого теста закрываем модульный клиент, чтобы не текло между тестами.
    yield
    await black_list.dispose()


def _use_client(handler):
    """Поднять модульный клиент на MockTransport с заданным обработчиком."""
    black_list.configure(
        httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://bl.test",
        )
    )


async def test_passport_in_list():
    def handler(request):
        assert request.url.path == "/check"
        assert request.url.params["passport"] == "111"
        return httpx.Response(200, json={"passport": "111", "in_terror_list": True})
    _use_client(handler)
    assert await black_list.check_passport("111") is True


async def test_passport_not_in_list():
    def handler(request):
        return httpx.Response(200, json={"passport": "222", "in_terror_list": False})
    _use_client(handler)
    assert await black_list.check_passport("222") is False


async def test_server_error_raises():
    def handler(request):
        return httpx.Response(500)
    _use_client(handler)
    with pytest.raises(black_list.BlackListError):
        await black_list.check_passport("333")


async def test_malformed_json_raises():
    def handler(request):
        return httpx.Response(200, json={"passport": "444"})  # нет in_terror_list
    _use_client(handler)
    with pytest.raises(black_list.BlackListError):
        await black_list.check_passport("444")


async def test_timeout_raises():
    def handler(request):
        raise httpx.TimeoutException("timed out")
    _use_client(handler)
    with pytest.raises(black_list.BlackListError):
        await black_list.check_passport("555")


async def test_external_call_duration_recorded():
    # Каждый вызов внешнего сервиса засекается в гистограмму времени.
    def handler(request):
        return httpx.Response(200, json={"in_terror_list": False})
    _use_client(handler)
    labels = {"service": "black_list"}
    name = "petbank_external_call_seconds_count"
    before = REGISTRY.get_sample_value(name, labels) or 0.0
    await black_list.check_passport("777")
    after = REGISTRY.get_sample_value(name, labels)
    assert after == before + 1


async def test_call_phases_duration_recorded():
    # Вызов чёрного списка засекается по фазам: обработка запроса сервером
    # (до получения заголовков ответа) и чтение тела ответа.
    def handler(request):
        return httpx.Response(200, json={"in_terror_list": False})
    _use_client(handler)
    name = "petbank_black_list_phase_seconds_count"
    before_req = REGISTRY.get_sample_value(name, {"phase": "request"}) or 0.0
    before_resp = REGISTRY.get_sample_value(name, {"phase": "response"}) or 0.0
    await black_list.check_passport("888")
    assert REGISTRY.get_sample_value(name, {"phase": "request"}) == before_req + 1
    assert REGISTRY.get_sample_value(name, {"phase": "response"}) == before_resp + 1


async def test_client_created_once_not_per_call(monkeypatch):
    # Клиент поднимается один раз в configure() и переиспользует пул соединений,
    # а не создаётся заново на каждый вызов check_passport.
    calls = {"n": 0}

    def counting():
        calls["n"] += 1
        return httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"in_terror_list": False})
            ),
            base_url="http://bl.test",
        )

    monkeypatch.setattr(black_list, "_make_client", counting)
    black_list.configure()
    await black_list.check_passport("1")
    await black_list.check_passport("2")
    assert calls["n"] == 1
    await black_list.dispose()


async def test_dispose_closes_client():
    # dispose() закрывает клиента и сбрасывает модульное состояние.
    black_list.configure()
    client = black_list._client
    await black_list.dispose()
    assert black_list._client is None
    assert client.is_closed
