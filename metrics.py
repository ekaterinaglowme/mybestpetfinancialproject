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

# --- Защита /applications под нагрузкой ---
# Запросы, отклонённые rate limiter (429).
RATE_LIMITED = Counter(
    "petbank_rate_limited_total",
    "Запросы к /applications, отклонённые rate limiter (429)",
)

# Запросы, прерванные таймаутом-предохранителем (503).
REQUEST_TIMEOUTS = Counter(
    "petbank_request_timeouts_total",
    "Запросы к /applications, прерванные по таймауту (503)",
)
