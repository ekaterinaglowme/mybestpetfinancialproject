"""Мок БКИ «КредБюро» для чёрно-ящичных тестов.

Контракт реального бюро (как в app/src/bki.py): POST /report с XML
windows-1251 → кредитный отчёт XML windows-1251. Разные паспорта дают
детерминированные сценарии; незнакомые паспорта → «истории нет» (Код=3),
чтобы не влиять на сценарии us1–us4.
Без сторонних зависимостей (stdlib) — контейнер запускает файл на python:3.13-slim.
"""

import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ВАЖНО: паспорта продублированы литералами в test_us5_checks_pipeline.py —
# держать значения синхронно.
CLEAN_PASSPORT = "0000024949"       # Код=0, история без просрочек
NO_HISTORY_PASSPORT = "6516841025"  # Код=3, сведений нет
DELINQUENT_PASSPORT = "0000990052"  # Код=0, списанный договор с долгом (стоп)
DOWN_PASSPORT = "0000000009"        # Код=9 всегда → у приложения fail-closed (отказ)

_HEAD = '<?xml version="1.0" encoding="windows-1251"?>'


def _report(inner: str) -> bytes:
    xml = _HEAD + '<КредитныйОтчетОтвет ВерсияФормата="2.4">' + inner + "</КредитныйОтчетОтвет>"
    return xml.encode("windows-1251")


def _contract(state: str, principal: int, overdue: int, plt: str) -> str:
    return (
        f'<Договор><Тип Код="6"/><Состояние Код="{state}"/>'
        f"<Суммы><СуммаОбязательства>{principal}</СуммаОбязательства>"
        f"<ПросроченнаяЗадолженность>{overdue}</ПросроченнаяЗадолженность></Суммы>"
        f"<ПлатежнаяДисциплина><ПлтСтрока>{plt}</ПлтСтрока></ПлатежнаяДисциплина></Договор>"
    )


def _ok_report(contracts: str, score: int) -> bytes:
    return _report(
        "<Служебная><КодРезультата>0</КодРезультата></Служебная>"
        f"<Скоринг><Балл>{score}</Балл></Скоринг>"
        f"<СведенияОбОбязательствах>{contracts}</СведенияОбОбязательствах>"
        '<ИнформационнаяЧасть><Запросы За30Дней="1" За90Дней="2" За12Месяцев="3"/></ИнформационнаяЧасть>'
    )


NO_HISTORY_XML = _report("<Служебная><КодРезультата>3</КодРезультата></Служебная>")
RETRY_XML = _report("<Служебная><КодРезультата>9</КодРезультата></Служебная>")
CLEAN_XML = _ok_report(_contract("12", 5000000, 0, "11111111"), score=720)
DELINQUENT_XML = _ok_report(
    _contract("12", 5000000, 0, "1111") + _contract("52", 434900, 434900, "9"),
    score=510,
)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._send(200, b'{"status": "ok"}', "application/json")
            return
        self._send(404, b"{}", "application/json")

    def do_POST(self):
        if self.path != "/report":
            self._send(404, b"{}", "application/json")
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("windows-1251", errors="replace")
        m = re.search(r'Серия="(\d+)"\s+Номер="(\d+)"', body)
        passport = (m.group(1) + m.group(2)) if m else ""
        if passport == DOWN_PASSPORT:
            payload = RETRY_XML
        elif passport == CLEAN_PASSPORT:
            payload = CLEAN_XML
        elif passport == DELINQUENT_PASSPORT:
            payload = DELINQUENT_XML
        else:
            payload = NO_HISTORY_XML
        self._send(200, payload, "application/xml; charset=windows-1251")

    def _send(self, code: int, data: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # тишина в логах контейнера
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8091), Handler).serve_forever()
