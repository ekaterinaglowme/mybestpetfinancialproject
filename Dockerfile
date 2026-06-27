# PetBank — образ FastAPI-приложения.
# Версия Python совпадает с той, что в CI (.github/workflows/ci.yml).
FROM python:3.14-slim

# Не писать .pyc и не буферизовать вывод — логи сразу видны в контейнере.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# Сначала зависимости — слой кешируется, пока requirements.txt не менялся.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Затем код приложения: server.py + вспомогательные модули.
# metrics.py — метрики, logging_setup.py — JSON-логи, ratelimit.py — рейт-лимит,
# request_timeout.py — таймаут, db.py/models.py/repository.py — слой БД,
# main.py — точка входа.
COPY server.py main.py metrics.py logging_setup.py ratelimit.py request_timeout.py db.py models.py repository.py ./

# Версия/коммит приложения для метрики petbank_app_info. Прокидывается из CI
# build-arg GIT_COMMIT (github.sha); локально по умолчанию "unknown".
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=$GIT_COMMIT

# Запуск под непривилегированным пользователем, а не root.
RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

# /health отдаёт 200, когда сервер жив. curl в slim-образе нет — проверяем через python.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["python", "main.py"]
