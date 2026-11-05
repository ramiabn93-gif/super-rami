"""Paper (fake-money) portfolio. Nothing here touches a real broker.

Every buy/sell is a preview unless --yes is given, and every order goes
through the risk rules first.

Examples:
  python3 paper.py init --usd 10000 --sar 37500
  python3 paper.py buy AAPL 5             # preview
  python3 paper.py buy AAPL 5 --yes       # execute (fake money)
  python3 paper.py buy 2222 --amount 3000 --yes
  python3 paper.py sell AAPL 2 --yes
  python3 paper.py status
  python3 paper.py apply-plan --yes
  python3 paper.py history
"""
import argparse
import datetime as dt
import json
import math

import risk
from common import (RamiError, allows_fractional, currency_of, fmt_money, last_price,
                    load_config, load_json, normalize_symbol, path, run, save_json)

PF_PATH = None


def pf_path():
    return PF_PATH or path("paper_portfolio.json")


def load_pf():
    pf = load_json(pf_path(), None)
    if pf is None:
        raise RamiError("ما فيه محفظة وهمية. ابدأ بـ: python3 paper.py init")
    return pf


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def get_price(symbol, override=None):
    return float(override) if override else last_price(symbol)


def execute(pf, symbol, side, qty, price, cfg, confirm):
    """Risk-check one order, then apply it if confirm is True. Returns (ok, issues)."""
    cur = currency_of(symbol)
    issues = risk.check_order(pf, symbol, side, qty, price, cur, cfg)
    if risk.verdict(issues) == "BLOCK" or not confirm:
        return False, issues
    pos = pf["positions"].get(symbol, {"qty": 0.0, "avg_cost": 0.0, "currency": cur})
    value = qty * price
    if side == "buy":
        new_qty = pos["qty"] + qty
        pos["avg_cost"] = (pos["qty"] * pos["avg_cost"] + value) / new_qty
        pos["qty"] = new_qty
        pf["cash"][cur] -= value
    else:
        pos["qty"] -= qty
        pf["cash"][cur] += value
        pf.setdefault("realized", {}).setdefault(cur, 0.0)
        pf["realized"][cur] += (price - pos["avg_cost"]) * qty
    if pos["qty"] <= 1e-9:
        pf["positions"].pop(symbol, None)
    else:
        pf["positions"][symbol] = pos
    pf["trades"].append({"time": now(), "side": side, "symbol": symbol, "qty": qty,
                         "price": price, "currency": cur})
    return True, issues


def print_issues(issues):
    for level, msg in issues:
        print(f"  {'⛔' if level == 'BLOCK' else '⚠️'} {msg}")


def cmd_init(a, cfg):
    if load_json(pf_path(), None) and not a.reset:
        raise RamiError("المحفظة موجودة. استخدم --reset لو تبي تبدأ من جديد (يمسح الصفقات).")
    start = cfg["paper_start_cash"]
    pf = {"created": now(),
          "cash": {"USD": float(a.usd if a.usd is not None else start["USD"]),
                   "SAR": float(a.sar if a.sar is not None else start["SAR"])},
          "start_cash": {}, "positions": {}, "trades": [], "realized": {}}
    pf["start_cash"] = dict(pf["cash"])
    save_json(pf_path(), pf)
    print(f"✅ محفظة وهمية جديدة: {fmt_money(pf['cash']['USD'], 'USD')} و {fmt_money(pf['cash']['SAR'], 'SAR')}")


def cmd_order(a, cfg, side):
    pf = load_pf()
    sym = normalize_symbol(a.symbol)
    price = get_price(sym, a.price)
    if a.amount:
        qty = a.amount / price
        qty = math.floor(qty * 10000) / 10000 if allows_fractional(sym) else math.floor(qty)
    else:
        qty = float(a.qty)
    if not allows_fractional(sym) and qty != int(qty):
        raise RamiError("السوق السعودي ما يقبل كسور أسهم، استخدم عدد صحيح.")
    cur = currency_of(sym)
    word = "شراء" if side == "buy" else "بيع"
    print(f"🧾 {word} {qty:g} × {sym} بسعر {fmt_money(price, cur)} = {fmt_money(qty * price, cur)}")
    ok, issues = execute(pf, sym, side, qty, price, cfg, a.yes)
    print_issues(issues)
    if risk.verdict(issues) == "BLOCK":
        print("❌ مرفوضة من قواعد المخاطر.")
        raise SystemExit(2)
    if not a.yes:
        print("👀 معاينة بس. للتنفيذ (فلوس وهمية) أعد الأمر مع --yes")
        return
    save_json(pf_path(), pf)
    print(f"✅ تم. الكاش الحين {fmt_money(pf['cash'][cur], cur)}")


