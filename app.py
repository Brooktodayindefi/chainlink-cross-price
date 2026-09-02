"""
Local web UI — step 4 of the cross-price tool.

Serves index.html plus a small JSON API on top of cross.py / history.py:

    GET /api/assets                             -> selectable assets + day presets
    GET /api/history?base=X&quote=Y&days=N      -> cross_series() output

    python app.py [port]      # default 8787
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import history
from cross import FEEDS, MARKET, EXRATE, MARKET_VIA_EXRATE, methods, PriceError

ROOT = os.path.dirname(os.path.abspath(__file__))
ASSETS = sorted(set(MARKET) | set(EXRATE) | {"USD"})
DAY_PRESETS = (7, 30, 90, 180, 365)
MAX_DAYS = 1825
_lock = threading.Lock()  # serialize RPC walks; the round cache isn't thread-safe


def method_label(asset, m):
    if m == "market":
        if asset in MARKET:
            return f"market @ {FEEDS[MARKET[asset]]['chain']}"
        # market-via-exrate alias (no direct market feed exists)
        return f"market · via {FEEDS[EXRATE[asset]]['quote']}"
    src = FEEDS[EXRATE[asset]].get("src")
    return f"exrate · {src}" if src else "exrate"


EXPLORER = {
    "ethereum": "https://etherscan.io",
    "arbitrum": "https://arbiscan.io",
    "base": "https://basescan.org",
    "optimism": "https://optimistic.etherscan.io",
    "mantle": "https://mantlescan.xyz",
    "plasma": "https://plasmascan.to",
}


def leg_info(name):
    f = FEEDS[name]
    explorer = EXPLORER.get(f["chain"])
    url = f.get("url") or (f"{explorer}/address/{f['address']}" if explorer else None)
    return {"name": name, "chain": f["chain"], "kind": f["kind"],
            "src": f.get("src", "Chainlink"), "address": f["address"],
            "url": url, "note": f.get("note")}


ASSET_INFO = [{"asset": a,
               "methods": [{"id": m, "label": method_label(a, m)}
                           for m in methods(a)]}
              for a in ASSETS]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            self._file("index.html", "text/html; charset=utf-8")
        elif url.path == "/api/assets":
            self._json(200, {"assets": ASSET_INFO, "days": list(DAY_PRESETS),
                             "max_days": MAX_DAYS})
        elif url.path == "/api/history":
            self._history(parse_qs(url.query))
        else:
            self._json(404, {"error": "not found"})

    def _history(self, q):
        base = q.get("base", [""])[0]
        quote = q.get("quote", [""])[0]
        base_method = q.get("base_method", [""])[0] or None
        quote_method = q.get("quote_method", [""])[0] or None
        days = q.get("days", ["30"])[0]
        days = int(days) if days.isdigit() else 0
        if base not in ASSETS or quote not in ASSETS:
            self._json(400, {"error": "base/quote must be listed assets"})
            return
        if not 1 <= days <= MAX_DAYS:
            self._json(400, {"error": f"days must be 1..{MAX_DAYS}"})
            return
        for asset, m in ((base, base_method), (quote, quote_method)):
            if m and m not in methods(asset):
                self._json(400, {"error": f"{asset} has no {m} feed"})
                return
        try:
            with _lock:
                result = history.cross_series(base, quote, days,
                                              base_method, quote_method)
            result["derivation"] = {
                "base": [leg_info(n) for n in result["base_legs"]],
                "quote": [leg_info(n) for n in result["quote_legs"]],
                "cancelled": [leg_info(n) for n in result.get("cancelled", [])],
            }
            self._json(200, result)
        except PriceError as e:
            self._json(400, {"error": str(e)})
        except Exception as e:
            self._json(502, {"error": f"upstream RPC failure: {e}"})

    def _file(self, name, ctype):
        with open(os.path.join(ROOT, name), "rb") as f:
            body = f.read()
        self._respond(200, ctype, body)

    def _json(self, status, obj):
        self._respond(status, "application/json", json.dumps(obj).encode())

    def _respond(self, status, ctype, body):
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    # local: python app.py [port] -> binds 127.0.0.1. Hosted: the platform
    # sets PORT (Render/Fly/Docker) and we bind all interfaces; HOST overrides.
    if len(sys.argv) > 1:
        host, port = "127.0.0.1", int(sys.argv[1])
    else:
        port = int(os.environ.get("PORT", "8787"))
        host = os.environ.get("HOST",
                              "0.0.0.0" if "PORT" in os.environ else "127.0.0.1")
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"cross-price UI on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
