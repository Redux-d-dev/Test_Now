"""
proxy_test.py - STANDALONE test. Touches nothing else in this project.

Question it answers: can a mitmproxy FORWARD proxy be reached through a public
host:port (e.g. your Render URL), the way a Chrome extension would use it?

What it does: starts mitmproxy in regular (forward proxy) mode on $PORT, protected
by a username/password, and prints a "[probe]" line for everything that reaches it.
That log tells you whether Render's front door lets proxy traffic (CONNECT) through.

Needs: pip install mitmproxy

Env vars:
    PROXY_USER, PROXY_PASS   optional. Hardcoded defaults for this test: tester / testpass123
                             (the proxy always requires a login, so it is never an open proxy).
    PORT                     Render sets it itself; locally it defaults to 8080.

Render: Start command  ->  python proxy_test.py
        (if the build fails on the Python version, mitmproxy needs a recent Python: set the
         PYTHON_VERSION env var in Render)

--- Test 1: locally first (should work, proves the script itself is fine) ---
    python proxy_test.py
    curl -v -k -x http://tester:testpass123@localhost:8080 https://example.com

--- Test 2: through Render (-k because mitmproxy re-signs example.com with its own CA) ---
    curl -v -k -x https://tester:testpass123@YOUR-APP.onrender.com:443 https://example.com

--- Test 3: plain http through Render (no CONNECT involved) ---
    curl -v -x https://tester:testpass123@YOUR-APP.onrender.com:443 http://example.com

How to read it (curl output + Render logs):
    Render log shows "[probe] CONNECT received"  -> a forward proxy works through Render.
    curl says "CONNECT tunnel failed" and the log
      shows NO "[probe]" line                    -> Render's front door blocks CONNECT.
    Test 3 works but Test 2 fails                -> only CONNECT (https) is blocked.
    curl gets 407                                -> it reached the proxy; check user/pass.
"""
import asyncio
import os
import sys

from mitmproxy import options
from mitmproxy.tools.dump import DumpMaster

PORT = int(os.environ.get("PORT", "8080"))
# Hardcoded for this throwaway test (env vars still override if set)
USER = os.environ.get("PROXY_USER", "tester")
PASS = os.environ.get("PROXY_PASS", "testpass123")


class Probe:
    """Logs only. Never changes any traffic."""

    def client_connected(self, client):
        print(f"[probe] TCP connection from {client.peername}", flush=True)

    def http_connect(self, flow):
        print(f"[probe] CONNECT received -> {flow.request.host}:{flow.request.port}", flush=True)

    def request(self, flow):
        print(f"[probe] HTTP {flow.request.method} {flow.request.pretty_url}", flush=True)

    def error(self, flow):
        print(f"[probe] flow error: {flow.error}", flush=True)


async def main() -> None:
    if not USER or not PASS or ":" in USER or ":" in PASS:
        sys.exit("Set PROXY_USER and PROXY_PASS (no ':' in either). Refusing to run as an open proxy.")

    opts = options.Options(
        listen_host="0.0.0.0",
        listen_port=PORT,
        proxyauth=f"{USER}:{PASS}",
    )
    master = DumpMaster(opts, with_termlog=True, with_dumper=False)
    master.addons.add(Probe())
    print(f"[probe] forward proxy starting on 0.0.0.0:{PORT} (auth required)", flush=True)
    await master.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
