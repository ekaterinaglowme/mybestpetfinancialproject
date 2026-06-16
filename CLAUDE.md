# CLAUDE.md

## Git workflow
- Перед созданием новой ветки — обновить её от main: `git fetch origin && git rebase origin/main`
- Деплой на VM только при push в main (после зелёных тестов)

## Deploy
- Сервис: systemd `petbank.service`, `Type=simple`, порт 8000, venv `/opt/petbank/.venv`
- После `systemctl restart` нужно ждать (`sleep 10`) перед health-check — uvicorn стартует не мгновенно
- Проверить логи сервиса: `journalctl -u petbank -n 50`
