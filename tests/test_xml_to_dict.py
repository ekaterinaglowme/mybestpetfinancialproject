"""Приведение XML-ответа БКИ к JSON-словарю для журнала external_service_calls."""

from bki_parse import xml_to_dict


def test_attributes_text_and_repeats():
    raw = '<r a="1"><x>hi</x><y>1</y><y>2</y></r>'
    assert xml_to_dict(raw) == {"r": {"@a": "1", "x": "hi", "y": ["1", "2"]}}


def test_nested_bki_like():
    raw = (
        '<Отчет><Скоринг><Балл Метод="SCR-11">702</Балл></Скоринг>'
        '<Обяз КоличествоДоговоров="2"><Договор>a</Договор><Договор>b</Договор></Обяз>'
        "</Отчет>"
    )
    result = xml_to_dict(raw)
    assert result["Отчет"]["Скоринг"]["Балл"] == {"@Метод": "SCR-11", "#text": "702"}
    assert result["Отчет"]["Обяз"]["@КоличествоДоговоров"] == "2"
    assert result["Отчет"]["Обяз"]["Договор"] == ["a", "b"]


def test_plain_leaf_is_text():
    assert xml_to_dict("<a>значение</a>") == {"a": "значение"}
