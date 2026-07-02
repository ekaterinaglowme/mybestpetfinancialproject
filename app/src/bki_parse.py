"""Чистый разбор протокола БКИ «КредБюро»: XML windows-1251 → фичи.

Без IO и без httpx — только сборка запроса и разбор ответа, поэтому
тестируется на примерах из регламента бюро (ред. 2.4). Клиент — в bki.py.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass


class BkiParseError(Exception):
    """Ответ бюро не удалось разобрать (битый XML / нет обязательных полей)."""


class BkiRetryable(Exception):
    """Бюро ответило «сервис временно недоступен» (КодРезультата=9)."""


# Ранг символа платёжной дисциплины (ПлтСтрока): чем выше — тем хуже.
# 1 — вовремя, A — просрочка 1–29 дн, 2–5 — 30+…120+, 9 — безнадёжный долг.
_PLT_RANK = {"1": 0, "A": 1, "2": 2, "3": 3, "4": 4, "5": 5, "9": 6}
# «Нет данных» — в ранжировании и подсчёте просрочек не участвует.
_PLT_SKIP = {"X", "0"}

# Состояния договора: 13 — активный, 12 — погашен, 59 — просрочен, 52 — списан.
_STATE_NEGATIVE = {"52", "59"}       # негатив в истории
_STATE_ALIVE = {"13", "52", "59"}    # долг ещё не закрыт (для закредитованности)


@dataclass(frozen=True)
class BkiFeatures:
    """Фичи кредитного отчёта — будущие входы скоркарты (суммы в копейках)."""

    score: int | None
    n_contracts: int
    has_writeoff: bool
    has_current_delinquency: bool
    overdue_amount_kop: int
    max_dpd: int | None
    n_late: int
    debt_load_kop: int
    inq_30: int | None
    inq_90: int | None
    inq_365: int | None


@dataclass(frozen=True)
class ParsedReport:
    """Итог разбора ответа бюро: код результата + фичи (только при коде 0)."""

    result_code: int
    features: BkiFeatures | None


def build_request_xml(passport: str, partner_code: str) -> bytes:
    """XML запроса в бюро: паспорт «4512123456» → Серия=4512, Номер=123456."""
    seriya, nomer = passport[:4], passport[4:]
    xml = (
        '<?xml version="1.0" encoding="windows-1251"?>'
        f'<Запрос><Партнер Код="{partner_code}"/>'
        f'<Субъект><Паспорт Серия="{seriya}" Номер="{nomer}"/></Субъект></Запрос>'
    )
    return xml.encode("windows-1251")


def parse_report(raw: bytes) -> ParsedReport:
    """Разбор ответа. КодРезультата: 0 → фичи, 9 → BkiRetryable, иное → без фич."""
    try:
        root = ET.fromstring(raw)
        result_code = int(root.findtext("Служебная/КодРезультата"))
    except (ET.ParseError, TypeError, ValueError) as exc:
        raise BkiParseError(str(exc)) from exc
    if result_code == 9:
        raise BkiRetryable("бюро временно недоступно (КодРезультата=9)")
    if result_code != 0:
        return ParsedReport(result_code=result_code, features=None)
    return ParsedReport(result_code=0, features=_extract_features(root))


def _int_or_none(text: str | None) -> int | None:
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _extract_features(root: ET.Element) -> BkiFeatures:
    contracts = root.findall(".//Договор")

    has_writeoff = False
    has_current_delinquency = False
    overdue_total = 0
    debt_load = 0
    max_rank: int | None = None
    n_late = 0
    for contract in contracts:
        state_el = contract.find("Состояние")
        state = state_el.get("Код") if state_el is not None else None
        overdue = _int_or_none(contract.findtext("Суммы/ПросроченнаяЗадолженность")) or 0
        principal = _int_or_none(contract.findtext("Суммы/СуммаОбязательства")) or 0

        if state in _STATE_NEGATIVE:
            has_writeoff = True
            if overdue > 0:
                has_current_delinquency = True
        if state in _STATE_ALIVE:
            debt_load += principal
        overdue_total += overdue

        plt = contract.findtext("ПлатежнаяДисциплина/ПлтСтрока") or ""
        for ch in plt:
            if ch in _PLT_SKIP:
                continue
            rank = _PLT_RANK.get(ch)
            if rank is None:
                continue
            max_rank = rank if max_rank is None else max(max_rank, rank)
            if rank > 0:
                n_late += 1

    inq = root.find("ИнформационнаяЧасть/Запросы")
    inq_30 = _int_or_none(inq.get("За30Дней")) if inq is not None else None
    inq_90 = _int_or_none(inq.get("За90Дней")) if inq is not None else None
    inq_365 = _int_or_none(inq.get("За12Месяцев")) if inq is not None else None

    return BkiFeatures(
        score=_int_or_none(root.findtext("Скоринг/Балл")),
        n_contracts=len(contracts),
        has_writeoff=has_writeoff,
        has_current_delinquency=has_current_delinquency,
        overdue_amount_kop=overdue_total,
        max_dpd=max_rank,
        n_late=n_late,
        debt_load_kop=debt_load,
        inq_30=inq_30,
        inq_90=inq_90,
        inq_365=inq_365,
    )
