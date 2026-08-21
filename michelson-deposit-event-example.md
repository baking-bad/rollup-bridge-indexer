# Michelson XTZ deposit с `deposit`-event'ом — confirmed example

Живой пример L1→Michelson XTZ-депозита на **Tezos X previewnet** с задеплоенным
`deposit`-event'ом (L2-1327). Подтверждён через RPC ноды 2026-06-02.

## Операция

| Поле | Значение |
|---|---|
| Hash | `opAhDWYxwDWFnKXG892itvC1TmMtUbeuSThVopzVDGd41mRxomE` |
| Level (L2 Michelson) | 562967 |
| Block | `BLJwkoiJYmsYJEwoyMWNYMZEFcfe3xSaFa66Fmq16CnqbwhqLSE` |
| Timestamp | 2026-06-02T15:03:30Z |
| Source | `tz1Ke2h7sDdakHJQh8WX4Z372du1KChsksyU` (TEZLINK_DEPOSITOR) |
| Destination | `tz1PSJR6wBtoiv56Uz1w1bBxeoBnWpDYMwV7` (receiver) |
| Amount | `1000000` mutez (1 tez) |
| counter / fee / gas_limit / storage_limit | 0 / 0 / 0 / 0 |
| TzKT UI | https://previewnet.tezosx.tzkt.io/opAhDWYxwDWFnKXG892itvC1TmMtUbeuSThVopzVDGd41mRxomE/0 |

## ⚠ КРИТИЧНО ДЛЯ ИНДЕКСАЦИИ: event виден только в RPC ноды, НЕ в TzKT

На previewnet (TzKT v1.17.1, `api.previewnet.tezosx.tzkt.io/v1/`) эта операция
отдаётся как **голый `transaction` с `hasInternals: false`** и без contract-event'ов:

- `GET /v1/operations/transactions/opAhDW…` → `hasInternals: false`, никаких internal results.
- `GET /v1/contracts/events?level=562967` → `[]`.

Сам `deposit`-event присутствует **только в receipt'е транзакции в ноде**
(`michelson.previewnet.tezosx.nomadic-labs.com`), в
`metadata.internal_operation_results[]`. Это тот же класс observability-gap,
что и с NAC reverse-flow (event есть в state ноды, но TzKT его не моделирует —
source у event'а это implicit-аккаунт `tz1Ke2h7…`, а не контракт).

**Вывод:** план «подписаться через TzKT на транзакции с `internal_operation_results`
содержащими `tag=deposit`» на текущем previewnet **не сработает** — TzKT эти данные
не отдаёт. Нужно либо тянуть receipt напрямую с RPC ноды, либо дождаться поддержки
этих event'ов в TzKT-инстансе Tezos X. Проверить статус до реализации хендлера.

## Где взять event (ground truth) — RPC ноды

```bash
curl -s "https://michelson.previewnet.tezosx.nomadic-labs.com/chains/main/blocks/562967" \
  | jq '.operations[3][0].contents[0].metadata'
```

Manager-операции лежат в `.operations[3]` (validation pass 3), как и в обычном Tezos.

## Полный content операции (из ноды)

```json
{
  "kind": "transaction",
  "source": "tz1Ke2h7sDdakHJQh8WX4Z372du1KChsksyU",
  "fee": "0",
  "counter": "0",
  "gas_limit": "0",
  "storage_limit": "0",
  "amount": "1000000",
  "destination": "tz1PSJR6wBtoiv56Uz1w1bBxeoBnWpDYMwV7",
  "metadata": {
    "operation_result": {
      "status": "applied",
      "balance_updates": [
        { "kind": "contract", "contract": "tz1Ke2h7sDdakHJQh8WX4Z372du1KChsksyU", "change": "-1000000", "origin": "block" },
        { "kind": "contract", "contract": "tz1PSJR6wBtoiv56Uz1w1bBxeoBnWpDYMwV7", "change": "1000000",  "origin": "block" }
      ]
    },
    "internal_operation_results": [
      {
        "kind": "event",
        "source": "tz1Ke2h7sDdakHJQh8WX4Z372du1KChsksyU",
        "nonce": 0,
        "type": {
          "prim": "pair",
          "args": [
            { "prim": "nat", "annots": ["%inbox_level"] },
            { "prim": "nat", "annots": ["%inbox_msg_id"] }
          ]
        },
        "tag": "deposit",
        "payload": {
          "prim": "Pair",
          "args": [ { "int": "3599297" }, { "int": "8" } ]
        },
        "result": { "status": "applied" }
      }
    ]
  }
}
```

## Парсинг для индексера

Из event'а (`internal_operation_results[*]` где `kind=event` И `tag="deposit"`):

| Источник | Поле | Значение в примере | Тип |
|---|---|---|---|
| `payload.args[0].int` | `inbox_level` (`%inbox_level`) | `3599297` | nat → int |
| `payload.args[1].int` | `inbox_msg_id` (`%inbox_msg_id`) | `8` | nat → int |

Из самой outer-transaction:

| Поле | Значение | Назначение |
|---|---|---|
| `destination` | `tz1PSJR6wBtoiv56Uz1w1bBxeoBnWpDYMwV7` | L2-receiver (tz1) |
| `amount` | `1000000` | mutez, токен = `xtz` |

`(inbox_level, inbox_msg_id) = (3599297, 8)` — **deterministic ключ** для матчинга
с L1-стороной через существующий `check_pending_etherlink_deposits` в
`handlers/bridge_matcher.py` (матч по `(inbox_message_level, inbox_message_index)`),
как Path #1 на EVM-стороне.

## Дискриминация L1 deposit vs NAC reverse-flow

Оба идут от `source=tz1Ke2h7…`. Разделяет их наличие `kind=event, tag="deposit"`:

| Сценарий | event `tag=deposit` | outer `amount` | `hasInternals` (по RPC) |
|---|---|---|---|
| L1 deposit (этот пример) | да | > 0 | внутри есть event-результат |
| NAC reverse-forward | нет (`tag="crac"` и др.) | 0 | есть internal transaction |

## Эталонная форма event'а (для справки)

```
internal_operation_results[]:
  kind:    event
  source:  tz1Ke2h7sDdakHJQh8WX4Z372du1KChsksyU   (TEZLINK_DEPOSITOR)
  nonce:   0
  tag:     "deposit"
  type:    pair (nat %inbox_level) (nat %inbox_msg_id)
  payload: Pair(<inbox_level>, <inbox_msg_id>)
```

## Источники / связанные

- Эндпоинты previewnet: TzKT API `https://api.previewnet.tezosx.tzkt.io/v1/`,
  RPC ноды (Michelson) `https://michelson.previewnet.tezosx.nomadic-labs.com`.
- L2-1327 (deposit event в kernel), MR `tezos/-/merge_requests/21877`.
- Проверено: 2026-06-02 через RPC ноды.
