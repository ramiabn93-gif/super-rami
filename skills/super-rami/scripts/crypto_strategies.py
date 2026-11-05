"""Five crypto strategies + a shared position manager + backtester.

Pure functions on bar lists: bar = {"t": open_time_ms, "o", "h", "l", "c", "v"}.
Signals return +1 (long), -1 (short) or 0. Spot trading ignores shorts.

Strategies:
  trend      EMA fast/slow cross, filtered by a long EMA            (strong coins)
  breakout   close beyond the N-bar range with a volume surge       (memes)
  rsi_dip    RSI leaves oversold/overbought in the direction of trend (strong coins)
  squeeze    Bollinger bandwidth squeeze, then a close outside a band (both)
  momentum   volume spike + strong close, long only                  (memes)

Position manager (same for backtest and live):
  - initial stop = entry -/+ stop_atr x ATR
  - trailing stop = best price -/+ trail_atr x ATR, once in profit by 1 ATR
  - strategy exit (trend: opposite cross, rsi_dip: RSI back to the middle)
  - day end: close unless the trend is still strong and the trade is in profit
  - max_hold_days: always close after this
"""
import datetime as dt
import math

STRATEGIES = ("trend", "breakout", "rsi_dip", "squeeze", "momentum")

DEFAULT_PARAMS = {
    "ema_fast": 9, "ema_slow": 21, "ema_trend": 200,
    "range_len": 20, "vol_len": 20, "vol_mult": 2.0,
    "rsi_len": 14, "rsi_low": 30, "rsi_high": 70, "rsi_exit": 55,
    "bb_len": 20, "bb_std": 2.0, "squeeze_lookback": 100,
    "mom_vol_mult": 3.0, "mom_bars": 4, "mom_min_pct": 3.0,
    "atr_len": 14, "stop_atr": 2.0, "trail_atr": 2.0,
    "day_end_utc": 20,          # 23:00 Riyadh
    "max_hold_days": 3,
}

NAMES_AR = {"trend": "تتبع الاتجاه", "breakout": "كسر النطاق مع الحجم", "rsi_dip": "ارتداد RSI",
            "squeeze": "ضغط بولينجر", "momentum": "صائد زخم الميمز"}


# ---------------------------------------------------------------- indicators

class Ind:
    """Indicators computed once per bar list."""

    def __init__(self, bars, p):
        c = [b["c"] for b in bars]
        self.c = c
        self.ema_fast = ema(c, p["ema_fast"])
        self.ema_slow = ema(c, p["ema_slow"])
        self.ema_trend = ema(c, p["ema_trend"])
        self.ema20 = ema(c, 20)
        self.rsi = rsi(c, p["rsi_len"])
        self.atr = atr(bars, p["atr_len"])
        self.bb_mid, self.bb_up, self.bb_lo = bollinger(c, p["bb_len"], p["bb_std"])


def ema(x, n):
    out, k, e = [], 2 / (n + 1), x[0] if x else 0
    for v in x:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(c, n):
    out = [None] * len(c)
    if len(c) <= n:
        return out
    g = sum(max(c[i] - c[i - 1], 0) for i in range(1, n + 1)) / n
    l = sum(max(c[i - 1] - c[i], 0) for i in range(1, n + 1)) / n
    out[n] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        g, l = (g * (n - 1) + max(d, 0)) / n, (l * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def atr(bars, n):
    out, a = [], None
    for i, b in enumerate(bars):
        tr = b["h"] - b["l"] if i == 0 else max(b["h"] - b["l"], abs(b["h"] - bars[i - 1]["c"]),
                                                abs(b["l"] - bars[i - 1]["c"]))
        a = tr if a is None else (a * (n - 1) + tr) / n
        out.append(a)
    return out


def bollinger(c, n, k):
    mid, up, lo = [None] * len(c), [None] * len(c), [None] * len(c)
    for i in range(n - 1, len(c)):
        w = c[i - n + 1:i + 1]
        m = sum(w) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / n)
        mid[i], up[i], lo[i] = m, m + k * sd, m - k * sd
    return mid, up, lo


def avg(xs):
    return sum(xs) / len(xs) if xs else 0


# ---------------------------------------------------------------- entries

def signal(name, bars, ind, i, p):
    if i < max(p["ema_trend"], p["squeeze_lookback"], 60) or i >= len(bars):
        return 0
    b, c = bars[i], ind.c
    if name == "trend":
        up = ind.ema_fast[i - 1] <= ind.ema_slow[i - 1] and ind.ema_fast[i] > ind.ema_slow[i]
        dn = ind.ema_fast[i - 1] >= ind.ema_slow[i - 1] and ind.ema_fast[i] < ind.ema_slow[i]
        if up and c[i] > ind.ema_trend[i]:
            return 1
        if dn and c[i] < ind.ema_trend[i]:
            return -1
    elif name == "breakout":
        n = p["range_len"]
        prev = bars[i - n:i]
        vol_ok = b["v"] > p["vol_mult"] * avg([x["v"] for x in bars[i - p["vol_len"]:i]])
        if vol_ok and c[i] > max(x["h"] for x in prev):
            return 1
        if vol_ok and c[i] < min(x["l"] for x in prev):
            return -1
    elif name == "rsi_dip":
        r0, r1 = ind.rsi[i - 1], ind.rsi[i]
        if r0 is None or r1 is None:
            return 0
        if r0 < p["rsi_low"] <= r1 and c[i] > ind.ema_trend[i]:
            return 1
        if r0 > p["rsi_high"] >= r1 and c[i] < ind.ema_trend[i]:
            return -1
    elif name == "squeeze":
        bw = [(ind.bb_up[j] - ind.bb_lo[j]) / ind.bb_mid[j]
              for j in range(i - p["squeeze_lookback"], i) if ind.bb_mid[j]]
        if not bw or bw[-1] > min(bw) * 1.1:        # previous bar must be (near) the tightest
            return 0
        if c[i] > ind.bb_up[i]:
            return 1
        if c[i] < ind.bb_lo[i]:
            return -1
    elif name == "momentum":
        spike = b["v"] > p["mom_vol_mult"] * avg([x["v"] for x in bars[i - 50:i]])
        move = (c[i] / c[i - p["mom_bars"]] - 1) * 100
        rng = b["h"] - b["l"]
        strong_close = rng > 0 and (b["c"] - b["l"]) / rng >= 0.7
        if spike and move >= p["mom_min_pct"] and strong_close and c[i] > ind.ema20[i]:
            return 1
    return 0


