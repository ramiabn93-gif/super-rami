"""وش أشتري اليوم؟ — Super Rami's daily recommendation.

Runs the stock picker on the US and Saudi markets and checks the crypto
picks for a live entry signal, then prints one plain list: what to buy,
why, and how much (if a budget is given).

Examples:
  python3 advisor.py
  python3 advisor.py --budget 2000
  python3 advisor.py --budget 1000 --markets us crypto
"""
import argparse
import json
import time

import crypto_strategies as cs
import picker
from common import RamiError, fetch_history, load_config, run

SAR_PER_USD = 3.75          # the riyal is pegged to the dollar


def stock_ideas(market, top, fetch=fetch_history):
    metrics = []
    for s in picker.UNIVERSE[market]:
        try:
            rows, _ = fetch(s, range_="1y")
            metrics.append(picker.evaluate(s, [r[1] for r in rows]))
        except RamiError:
            continue
    ideas = []
    for m in picker.pick(metrics, top):
        name = m["symbol"] if market == "us" else f"{m['symbol']} {picker.NAMES_SA.get(m['symbol'], '')}".strip()
        why = (f"صاعد {m['ret_6m']:+.0f}% في 6 أشهر و{m['ret_1m']:+.0f}% آخر شهر، "
               f"وفوق متوسط 200 يوم، وتذبذبه {m['vol']:.0f}%")
        ideas.append({"market": market, "symbol": m["symbol"], "name": name, "price": m["price"],
                      "currency": "USD" if market == "us" else "SAR", "action": "اشتر",
                      "why": why, "weight": m["weight"]})
    return ideas


def crypto_ideas(fetch_bars=None):
    import binance_client as bn
    import crypto_bot
    cfg = crypto_bot.ccfg()
    if not load_config().get("crypto", {}).get("last_autopick"):
        crypto_bot.run_autopick(cfg)
        cfg = crypto_bot.ccfg()
    fetch_bars = fetch_bars or (lambda s: bn.klines(s, cfg["interval"], 400, cfg["market"]))
    p = crypto_bot.params(cfg)
    ideas = []
    now = time.time() * 1000
    for sym in crypto_bot.watchlist(cfg):
        try:
            bars = [b for b in fetch_bars(sym) if b.get("close_t", 0) < now]
        except RamiError:
            continue
        if len(bars) < 250:
            continue
        ind = cs.Ind(bars, p)
        name = crypto_bot.strategy_for(cfg, sym)
        sig = cs.signal(name, bars, ind, len(bars) - 1, p)
        meme = crypto_bot.is_meme(cfg, sym)
        trend_up = bars[-1]["c"] > ind.ema_trend[-1]
        if sig == 1:
            action, why = "اشتر الحين", f"طلعت إشارة {cs.NAMES_AR[name]} على آخر شمعة"
        elif trend_up:
            action, why = "راقب", f"اتجاهها صاعد، وأنتظر إشارة {cs.NAMES_AR[name]} للدخول"
        else:
            continue                                   # downtrend and no signal: not an idea
        stop = bars[-1]["c"] - p["stop_atr"] * ind.atr[-1]
        ideas.append({"market": "crypto", "symbol": sym, "name": sym.replace("USDT", ""),
                      "price": bars[-1]["c"], "currency": "USD", "action": action,
                      "why": why + (" — ميمز: مخاطرة عالية" if meme else ""),
                      "stop": stop, "meme": meme})
    ideas.sort(key=lambda x: (x["action"] != "اشتر الحين", x["meme"]))
    return ideas


def allocate(ideas_by_market, budget):
    """Split the budget equally across markets that have buy ideas, then inside each market."""
    buy = {m: [i for i in ideas if i["action"] in ("اشتر", "اشتر الحين")]
           for m, ideas in ideas_by_market.items()}
    active = [m for m, ideas in buy.items() if ideas]
    if not active or not budget:
        return
    share = budget / len(active)
    for m in active:
        items = buy[m]
        if m == "crypto":
            weights = [0.5 if i["meme"] else 1.0 for i in items]       # memes get half
        else:
            weights = [i["weight"] for i in items]
        total = sum(weights)
        for i, w in zip(items, weights):
            usd = share * w / total
            i["amount_usd"] = round(usd, 2)
            i["amount_local"] = round(usd * (SAR_PER_USD if i["currency"] == "SAR" else 1), 2)


def main():
    ap = argparse.ArgumentParser(description="وش أشتري اليوم؟")
    ap.add_argument("--budget", type=float, help="الميزانية بالدولار")
    ap.add_argument("--markets", nargs="+", choices=["us", "sa", "crypto"], default=["us", "sa", "crypto"])
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    ideas = {}
    for m in a.markets:
        try:
            ideas[m] = crypto_ideas()[:a.top + 2] if m == "crypto" else stock_ideas(m, a.top)
        except RamiError as e:
            print(f"⚠️ تعذر فحص {m}: {e}")
            ideas[m] = []
    allocate(ideas, a.budget)
    if a.json:
        print(json.dumps(ideas, ensure_ascii=False, indent=2, default=str))
        return

    titles = {"us": "🇺🇸 أسهم أمريكية", "sa": "🇸🇦 أسهم سعودية", "crypto": "🪙 عملات رقمية"}
    print("🦸 سوبر رامي — وش تشتري اليوم\n")
    for m in a.markets:
        print(titles[m])
        if not ideas[m]:
            print("   ما فيه شي يستاهل الحين. خل فلوسك كاش في هذا السوق.\n")
            continue
        for i in ideas[m]:
            cur = "ر.س" if i["currency"] == "SAR" else "$"
            line = f"   {'✅' if i['action'].startswith('اشتر') else '👀'} {i['action']}: {i['name']} — {i['price']:,.6g} {cur}"
            if i.get("amount_local"):
                line += f" — حط {i['amount_local']:,.0f} {cur}"
            print(line)
            print(f"      ليش: {i['why']}")
            if i.get("stop"):
                print(f"      اطلع إذا نزل تحت {i['stop']:,.6g}")
        print()
    if not a.budget:
        print("💡 قلي ميزانيتك (--budget) وأقولك كم تحط في كل وحدة.")
    print("⚠️ هذا اختيار بقواعد (اتجاه + زخم + إشارات)، مو ضمان ربح. الأسهم للمدى المتوسط، والعملات للمضاربة القصيرة.")


if __name__ == "__main__":
    run(main)
