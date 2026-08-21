#!/usr/bin/env bash
# Гейты готовности инфры перед редеплоем tezosx-shadownet после previewnet reset 2026-07-20.
# Все сервисы сохраняют URL — скрипт проверяет, что каждый уже ПЕРЕКЛЮЧЁН на новую цепь/роллап.
# Запуск: bash docs/previewnet-reset-check.sh   (exit 0 = все зелёные, можно деплоить)

set -u
NEW_ROLLUP=sr1BvTT7tVNneG2TeP18sixq57TxsZELebLu
RESET_EPOCH=$(date -d '2026-07-20T12:00:00Z' +%s)  # 14:00 CET — всё старше = старая цепь
OLD_L2_HEIGHT=1211532                               # высота старой цепи на 17.07 — маркер «архив не вайпнут»

pass=0; fail=0
ok()   { echo "  PASS: $1"; pass=$((pass+1)); }
bad()  { echo "  FAIL: $1"; fail=$((fail+1)); }

echo "[1/4] Rollup node (previewnet-smart.tzkt.io) репойнчен на новый роллап"
addr=$(curl -sf -m 10 https://previewnet-smart.tzkt.io/global/smart_rollup_address | tr -d '"')
[ "$addr" = "$NEW_ROLLUP" ] && ok "smart_rollup_address = $addr" || bad "smart_rollup_address = '${addr:-нет ответа}' (ждём $NEW_ROLLUP)"

echo "[2/4] EVM-нода отдаёт новую цепь (генезис свежее момента сброса)"
gen_ts=$(curl -sf -m 10 https://evm.previewnet.tezosx.nomadic-labs.com -X POST -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"eth_getBlockByNumber","params":["0x0",false],"id":1}' \
  | python3 -c 'import json,sys; print(int(json.load(sys.stdin)["result"]["timestamp"],16))' 2>/dev/null)
node_height=$(curl -sf -m 10 https://evm.previewnet.tezosx.nomadic-labs.com -X POST -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
  | python3 -c 'import json,sys; print(int(json.load(sys.stdin)["result"],16))' 2>/dev/null)
if [ -n "${gen_ts:-}" ] && [ "$gen_ts" -ge "$RESET_EPOCH" ]; then
  ok "genesis timestamp $(date -u -d @"$gen_ts" +%FT%TZ), height=${node_height:-?}"
else
  bad "genesis timestamp $([ -n "${gen_ts:-}" ] && date -u -d @"$gen_ts" +%FT%TZ || echo 'нет ответа') — старая цепь или нода лежит"
fi

echo "[3/4] Архив (etherlink-archive-previewnet.dipdup.net) вайпнут и синкает новую цепь"
arc_height=$(curl -sf -m 10 https://etherlink-archive-previewnet.dipdup.net/height)
if [ -z "${arc_height:-}" ]; then
  bad "архив не отвечает"
elif [ "$arc_height" -ge "$OLD_L2_HEIGHT" ]; then
  bad "height=$arc_height ≈ старая цепь ($OLD_L2_HEIGHT) — НЕ вайпнут, деплой индексера запрещён"
elif [ -n "${node_height:-}" ] && [ "$arc_height" -le "$node_height" ]; then
  ok "height=$arc_height (нода: $node_height) — новая цепь"
else
  ok "height=$arc_height — похоже на новую цепь (сверить с нодой вручную)"
fi

echo "[4/4] Michelson-TzKT (api.previewnet.tezosx.tzkt.io) вайпнут под новую цепь"
head_ts=$(curl -sf -m 10 https://api.previewnet.tezosx.tzkt.io/v1/head \
  | python3 -c 'import json,sys,datetime; h=json.load(sys.stdin); print(int(datetime.datetime.fromisoformat(h["timestamp"].replace("Z","+00:00")).timestamp()), h["level"])' 2>/dev/null)
if [ -n "${head_ts:-}" ] && [ "${head_ts%% *}" -ge "$RESET_EPOCH" ] && [ "${head_ts##* }" -lt "$OLD_L2_HEIGHT" ]; then
  ok "head level=${head_ts##* }, timestamp свежий"
else
  if [ -n "${head_ts:-}" ]; then
    bad "head level=${head_ts##* }, timestamp $(date -u -d @"${head_ts%% *}" +%FT%TZ) — ещё старая цепь / реиндекс не начался"
  else
    bad "нет ответа"
  fi
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "ВСЕ ГЕЙТЫ ЗЕЛЁНЫЕ ($pass/4) — можно переходить к редеплою индексера."
else
  echo "НЕ ГОТОВО: $fail из 4 гейтов красные. Индексер не деплоить."
fi
exit "$fail"