def cmd_status(a, cfg):
    pf = load_pf()
    prices = {}
    for s in pf["positions"]:
        try:
            prices[s] = get_price(s)
        except RamiError:
            prices[s] = pf["positions"][s]["avg_cost"]
            print(f"تعذر جلب سعر {s}، استخدمت سعر الشراء.")
    rows = []
    for cur in ("USD", "SAR"):
        pos = {s: p for s, p in pf["positions"].items() if p["currency"] == cur}
        total = risk.portfolio_value(pf, prices, cur)
        start = pf["start_cash"].get(cur, 0)
        if not pos and total == start:
            if start:
                print(f"\n{cur}: كاش فقط {fmt_money(total, cur)}")
            continue
        print(f"\n— محفظة {cur} —")
        print(f"{'الرمز':<9} {'الكمية':>10} {'متوسط التكلفة':>14} {'السعر':>12} {'القيمة':>14} {'الربح/الخسارة':>16} {'الوزن':>7}")
        for s, p in sorted(pos.items()):
            px = prices[s]
            val = p["qty"] * px
            pnl = (px - p["avg_cost"]) * p["qty"]
            pnl_pct = (px / p["avg_cost"] - 1) * 100
            print(f"{s:<9} {p['qty']:>10g} {p['avg_cost']:>14,.2f} {px:>12,.2f} {val:>14,.2f} "
                  f"{pnl:>+10,.2f} ({pnl_pct:+.1f}%) {val / total * 100:>6.1f}%")
            rows.append({"symbol": s, "qty": p["qty"], "price": px, "value": val, "pnl": pnl})
        cash = pf["cash"][cur]
        print(f"الكاش: {fmt_money(cash, cur)} ({cash / total * 100:.1f}%)")
        change = (total / start - 1) * 100 if start else 0
        print(f"الإجمالي: {fmt_money(total, cur)}  — من البداية {change:+.2f}%"
              f"  — أرباح محققة {fmt_money(pf.get('realized', {}).get(cur, 0), cur)}")
    if a.json:
        print(json.dumps({"cash": pf["cash"], "positions": rows}, ensure_ascii=False, indent=2))


def cmd_apply_plan(a, cfg):
    plan = load_json(path("plans", "latest.json"), None)
    if not plan:
        raise RamiError("ما فيه خطة محفوظة. شغّل budget.py ... --write أول.")
    pf = load_pf()
    print(f"📋 تنفيذ الخطة ({plan['currency']}) على المحفظة الوهمية{'' if a.yes else ' — معاينة'}:")
    done = 0
    for l in plan["lines"]:
        if l["qty"] <= 0:
            continue
        price = l["price"] if a.plan_prices else get_price(l["symbol"])
        ok, issues = execute(pf, l["symbol"], "buy", l["qty"], price, cfg, a.yes)
        mark = "✅" if ok else ("⛔" if risk.verdict(issues) == "BLOCK" else "👀")
        print(f" {mark} شراء {l['qty']:g} × {l['symbol']} @ {price:,.2f}")
        print_issues(issues)
        done += ok
    if a.yes:
        save_json(pf_path(), pf)
        print(f"تم تنفيذ {done} أوامر.")
    else:
        print("للتنفيذ أعد الأمر مع --yes")


def cmd_history(a, cfg):
    pf = load_pf()
    for t in pf["trades"][-a.last:]:
        word = "شراء" if t["side"] == "buy" else "بيع"
        print(f"{t['time']}  {word:<4} {t['qty']:>10g} × {t['symbol']:<8} @ {fmt_money(t['price'], t['currency'])}")
    if not pf["trades"]:
        print("ما فيه صفقات بعد.")


def main():
    ap = argparse.ArgumentParser(description="المحفظة الوهمية لسوبر رامي")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--usd", type=float)
    p.add_argument("--sar", type=float)
    p.add_argument("--reset", action="store_true")
    for name in ("buy", "sell"):
        p = sub.add_parser(name)
        p.add_argument("symbol")
        p.add_argument("qty", nargs="?")
        p.add_argument("--amount", type=float, help="مبلغ بدل الكمية")
        p.add_argument("--price", type=float, help="سعر يدوي")
        p.add_argument("--yes", action="store_true", help="تأكيد التنفيذ")
    p = sub.add_parser("status")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("apply-plan")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--plan-prices", action="store_true", help="استخدم أسعار الخطة بدل الأسعار الحالية")
    p = sub.add_parser("history")
    p.add_argument("--last", type=int, default=30)
    a = ap.parse_args()
    cfg = load_config()

    if a.cmd in ("buy", "sell") and not (a.qty or a.amount):
        ap.error("حدد الكمية أو --amount")
    {"init": cmd_init, "status": cmd_status, "apply-plan": cmd_apply_plan, "history": cmd_history,
     "buy": lambda a, c: cmd_order(a, c, "buy"), "sell": lambda a, c: cmd_order(a, c, "sell")}[a.cmd](a, cfg)


if __name__ == "__main__":
    run(main)
