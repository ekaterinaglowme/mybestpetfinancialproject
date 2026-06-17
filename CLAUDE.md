# CLAUDE.md

## Git workflow
- Перед созданием новой ветки — обновить её от main: `git fetch origin && git rebase origin/main`
- Деплой на VM только при push в main (после зелёных тестов)

## Deploy
- Прод в Docker: образ `ghcr.io/ekaterinaglowme/mybestpetfinancialproject:latest` — собирается job `docker` в CI, пушится при push в main
- Деплой на VM = `docker pull` + пересоздание контейнера `docker run -d --restart unless-stopped --name petbank -p 8000:8000` (без systemd; автозапуск после ребута/падения даёт сам Docker)
- Юзер `deploy` должен быть в группе `docker`; pull приватного образа — через `docker login ghcr.io` (в CI это встроенный `GITHUB_TOKEN`)
- После `docker run` ждём (`sleep 10`) перед health-check — uvicorn в контейнере стартует не мгновенно
- Логи/статус на VM: `docker logs -f petbank`, `docker ps`
