# Деплой PetBank

CI/CD на GitHub Actions (`.github/workflows/ci.yml`):

- **PR в `main`** → тесты (`pytest`) + сборка Docker-образа (без публикации). Деплоя нет.
- **push/мёрж в `main`** → тесты → сборка и публикация образа в ghcr → деплой на VM
  (каждый следующий шаг только если предыдущий зелёный).

Деплой = на VM подтягивается свежий образ из GitHub Container Registry и контейнер
пересоздаётся командой `docker run`. **systemd не используется**: автозапуск
контейнера после перезагрузки или падения обеспечивает сам Docker
(`--restart unless-stopped`). Сервис слушает `:8000`.

Образ: `ghcr.io/ekaterinaglowme/mybestpetfinancialproject:latest`
(приватный, привязан к репозиторию).

## ⚠️ Перед мёржем в main

После мёржа CI сам делает `docker pull` + `docker run`. Чтобы это не упало, на VM
заранее должен стоять Docker, а пользователь `deploy` — уметь им управлять.

**До мёржа вручную на VM (под root):**
1. Установить Docker Engine: `curl -fsSL https://get.docker.com | sh`.
2. Добавить `deploy` в группу `docker`: `usermod -aG docker deploy`
   (затем новый SSH-сеанс, чтобы членство применилось).

Всё. Юнит systemd и sudo-правила больше не нужны — если остались с прошлой схемы,
их можно убрать: `rm /etc/systemd/system/petbank.service /etc/sudoers.d/petbank-deploy`
(а старый сервис сначала погасить: `systemctl disable --now petbank`).

> ⚠️ Членство в группе `docker` фактически равно root на этой машине. Для учебного
> стенда ок; в проде деплой-аккаунт стоит изолировать (rootless Docker / отдельный хост).

## Что нужно в GitHub Secrets / Variables

Выставляет владелец репозитория (Settings → Secrets and variables → Actions):

| Имя         | Где      | Значение                                  |
|-------------|----------|-------------------------------------------|
| `SSH_HOST`  | Variable | IP виртуалки (`212.147.238.3`)            |
| `SSH_USER`  | Variable | `deploy`                                   |
| `SSH_KEY`   | Secret   | приватный deploy-ключ (ed25519, целиком)  |

Вход в ghcr — через встроенный `GITHUB_TOKEN` (права `packages: write` на сборку и
`packages: read` на деплой), отдельный токен не нужен.

## Подготовка сервера (один раз)

```bash
# пользователь deploy
useradd -m -s /bin/bash deploy
install -d -m 700 -o deploy -g deploy ~deploy/.ssh
echo "<публичный deploy-ключ>" >> ~deploy/.ssh/authorized_keys
chown deploy:deploy ~deploy/.ssh/authorized_keys && chmod 600 ~deploy/.ssh/authorized_keys

# Docker Engine + deploy в группу docker
curl -fsSL https://get.docker.com | sh
usermod -aG docker deploy
systemctl enable --now docker

# фаервол
ufw allow OpenSSH
ufw allow 8000/tcp
ufw --force enable
```

Первый запуск (если не дожидаться деплоя из `main`) — под пользователем `deploy`:

```bash
docker login ghcr.io           # аккаунт с доступом к приватному пакету
docker pull ghcr.io/ekaterinaglowme/mybestpetfinancialproject:latest
docker run -d --restart unless-stopped --name petbank -p 8000:8000 \
  ghcr.io/ekaterinaglowme/mybestpetfinancialproject:latest
curl http://127.0.0.1:8000/health
```

## Управление контейнером на VM

```bash
docker ps                 # запущен ли petbank
docker logs -f petbank    # логи
docker restart petbank    # перезапуск
docker rm -f petbank      # остановить и удалить
```

## Рекомендации (не блокеры)

- Сервис отдаёт персональные данные (ФИО, телефон, дата рождения) по plaintext HTTP
  на `0.0.0.0:8000`. Для учебного стенда ок; в проде — reverse-proxy (nginx) + TLS.
- Branch protection на `main`: «Require status checks to pass → Tests» — чтобы PR
  нельзя было смёржить с красными тестами.
- Образ в ghcr приватный. Сделать публичным (тогда `docker login` на VM не нужен) —
  в репозитории: Packages → пакет → Package settings → Change visibility.
