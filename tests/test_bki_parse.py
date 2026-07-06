"""Разбор протокола БКИ: XML windows-1251 → фичи. Примеры — из регламента бюро."""

import pytest

from bki_parse import (
    BkiParseError,
    BkiRetryable,
    build_request_xml,
    parse_report,
)


def _report_xml(inner: str) -> bytes:
    """Обёртка ответа бюро вокруг подставляемой середины."""
    xml = (
        '<?xml version="1.0" encoding="windows-1251"?>'
        '<КредитныйОтчетОтвет ВерсияФормата="2.4" КодУчастника="7742">'
        + inner +
        "</КредитныйОтчетОтвет>"
    )
    return xml.encode("windows-1251")


# Ответ с историей — структура примера 6.1 регламента (сокращён до сути).
FULL_REPORT = _report_xml(
    "<Служебная><ИдОтвета>x</ИдОтвета><КодРезультата>0</КодРезультата></Служебная>"
    '<Субъект><ФИО Фамилия="Сидоров" Имя="Dmitry"/></Субъект>'
    '<Скоринг><Балл Метод="SCR-11">702</Балл></Скоринг>'
    '<СведенияОбОбязательствах КоличествоДоговоров="3">'
    '<Договор НомерЗаписи="1"><Тип Код="6"/><Состояние Код="12"/>'
    '<Суммы Валюта="RUB"><СуммаОбязательства>32967400</СуммаОбязательства>'
    "<ПросроченнаяЗадолженность>0</ПросроченнаяЗадолженность></Суммы>"
    '<ПлатежнаяДисциплина Формат="СП-МЕС"><ПлтСтрока>1111A111</ПлтСтрока></ПлатежнаяДисциплина>'
    "</Договор>"
    '<Договор НомерЗаписи="2"><Тип Код="6"/><Состояние Код="13"/>'
    '<Суммы Валюта="RUB"><СуммаОбязательства>14112200</СуммаОбязательства>'
    "<ПросроченнаяЗадолженность>0</ПросроченнаяЗадолженность></Суммы>"
    '<ПлатежнаяДисциплина Формат="СП-МЕС"><ПлтСтрока>11X11</ПлтСтрока></ПлатежнаяДисциплина>'
    "</Договор>"
    '<Договор НомерЗаписи="3"><Тип Код="9"/><Состояние Код="52"/>'
    '<Суммы Валюта="RUB"><СуммаОбязательства>434900</СуммаОбязательства>'
    "<ПросроченнаяЗадолженность>434900</ПросроченнаяЗадолженность></Суммы>"
    '<ПлатежнаяДисциплина Формат="СП-МЕС"><ПлтСтрока>9</ПлтСтрока></ПлатежнаяДисциплина>'
    "</Договор>"
    "</СведенияОбОбязательствах>"
    '<ИнформационнаяЧасть><Запросы За30Дней="1" За90Дней="3" За12Месяцев="6"/></ИнформационнаяЧасть>'
)

# Ответ «истории нет» — пример 6.2 регламента.
NO_HISTORY = _report_xml(
    "<Служебная><КодРезультата>3</КодРезультата></Служебная>"
    "<Пояснение>СВЕДЕНИЯ ПО СУБЪЕКТУ В БЮРО НЕ НАЙДЕНЫ</Пояснение>"
)

RETRY_LATER = _report_xml("<Служебная><КодРезультата>9</КодРезультата></Служебная>")


def test_request_xml_splits_passport_and_encodes_cp1251():
    raw = build_request_xml("4512123456", "PETBANK")
    text = raw.decode("windows-1251")
    assert 'Серия="4512"' in text
    assert 'Номер="123456"' in text
    assert 'Код="PETBANK"' in text
    assert 'encoding="windows-1251"' in text


def test_full_report_features():
    parsed = parse_report(FULL_REPORT)
    assert parsed.result_code == 0
    f = parsed.features
    assert f.score == 702
    assert f.n_contracts == 3
    assert f.has_writeoff is True                # договор с Состояние=52
    assert f.has_current_delinquency is True     # 52 И просрочка > 0
    assert f.overdue_amount_kop == 434900
    assert f.max_dpd == 6                        # худший символ — «9»
    assert f.n_late == 2                         # «A» и «9»; «X» не считается
    assert f.debt_load_kop == 14112200 + 434900  # живой долг: Код 13 и 52/59 → 13 + 52
    assert (f.inq_30, f.inq_90, f.inq_365) == (1, 3, 6)


def test_no_history_has_no_features():
    parsed = parse_report(NO_HISTORY)
    assert parsed.result_code == 3
    assert parsed.features is None


def test_result_code_9_raises_retryable():
    with pytest.raises(BkiRetryable):
        parse_report(RETRY_LATER)


def test_broken_xml_raises_parse_error():
    with pytest.raises(BkiParseError):
        parse_report(b"<xml broken")


def test_missing_result_code_raises_parse_error():
    with pytest.raises(BkiParseError):
        parse_report(_report_xml("<Служебная></Служебная>"))
