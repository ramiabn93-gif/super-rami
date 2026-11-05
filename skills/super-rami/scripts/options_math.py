"""Small option helpers: Black-Scholes estimate and SPX tick rounding.

The estimate is only used by the simulator. Live trading uses real
bid/ask quotes from the broker.
"""
import math


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(spot, strike, minutes_left, right, vol=0.15, rate=0.04):
    """European option price. minutes_left = minutes until expiry (0DTE: until 16:00 ET)."""
    t = max(minutes_left, 0) / (252 * 390)          # trading-minute year
    intrinsic = max(spot - strike, 0) if right == "C" else max(strike - spot, 0)
    if t <= 0 or vol <= 0:
        return intrinsic
    sd = vol * math.sqrt(t)
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2) * t) / sd
    d2 = d1 - sd
    disc = math.exp(-rate * t)
    if right == "C":
        return spot * _ncdf(d1) - strike * disc * _ncdf(d2)
    return strike * disc * _ncdf(-d2) - spot * _ncdf(-d1)


def spx_tick(price):
    """SPX options tick: 0.05 under $3, 0.10 at $3 and above."""
    return 0.05 if price < 3 else 0.10


def round_tick(price, up=False):
    tick = spx_tick(price)
    n = price / tick
    n = math.ceil(n - 1e-9) if up else math.floor(n + 1e-9)
    return round(max(n * tick, 0.05), 2)
