"""SPX Smart Rally strategy (Rami's rules), 1-minute bars.

CALL setup (demand):
  1. A swing low followed by a rally of at least min_rally_pts.
  2. Save the demand zone [low, low + zone_pts].
  3. Wait for price to come back into the zone.
  4. Bullish divergence at the retest: RSI and/or MACD higher than at the swing low.
  5. Bullish BOS: a candle closes above the last micro swing high.
  6. Signal: CALL, OTM strike.
PUT setup is the mirror image (supply zone, bearish divergence, bearish BOS).

This tool only finds signals and backtests them on the index price.
It never places orders. Option prices (0DTE) are not modeled.

Examples:
  python3 strategy.py scan                  # today's signals (and alert if fresh)
  python3 strategy.py backtest              # last 5 days of 1-minute data
  python3 strategy.py backtest --csv my.csv # your own data: time,open,high,low,close
"""
import argparse
import csv
import datetime as dt
import json
import math
from zoneinfo import ZoneInfo

from common import RamiError, fetch_history, load_config, run

NY = ZoneInfo("America/New_York")


# ---------------------------------------------------------------- indicators

def rsi_series(c, n=14):
    out = [None] * len(c)
    if len(c) <= n:
        return out
    g = sum(max(c[i] - c[i - 1], 0) for i in range(1, n + 1)) / n
    l = sum(max(c[i - 1] - c[i], 0) for i in range(1, n + 1)) / n
    out[n] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n
        l = (l * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def ema_series(c, n):
    k = 2 / (n + 1)
    out, e = [], c[0]
    for x in c:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def macd_series(c, fast=12, slow=26):
    f, s = ema_series(c, fast), ema_series(c, slow)
    return [a - b for a, b in zip(f, s)]


# ---------------------------------------------------------------- bars

def bars_from_rows(rows):
    return [{"t": r[0], "c": r[1], "h": r[3], "l": r[4], "o": r[5]} for r in rows]


def bars_from_csv(p):
    out = []
    with open(p, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {k.strip().lower(): v for k, v in row.items()}
            t = row.get("time") or row.get("datetime") or row.get("date")
            try:
                ts = float(t)
            except ValueError:
                d = dt.datetime.fromisoformat(t.replace("Z", "+00:00"))
                ts = (d if d.tzinfo else d.replace(tzinfo=NY)).timestamp()
            out.append({"t": ts, "o": float(row["open"]), "h": float(row["high"]),
                        "l": float(row["low"]), "c": float(row["close"])})
    if not out:
        raise RamiError("ملف CSV فاضي.")
    return sorted(out, key=lambda b: b["t"])


def ny_time(ts):
    return dt.datetime.fromtimestamp(ts, NY)


# ---------------------------------------------------------------- detection

def is_pivot(bars, i, k, kind, end=None):
    """Swing point at i using k bars each side, looking no further than end."""
    end = len(bars) - 1 if end is None else end
    if i - k < 0 or i + k > end:
        return False
    key = "l" if kind == "low" else "h"
    v = bars[i][key]
    left = [bars[j][key] for j in range(i - k, i)]
    right = [bars[j][key] for j in range(i + 1, i + k + 1)]
    if kind == "low":
        return all(v <= x for x in left) and all(v < x for x in right)
    return all(v >= x for x in left) and all(v > x for x in right)


def divergence_ok(mode, ind_now, ind_then, bullish):
    checks = [now is not None and then is not None and (now > then if bullish else now < then)
              for now, then in zip(ind_now, ind_then)]
    rsi_ok, macd_ok = checks
    return {"rsi": rsi_ok, "macd": macd_ok, "both": rsi_ok and macd_ok,
            "any": rsi_ok or macd_ok}[mode]


def find_signals(bars, s):
    """Walk bars in order (no look-ahead) and return signals."""
    closes = [b["c"] for b in bars]
    rsi, macd = rsi_series(closes), macd_series(closes)
    k, m = s["pivot_bars"], s["micro_pivot_bars"]
    zones, signals = [], []
    micro_highs, micro_lows = [], []  # confirmed micro swing indexes
    day = None

    for j, b in enumerate(bars):
        d = ny_time(b["t"]).date()
        if d != day:                  # 0DTE: zones live within one session
            day, zones, micro_highs, micro_lows = d, [], [], []
            day_start = j

        # newly confirmed swing points (bar j confirms pivots k/m bars back)
        p = j - k
        if p >= day_start:
            for kind in ("low", "high"):
                if is_pivot(bars, p, k, kind, j):
                    lvl = bars[p]["l" if kind == "low" else "h"]
                    zones.append({"kind": "demand" if kind == "low" else "supply", "p": p, "lvl": lvl,
                                  "rally": False, "touch": None, "ext": None, "done": False})
        q = j - m
        if q >= day_start:
            if is_pivot(bars, q, m, "high", j):
                micro_highs.append(q)
            if is_pivot(bars, q, m, "low", j):
                micro_lows.append(q)

        for z in zones:
            if z["done"] or z["p"] >= j - k:
                continue
            bull = z["kind"] == "demand"
            lvl = z["lvl"]
            if j - z["p"] > s["zone_max_age"]:
                z["done"] = True
                continue
            if not z["rally"]:
                if (b["l"] < lvl) if bull else (b["h"] > lvl):
                    z["done"] = True          # swing broken before the rally
                elif (b["h"] - lvl if bull else lvl - b["l"]) >= s["min_rally_pts"]:
                    z["rally"] = True
                continue
            top = lvl + s["zone_pts"] if bull else lvl - s["zone_pts"]
            if z["touch"] is None:
                if (b["l"] <= top) if bull else (b["h"] >= top):
                    z["touch"], z["ext"] = j, j
                else:
                    continue
            # zone broken through: invalidate
            if (b["l"] < lvl - s["zone_pts"]) if bull else (b["h"] > lvl + s["zone_pts"]):
                z["done"] = True
                continue
            # track the extreme bar of the retest
            if (b["l"] < bars[z["ext"]]["l"]) if bull else (b["h"] > bars[z["ext"]]["h"]):
                z["ext"] = j
            if j - z["touch"] > s["max_bos_wait"]:
                z["done"] = True
                continue
            # BOS: close beyond the last micro swing formed after the zone pivot
            swings = [x for x in (micro_highs if bull else micro_lows) if z["p"] < x < j]
            if not swings:
                continue
            ref = bars[swings[-1]]["h" if bull else "l"]
            broke = b["c"] > ref if bull else b["c"] < ref
            if not broke:
                continue
            e, p0 = z["ext"], z["p"]
            if not divergence_ok(s["divergence"], (rsi[e], macd[e]), (rsi[p0], macd[p0]), bull):
                z["done"] = True              # BOS without divergence: skip the setup
                continue
            z["done"] = True
            price = b["c"]
            step = s["strike_step"]
            strike = (math.ceil((price + s["otm_pts"]) / step) * step if bull
                      else math.floor((price - s["otm_pts"]) / step) * step)
            signals.append({
                "i": j, "time": ny_time(b["t"]).strftime("%Y-%m-%d %H:%M"),
                "side": "CALL" if bull else "PUT", "price": round(price, 2),
                "zone": [round(lvl, 2), round(top, 2)], "bos_level": round(ref, 2),
                "strike": strike,
                "rsi": [round(rsi[p0] or 0, 1), round(rsi[e] or 0, 1)],
                "macd": [round(macd[p0], 2), round(macd[e], 2)],
            })
    return signals


def backtest(bars, signals, s):
    """Score each signal on the index: target vs stop within hold_minutes."""
    res = []
    for sig in signals:
        i, entry, up = sig["i"], sig["price"], sig["side"] == "CALL"
        day = ny_time(bars[i]["t"]).date()
        outcome, move, mfe, mae = "flat", 0.0, 0.0, 0.0
        for b in bars[i + 1:i + 1 + s["hold_minutes"]]:
            if ny_time(b["t"]).date() != day:
                break
            fav = (b["h"] - entry) if up else (entry - b["l"])
            adv = (entry - b["l"]) if up else (b["h"] - entry)
            mfe, mae = max(mfe, fav), max(mae, adv)
            if adv >= s["stop_pts"]:          # assume the stop hits first in a tie
                outcome = "loss"
                break
            if fav >= s["target_pts"]:
                outcome = "win"
                break
            move = (b["c"] - entry) if up else (entry - b["c"])
        res.append(dict(sig, outcome=outcome, move=round(move, 2), mfe=round(mfe, 2), mae=round(mae, 2)))
    wins = sum(r["outcome"] == "win" for r in res)
    losses = sum(r["outcome"] == "loss" for r in res)
    return {"trades": len(res), "wins": wins, "losses": losses, "flat": len(res) - wins - losses,
            "win_rate": wins / (wins + losses) * 100 if wins + losses else None, "results": res}


# ---------------------------------------------------------------- CLI

def load_bars(a, s):
    if a.csv:
        return bars_from_csv(a.csv)
    rows, _ = fetch_history(s["symbol"], range_=f"{a.days}d", interval="1m", max_age=55)
    return bars_from_rows(rows)


def main():
    ap = argparse.ArgumentParser(description="استراتيجية SPX Smart Rally — إشارات وباك تست فقط")
    ap.add_argument("cmd", choices=["scan", "backtest", "rules"])
    ap.add_argument("--csv", help="ملف بيانات دقيقة: time,open,high,low,close")
    ap.add_argument("--days", type=int, default=5, help="أيام البيانات (ياهو يعطي 7 كحد أقصى للدقيقة)")
    ap.add_argument("--fresh", type=int, default=3, help="scan: الإشارة تعتبر جديدة خلال كم دقيقة")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    s = load_config()["spx_strategy"]

    if a.cmd == "rules":
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return

    bars = load_bars(a, s)
    signals = find_signals(bars, s)

    if a.cmd == "scan":
        last_day = ny_time(bars[-1]["t"]).date()
        today = [x for x in signals if ny_time(bars[x["i"]]["t"]).date() == last_day]
        fresh = [x for x in today if len(bars) - 1 - x["i"] < a.fresh]
        if a.json:
            print(json.dumps({"last_bar": ny_time(bars[-1]["t"]).isoformat(), "today": today,
                              "fresh": fresh}, ensure_ascii=False, indent=2))
            return
        print(f"🕐 آخر شمعة: {ny_time(bars[-1]['t']).strftime('%Y-%m-%d %H:%M')} (نيويورك) — السعر {bars[-1]['c']:,.2f}")
        if fresh:
            for x in fresh:
                icon = "🟢" if x["side"] == "CALL" else "🔴"
                print(f"🚨 {icon} إشارة {x['side']} الحين عند {x['price']:,.2f} — "
                      f"strike مقترح {x['strike']} — المنطقة {x['zone'][0]}–{x['zone'][1]}")
        else:
            print("ما فيه إشارة جديدة الحين.")
        if today:
            print("\nإشارات اليوم:")
            for x in today:
                print(f"  {x['time']}  {x['side']:<4} @ {x['price']:,.2f}  strike {x['strike']}  "
                      f"RSI {x['rsi'][0]}→{x['rsi'][1]}")
        print("\nإشارة فقط. سوبر رامي ما ينفّذ عقود.")
        return

    bt = backtest(bars, signals, s)
    if a.json:
        print(json.dumps(bt, ensure_ascii=False, indent=2))
        return
    days = sorted({ny_time(b["t"]).date() for b in bars})
    print(f"🧪 باك تست على {len(days)} أيام ({days[0]} → {days[-1]}) — {len(bars):,} شمعة دقيقة")
    print(f"الهدف {s['target_pts']} نقاط، الوقف {s['stop_pts']} نقاط، أقصى مدة {s['hold_minutes']} دقيقة\n")
    for r in bt["results"]:
        mark = {"win": "✅", "loss": "❌", "flat": "➖"}[r["outcome"]]
        print(f" {mark} {r['time']}  {r['side']:<4} @ {r['price']:,.2f}  أفضل {r['mfe']:+.1f}  أسوأ {-r['mae']:+.1f}")
    wr = f"{bt['win_rate']:.0f}%" if bt["win_rate"] is not None else "—"
    print(f"\nالصفقات {bt['trades']} — ربح {bt['wins']} — خسارة {bt['losses']} — بدون نتيجة {bt['flat']} — نسبة النجاح {wr}")
    print("مهم: هذا على سعر المؤشر. عقود 0DTE OTM تخسر قيمتها بسرعة مع الوقت، فالنتيجة الفعلية على العقود غالباً أسوأ.")


if __name__ == "__main__":
    run(main)
