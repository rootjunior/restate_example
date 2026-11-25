# Restate + FastAPI: два Python-сервиса в Docker Compose

Приложение из двух Python-сервисов и Restate, который поднимается в отдельном контейнере
вместе с UI. Вызов идёт по цепочке **Swagger → api-service → Restate → order-service →
Restate → api-service (callback)**, то есть оба сервиса вызывают друг друга только через
Restate.

```
┌────────────────────┐   POST /orders    ┌──────────────────────────────┐
│ Swagger UI         │ ────────────────► │ api-service                  │
│ :8000/docs         │                   │ FastAPI :8000                │
└────────────────────┘                   │ Restate endpoint :9080       │
                                         │ (NotificationService)        │
                                         └───────────┬──────────────────┘
                                                     │ POST /OrderService/process_order
                                                     │ (Restate ingress :8080)
                                                     ▼
                                         ┌──────────────────────────────┐
                                         │ Restate                      │
                                         │ ingress :8080 / UI :9070     │
                                         └───────────┬──────────────────┘
                                                     │ durable invocation
                                                     ▼
                                         ┌──────────────────────────────┐
                                         │ order-service                │
                                         │ Restate endpoint :9080       │
                                         │ (OrderService)               │
                                         └───────────┬──────────────────┘
                                                     │ ctx.service_call()
                                                     │ через Restate
                                                     ▼
                                         NotificationService.notify_customer
                                         (снова api-service, :9080)
```

Что происходит при вызове:

1. `api-service` (FastAPI, порт 8000) принимает `POST /orders` из Swagger.
2. Он вызывает `OrderService/process_order` **через ingress Restate** (порт 8080).
3. `order-service` выполняет durable-шаги: валидация, резерв товара, durable-таймер,
   списание оплаты.
4. Затем `order-service` вызывает `NotificationService/notify_customer` **через Restate**
   (`ctx.service_call`). Этот хендлер реализован в `api-service` и обслуживается на порту 9080.
5. Restate возвращает результат дочернего вызова в `order-service`, а тот — в FastAPI и Swagger.

Оба вызова — `OrderService/process_order` и `NotificationService/notify_customer` — это
отдельные invocation, которые видны в UI Restate. Родительский invocation ждёт дочерний,
поэтому в журнале видна вся цепочка.

## Запуск

Нужен Docker Desktop (Docker Compose v2+).

```bash
docker compose up --build
```

Первый запуск занимает несколько минут: скачиваются образы Restate и uv, собираются два
Python-образа. Разовый контейнер `restate-register` должен завершиться с кодом `0` — он
регистрирует оба deployment в Restate. При запуске в фоне это видно так:

```bash
docker compose ps -a
```

| Что | URL |
| --- | --- |
| Swagger (ручка вызова) | http://localhost:8000/docs |
| Restate UI | http://localhost:9070/ui |
| Restate ingress (для curl) | http://localhost:8080 |
| Healthcheck api-service | http://localhost:8000/health |

## Как проверить сквозной вызов

1. Откройте Swagger: http://localhost:8000/docs
2. Разверните `POST /orders` → **Try it out**.
3. Нажмите **Execute** (пример тела уже подставлен).
4. Swagger покажет ответ: `payment_ref` и `notification_ref`.
5. Откройте Restate UI: http://localhost:9070/ui → **Invocations**. В списке будет
   `OrderService/process_order`; внутри — журнал шагов и дочерний вызов
   `NotificationService/notify_customer`.

Журнал `OrderService/process_order` содержит шаги:

- `validate order`
- `reserve stock`
- `warehouse delay` (durable sleep)
- `charge payment`
- вызов `NotificationService/notify_customer` (дочерний invocation)

Журнал `NotificationService/notify_customer` содержит шаг `deliver notification`.

То же самое из терминала:

```bash
curl -s -X POST http://localhost:8000/orders \
  -H 'Content-Type: application/json' \
  -d '{"customer":"Ada Lovelace","item":"Mechanical keyboard","quantity":2,"amount":399.80}'
```

Пример ответа:

```json
{
  "order_id": "order-1a2b3c4d",
  "status": "processed",
  "payment_ref": "0f3c9f1e-...",
  "notification_ref": "7d2e1c4a-...",
  "steps": [
    "order validated",
    "reserved 2 x Mechanical keyboard",
    "payment 0f3c9f1e-... charged",
    "notification 7d2e1c4a-... sent"
  ]
}
```

Логи обоих сервисов:

```bash
docker compose logs -f order-service api-service
```

## Idempotency key

