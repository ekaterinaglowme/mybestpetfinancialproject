"""Клиент БКИ «КредБюро»: POST /report, XML windows-1251, один ретрай.

Любой сбой (сеть / HTTP / битый XML / «повторите позже») → пауза
BKI_RETRY_DELAY_SECONDS и одна повторная попытка; снова сбой → BkiOutcome
со status="unavailable". Fail-open применяет вызывающий код: заявка едет
дальше без фич бюро (в отличие от чёрного списка — тот fail-closed).
"""

import asyncio
import logging
import os
from dataclasses import dataclass

import httpx

from bki_parse import (
    BkiFeatures,
    BkiParseError,
    BkiRetryable,
    build_request_xml,
    parse_report,
)
from metrics import BKI_RESULT, EXTERNAL_CALL_SECONDS

BKI_URL = os.environ.get("BKI_URL", "http://212.147.238.3:8091")
BKI_TIMEOUT_SECONDS = float(os.environ.get("BKI_TIMEOUT_SECONDS", "3.0"))
BKI_RETRY_DELAY_SECONDS = float(os.environ.get("BKI_RETRY_DELAY_SECONDS", "10"))
BKI_PARTNER_CODE = os.environ.get("BKI_PARTNER_CODE", "PETBANK")

logger = logging.getLogger("petbank.bki")


class BkiError(Exception):
    """Не удалось получить корректный ответ бюро (сеть/HTTP)."""


@dataclass(frozen=True)
class BkiOutcome:
    """Итог похода в бюро: статус + фичи (при ok) + сырой ответ (если был)."""

    status: str                    # ok | no_history | unavailable
    features: BkiFeatures | None   # только при status="ok"
    raw_xml: str | None            # сырой ответ бюро, если хоть что-то пришло


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=BKI_URL, timeout=BKI_TIMEOUT_SECONDS)


# Один клиент на всё приложение (keep-alive), как у чёрного списка.
_client: httpx.AsyncClient | None = None


def configure(client: httpx.AsyncClient | None = None) -> None:
    """Поднять клиент БКИ (по умолчанию — рабочий; в тестах — инжектят)."""
    global _client
    _client = client if client is not None else _make_client()


async def dispose() -> None:
    """Закрыть клиент и сбросить модульное состояние."""
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None


async def _http_call(passport: str) -> bytes:
    """Один HTTP-поход в бюро; тело ответа как есть. Сбой → BkiError."""
    assert _client is not None, "bki.configure() не был вызван"
    body = build_request_xml(passport, BKI_PARTNER_CODE)
    try:
        with EXTERNAL_CALL_SECONDS.labels(service="bki").time():
            resp = await _client.post(
                "/report",
                content=body,
                headers={"Content-Type": "application/xml; charset=windows-1251"},
            )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise BkiError(str(exc)) from exc
    return resp.content


def _decode(raw: bytes | None) -> str | None:
    return raw.decode("windows-1251", errors="replace") if raw is not None else None


async def get_report_with_retry(passport: str) -> BkiOutcome:
    """Поход в бюро с одним ретраем. Никогда не бросает — сбой = unavailable."""
    last_raw: bytes | None = None
    for attempt in (1, 2):
        try:
            raw = await _http_call(passport)
            last_raw = raw
            parsed = parse_report(raw)
        except (BkiError, BkiRetryable, BkiParseError) as exc:
            logger.warning("БКИ: попытка %d не удалась: %s", attempt, exc)
            if attempt == 1:
                await asyncio.sleep(BKI_RETRY_DELAY_SECONDS)
            continue
        if parsed.result_code == 0:
            status = "ok"
        elif parsed.result_code == 3:
            status = "no_history"
        else:
            # Неизвестный код результата — фичам не верим.
            logger.warning("БКИ: неизвестный КодРезультата=%d", parsed.result_code)
            status = "unavailable"
        BKI_RESULT.labels(status=status).inc()
        return BkiOutcome(status=status, features=parsed.features, raw_xml=_decode(raw))

    BKI_RESULT.labels(status="unavailable").inc()
    return BkiOutcome(status="unavailable", features=None, raw_xml=_decode(last_raw))
