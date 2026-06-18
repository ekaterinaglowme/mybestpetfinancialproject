"""JSON-логирование для PetBank.

Каждое лог-событие пишется одной JSON-строкой в stdout. В каждую запись через
record factory добавляется request_id текущего HTTP-запроса — так логи одного
запроса (включая бизнес-логи в make_decision) связываются между собой.
"""

import contextvars
import datetime
import json
import logging
import sys

# request_id текущего запроса; вне запроса — пустая строка.
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)

# Имя нашего хендлера — чтобы setup_logging был идемпотентным и не трогал чужие
# хендлеры (например, тот, что добавляет pytest caplog).
_HANDLER_NAME = "petbank_json"


class JsonFormatter(logging.Formatter):
    """Сериализует LogRecord в одну JSON-строку."""

    # Стандартные атрибуты LogRecord текущей версии Python — всё, что НЕ в этом
    # наборе, считается structured-полем из extra и попадает в JSON.
    _RESERVED = set(
        logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
    ) | {"message", "asctime", "request_id"}

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.datetime.fromtimestamp(
            record.created, datetime.timezone.utc
        )
        payload = {
            "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", "")
        if request_id:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key not in self._RESERVED:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


# Базовая фабрика записей — захватываем один раз, чтобы повторные setup_logging
# не оборачивали фабрику многократно.
_BASE_FACTORY = logging.getLogRecordFactory()


def _install_request_id_factory() -> None:
    def factory(*args, **kwargs):
        record = _BASE_FACTORY(*args, **kwargs)
        record.request_id = request_id_ctx.get()
        return record

    logging.setLogRecordFactory(factory)


def setup_logging(level: int = logging.INFO) -> None:
    """Идемпотентно: один JSON-хендлер на stdout + инъекция request_id."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "name", None) == _HANDLER_NAME:
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.name = _HANDLER_NAME
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    _install_request_id_factory()
