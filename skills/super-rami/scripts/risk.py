"""Risk rules. Every paper order passes through check_order() first.

Rules come from config.json so they can be changed without editing code.
Returns a list of (level, message) where level is "BLOCK" or "WARN".
"""


def portfolio_value(pf, prices, currency):
    """Cash + market value of positions in one currency."""
    total = pf["cash"].get(currency, 0.0)
    for sym, pos in pf["positions"].items():
        if pos["currency"] == currency:
            total += pos["qty"] * prices.get(sym, pos["avg_cost"])
    return total


def check_order(pf, symbol, side, qty, price, currency, cfg, prices=None):
    prices = dict(prices or {})
    prices[symbol] = price
    issues = []
    value = qty * price
    total = portfolio_value(pf, prices, currency)
    cash = pf["cash"].get(currency, 0.0)
    held = pf["positions"].get(symbol, {}).get("qty", 0.0)

    if qty <= 0:
        return [("BLOCK", "الكمية لازم تكون أكبر من صفر.")]
    if total <= 0:
        return [("BLOCK", f"ما عندك رصيد بعملة {currency}.")]

    if side == "sell":
        if qty > held + 1e-9:
            issues.append(("BLOCK", f"تبي تبيع {qty:g} وعندك {held:g} بس."))
        return issues

    # --- buy rules
    if price < cfg["min_price"]:
        issues.append(("BLOCK", f"السعر {price:.2f} أقل من الحد الأدنى {cfg['min_price']} (أسهم رخيصة عالية المخاطرة)."))
    if value > cash + 1e-9:
        issues.append(("BLOCK", f"قيمة الصفقة {value:,.2f} أكبر من الكاش المتوفر {cash:,.2f}."))
    buffer = total * cfg["cash_buffer_pct"] / 100
    if cash - value < buffer - 1e-9:
        issues.append(("BLOCK", f"الصفقة تنزل الكاش تحت الاحتياطي {cfg['cash_buffer_pct']:g}% ({buffer:,.2f})."))
    trade_pct = value / total * 100
    if trade_pct > cfg["max_trade_pct"] + 1e-9:
        issues.append(("BLOCK", f"الصفقة {trade_pct:.1f}% من المحفظة، والحد {cfg['max_trade_pct']:g}%."))
    pos_pct = (held + qty) * price / total * 100
    if pos_pct > cfg["max_position_pct"] + 1e-9:
        issues.append(("BLOCK", f"السهم بيصير {pos_pct:.1f}% من المحفظة، والحد {cfg['max_position_pct']:g}%."))
    elif pos_pct > cfg["max_position_pct"] * 0.8:
        issues.append(("WARN", f"السهم بيصير {pos_pct:.1f}% من المحفظة، قريب من الحد."))
    return issues


def verdict(issues):
    return "BLOCK" if any(l == "BLOCK" for l, _ in issues) else "PASS"
