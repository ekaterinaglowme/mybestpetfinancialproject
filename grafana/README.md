# Grafana-дашборды PetBank

Версионируемые модели дашбордов, чтобы к ним можно было возвращаться и
восстанавливать после пересборки/очистки Grafana.

## Что здесь

| Файл | Дашборд | uid |
|---|---|---|
| `dashboards/petbank-business.json` | **PetBank — бизнес-метрики** (решения, одобрение, страны, причины отказов, суммы, HTTP по эндпоинтам, логи) | `petbank-business` |

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