`api-service` передаёт `order_id` в заголовке `idempotency-key`. Restate запоминает результат:
повторный вызов с тем же `order_id` не создаёт новое выполнение и не списывает деньги второй раз,
а возвращает сохранённый результат первого вызова. Чтобы получить новый вызов, оставьте
`order_id` пустым (сгенерируется автоматически) или укажите другой.

## Структура проекта

```
.
├── docker-compose.yml
├── api-service/
│   ├── Dockerfile              # uv + Python 3.13
│   ├── pyproject.toml
│   ├── uv.lock
│   └── app/
│       ├── api.py              # FastAPI + Swagger + вызов OrderService
│       ├── notifications.py    # NotificationService (callback-хендлер)
│       └── main.py             # Hypercorn: FastAPI :8000 + Restate :9080
└── order-service/
    ├── Dockerfile              # uv + Python 3.13
    ├── pyproject.toml
    ├── uv.lock
    ├── hypercorn-config.toml
    └── app/
        ├── main.py             # Restate Service OrderService
        └── contracts.py        # типизированный клиент NotificationService
```

## Как это устроено

- **order-service** поднимает ASGI-приложение Restate SDK через Hypercorn на `0.0.0.0:9080`
  (HTTP/2, h2c). Наружу этот порт не публикуется — с ним общается только Restate.
- **api-service** одним процессом поднимает два ASGI-приложения в общем event loop Hypercorn:
  FastAPI на `:8000` (публикуется на хост) и Restate endpoint на `:9080` для
  `NotificationService` (доступен только внутри compose-сети).
- `restate-register` последовательно выполняет:
  `restate deployments register --force http://order-service:9080` и
  `restate deployments register --force http://api-service:9080`.
  Restate сам опрашивает endpoint (`/discover`), получает список сервисов и хендлеров и
  начинает маршрутизировать вызовы.
- api-service вызывает `http://restate:8080/OrderService/process_order` обычным HTTP POST.
  Restate создаёт invocation, выполняет хендлер и возвращает результат вызывающему.
- Внутри `OrderService.process_order` вызов `ctx.service_call(notify_customer, ...)` создаёт
  дочерний invocation `NotificationService/notify_customer`. Restate сам находит deployment
  api-service, доставляет вызов, повторяет его при сбоях и сохраняет результат в журнале
  родительского вызова.
- Всё, что хендлеры делают через `ctx` (`run_typed`, `sleep`, `uuid`, `service_call`), попадает
  в журнал invocation. Поэтому в UI видно каждый шаг, его длительность и результат.

## Порты и остановка

Порты можно переопределить переменными окружения:

```bash
API_PORT=8001 RESTATE_INGRESS_PORT=8081 RESTATE_ADMIN_PORT=9071 docker compose up --build
```

Остановить:

```bash
docker compose down
```

Остановить и удалить данные Restate (история invocations, state, регистрации deployments):

```bash
docker compose down -v
```

При каждом `docker compose up` контейнер `restate-register` перерегистрирует оба deployment
с флагом `--force`, чтобы подхватить изменения кода. Поэтому в его логах может появляться
предупреждение о перезаписи deployment и рост `Revision` — это нормально для dev-примера.

## Если что-то не работает

- **В Swagger ответ 502 с текстом `Service 'OrderService' not found`** — регистрация ещё не
  прошла. Посмотрите `docker compose ps -a` и `docker compose logs restate-register`.
- **`Service 'NotificationService' not found`** — api-service ещё не зарегистрировался либо
  упал его Restate endpoint на 9080. Смотрите `docker compose logs api-service`.
- **`restate-register` долго не завершается** — один из сервисов не готов или упал.
  Смотрите `docker compose logs order-service api-service`.
- **Порт занят** — задайте другие значения `API_PORT`, `RESTATE_INGRESS_PORT`,
  `RESTATE_ADMIN_PORT` (см. выше).
- **UI не открывается** — подождите 5–10 секунд после старта Restate и обновите страницу.
- **В логах `ASGI Framework Lifespan error, continuing without Lifespan support`** — это
  нормально: Restate SDK не использует ASGI lifespan, Hypercorn просто продолжает работу.
- **Restate падает с `node-name is required`** — volume `/restate-data` создан другой нодой.
  В compose имя ноды закреплено (`--node-name restate-1`); если меняли имя, удалите volume:
  `docker compose down -v`.

## Версии

- Restate Server `1.8.1`, Restate CLI `1.8.1`
- Restate Python SDK `1.5.5`
- Python `3.13`, зависимости ставятся через [uv](https://docs.astral.sh/uv/)
- FastAPI + Hypercorn в api-service, Hypercorn в order-service
