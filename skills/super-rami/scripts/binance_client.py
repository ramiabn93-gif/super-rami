"""Minimal Binance REST client (standard library only): spot + USD-M futures.

Public market data needs no key. Orders need BINANCE_API_KEY and
BINANCE_API_SECRET in the environment (testnet keys while testnet=true).
Create the key with trading permission only, withdrawals OFF, and an IP whitelist.
"""
import hashlib
import hmac
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from common import RamiError

URLS = {
    ("spot", False): "https://api.binance.com",
    ("spot", True): "https://testnet.binance.vision",
    ("futures", False): "https://fapi.binance.com",
    ("futures", True): "https://testnet.binancefuture.com",
}
DATA_URLS = {"spot": "https://api.binance.com/api/v3/klines",
             "futures": "https://fapi.binance.com/fapi/v1/klines"}


def sign(secret, query):
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def floor_step(x, step):
    if step <= 0:
        return x
    digits = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(math.floor(x / step + 1e-9) * step, digits)


def _request(method, url, headers=None, body=None, timeout=20):
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=body)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read())
        except Exception:  # noqa: BLE001
            err = {"msg": str(e)}
        raise RamiError(f"باينانس رفضت الطلب: {err.get('msg')} (code {err.get('code')})")
    except (urllib.error.URLError, TimeoutError) as e:
        raise RamiError(f"تعذر الاتصال بباينانس: {e}")


def klines(symbol, interval="15m", limit=500, market="spot", end_time=None, http=_request):
    q = {"symbol": symbol.upper(), "interval": interval, "limit": min(limit, 1000 if market == "spot" else 1500)}
    if end_time:
        q["endTime"] = int(end_time)
    raw = http("GET", DATA_URLS[market] + "?" + urllib.parse.urlencode(q))
    return [{"t": int(k[0]), "o": float(k[1]), "h": float(k[2]), "l": float(k[3]),
             "c": float(k[4]), "v": float(k[5]), "close_t": int(k[6])} for k in raw]


def history(symbol, interval, total, market="spot", http=_request):
    """Fetch up to `total` bars by paging backwards."""
    out, end = [], None
    while len(out) < total:
        chunk = klines(symbol, interval, min(1000, total - len(out)), market, end, http)
        if not chunk:
            break
        out = chunk + out
        end = chunk[0]["t"] - 1
        if len(chunk) < 2:
            break
    seen, uniq = set(), []
    for b in out:
        if b["t"] not in seen:
            seen.add(b["t"])
            uniq.append(b)
    return uniq[-total:]