# ---------------------------------------------------------------- position manager

def new_position(symbol, strategy, side, entry, atr_now, t_ms, p, qty=None):
    stop = entry - side * p["stop_atr"] * atr_now
    return {"symbol": symbol, "strategy": strategy, "side": side, "entry": entry, "qty": qty,
            "stop": stop, "best": entry, "opened": t_ms, "held_overnight": False}


def stop_hit(pos, bar):
    """Exit price if the bar touched the stop, else None (gaps fill at the open)."""
    if pos["side"] == 1 and bar["l"] <= pos["stop"]:
        return min(bar["o"], pos["stop"])
    if pos["side"] == -1 and bar["h"] >= pos["stop"]:
        return max(bar["o"], pos["stop"])
    return None


def manage(pos, bars, ind, i, p):
    """Update the trailing stop at bar i's close. Returns an exit reason or None."""
    b, s, a = bars[i], pos["side"], ind.atr[i]
    pos["best"] = max(pos["best"], b["h"]) if s == 1 else min(pos["best"], b["l"])
    if (pos["best"] - pos["entry"]) * s >= a:                   # in profit by 1 ATR: trail
        trail = pos["best"] - s * p["trail_atr"] * a
        pos["stop"] = max(pos["stop"], trail) if s == 1 else min(pos["stop"], trail)

    name = pos["strategy"]
    if name == "trend" and (ind.ema_fast[i] - ind.ema_slow[i]) * s < 0:
        return "انعكاس الاتجاه"
    if name == "rsi_dip" and ind.rsi[i] is not None:
        if (s == 1 and ind.rsi[i] >= p["rsi_exit"]) or (s == -1 and ind.rsi[i] <= 100 - p["rsi_exit"]):
            return "RSI رجع للوسط"

    now = dt.datetime.fromtimestamp(b["t"] / 1000, dt.timezone.utc) + dt.timedelta(milliseconds=bar_ms(bars))
    opened = dt.datetime.fromtimestamp(pos["opened"] / 1000, dt.timezone.utc)
    if (now - opened).total_seconds() >= p["max_hold_days"] * 86400:
        return f"وصل أقصى مدة {p['max_hold_days']} أيام"
    if now.hour == p["day_end_utc"] and now.minute < bar_ms(bars) / 60000:
        strong = (b["c"] - ind.ema_trend[i]) * s > 0 and (b["c"] - pos["entry"]) * s > 0
        if not strong:
            return "نهاية اليوم والاتجاه ضعيف"
        pos["held_overnight"] = True
    return None


def bar_ms(bars):
    return bars[1]["t"] - bars[0]["t"] if len(bars) > 1 else 60000


# ---------------------------------------------------------------- backtest

def backtest(bars, name, p, fee_pct=0.1, allow_short=False, symbol=""):
    ind = Ind(bars, p)
    trades, pos = [], None
    equity, peak, max_dd = 1.0, 1.0, 0.0
    for i in range(len(bars)):
        b = bars[i]
        if pos:
            px, why = stop_hit(pos, b), "وقف"
            if px is None:
                why = manage(pos, bars, ind, i, p)
                px = b["c"] if why else None
            if px is not None:
                r = (px / pos["entry"] - 1) * pos["side"] - 2 * fee_pct / 100
                equity *= 1 + r
                peak = max(peak, equity)
                max_dd = max(max_dd, 1 - equity / peak)
                trades.append({"side": pos["side"], "entry": pos["entry"], "exit": px, "ret": r * 100,
                               "why": why, "hours": (b["t"] - pos["opened"]) / 3.6e6,
                               "overnight": pos["held_overnight"]})
                pos = None
                continue
        if not pos and i + 1 < len(bars):
            sg = signal(name, bars, ind, i, p)
            if sg == -1 and not allow_short:
                sg = 0
            if sg:
                nb = bars[i + 1]
                pos = new_position(symbol, name, sg, nb["o"], ind.atr[i], nb["t"], p)
    wins = [t for t in trades if t["ret"] > 0]
    losses = [t for t in trades if t["ret"] <= 0]
    gross_loss = -sum(t["ret"] for t in losses)
    return {
        "strategy": name, "trades": len(trades),
        "win_rate": len(wins) / len(trades) * 100 if trades else 0,
        "total_return": (equity - 1) * 100, "max_dd": max_dd * 100,
        "profit_factor": sum(t["ret"] for t in wins) / gross_loss if gross_loss else (99.0 if wins else 0),
        "avg_hours": avg([t["hours"] for t in trades]),
        "overnight": sum(t["overnight"] for t in trades),
        "list": trades,
    }


def score(r):
    """Rank strategies: return adjusted for drawdown, needs some trades."""
    if r["trades"] < 3:
        return -1e9
    return r["total_return"] - 0.5 * r["max_dd"]
