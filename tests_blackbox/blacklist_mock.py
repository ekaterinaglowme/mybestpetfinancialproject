"""Мок внешнего сервиса СтопЛиста для чёрно-ящичных тестов.

Контракт реального сервиса (как в app/src/black_list.py):
    GET /check?passport=... -> {"in_terror_list": bool}

Паспорта из BLACKLISTED считаются «в списке», остальные — чистыми.
Без сторонних зависимостей (stdlib http.server) — чтобы не собирать отдельный
образ: контейнер просто запускает этот файл на python:3.13-slim.
"""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# ВАЖНО: эти паспорта продублированы литералами в тестах (test_us3_applications_v2.py,
# test_us4_ops.py) — держать значения синхронно с тамошними *_PASSPORT.
# Паспорт, который мок всегда считает «в чёрном списке».
BLACKLISTED = {"0000000000"}
# Паспорт, на котором мок имитирует сбой сервиса (HTTP 500).
ERROR_PASSPORTS = {"5000000000"}
# Паспорт, на котором мок отвечает с большой задержкой (провоцирует таймаут запроса).
SLOW_PASSPORTS = {"9999999999"}
SLOW_DELAY_SECONDS = 2.0


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json(200, {"status": "ok"})
            return
        if parsed.path == "/check":
            passport = parse_qs(parsed.query).get("passport", [""])[0]
            if passport in ERROR_PASSPORTS:
                self._json(500, {"error": "service unavailable"})
                return
            if passport in SLOW_PASSPORTS:
                time.sleep(SLOW_DELAY_SECONDS)
            self._json(200, {"in_terror_list": passport in BLACKLISTED})
            return
        self._json(404, {"error": "not found"})

    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # тишина в логах контейнера
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
