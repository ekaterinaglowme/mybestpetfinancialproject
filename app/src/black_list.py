"""Клиент сервиса чёрного списка паспортов.

Внешний сервис: GET {BLACK_LIST_URL}/check?passport=... -> {"in_terror_list": bool}.
Любой сбой связи/ответа -> BlackListError; вызывающий применяет fail-closed
(отклоняет заявку).
"""

import os

import httpx

from metrics import EXTERNAL_CALL_SECONDS

BLACK_LIST_URL = os.environ.get("BLACK_LIST_URL", "http://212.147.238.3:8090")
BLACK_LIST_TIMEOUT_SECONDS = float(os.environ.get("BLACK_LIST_TIMEOUT_SECONDS", "0.8"))


class BlackListError(Exception):
    """Не удалось получить корректный ответ от сервиса чёрного списка."""


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=BLACK_LIST_URL, timeout=BLACK_LIST_TIMEOUT_SECONDS,
    )


async def check_passport(passport: str) -> bool:
    """True — паспорт в чёрном списке. Бросает BlackListError при любом сбое."""
    try:
        async with _make_client() as client:
            with EXTERNAL_CALL_SECONDS.labels(service="black_list").time():
                resp = await client.get("/check", params={"passport": passport})
            resp.raise_for_status()
            return bool(resp.json()["in_terror_list"])
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        raise BlackListError(str(exc)) from exc
