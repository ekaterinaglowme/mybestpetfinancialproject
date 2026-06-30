# Grafana-дашборды PetBank

Версионируемые модели дашбордов, чтобы к ним можно было возвращаться и
восстанавливать после пересборки/очистки Grafana.

## Что здесь

| Файл | Дашборд | uid |
|---|---|---|
| `dashboards/petbank-business.json` | **PetBank — бизнес-метрики** (решения, одобрение, страны, причины отказов, суммы, HTTP по эндпоинтам, логи) | `petbank-business` |
| `provisioning/alerting/petbank-up.yaml` | **Алерт «PetBank недоступен»** — Grafana-managed rule на `up{job="petbank"} == 0` | `petbank-up-down` |
| `provisioning/alerting/petbank-host.yaml` | **Алерт «Мало места на диске VM»** — `< 15%` на `/` (требует node_exporter) | `petbank-disk-low` |

> Пай-чарты «Доля стран / Причины отказов» считаются через `increase(...[$__range])`
> — итог за выбранный период, а не накопленный с момента старта процесса. Поэтому
> деплой (рестарт контейнера сбрасывает счётчики Prometheus в 0) их больше не
> «роняет». Остальные панели и так на `rate()`.

HTTP RED + CPU/RAM закрывает готовый community-дашборд **«FastAPI Observability»**
([grafana.com #22676](https://grafana.com/grafana/dashboards/22676/)) — он не вендорится
здесь, переимпортируется напрямую с grafana.com (вход `DS_PROMETHEUS` → `prom-3`).

## Источники данных, которые ожидает дашборд (орг «Katya», orgId 3)

- **Prometheus** — uid `prom-3` (метрики `petbank_*`, `http_*`)
- **Loki** — uid `loki-katya-3` (логи, панель «Логи PetBank»)

uid'ы зашиты в JSON. На другом инстансе Grafana с другими uid — переотобразить
источники при импорте.

## Как восстановить / вернуться

**Через UI:** Dashboards → New → **Import** → загрузить `dashboards/petbank-business.json`.
Импорт по `uid` перезапишет существующий дашборд.

**Через API:**

```bash
curl -u <user>:<pass> -H "X-Grafana-Org-Id: 3" -H "Content-Type: application/json" \
  -X POST http://<grafana-host>:3000/api/dashboards/db \
  -d "$(jq -c '{dashboard: ., overwrite: true}' grafana/dashboards/petbank-business.json)"
```

**Через provisioning (авто-загрузка при старте):** смонтировать `dashboards/` в
Grafana и добавить провайдер в `provisioning/dashboards/*.yaml`.

## Алерт «PetBank недоступен» (`up == 0`)

`provisioning/alerting/petbank-up.yaml` — Grafana-managed alert rule. Срабатывает,
когда приложение не отвечает на scrape Prometheus:

- `up{job="petbank"} == 0` — таргет есть, но scrape падает (контейнер лёг);
- серия `up` пропала — таргет исчез из Prometheus (ловится через `noDataState: Alerting`).

В отличие от нулевых бизнес-счётчиков (это может быть просто отсутствие трафика),
`up == 0` — признак именно недоступности сервиса и от трафика не зависит.

**Применение — через provisioning-маунт** (предпочтительно: переживает рестарт Grafana):
смонтировать `provisioning/alerting/` в `/etc/grafana/provisioning/alerting/` контейнера
Grafana и перезапустить его. Правило подхватится при старте.

**Или через Alerting provisioning API:**

```bash
# нужен admin-доступ к Grafana; uid датасорса Prometheus = prom-3, orgId = 3
curl -u <admin>:<pass> -H "X-Grafana-Org-Id: 3" -H "Content-Type: application/yaml" \
  -X POST http://<grafana-host>:3000/api/v1/provisioning/alert-rules \
  --data-binary @grafana/provisioning/alerting/petbank-up.yaml
```

## Алерт «Мало места на диске VM» (`< 15%` на `/`)

`provisioning/alerting/petbank-host.yaml` — предупреждает, когда на корневой ФС VM
остаётся мало места, **до** того как всё ляжет. Именно переполнение диска уронило
дашборды 2026-06-30 (Prometheus не мог писать TSDB), а `up == 0` этот случай не ловит —
приложение оставалось живо.

> ⚠️ **Требует node_exporter.** Метрики `node_filesystem_*` отдаёт он. Если на VM его
> нет или Prometheus его не скрейпит — правило останется в `NoData` и работать не будет.
> Проверка: в Prometheus query `node_filesystem_avail_bytes` должен что-то вернуть; если
> пусто — поднять node_exporter (обычно `:9100`) и добавить target в `prometheus.yml`
> (мониторинг-стек живёт на VM вне этого репозитория). Применение — как у `petbank-up.yaml`.

> **Чтобы реально приходило уведомление** (а не только Firing в Alerting UI),
> правилу нужен contact point + notification policy. По умолчанию алерт уйдёт в
> дефолтный contact point Grafana. Для Telegram/email — завести contact point
> (Alerting → Contact points) и при желании завендорить его рядом в
> `provisioning/alerting/`. Токены/SMTP в репозиторий не коммитим.

