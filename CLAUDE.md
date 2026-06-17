# CLAUDE.md

## Git workflow
- Перед созданием новой ветки — обновить её от main: `git fetch origin && git rebase origin/main`
- Деплой на VM только при push в main (после зелёных тестов)

## Deploy
- Прод в Docker: образ `ghcr.io/ekaterinaglowme/mybestpetfinancialproject:latest` — собирается job `docker` в CI, пушится при push в main
- На VM сервис — systemd `petbank.service` (`User=deploy`), запускает контейнер (`docker run`), порт 8000; деплой = `docker pull` + `systemctl restart petbank` (venv больше не используется)
- Юзер `deploy` должен быть в группе `docker`; pull приватного образа — через `docker login ghcr.io` (в CI это встроенный `GITHUB_TOKEN`)
- После `systemctl restart` нужно ждать (`sleep 10`) перед health-check — uvicorn в контейнере стартует не мгновенно
- Проверить логи сервиса: `journalctl -u petbank -n 50`
