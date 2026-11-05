"""Technical snapshot of a stock (US or Tadawul): price, moving averages,
RSI, volatility, 52-week range, drawdown. Facts, not advice.

Examples:
  python3 analyze.py AAPL
  python3 analyze.py 2222          # Saudi Aramco (becomes 2222.SR)
  python3 analyze.py NVDA 1120 --json
"""
import argparse
import json
import math

from common import fetch_history, fmt_money, run


def sma(values, n):
    return sum(values[-n:]) / n if len(values) >= n else None


def rsi(values, n=14):
    """Wilder's RSI."""
    if len(values) <= n:
        return None
    gains = losses = 0.0
    for a, b in zip(values[:n], values[1:n + 1]):
        d = b - a
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    for a, b in zip(values[n:], values[n + 1:]):
        d = b - a
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def volatility(values, n=60):
    """Annualised volatility of daily returns, in %."""
    v = values[-(n + 1):]
    if len(v) < 3:
        return None
    rets = [math.log(b / a) for a, b in zip(v, v[1:]) if a > 0]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252) * 100


def pct(a, b):
    return (a / b - 1) * 100 if b else None


def analyze(rows, meta):
    closes = [r[1] for r in rows]
    price = float(meta["price"])
    hi, lo = max(closes[-252:]), min(closes[-252:])
    m = {
        "symbol": meta["symbol"], "name": meta["name"], "currency": meta["currency"],
        "price": price,
        "sma20": sma(closes, 20), "sma50": sma(closes, 50), "sma200": sma(closes, 200),
        "rsi14": rsi(closes), "volatility_pct": volatility(closes),
        "high_52w": hi, "low_52w": lo, "from_high_pct": pct(price, hi),
        "change_1m_pct": pct(price, closes[-22]) if len(closes) > 22 else None,
        "change_3m_pct": pct(price, closes[-64]) if len(closes) > 64 else None,
        "change_1y_pct": pct(price, closes[0]) if len(closes) > 200 else None,
    }
    notes = []
    s50, s200, r = m["sma50"], m["sma200"], m["rsi14"]
    if s50 and s200:
        if price > s50 > s200:
            notes.append("الاتجاه صاعد: السعر فوق متوسط 50 و200 يوم.")
        elif price < s50 < s200:
            notes.append("الاتجاه هابط: السعر تحت متوسط 50 و200 يوم.")
        else:
            notes.append("الاتجاه مختلط: السعر بين المتوسطات.")
    if r is not None:
        if r >= 70:
            notes.append(f"RSI {r:.0f}: منطقة تشبع شرائي، السهم ارتفع بسرعة.")
        elif r <= 30:
            notes.append(f"RSI {r:.0f}: منطقة تشبع بيعي، السهم نزل بسرعة.")
        else:
            notes.append(f"RSI {r:.0f}: محايد.")
    v = m["volatility_pct"]
    if v is not None:
        level = "عالي" if v > 45 else "متوسط" if v > 25 else "منخفض"
        notes.append(f"التذبذب السنوي {v:.0f}% ({level}).")
    if m["from_high_pct"] is not None and m["from_high_pct"] < -20:
        notes.append(f"السعر أقل من أعلى قمة في سنة بـ {abs(m['from_high_pct']):.0f}%.")
    m["notes"] = notes
    return m


def show(m):
    cur = m["currency"]

    def f(x, p=False):
        if x is None:
            return "—"
        return f"{x:+.1f}%" if p else fmt_money(x, cur)

    print(f"📈 {m['name']} ({m['symbol']})")
    print(f"  السعر الحالي     {f(m['price'])}")
    print(f"  متوسط 20/50/200  {f(m['sma20'])} / {f(m['sma50'])} / {f(m['sma200'])}")
    print(f"  أعلى/أدنى سنة    {f(m['high_52w'])} / {f(m['low_52w'])}  (من القمة {f(m['from_high_pct'], True)})")
    print(f"  التغير شهر/3 أشهر/سنة  {f(m['change_1m_pct'], True)} / {f(m['change_3m_pct'], True)} / {f(m['change_1y_pct'], True)}")
    for n in m["notes"]:
        print(f"  • {n}")
    print()


def main():
    ap = argparse.ArgumentParser(description="تحليل فني لسهم")
    ap.add_argument("symbols", nargs="+", help="رمز أمريكي مثل AAPL أو رقم سعودي مثل 2222")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    results = [analyze(*fetch_history(s, range_="1y")) for s in a.symbols]
    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    for m in results:
        show(m)
    print("هذي أرقام وصفية، مو نصيحة شراء أو بيع.")


if __name__ == "__main__":
    run(main)
