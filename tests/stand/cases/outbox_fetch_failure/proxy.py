#!/usr/bin/env python3
"""Fault-injecting pass-through proxy for the `rollup_node` datasource.

Sits between the indexer and the real rollup node so a single outbox-message fetch can be
made to fail deterministically, with everything else served byte-identically to the clean
run. Two arms of the same case differ only by `PROXY_FAIL_LEVELS`.

Modes
  serve                  run the proxy (default)
  prewarm LO HI [WANT]   fill the response cache for `global/block/<L>/outbox/<L>/messages`,
                         L in [LO, HI), concurrently — so a `serve` run never waits on
                         upstream and both arms see identical upstream bodies. Exits
                         non-zero unless the cache ends up holding every level of the range
                         (and exactly WANT entries, when given): a cold entry would be
                         fetched under the datasource's `request_timeout`, which is far
                         below this proxy's 30s upstream timeout, and the resulting
                         TimeoutError is indistinguishable from the injected fault.
  check PORT TOKEN LEVELS STATUS
                         poll `/__armed` until the proxy answers and assert it is the one
                         this arm meant to start (same token) with the intended fault spec.
                         Exits non-zero otherwise — an arm run against the previous arm's
                         proxy proves nothing.

Env
  PROXY_PORT        listen port (default 8642)
  PROXY_UPSTREAM    real rollup node (default https://previewnet-smart.tzkt.io)
  PROXY_CACHE_DIR   response cache dir (default /tmp/bridge_outbox_fetch_failure_cache)
  PROXY_FAIL_LEVELS comma-separated outbox levels to fail; empty = pass-through (CONTROL)
  PROXY_FAIL_STATUS HTTP status to answer with (default 500 — the production shape: a wedged
                    rollup node 500s for every level above its processed_level)
  PROXY_TOKEN       identity string echoed by `/__armed` (default: a random one)
  PROXY_LOG         request log path (default /tmp/bridge_outbox_fetch_failure_proxy.log)

The failure is applied BEFORE the cache, so every retry of the same request fails too.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web

PORT = int(os.environ.get('PROXY_PORT', '8642'))
UPSTREAM = os.environ.get('PROXY_UPSTREAM', 'https://previewnet-smart.tzkt.io').rstrip('/')
CACHE_DIR = Path(os.environ.get('PROXY_CACHE_DIR', '/tmp/bridge_outbox_fetch_failure_cache'))
FAIL_STATUS = int(os.environ.get('PROXY_FAIL_STATUS', '500'))
TOKEN = os.environ.get('PROXY_TOKEN') or uuid.uuid4().hex
LOG_PATH = Path(os.environ.get('PROXY_LOG', '/tmp/bridge_outbox_fetch_failure_proxy.log'))

FAIL_LEVELS = {int(x) for x in os.environ.get('PROXY_FAIL_LEVELS', '').replace(',', ' ').split()}

# A wedged node: it stopped applying L1 blocks but still answers RPC. `head/level` reports the
# last level it applied, and every level above that has no hash it can resolve — the shape of
# the 2026-08-20 fault. 0 means the node is healthy and this is passed straight through.
WEDGED_AT = int(os.environ.get('PROXY_WEDGED_AT', '0'))

OUTBOX_RE = re.compile(r'^global/block/(\d+)/outbox/(\d+)/messages$')
HEAD_LEVEL_PATH = 'global/block/head/level'


def _cache_file(path: str, query: str) -> Path:
    key = hashlib.sha256(f'{path}?{query}'.encode()).hexdigest()[:32]
    return CACHE_DIR / f'{key}.bin'


def _log(line: str) -> None:
    with LOG_PATH.open('a') as fh:
        fh.write(line + '\n')


async def _fetch(session: aiohttp.ClientSession, path: str, query: str) -> tuple[int, bytes]:
    url = f'{UPSTREAM}/{path}' + (f'?{query}' if query else '')
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        return resp.status, await resp.read()


async def armed(request: web.Request) -> web.Response:
    """Identity + fault spec of THIS process. `check` asserts an arm talks to the right proxy."""
    return web.json_response(
        {
            'token': TOKEN,
            'pid': os.getpid(),
            'port': PORT,
            'upstream': UPSTREAM,
            'cache_dir': str(CACHE_DIR),
            'fail_levels': sorted(FAIL_LEVELS),
            'fail_status': FAIL_STATUS,
            'wedged_at': WEDGED_AT,
            'log': str(LOG_PATH),
        }
    )


async def handle(request: web.Request) -> web.Response:
    path = request.match_info.get('tail', '').lstrip('/')
    query = request.rel_url.query_string

    if WEDGED_AT and path == HEAD_LEVEL_PATH:
        _log(f'WEDGE head/level -> {WEDGED_AT}')
        return web.Response(status=200, body=str(WEDGED_AT).encode(), content_type='application/json')

    match = OUTBOX_RE.match(path)
    if match and WEDGED_AT and int(match.group(2)) > WEDGED_AT:
        _log(f'WEDGE {FAIL_STATUS} {path}')
        return web.Response(
            status=FAIL_STATUS,
            body=b'{"kind":"temporary","id":"failure","msg":"Cannot retrieve hash of level"}',
            content_type='application/json',
        )

    if match and int(match.group(2)) in FAIL_LEVELS:
        _log(f'FAIL {FAIL_STATUS} {path}')
        return web.Response(status=FAIL_STATUS, body=b'injected upstream failure', content_type='text/plain')

    cache_file = _cache_file(path, query)
    if cache_file.exists():
        _log(f'HIT  {path}')
        return web.Response(status=200, body=cache_file.read_bytes(), content_type='application/json')

    status, body = await _fetch(request.app['session'], path, query)
    _log(f'MISS {status} {path}')
    if status == 200:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(body)
    return web.Response(status=status, body=body, content_type='application/json')


async def _on_startup(app: web.Application) -> None:
    app['session'] = aiohttp.ClientSession()


async def _on_cleanup(app: web.Application) -> None:
    await app['session'].close()


def serve() -> int:
    LOG_PATH.write_text('')
    app = web.Application()
    app.router.add_route('GET', '/__armed', armed)
    app.router.add_route('*', '/{tail:.*}', handle)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    spec = sorted(FAIL_LEVELS) or 'none (pass-through)'
    print(f'proxy :{PORT} -> {UPSTREAM}  fail={spec}/{FAIL_STATUS}  token={TOKEN}  pid={os.getpid()}', flush=True)
    try:
        web.run_app(app, host='127.0.0.1', port=PORT, print=None)
    except OSError as exc:  # a port still held by the previous arm must not look like a clean start
        print(f'proxy: FAILED to bind 127.0.0.1:{PORT}: {exc}', flush=True)
        return 1
    return 0


def check(port: int, token: str, levels: str, status: int, tries: int = 40) -> int:
    """Poll `/__armed` and assert the answering proxy is this arm's, with this arm's fault."""
    want_levels = sorted({int(x) for x in levels.replace(',', ' ').split()})
    body: dict = {}
    for _ in range(tries):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/__armed', timeout=2) as resp:
                body = json.loads(resp.read())
            break
        except (OSError, urllib.error.URLError, ValueError):
            time.sleep(0.5)
    else:
        print(f'proxy check: nothing answered http://127.0.0.1:{port}/__armed', flush=True)
        return 1

    print(f'proxy armed: {json.dumps(body, sort_keys=True)}', flush=True)
    problems = []
    if body.get('token') != token:
        problems.append(f'token {body.get("token")} != {token} (a stale proxy is holding the port)')
    if body.get('fail_levels') != want_levels:
        problems.append(f'fail_levels {body.get("fail_levels")} != {want_levels}')
    if body.get('fail_status') != status:
        problems.append(f'fail_status {body.get("fail_status")} != {status}')
    for problem in problems:
        print(f'proxy check: {problem}', flush=True)
    return 1 if problems else 0


async def _prewarm(lo: int, hi: int) -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(20)
    filled = 0
    async with aiohttp.ClientSession() as session:

        async def one(level: int) -> None:
            nonlocal filled
            path = f'global/block/{level}/outbox/{level}/messages'
            cache_file = _cache_file(path, '')
            if cache_file.exists():
                return
            async with sem:
                for _ in range(3):
                    try:
                        status, body = await _fetch(session, path, '')
                    except Exception:  # retry any transport error
                        await asyncio.sleep(1)
                        continue
                    if status == 200:
                        cache_file.write_bytes(body)
                        filled += 1
                    return

        await asyncio.gather(*(one(level) for level in range(lo, hi)))

    present = sum(1 for level in range(lo, hi) if _cache_file(f'global/block/{level}/outbox/{level}/messages', '').exists())
    print(f'prewarm: {present}/{hi - lo} levels cached ({filled} new) in {CACHE_DIR}', flush=True)
    return present


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == 'prewarm':
        lo, hi = int(argv[1]), int(argv[2])
        want = int(argv[3]) if len(argv) > 3 else hi - lo
        present = asyncio.run(_prewarm(lo, hi))
        if present != hi - lo or present != want:
            print(f'prewarm: FAILED — {present} cached entries, expected {want}', flush=True)
            return 1
        return 0
    if argv and argv[0] == 'check':
        return check(int(argv[1]), argv[2], argv[3], int(argv[4]))
    return serve()


if __name__ == '__main__':
    raise SystemExit(main())
