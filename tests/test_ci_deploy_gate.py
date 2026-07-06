"""«Ворота» после деплоя: неуспешная проба обязана валить джобу деплоя.

Деплой на VM заканчивается пробами `curl -fsS .../health` и `.../ready`. Красный
деплой при неготовом сервисе держится на флаге `-f` (fail): без него curl вернул
бы успех даже на 503, и битый деплой уехал бы «зелёным».

Тесты фиксируют оба свойства, чтобы их нельзя было сломать незаметно:
  1. механизм — `curl -f` реально валит шаг на 503 и не мешает на 200;
  2. workflow — сам ci.yml дёргает обе пробы через `curl -f` в &&-цепочке.
"""

import http.server
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

CI_YML = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"

curl_required = pytest.mark.skipif(
    shutil.which("curl") is None, reason="нужен системный curl"
)


class _ProbeHandler(http.server.BaseHTTPRequestHandler):
    """Мок пробы: /health отвечает 200 (готов), всё прочее — 503 (не готов)."""

    def do_GET(self):
        code = 200 if self.path == "/health" else 503
        self.send_response(code)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):  # не засорять вывод тестов
        pass


@pytest.fixture
def probe_url():
    """Локальный сервер-заглушка; отдаёт base_url и сам гасится после теста."""
    srv = http.server.HTTPServer(("127.0.0.1", 0), _ProbeHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        srv.shutdown()


def _curl(*args: str) -> int:
    """Запустить curl молча, вернуть код завершения (как в &&-цепочке деплоя)."""
    return subprocess.run(
        ["curl", *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode


@curl_required
def test_gotovaya_proba_ne_valit_deploy(probe_url):
    # 200 → `curl -fsS` завершается кодом 0 → &&-цепочка идёт дальше, деплой зелёный.
    assert _curl("-fsS", f"{probe_url}/health") == 0


@curl_required
def test_negotovaya_proba_krasit_deploy(probe_url):
    # 503 → `curl -fsS` завершается ненулевым кодом → &&-цепочка рвётся, ssh
    # возвращает ошибку → джоба деплоя КРАСНАЯ. Ровно это поведение и нужно.
    assert _curl("-fsS", f"{probe_url}/ready") != 0


@curl_required
def test_bez_flaga_f_bityj_deploy_uehal_by_zelenym(probe_url):
    # Контроль-«антипример»: БЕЗ -f тот же 503 даёт код 0 (curl «успешно скачал»
    # тело ошибки) → деплой был бы зелёным. Показывает, зачем -f обязателен.
    assert _curl("-sS", f"{probe_url}/ready") == 0


def test_workflow_probit_obe_proby_s_flagom_f():
    # Guard: обе пробы в деплое обязаны идти через `curl -f` внутри &&-цепочки.
    # Уберут -f или саму пробу — тест покраснеет здесь, ещё до мержа в main.
    text = CI_YML.read_text(encoding="utf-8")
    for path in ("/health", "/ready"):
        m = re.search(
            rf"&&\s*curl\s+(-\S+)\s+http://127\.0\.0\.1:8000{re.escape(path)}", text
        )
        assert m, f"проба {path} должна идти в &&-цепочке (падение обрывает деплой)"
        assert "f" in m.group(1), (
            f"проба {path} без флага -f: битый деплой уедет зелёным"
        )
