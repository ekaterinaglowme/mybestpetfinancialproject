import httpx
import pytest

import black_list


def _patch_client(monkeypatch, handler):
    def factory():
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://bl.test",
        )
    monkeypatch.setattr(black_list, "_make_client", factory)


async def test_passport_in_list(monkeypatch):
    def handler(request):
        assert request.url.path == "/check"
        assert request.url.params["passport"] == "111"
        return httpx.Response(200, json={"passport": "111", "in_terror_list": True})
    _patch_client(monkeypatch, handler)
    assert await black_list.check_passport("111") is True


async def test_passport_not_in_list(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"passport": "222", "in_terror_list": False})
    _patch_client(monkeypatch, handler)
    assert await black_list.check_passport("222") is False


async def test_server_error_raises(monkeypatch):
    def handler(request):
        return httpx.Response(500)
    _patch_client(monkeypatch, handler)
    with pytest.raises(black_list.BlackListError):
        await black_list.check_passport("333")


async def test_malformed_json_raises(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"passport": "444"})  # нет in_terror_list
    _patch_client(monkeypatch, handler)
    with pytest.raises(black_list.BlackListError):
        await black_list.check_passport("444")


async def test_timeout_raises(monkeypatch):
    def handler(request):
        raise httpx.TimeoutException("timed out")
    _patch_client(monkeypatch, handler)
    with pytest.raises(black_list.BlackListError):
        await black_list.check_passport("555")
