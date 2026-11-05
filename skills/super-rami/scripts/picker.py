"""Auto-pick stocks for Rami: scans a universe of large, liquid stocks and
ranks them by trend + momentum, adjusted for volatility.

Rules (no prediction, just filters and ranking):
  - keep only stocks in an uptrend: price > SMA200 and SMA50 > SMA200
  - momentum = 50% x 6-month + 30% x 3-month + 20% x 1-month return
  - score = momentum / annual volatility (smoother winners rank higher)
  - weights = inverse volatility (calmer stocks get more money)

Examples:
  python3 picker.py --market us
  python3 picker.py --market sa --top 5
  python3 picker.py --market us --budget 2000 --write     # also builds the budget plan
"""
import argparse
import json
import math

import budget as budget_mod
from common import RamiError, fetch_history, load_config, path, run, save_json

UNIVERSE = {
    "us": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "BRK-B", "JPM",
           "V", "MA", "LLY", "UNH", "XOM", "JNJ", "PG", "COST", "HD", "ABBV", "MRK", "PEP",
           "KO", "WMT", "BAC", "NFLX", "ADBE", "CRM", "AMD", "ORCL", "CSCO", "TMO", "ACN",
           "MCD", "LIN", "INTU", "QCOM", "TXN", "AMAT", "CAT", "GE", "IBM", "NOW", "UBER", "ISRG"],
    "sa": ["2222", "1120", "1180", "2010", "7010", "1150", "1211", "2082", "1010", "1060",
           "2280", "4190", "5110", "1140", "2020", "7020", "4013", "2050", "4002", "1050"],
}

NAMES_SA = {"2222": "أرامكو", "1120": "الراجحي", "1180": "الأهلي", "2010": "سابك", "7010": "STC",
            "1150": "الإنماء", "1211": "معادن", "2082": "أكوا باور", "1010": "الرياض", "1060": "الأول",
            "2280": "المراعي", "4190": "جرير", "5110": "الكهرباء", "1140": "البلاد",
            "2020": "سابك للمغذيات", "7020": "موبايلي", "4013": "د. سليمان الحبيب", "2050": "صافولا",
            "4002": "المواساة", "1050": "الفرنسي"}


def ret(closes, n):
    return closes[-1] / closes[-n - 1] - 1 if len(closes) > n else None


def vol(closes, n=126):
    v = closes[-(n + 1):]
    r = [math.log(b / a) for a, b in zip(v, v[1:]) if a > 0]
    if len(r) < 20:
        return None
    m = sum(r) / len(r)
    return math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1)) * math.sqrt(252)


def evaluate(symbol, closes):
    """Return metrics dict, or None if not enough data."""
    if len(closes) < 210:
        return None
    sma50, sma200 = sum(closes[-50:]) / 50, sum(closes[-200:]) / 200
    r1, r3, r6 = ret(closes, 21), ret(closes, 63), ret(closes, 126)
    v = vol(closes)
    if None in (r1, r3, r6) or not v:
        return None
    mom = 0.5 * r6 + 0.3 * r3 + 0.2 * r1
    uptrend = closes[-1] > sma200 and sma50 > sma200
    return {"symbol": symbol, "price": closes[-1], "ret_1m": r1 * 100, "ret_3m": r3 * 100,
            "ret_6m": r6 * 100, "vol": v * 100, "momentum": mom * 100,
            "uptrend": uptrend, "score": mom / v if uptrend and mom > 0 else None}


def pick(metrics, top):
    ranked = sorted((m for m in metrics if m and m["score"] is not None), key=lambda m: -m["score"])[:top]
    inv = [1 / m["vol"] for m in ranked]
    total = sum(inv) or 1
    for m, w in zip(ranked, inv):
        m["weight"] = round(w / total * 100, 1)
    return ranked


def main():
    ap = argparse.ArgumentParser(description="اختيار أسهم تلقائي")
    ap.add_argument("--market", choices=["us", "sa"], default="us")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--budget", type=float, help="يبني خطة الميزانية بعد الاختيار")
    ap.add_argument("--write", action="store_true", help="احفظ الاختيار (والخطة) للمحفظة الوهمية")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    metrics, failed = [], []
    for s in UNIVERSE[a.market]:
        try:
            rows, _ = fetch_history(s, range_="1y")
            metrics.append(evaluate(s, [r[1] for r in rows]))
        except RamiError:
            failed.append(s)
    chosen = pick(metrics, a.top)
    if not chosen:
        raise RamiError("ولا سهم عدّى الشروط الحين (السوق ضعيف). الأفضل تبقى كاش.")

    key = (lambda s: s) if a.market == "us" else (lambda s: s + ".SR")
    weights = {key(m["symbol"]): m["weight"] for m in chosen}
    result = {"market": a.market, "picks": chosen, "skipped_downtrend": sum(1 for m in metrics if m and not m["uptrend"])}
    if a.write:
        save_json(path("targets", f"auto_{a.market}.json"), {"source": "picker", "weights": weights})
    if a.budget:
        prices = {key(m["symbol"]): m["price"] for m in chosen}
        plan = budget_mod.make_plan(a.budget, weights, prices, load_config())
        result["plan"] = plan
        if a.write:
            save_json(path("plans", "latest.json"), plan)
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    label = "الأمريكي" if a.market == "us" else "السعودي"
    print(f"🎯 اختيار سوبر رامي من السوق {label} ({len(UNIVERSE[a.market])} سهم، "
          f"{result['skipped_downtrend']} استبعدتها لأنها في اتجاه هابط)\n")
    print(f"{'#':>2}  {'السهم':<18} {'السعر':>10} {'شهر':>8} {'3 أشهر':>8} {'6 أشهر':>8} {'التذبذب':>8} {'الوزن':>7}")
    for i, m in enumerate(chosen, 1):
        name = m["symbol"] if a.market == "us" else f"{m['symbol']} {NAMES_SA.get(m['symbol'], '')}"
        print(f"{i:>2}  {name:<18} {m['price']:>10,.2f} {m['ret_1m']:>+7.1f}% {m['ret_3m']:>+7.1f}% "
              f"{m['ret_6m']:>+7.1f}% {m['vol']:>7.0f}% {m['weight']:>6.1f}%")
    if failed:
        print(f"\nتعذر جلب: {', '.join(failed)}")
    if a.budget:
        p = result["plan"]
        print(f"\n💰 الخطة بميزانية {a.budget:,.0f}:")
        for l in p["lines"]:
            print(f"   {l['symbol']:<9} {l['qty']:g} سهم × {l['price']:,.2f} = {l['cost']:,.2f}")
        print(f"   كاش باقي: {p['cash_left']:,.2f}")
        if a.write:
            print("   انحفظت. نفّذها على المحفظة الوهمية: python3 paper.py apply-plan")
    print("\nالاختيار مبني على قواعد (اتجاه + زخم + تذبذب)، مو توقع. مو نصيحة مالية.")


if __name__ == "__main__":
    run(main)
