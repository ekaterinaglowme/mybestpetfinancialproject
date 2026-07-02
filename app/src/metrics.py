"""Бизнес-метрики PetBank для Prometheus.

Метрики регистрируются в стандартном REGISTRY prometheus_client и попадают в
тот же /metrics, который поднимает instrumentator.
"""

import os

from prometheus_client import Counter, Histogram, Info

# Обработанные заявки по итогу решения.
# status: approved | declined; country — нормализованная строка страны.
DECISIONS = Counter(
    "petbank_decisions_total",
    "Обработанные заявки по итогу решения",
    ["status", "country"],
)

# Причины отказов по ограниченному набору категорий (без сырого текста причины —
# чтобы не плодить кардинальность лейблов).
REJECTION_REASONS = Counter(
    "petbank_rejection_reasons_total",
    "Причины отказов по категориям",
    ["reason"],
)

# Распределение запрошенных сумм заявок (руб.).
APPLICATION_AMOUNT_RUB = Histogram(
    "petbank_application_amount_rub",
    "Распределение запрошенных сумм заявок (руб.)",
    buckets=(10_000, 50_000, 100_000, 250_000, 500_000, 1_000_000, float("inf")),
)

# Версия и коммит приложения (build-info). Значения прокидываются при сборке
# Docker-образа: CI передаёт build-arg GIT_COMMIT=${github.sha}, Dockerfile кладёт
# его в переменную окружения. Локально/без сборки — "unknown".
_COMMIT = os.environ.get("GIT_COMMIT", "unknown")
_VERSION = os.environ.get("APP_VERSION") or (
    _COMMIT[:12] if _COMMIT != "unknown" else "dev"
)
APP_INFO = Info("petbank_app", "Версия и коммит приложения PetBank")
APP_INFO.info({"version": _VERSION, "commit": _COMMIT})

# --- Защита /applications/v2 под нагрузкой ---
# Запросы, отклонённые rate limiter (429).
RATE_LIMITED = Counter(
    "petbank_rate_limited_total",
    "Запросы к /applications/v2, отклонённые rate limiter (429)",
)

# --- Латентность записи в БД и обращений к внешним сервисам ---
# Время отдельных операций записи в БД (сек), с разбивкой по операции.
DB_WRITE_SECONDS = Histogram(
    "petbank_db_write_seconds",
    "Время операций записи в БД (сек)",
    ["operation"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, float("inf")),
)

# Время фиксации транзакции на запрос (сек) — суммарная запись за запрос.
DB_TRANSACTION_SECONDS = Histogram(
    "petbank_db_transaction_seconds",
    "Время фиксации транзакции БД на запрос (сек)",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, float("inf")),
)

# Время обращения к внешним сервисам (сек), с разбивкой по сервису.
# Бакеты тянутся к ~0.8 с — таймауту клиента чёрного списка.
EXTERNAL_CALL_SECONDS = Histogram(
    "petbank_external_call_seconds",
    "Время обращений к внешним сервисам (сек)",
    ["service"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.4, 0.8, 1.0, float("inf")),
)

# Латентность вызова чёрного списка с разбивкой по фазам (сек):
#   request  — от отправки запроса до получения заголовков ответа
#              (сетевой round-trip + время обработки заявки сервисом);
#   response — чтение/получение тела ответа после заголовков
#              (отправка ответа сервисом).
# Сумма фаз примерно равна общему petbank_external_call_seconds{service="black_list"}.
BLACK_LIST_PHASE_SECONDS = Histogram(
    "petbank_black_list_phase_seconds",
    "Латентность вызова чёрного списка по фазам (сек)",
    ["phase"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.4, 0.8, 1.0, float("inf")),
)

# Итог похода в БКИ по заявке: ok — отчёт получен; no_history — бюро
# ответило «сведений нет»; unavailable — обе попытки не удались (fail-open).
BKI_RESULT = Counter(
    "petbank_bki_result_total",
    "Итоги обращений в БКИ по заявкам",
    ["status"],
)
