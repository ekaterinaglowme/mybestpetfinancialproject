"""Клиент сервиса чёрного списка паспортов.

Внешний сервис: GET {BLACK_LIST_URL}/check?passport=... -> {"in_terror_list": bool}.
Любой сбой связи/ответа -> BlackListError; вызывающий применяет fail-closed
(отклоняет заявку).
"""

import os

import httpx

from metrics import BLACK_LIST_PHASE_SECONDS, EXTERNAL_CALL_SECONDS

BLACK_LIST_URL = os.environ.get("BLACK_LIST_URL", "http://212.147.238.3:8090")
BLACK_LIST_TIMEOUT_SECONDS = float(os.environ.get("BLACK_LIST_TIMEOUT_SECONDS", "0.8"))


class BlackListError(Exception):
    """Не удалось получить корректный ответ от сервиса чёрного списка."""


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=BLACK_LIST_URL, timeout=BLACK_LIST_TIMEOUT_SECONDS,
    )


# Один клиент на всё приложение: переиспользует keep-alive пул соединений.
# Поднимается в configure() (в lifespan), закрывается в dispose().
_client: httpx.AsyncClient | None = None


def configure(client: httpx.AsyncClient | None = None) -> None:
    """Поднять клиент чёрного списка (по умолчанию — рабочий; в тестах — инжектят)."""
    global _client
    _client = client if client is not None else _make_client()


async def dispose() -> None:
    """Закрыть клиент и сбросить модульное состояние."""
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None


async def check_passport(passport: str) -> bool:
    """True — паспорт в чёрном списке. Бросает BlackListError при любом сбое."""
    assert _client is not None, "black_list.configure() не был вызван"
    # Стримим ответ вручную, чтобы развести две фазы вызова: время обработки
    # заявки сервисом (до заголовков ответа) и время получения тела ответа.
    request = _client.build_request("GET", "/check", params={"passport": passport})
    try:
        with EXTERNAL_CALL_SECONDS.labels(service="black_list").time():
            with BLACK_LIST_PHASE_SECONDS.labels(phase="request").time():
                resp = await _client.send(request, stream=True)
            try:
                with BLACK_LIST_PHASE_SECONDS.labels(phase="response").time():
                    await resp.aread()
            finally:
                await resp.aclose()
        resp.raise_for_status()
        return bool(resp.json()["in_terror_list"])
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        raise BlackListError(str(exc)) from exc
