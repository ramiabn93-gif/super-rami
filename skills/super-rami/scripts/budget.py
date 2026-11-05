"""Turn target weights into a share-by-share plan that fits your budget.

Keeps a cash buffer, caps any one stock, and buys whole shares for
Tadawul (fractional shares allowed for US unless --whole).

Examples:
  python3 budget.py --budget 5000 --weights "AAPL=40,MSFT=35,GOOGL=25"
  python3 budget.py --budget 20000 --weights "2222=50,1120=50"
  python3 budget.py --budget 5000 --target buffett --write
"""
import argparse
import json
import math

from common import (RamiError, allows_fractional, currency_of, fmt_money, last_price,
                    load_config, load_json, normalize_symbol, path, run, save_json)


def parse_pairs(text):
    out = {}
    for part in filter(None, (p.strip() for p in text.split(","))):
        if "=" not in part:
            raise RamiError(f"الصيغة لازم تكون رمز=رقم، وصلني '{part}'")
        k, v = part.split("=", 1)
        out[normalize_symbol(k)] = float(v)
    return out


def make_plan(budget, weights, prices, cfg, whole=False):
    if budget <= 0:
        raise RamiError("الميزانية لازم تكون أكبر من صفر.")
    currencies = {currency_of(s) for s in weights}
    if len(currencies) != 1:
        raise RamiError("لا تخلط أسهم أمريكية وسعودية في خطة وحدة، سوّ خطة لكل سوق.")
    cur = currencies.pop()
    wsum = sum(w for w in weights.values() if w > 0)
    if wsum <= 0:
        raise RamiError("الأوزان لازم تكون موجبة.")

    investable = budget * (1 - cfg["cash_buffer_pct"] / 100)
    cap = budget * cfg["max_position_pct"] / 100
    lines, spent, capped = [], 0.0, []
    for sym, w in sorted(weights.items(), key=lambda kv: -kv[1]):
        if w <= 0:
            continue
        price = prices[sym]
        alloc = investable * w / wsum
        if alloc > cap:
            capped.append(sym)
            alloc = cap
        if whole or not allows_fractional(sym):
            qty = math.floor(alloc / price)
        else:
            qty = math.floor(alloc / price * 10000) / 10000
        if price < cfg["min_price"]:
            qty = 0
        cost = qty * price
        spent += cost
        lines.append({"symbol": sym, "weight": w / wsum * 100, "price": price, "qty": qty,
                      "cost": cost, "pct_of_budget": cost / budget * 100})
    return {"currency": cur, "budget": budget, "invested": spent, "cash_left": budget - spent,
            "cash_left_pct": (budget - spent) / budget * 100, "capped": capped, "lines": lines}


def main():
    ap = argparse.ArgumentParser(description="تقسيم الميزانية على الأسهم")
    ap.add_argument("--budget", type=float, required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--weights", help='مثل "AAPL=40,MSFT=60"')
    g.add_argument("--target", help="اسم ملف من copycat --write مثل buffett")
    ap.add_argument("--prices", help='أسعار يدوية بدل الجلب، مثل "AAPL=190"')
    ap.add_argument("--whole", action="store_true", help="أسهم كاملة بس")
    ap.add_argument("--write", action="store_true", help="حفظ الخطة للمحفظة الوهمية")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    cfg = load_config()

    if a.target:
        t = load_json(path("targets", f"{a.target.lower()}.json"), None)
        if not t:
            raise RamiError(f"ما لقيت أوزان محفوظة باسم {a.target}. شغّل copycat.py {a.target} --write أول.")
        weights = {normalize_symbol(k): v for k, v in t["weights"].items()}
    else:
        weights = parse_pairs(a.weights)

    prices = parse_pairs(a.prices) if a.prices else {}
    for s in weights:
        if s not in prices:
            prices[s] = last_price(s)

    plan = make_plan(a.budget, weights, prices, cfg, a.whole)
    if a.write:
        save_json(path("plans", "latest.json"), plan)
        plan["saved_to"] = path("plans", "latest.json")
    if a.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return

    cur = plan["currency"]
    print(f"💰 خطة بميزانية {fmt_money(a.budget, cur)} — كاش احتياطي {cfg['cash_buffer_pct']:g}% — حد السهم {cfg['max_position_pct']:g}%\n")
    print(f"{'الرمز':<9} {'الوزن':>7} {'السعر':>12} {'الكمية':>10} {'التكلفة':>14} {'من الميزانية':>12}")
    for l in plan["lines"]:
        print(f"{l['symbol']:<9} {l['weight']:>6.1f}% {l['price']:>12,.2f} {l['qty']:>10g} {l['cost']:>14,.2f} {l['pct_of_budget']:>11.1f}%")
    print(f"\nالمستثمر: {fmt_money(plan['invested'], cur)}   الكاش الباقي: {fmt_money(plan['cash_left'], cur)} ({plan['cash_left_pct']:.1f}%)")
    if plan["capped"]:
        print("انقصّت هذي الأسهم عند الحد الأعلى والباقي صار كاش:", "، ".join(plan["capped"]))
    zero = [l["symbol"] for l in plan["lines"] if l["qty"] == 0]
    if zero:
        print("ما تكفي الميزانية لسهم واحد من:", "، ".join(zero))
    if a.write:
        print(f"انحفظت الخطة. نفّذها على المحفظة الوهمية: python3 paper.py apply-plan")


if __name__ == "__main__":
    run(main)