class Binance:
    def __init__(self, market="spot", testnet=True, key=None, secret=None, http=_request):
        if market not in ("spot", "futures"):
            raise RamiError("market لازم يكون spot أو futures")
        self.market, self.testnet, self.http = market, testnet, http
        self.base = URLS[(market, testnet)]
        self.key = key or os.environ.get("BINANCE_API_KEY")
        self.secret = secret or os.environ.get("BINANCE_API_SECRET")
        self._filters = {}

    # ------------------------------------------------------------ plumbing
    def _p(self, path):
        return ("/api/v3" if self.market == "spot" else "/fapi/v1") + path

    def signed(self, method, path, params=None):
        if not (self.key and self.secret):
            raise RamiError("حط مفاتيح باينانس في متغيرات البيئة BINANCE_API_KEY و BINANCE_API_SECRET "
                            "(على جهازك، لا ترسلها في المحادثة).")
        q = dict(params or {}, timestamp=int(time.time() * 1000), recvWindow=5000)
        qs = urllib.parse.urlencode(q)
        qs += "&signature=" + sign(self.secret, qs)
        return self.http(method, f"{self.base}{path}?{qs}", headers={"X-MBX-APIKEY": self.key})

    def filters(self, symbol):
        if symbol not in self._filters:
            info = self.http("GET", f"{self.base}{self._p('/exchangeInfo')}?symbol={symbol}")
            s = next((x for x in info["symbols"] if x["symbol"] == symbol), None)
            if not s:
                raise RamiError(f"العملة {symbol} غير موجودة في {self.market}")
            f = {x["filterType"]: x for x in s["filters"]}
            lot = f.get("MARKET_LOT_SIZE") or f.get("LOT_SIZE")
            if float(lot.get("stepSize", 0)) == 0:
                lot = f["LOT_SIZE"]
            notional = f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}
            self._filters[symbol] = {
                "step": float(lot["stepSize"]), "min_qty": float(lot["minQty"]),
                "tick": float(f["PRICE_FILTER"]["tickSize"]),
                "min_notional": float(notional.get("minNotional", notional.get("notional", 0)) or 0),
            }
        return self._filters[symbol]

    def price(self, symbol):
        path = "/ticker/price"
        return float(self.http("GET", f"{self.base}{self._p(path)}?symbol={symbol}")["price"])

    # ------------------------------------------------------------ orders
    def open(self, symbol, side, usdt, leverage=1):
        """Market entry. side 1=long, -1=short (futures only). Returns (qty, avg_price)."""
        f = self.filters(symbol)
        if self.market == "spot":
            if side != 1:
                raise RamiError("السبوت ما يدعم البيع على المكشوف.")
            r = self.signed("POST", "/api/v3/order", {"symbol": symbol, "side": "BUY", "type": "MARKET",
                                                     "quoteOrderQty": f"{usdt:.2f}"})
            qty = float(r["executedQty"])
            px = float(r["cummulativeQuoteQty"]) / qty if qty else self.price(symbol)
            return qty, px
        self.signed("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": int(leverage)})
        try:
            self.signed("POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": "ISOLATED"})
        except RamiError as e:
            if "-4046" not in str(e):                       # "No need to change margin type"
                raise
        px = self.price(symbol)
        qty = floor_step(usdt * leverage / px, f["step"])
        if qty < f["min_qty"] or qty * px < f["min_notional"]:
            raise RamiError(f"المبلغ صغير على {symbol} (أقل كمية {f['min_qty']}).")
        r = self.signed("POST", "/fapi/v1/order", {"symbol": symbol, "side": "BUY" if side == 1 else "SELL",
                                                  "type": "MARKET", "quantity": qty, "newOrderRespType": "RESULT"})
        return float(r.get("executedQty") or qty), float(r.get("avgPrice") or px)

    def close(self, symbol, side, qty):
        """Market exit of an open position."""
        f = self.filters(symbol)
        if self.market == "spot":
            asset = symbol[:-4] if symbol.endswith("USDT") else symbol
            acct = self.signed("GET", "/api/v3/account")
            free = next((float(b["free"]) for b in acct["balances"] if b["asset"] == asset), 0.0)
            q = floor_step(min(qty, free), f["step"])
            if q < f["min_qty"]:
                raise RamiError(f"الرصيد من {asset} أقل من أقل كمية للبيع.")
            r = self.signed("POST", "/api/v3/order", {"symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": q})
            ex = float(r["executedQty"])
            return float(r["cummulativeQuoteQty"]) / ex if ex else self.price(symbol)
        self.signed("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
        r = self.signed("POST", "/fapi/v1/order", {"symbol": symbol, "side": "SELL" if side == 1 else "BUY",
                                                  "type": "MARKET", "quantity": floor_step(qty, f["step"]),
                                                  "reduceOnly": "true", "newOrderRespType": "RESULT"})
        return float(r.get("avgPrice") or self.price(symbol))

    def set_stop(self, symbol, side, stop):
        """Futures only: exchange-side stop as a safety net if the bot goes offline."""
        if self.market != "futures":
            return
        f = self.filters(symbol)
        self.signed("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
        sp = floor_step(stop, f["tick"]) if side == 1 else math.ceil(stop / f["tick"]) * f["tick"]
        self.signed("POST", "/fapi/v1/order", {"symbol": symbol, "side": "SELL" if side == 1 else "BUY",
                                              "type": "STOP_MARKET", "stopPrice": f"{sp:.8f}".rstrip("0").rstrip("."),
                                              "closePosition": "true", "workingType": "MARK_PRICE"})
