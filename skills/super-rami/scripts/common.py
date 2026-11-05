"""Shared helpers for Super Rami: config, HTTP, symbols, prices.

Only the Python standard library is used. Nothing here places real orders.
"""
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request

HOME = os.environ.get("SUPER_RAMI_HOME") or os.path.join(os.path.expanduser("~"), "super-rami-data")

DEFAULT_CONFIG = {
    "sec_user_agent": "",        # "Your Name your@email.com" (required by SEC)
    "cash_buffer_pct": 10,       # keep this % of the budget in cash
    "max_position_pct": 20,      # no single stock above this % of the portfolio
    "max_trade_pct": 25,         # no single order above this % of the portfolio
    "min_price": 1.0,            # block penny stocks below this price
    "paper_start_cash": {"USD": 10000, "SAR": 37500},
    # SPX Smart Rally strategy (signals + backtest only, never executes)
    "spx_strategy": {
        "symbol": "^GSPC",          # SPX index on Yahoo
        "min_rally_pts": 15,        # rally/drop after the pivot, in points
        "zone_pts": 3,              # zone height above a low / below a high
        "pivot_bars": 3,            # bars each side to confirm a swing point
        "micro_pivot_bars": 2,      # bars each side for the BOS swing
        "divergence": "both",       # "rsi", "macd", "both" or "any"
        "max_bos_wait": 30,         # minutes to wait for BOS after the retest
        "zone_max_age": 240,        # forget zones older than this (minutes)
        "otm_pts": 10,              # strike distance from price for OTM
        "strike_step": 5,
        "hold_minutes": 15,         # backtest: measure move after this long
        "target_pts": 5,            # backtest: win if price moves this far
        "stop_pts": 5,              # backtest: loss if price moves against this far
    },
}

SEC_HEADERS_HOSTS = ("sec.gov",)
_last_sec_call = [0.0]


class RamiError(Exception):
    """An error with a message meant for the user."""


def path(*parts):
    p = os.path.join(HOME, *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def load_json(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(p, data):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in load_json(path("config.json"), {}).items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def save_config(cfg):
    save_json(path("config.json"), cfg)


# ---------------------------------------------------------------- HTTP

def http_get(url, headers=None, timeout=30, retries=3):
    """GET a URL and return bytes. Tests replace this function."""
    hdrs = {"Accept-Encoding": "gzip", "User-Agent": "Mozilla/5.0 (SuperRami)"}
    hdrs.update(headers or {})
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (404, 400, 403):
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise RamiError(f"تعذر جلب البيانات من {url}: {last}")


def sec_get(url):
    ua = os.environ.get("SEC_USER_AGENT") or load_config().get("sec_user_agent")
    if not ua:
        raise RamiError(
            "SEC تطلب اسمك وإيميلك في الطلبات. شغّل:\n"
            '  python3 setup.py --sec-user-agent "Your Name you@example.com"'
        )
    wait = 0.15 - (time.time() - _last_sec_call[0])  # SEC allows ~10 req/s
    if wait > 0:
        time.sleep(wait)
    _last_sec_call[0] = time.time()
    return http_get(url, headers={"User-Agent": ua})


# ---------------------------------------------------------------- symbols

def normalize_symbol(sym):
    s = sym.strip().upper()
    if s.isdigit() and len(s) == 4:   # Tadawul codes like 2222
        return s + ".SR"
    return s


def currency_of(symbol):
    return "SAR" if normalize_symbol(symbol).endswith(".SR") else "USD"


def allows_fractional(symbol):
    return currency_of(symbol) == "USD"


# ---------------------------------------------------------------- prices

def fetch_history(symbol, range_="1y", interval="1d", max_age=900):
    """Daily closes as [(unix_ts, close, volume)], cached for 15 minutes."""
    symbol = normalize_symbol(symbol)
    cache = path("cache", f"chart_{symbol}_{range_}_{interval}.json")
    cached = load_json(cache, None)
    if cached and time.time() - cached.get("_t", 0) < max_age:
        return cached["rows"], cached["meta"]
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?range={range_}&interval={interval}")
    raw = json.loads(http_get(url))
    rows, meta = parse_chart(raw, symbol)
    save_json(cache, {"_t": time.time(), "rows": rows, "meta": meta})
    return rows, meta


def parse_chart(raw, symbol):
    res = (raw.get("chart") or {}).get("result")
    if not res:
        err = (raw.get("chart") or {}).get("error") or {}
        raise RamiError(f"ما لقيت بيانات للسهم {symbol}: {err.get('description', 'رمز غير معروف')}")
    r = res[0]
    ts = r.get("timestamp") or []
    q = r["indicators"]["quote"][0]
    n = len(ts)
    cols = [q.get(k) or [None] * n for k in ("close", "volume", "high", "low", "open")]
    # row = (time, close, volume, high, low, open)
    rows = [(t, c, v or 0, h if h is not None else c, l if l is not None else c, o if o is not None else c)
            for t, c, v, h, l, o in zip(ts, *cols) if c is not None]
    if not rows:
        raise RamiError(f"ما فيه أسعار للسهم {symbol}")
    m = r.get("meta", {})
    meta = {"symbol": symbol, "currency": m.get("currency") or currency_of(symbol),
            "name": m.get("longName") or m.get("shortName") or symbol,
            "price": m.get("regularMarketPrice") or rows[-1][1]}
    return rows, meta


def last_price(symbol):
    _, meta = fetch_history(symbol, range_="5d")
    return float(meta["price"])


# ---------------------------------------------------------------- output

def fmt_money(x, cur):
    sign = "-" if x < 0 else ""
    return f"{sign}{abs(x):,.2f} {'$' if cur == 'USD' else 'ر.س'}"


def run(main):
    """Run a CLI entry point, printing user errors without a traceback."""
    try:
        main()
    except RamiError as e:
        print(f"خطأ: {e}", file=sys.stderr)
        sys.exit(1)
