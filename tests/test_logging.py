"""Тесты JSON-логирования: форматтер, middleware, корреляция request_id."""

import json
import logging

from logging_setup import JsonFormatter


def _record(name="server", level=logging.INFO, msg="сообщение", **extra):
    record = logging.LogRecord(name, level, __file__, 1, msg, (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formatter_outputs_valid_json_with_base_fields():
    out = JsonFormatter().format(_record(msg="Заявка одобрена"))
    data = json.loads(out)
    assert data["level"] == "INFO"
    assert data["logger"] == "server"
    assert data["message"] == "Заявка одобрена"
    assert "timestamp" in data
    # Кириллица не экранируется
    assert "Заявка одобрена" in out
    # request_id пуст → поле опущено
    assert "request_id" not in data


def test_formatter_includes_extra_fields():
    out = JsonFormatter().format(
        _record(name="petbank.access", event="http_request", status_code=200)
    )
    data = json.loads(out)
    assert data["event"] == "http_request"
    assert data["status_code"] == 200


def test_formatter_includes_request_id_when_set():
    data = json.loads(JsonFormatter().format(_record(request_id="abc123")))
    assert data["request_id"] == "abc123"
