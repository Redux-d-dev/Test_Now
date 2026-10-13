"""
ws_relay_test.py - STANDALONE SERVER-SIDE test (deploy this on Render). Touches nothing else.

Question it answers: Render only lets normal website traffic (and WebSockets) through, not
proxy traffic. Can we carry proxy traffic INSIDE a WebSocket instead?

How it works:
    - mitmproxy (forward proxy) listens on 127.0.0.1:8081, INSIDE the container only.
    - A web server on $PORT exposes  wss://<app>.onrender.com/tunnel?token=...
      Every WebSocket opened there is piped, byte for byte, to mitmproxy.
    - GET /  answers "ok" (Render health check).
    - The token is hardcoded for this test (env TUNNEL_TOKEN overrides it).

Needs: pip install mitmproxy aiohttp
Render: Start command  ->  python ws_relay_test.py   (use this file's name)

The local half is ws_client_test.py. See the steps at the top of that file.

Render log lines to look for:
    [relay] mitmproxy listening on 127.0.0.1:8081
    [relay] tunnel opened            -> a WebSocket got through Render's front door
    [probe] CONNECT received -> ...  -> proxy traffic travelled through the tunnel
"""
import asyncio
import os

from aiohttp import WSMsgType, web
from mitmproxy import options
from mitmproxy.tools.dump import DumpMaster

PORT = int(os.environ.get("PORT", "8080"))
TOKEN = os.environ.get("TUNNEL_TOKEN", "test-token-123")
MITM_HOST = "127.0.0.1"
MITM_PORT = 8081


class Probe:
    """Logs only. Never changes any traffic."""

    def http_connect(self, flow):
        print(f"[probe] CONNECT received -> {flow.request.host}:{flow.request.port}", flush=True)

    def request(self, flow):
        print(f"[probe] HTTP {flow.request.method} {flow.request.pretty_url}", flush=True)

    def error(self, flow):
        print(f"[probe] flow error: {flow.error}", flush=True)


async def health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def tunnel(request: web.Request) -> web.WebSocketResponse:
    if request.query.get("token") != TOKEN:
        return web.Response(status=403, text="forbidden")  # type: ignore[return-value]

    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=0)
    await ws.prepare(request)
    print("[relay] tunnel opened", flush=True)

    try:
        reader, writer = await asyncio.open_connection(MITM_HOST, MITM_PORT)
    except OSError as exc:
        print(f"[relay] cannot reach mitmproxy: {exc}", flush=True)
        await ws.close(code=1011, message=b"proxy down")
        return ws

    async def up() -> None:  # websocket -> mitmproxy
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                writer.write(msg.data)
                await writer.drain()
            else:
                break

    async def down() -> None:  # mitmproxy -> websocket
        while True:
            data = await reader.read(65536)
            if not data:
                break
            await ws.send_bytes(data)

    tasks = [asyncio.create_task(up()), asyncio.create_task(down())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        writer.close()
        if not ws.closed:
            await ws.close()
        print("[relay] tunnel closed", flush=True)
    return ws


async def main() -> None:
    opts = options.Options(listen_host=MITM_HOST, listen_port=MITM_PORT)
    master = DumpMaster(opts, with_termlog=True, with_dumper=False)
    master.addons.add(Probe())
    mitm_task = asyncio.create_task(master.run())
    await asyncio.sleep(3)
    if mitm_task.done():
        mitm_task.result()  # raises the real error if mitmproxy failed to start
    print(f"[relay] mitmproxy listening on {MITM_HOST}:{MITM_PORT}", flush=True)

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/tunnel", tunnel)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print(f"[relay] web server on 0.0.0.0:{PORT}", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
