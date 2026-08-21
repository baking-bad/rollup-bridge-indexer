# Previewnet reset 2026-07-20 — чеклист редеплоя tezosx-shadownet

Kill: пн 20.07 14:00 CET, restart 16:00 CET. L1 shadownet живёт, все URL/chain id не меняются.
Rollup: `sr1TCYofX…` → **`sr1BvTT7tVNneG2TeP18sixq57TxsZELebLu`** (origination L1 level **4218078**).

Гейты 1–4 проверяются одним скриптом (все сервисы сохраняют URL — проверяем, что каждый уже переключён на новую цепь):

```bash
bash docs/previewnet-reset-check.sh   # exit 0 = все зелёные, можно деплоить
```

Проверен 17.07 на старой цепи: корректно даёт 4/4 FAIL.

## Слои и владельцы

| # | Слой | Кто | Действие |
|---|------|-----|----------|
| 1 | Rollup node `previewnet-smart.tzkt.io` | команда (написать) | реинит под новый sr1 |
| 2 | EVM-нода `evm.previewnet…nomadic-labs.com` | Nomadic | ждём; надеемся, что быстро |
| 3 | **Архив `etherlink-archive-previewnet.dipdup.net`** | **мы — пункт №1** | вайпнуть сторадж, переключить/пересинкать с новой цепи EVM-ноды | 
| 4 | Michelson-TzKT `api.previewnet.tezosx.tzkt.io` | команда (написать) | wipe под новую цепь |
| 5 | env индексера | мы | таблица ниже |
| 6 | деплой индексера | мы | потушить всё → удалить volume → redeploy с нуля → проверить |

Непровайпнутый архив ядовит: chain id тот же → индексер молча втянет блоки мёртвой цепи. С #38 архив обязателен (все EVM-индексы, `url` non-nullable). Гейт 3 в скрипте ловит это по высоте (старая ≈ 1211532).

## 5. Целевой `.env.tezosx-shadownet`

Меняются:

```bash
SMART_ROLLUP_ADDRESS=sr1BvTT7tVNneG2TeP18sixq57TxsZELebLu   # был sr1TCYofX…
L1_FIRST_LEVEL=4218078                                       # был 3044225 (origination нового rollup)
L2_MICHELSON_FIRST_LEVEL=0                                   # был 188515; новая цепь целиком event-era (RC-ядро)
TAG=master                                                   # был pr-38 (#38 в master, образ 6ce1401 собран)
```

Остаются как есть, но связаны с переключением (проверить осознанно):

```bash
L2_FIRST_LEVEL=0                                             # уже 0
NATIVE_TICKETER=KT1FcWeWiEC7Ve5JMdZpKyvaFdsJv7n4GFzi         # rollup-агностичен (storage без sr1) — reuse
FA_TICKETERS=["KT1PCQgXdEvSHUnkFfe3KAdvczqJHNiw4WWA", "KT1WCou3hUPRdjzKsAsSMmPMqu9wjtzoAJrU", "KT1HtANsgibousngx6e1zQvE2WppNTuDZxV2"]  # то же — reuse
FAST_WITHDRAWAL_NATIVE_ADDRESS=tz1burnburnburnburnburnburnburjAYjjX  # ЗАГЛУШКА: fw отключён до передеплоя контракта.
    # Переменная обязательна для загрузки конфига. Burn-адрес (имплицитный, entrypoints нет) → паттерн
    # payout_withdrawal не сматчится никогда = ноль мусорных записей. Старый KT1AWvK… НЕ использовать:
    # у него живые клеймы старой цепи в окне 16–20.07 (L1_FIRST_LEVEL=4218078 их захватывает) → orphan-ряды.
    # Конфиг с tz1-заглушкой валидирован (dipdup config export, exit 0); runtime-зависимостей от fw нет
    # (матчер-степ при нуле строк — no-op). Новый KT1 → обновить env, рестарт; реиндекс не нужен
    # (advanced.reindex.config_modified: ignore).
```

Не меняются (URL/секреты/инфраструктура — не трогать):

```bash
NETWORK=tezosx-shadownet
TZKT_URL=https://api.shadownet.tzkt.io
METADATA_URL=https://metadata.dipdup.net
TEZOS_NODE_URL=https://rpc.tzkt.io/shadownet
ETHERLINK_NODE_URL=https://evm.previewnet.tezosx.nomadic-labs.com
ETHERLINK_NODE_WS=wss://evm.previewnet.tezosx.nomadic-labs.com
ETHERLINK_SUBSQUID_URL=https://etherlink-archive-previewnet.dipdup.net
ROLLUP_NODE_URL=https://previewnet-smart.tzkt.io/
TEZOSX_MICHELSON_TZKT_URL=https://api.previewnet.tezosx.tzkt.io
IMAGE=baking-bad/rollup-bridge-indexer
ETHERLINK_BRIDGE_INDEXER_SERVICE=tezosx-bridge-indexer-shadownet
ETHERLINK_BRIDGE_INDEXER_HOST=tezosx-bridge-shadownet.dipdup.net
SWARM_ROOT_DOMAIN=piltover.baking-bad.org
NODE_HOSTNAME=vadim
HASURA_URL=http://tezosx-bridge-indexer-shadownet_hasura:8080
POSTGRES_HOST=tezosx-bridge-indexer-shadownet_db
POSTGRES_PASSWORD=<как есть>
ADMIN_SECRET=<как есть>
SENTRY_DSN=<как есть>
SENTRY_ENVIRONMENT=bridge-tezosx-shadownet
BACKUP_ENABLED=0
```

Отдельно (в оверлее репо, не в env): адреса L2-токенов `l2_tzbtc/sirius/usdt` в `configs/tezosx-shadownet.yaml` — новая цепь = новые деплои; когда команда даст адреса → коммит + CI-образ.

## 6. Деплой индексера (после `previewnet-reset-check.sh` → 0)

- [ ] Потушить всё (стек tezosx-shadownet в Portainer)
- [ ] Удалить volume БД
- [ ] Обновить env по блоку 5 → redeploy с нуля
- [ ] Проверить bootstrap: пустой `dipdup_index` в начале = backfill в `on_restart`, не падение; смотреть рост `rollup_inbox_message`; liveness по `updated_at`, не по статусу
- [ ] Верификация: депозиты трёх путей (FA / XTZ-EVM / tz1-Michelson) + alias-резолв → сматченные `bridge_operation`
- [ ] Потом: стенд — `tests/stand/tezosx.env` + перезапись `window.env` кейсов
