"""Super Rami crypto bot for Binance (spot or USD-M futures).

Entries and exits can each be "auto" (bot places orders) or "alert"
(bot tells you; you trade yourself). Testnet is the default.

Examples:
  python3 crypto_bot.py backtest                      # rank the 5 strategies on each coin
  python3 crypto_bot.py backtest --symbols PEPEUSDT --days 60 --apply
  python3 crypto_bot.py scan                          # signals right now
  python3 crypto_bot.py run                           # start the bot
  python3 crypto_bot.py positions
  python3 crypto_bot.py set entry_mode auto           # or alert
  python3 crypto_bot.py set per_trade_usdt 50
  python3 crypto_bot.py stop | resume                 # kill switch
"""
import argparse
import datetime as dt
import json
import os
import time
import urllib.parse
import urllib.request

import binance_client as bn
import crypto_strategies as cs
from common import RamiError, load_config, load_json, path, run, save_config, save_json

DEFAULTS = {
    "market": "spot",               # "spot" or "futures"
    "testnet": True,                # false = real money (also needs SUPER_RAMI_LIVE=I_UNDERSTAND)
    "interval": "15m",
    "entry_mode": "alert",          # "auto" or "alert"
    "exit_mode": "alert",           # "auto" or "alert"
    "per_trade_usdt": None,         # set later; auto trading stays off until set
    "total_budget_usdt": None,
    "meme_size_mult": 0.5,          # memes trade at half size
    "leverage": 1,                  # futures only
    "max_leverage": 5,
    "max_open_positions": 3,
    "max_daily_loss_usdt": None,    # default: 3 x per_trade_usdt x stop size, see daily_loss_limit()
    "strong": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"],
    "memes": ["DOGEUSDT", "PEPEUSDT", "SHIBUSDT", "WIFUSDT", "BONKUSDT"],
    "default_strategy": {"strong": "trend", "memes": "breakout"},
    "assign": {},                   # symbol -> strategy (backtest --apply fills this)
    "params": {},                   # overrides of crypto_strategies.DEFAULT_PARAMS
}
INTERVAL_MS = {"1m": 60e3, "5m": 300e3, "15m": 900e3, "30m": 1800e3, "1h": 3600e3, "4h": 14400e3}


def ccfg():
    cfg = json.loads(json.dumps(DEFAULTS))
    for k, v in load_config().get("crypto", {}).items():
        cfg[k] = v
    return cfg


def params(cfg):
    p = dict(cs.DEFAULT_PARAMS)
    p.update(cfg.get("params") or {})
    return p


def is_meme(cfg, sym):
    return sym in cfg["memes"]


def strategy_for(cfg, sym):
    return cfg["assign"].get(sym) or cfg["default_strategy"]["memes" if is_meme(cfg, sym) else "strong"]


def watchlist(cfg):
    return list(dict.fromkeys(cfg["strong"] + cfg["memes"]))


def daily_loss_limit(cfg):
    if cfg["max_daily_loss_usdt"]:
        return float(cfg["max_daily_loss_usdt"])
    return float(cfg["per_trade_usdt"] or 0) * 0.15 * 3 if cfg["per_trade_usdt"] else 0.0


def kill_path():
    return path("STOP_CRYPTO")


# ---------------------------------------------------------------- notifications

def notify(msg):
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    with open(path("crypto", "log.txt"), "a", encoding="utf-8") as f:
        f.write(line + "\n")
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        try:
            data = urllib.parse.urlencode({"chat_id": chat, "text": f"🦸 سوبر رامي\n{msg}"}).encode()
            urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=10)
        except Exception as e:  # noqa: BLE001 - never let a failed alert stop the bot
            print(f"تعذر إرسال تيليجرام: {e}")


# ---------------------------------------------------------------- safety

def check_config(cfg):
    if cfg["market"] not in ("spot", "futures"):
        raise RamiError("market لازم يكون spot أو futures")
    for k in ("entry_mode", "exit_mode"):
        if cfg[k] not in ("auto", "alert"):
            raise RamiError(f"{k} لازم يكون auto أو alert")
    if cfg["leverage"] > cfg["max_leverage"]:
        raise RamiError(f"الرافعة {cfg['leverage']} أكبر من الحد {cfg['max_leverage']}.")
    if not cfg["testnet"] and os.environ.get("SUPER_RAMI_LIVE") != "I_UNDERSTAND":
        raise RamiError("testnet=false يعني فلوس حقيقية. لازم تكتب بنفسك: export SUPER_RAMI_LIVE=I_UNDERSTAND")


def entry_gate(cfg, state, sym, side, today):
    """Reasons not to auto-enter. Empty = OK."""
    r = []
    if os.path.exists(kill_path()):
        r.append("مفتاح الإيقاف مفعّل.")
    if not cfg["per_trade_usdt"]:
        r.append("ما حددت الميزانية (per_trade_usdt).")
    if side == -1 and cfg["market"] == "spot":
        r.append("بيع على المكشوف يحتاج عقود آجلة.")
    auto_open = [p for p in state["positions"].values() if p.get("auto")]
    if len(auto_open) >= cfg["max_open_positions"]:
        r.append(f"فيه {len(auto_open)} صفقات مفتوحة (الحد {cfg['max_open_positions']}).")
    if cfg["total_budget_usdt"] and cfg["per_trade_usdt"]:
        used = sum(p.get("usdt", 0) for p in auto_open)
        if used + size_usdt(cfg, sym) > float(cfg["total_budget_usdt"]):
            r.append("الصفقة تتعدى الميزانية الكلية.")
    lim = daily_loss_limit(cfg)
    if lim and state["daily"].get(today, 0) <= -lim:
        r.append(f"وصلنا حد الخسارة اليومي {lim:.0f}$.")
    return r


def size_usdt(cfg, sym):
    s = float(cfg["per_trade_usdt"] or 0)
    return s * cfg["meme_size_mult"] if is_meme(cfg, sym) else s


# ---------------------------------------------------------------- live bot

class Bot:
    def __init__(self, cfg, client=None, fetch=None, now=None):
        self.cfg, self.p = cfg, params(cfg)
        self.client = client or bn.Binance(cfg["market"], cfg["testnet"])
        self.fetch = fetch or (lambda s: bn.klines(s, cfg["interval"], 400, cfg["market"]))
        self.now = now or (lambda: time.time() * 1000)
        self.state_path = path("crypto", f"state_{cfg['market']}.json")
        self.state = load_json(self.state_path, None) or {"positions": {}, "daily": {}, "last_bar": {}}

    def save(self):
        save_json(self.state_path, self.state)

    def today(self):
        return dt.datetime.fromtimestamp(self.now() / 1000, dt.timezone.utc).date().isoformat()

    def step(self):
        """One pass over the watchlist. Call every ~20 seconds."""
        if os.path.exists(kill_path()):
            self.flatten("مفتاح الإيقاف")
            return False
        for sym in watchlist(self.cfg):
            try:
                self.check_symbol(sym)
            except RamiError as e:
                notify(f"⚠️ {sym}: {e}")
        self.save()
        return True

    def check_symbol(self, sym):
        pos = self.state["positions"].get(sym)
        last = self.state["last_bar"].get(sym, 0)
        step_ms = INTERVAL_MS[self.cfg["interval"]]
        if not pos and self.now() < last + 2 * step_ms:
            return                                         # no new closed candle yet
        bars = [b for b in self.fetch(sym) if b["close_t"] < self.now()]
        if len(bars) < 250:
            return
        new_bar = bars[-1]["t"] != last
        if pos:                                             # stop check on the live candle too
            px = cs.stop_hit(pos, bars[-1]) if new_bar else None
            if px is None and not new_bar:
                live = self.client.price(sym)
                px = live if (live - pos["stop"]) * pos["side"] <= 0 else None
            if px is not None:
                self.exit(sym, pos, "وقف الخسارة / الوقف المتحرك", px)
                pos = None
        if not new_bar:
            return
        self.state["last_bar"][sym] = bars[-1]["t"]
        ind = cs.Ind(bars, self.p)
        i = len(bars) - 1
        if pos:
            old = pos["stop"]
            why = cs.manage(pos, bars, ind, i, self.p)
            if why:
                self.exit(sym, pos, why, bars[-1]["c"])
            elif pos["stop"] != old and pos.get("auto"):
                self.client.set_stop(sym, pos["side"], pos["stop"])
            return
        name = strategy_for(self.cfg, sym)
        side = cs.signal(name, bars, ind, i, self.p)
        if side:
            self.enter(sym, name, side, bars, ind, i)

    def enter(self, sym, name, side, bars, ind, i):
        if side == -1 and self.cfg["market"] == "spot":
            return                                          # spot can't short: stay quiet
        price = bars[-1]["c"]
        pos = cs.new_position(sym, name, side, price, ind.atr[i], int(self.now()), self.p)
        word = "شراء 🟢" if side == 1 else "بيع على المكشوف 🔴"
        tag = "ميمز" if is_meme(self.cfg, sym) else "قوية"
        head = f"{word} {sym} ({tag}) — {cs.NAMES_AR[name]} — السعر {price:g} — الوقف {pos['stop']:.6g}"
        if self.cfg["entry_mode"] == "auto":
            reasons = entry_gate(self.cfg, self.state, sym, side, self.today())
            if reasons:
                notify(f"⏭️ إشارة {head}\nما نفذت تلقائياً: " + " ".join(reasons))
                pos["auto"] = False
            else:
                usdt = size_usdt(self.cfg, sym)
                qty, fill = self.client.open(sym, side, usdt, self.cfg["leverage"])
                pos.update(auto=True, qty=qty, entry=fill, usdt=usdt,
                           stop=fill - side * self.p["stop_atr"] * ind.atr[i], best=fill)
                self.client.set_stop(sym, side, pos["stop"])
                notify(f"✅ نفذت {head}\nالكمية {qty:g} بـ {usdt:.2f}$")
        else:
            pos["auto"] = False
            notify(f"🔔 تنبيه {head}")
        self.state["positions"][sym] = pos

    def exit(self, sym, pos, why, price):
        pnl_pct = (price / pos["entry"] - 1) * pos["side"] * 100
        held = (self.now() - pos["opened"]) / 3.6e6
        msg = f"{sym} — {why} — دخول {pos['entry']:g} ← خروج {price:g} ({pnl_pct:+.2f}%) بعد {held:.1f} ساعة"
        if pos.get("auto") and self.cfg["exit_mode"] == "auto":
            fill = self.client.close(sym, pos["side"], pos["qty"])
            pnl = (fill / pos["entry"] - 1) * pos["side"] * pos["usdt"] * self.cfg.get("leverage", 1)
            day = self.today()
            self.state["daily"][day] = self.state["daily"].get(day, 0) + pnl
            notify(f"💰 بعت تلقائياً {msg} — {pnl:+.2f}$")
        else:
            notify(f"🔔 بيع الحين: {msg}")
        self.state["positions"].pop(sym, None)

    def flatten(self, why):
        for sym, pos in list(self.state["positions"].items()):
            try:
                self.exit(sym, pos, why, self.client.price(sym))
            except RamiError as e:
                notify(f"⚠️ ما قدرت أقفل {sym}: {e}")
        self.save()


# ---------------------------------------------------------------- CLI

def cmd_backtest(a, cfg):
    p = params(cfg)
    syms = [s.upper() for s in a.symbols] if a.symbols else watchlist(cfg)
    per_day = 86400e3 / INTERVAL_MS[cfg["interval"]]
    fee = 0.1 if cfg["market"] == "spot" else 0.05
    best = {}
    for sym in syms:
        if a.csv:
            bars = load_csv(a.csv)
        else:
            bars = bn.history(sym, cfg["interval"], int(a.days * per_day) + 250, cfg["market"])
        results = [cs.backtest(bars, n, p, fee, cfg["market"] == "futures", sym) for n in cs.STRATEGIES]
        results.sort(key=cs.score, reverse=True)
        tag = "ميمز" if is_meme(cfg, sym) else "قوية"
        print(f"\n📊 {sym} ({tag}) — {len(bars):,} شمعة {cfg['interval']}")
        print(f"{'الاستراتيجية':<22}{'صفقات':>6}{'نجاح':>8}{'العائد':>10}{'أقصى تراجع':>12}{'PF':>6}{'متوسط ساعات':>12}{'بات ليلة':>9}")
        for r in results:
            print(f"{cs.NAMES_AR[r['strategy']]:<22}{r['trades']:>6}{r['win_rate']:>7.0f}%{r['total_return']:>+9.1f}%"
                  f"{r['max_dd']:>11.1f}%{r['profit_factor']:>6.2f}{r['avg_hours']:>12.1f}{r['overnight']:>9}")
        if cs.score(results[0]) > -1e9 and results[0]["total_return"] > 0:
            best[sym] = results[0]["strategy"]
            print(f"الأفضل: {cs.NAMES_AR[best[sym]]}")
        else:
            print("ولا استراتيجية ربحت على هذي الفترة.")
    if a.apply and best:
        full = load_config()
        c = full.setdefault("crypto", {})
        c.setdefault("assign", {}).update(best)
        save_config(full)
        print("\n✅ حفظت أفضل استراتيجية لكل عملة.")
    print("\nالعائد بعد الرسوم، وكل صفقة بكامل المبلغ. نتائج الماضي ما تضمن المستقبل.")


def load_csv(p):
    import csv
    out = []
    with open(p, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            r = {k.strip().lower(): v for k, v in r.items()}
            t = int(float(r.get("time") or r.get("open_time")))
            t = t * 1000 if t < 1e12 else t
            out.append({"t": t, "o": float(r["open"]), "h": float(r["high"]), "l": float(r["low"]),
                        "c": float(r["close"]), "v": float(r.get("volume") or 0)})
    return out


def cmd_scan(a, cfg):
    p = params(cfg)
    now = time.time() * 1000
    for sym in watchlist(cfg):
        try:
            bars = [b for b in bn.klines(sym, cfg["interval"], 400, cfg["market"]) if b["close_t"] < now]
        except RamiError as e:
            print(f"{sym}: {e}")
            continue
        ind = cs.Ind(bars, p)
        i = len(bars) - 1
        sigs = {n: cs.signal(n, bars, ind, i, p) for n in cs.STRATEGIES}
        hits = [f"{cs.NAMES_AR[n]} {'🟢' if s == 1 else '🔴'}" for n, s in sigs.items() if s]
        mine = strategy_for(cfg, sym)
        print(f"{sym:<10} {bars[-1]['c']:<14g} الاستراتيجية: {cs.NAMES_AR[mine]:<20} "
              f"{'إشارات: ' + '، '.join(hits) if hits else 'ما فيه إشارة'}")


def cmd_run(a, cfg):
    check_config(cfg)
    env = "تجريبي (testnet)" if cfg["testnet"] else "حقيقي ⚠️"
    notify(f"🚀 بدأ البوت — {cfg['market']} — {env} — الدخول: {cfg['entry_mode']} — الخروج: {cfg['exit_mode']}"
           f" — الفريم {cfg['interval']} — {len(watchlist(cfg))} عملات")
    if cfg["entry_mode"] == "auto" and not cfg["per_trade_usdt"]:
        notify("ℹ️ ما حددت الميزانية، فبرسل تنبيهات بس لين تحددها: crypto_bot.py set per_trade_usdt 50")
    bot = Bot(cfg)
    try:
        while bot.step():
            time.sleep(20)
    except KeyboardInterrupt:
        notify("⏹️ أوقفته يدوياً. الصفقات المفتوحة باقية.")
    finally:
        bot.save()


def cmd_set(a, cfg):
    key, raw = a.key, a.value
    if key not in DEFAULTS:
        raise RamiError(f"ما فيه إعداد اسمه {key}. الموجود: {', '.join(DEFAULTS)}")
    try:
        val = json.loads(raw)
    except json.JSONDecodeError:
        val = raw
    if key in ("strong", "memes") and isinstance(val, str):
        val = [s.strip().upper() for s in val.split(",") if s.strip()]
    full = load_config()
    full.setdefault("crypto", {})[key] = val
    test = ccfg()
    test[key] = val
    if key in ("market", "entry_mode", "exit_mode", "leverage", "max_leverage"):
        try:
            check_config(dict(test, testnet=True))
        except RamiError as e:
            raise RamiError(str(e))
    save_config(full)
    print(f"✅ {key} = {json.dumps(val, ensure_ascii=False)}")


def main():
    ap = argparse.ArgumentParser(description="بوت العملات الرقمية لسوبر رامي (باينانس)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("backtest")
    p.add_argument("--symbols", nargs="*")
    p.add_argument("--days", type=float, default=30)
    p.add_argument("--csv", help="بيانات عملة وحدة: time,open,high,low,close,volume")
    p.add_argument("--apply", action="store_true", help="احفظ الأفضل لكل عملة")
    sub.add_parser("scan")
    sub.add_parser("run")
    sub.add_parser("positions")
    sub.add_parser("rules")
    p = sub.add_parser("set")
    p.add_argument("key")
    p.add_argument("value")
    p = sub.add_parser("forget", help="احذف صفقة من المتابعة (إذا قفلتها بنفسك)")
    p.add_argument("symbol")
    sub.add_parser("stop")
    sub.add_parser("resume")
    a = ap.parse_args()
    cfg = ccfg()

    if a.cmd == "backtest":
        cmd_backtest(a, cfg)
    elif a.cmd == "scan":
        cmd_scan(a, cfg)
    elif a.cmd == "run":
        cmd_run(a, cfg)
    elif a.cmd == "set":
        cmd_set(a, cfg)
    elif a.cmd == "rules":
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
    elif a.cmd == "positions":
        st = load_json(path("crypto", f"state_{cfg['market']}.json"), {"positions": {}, "daily": {}})
        if not st["positions"]:
            print("ما فيه صفقات مفتوحة.")
        for s, p in st["positions"].items():
            hrs = (time.time() * 1000 - p["opened"]) / 3.6e6
            print(f"{s:<10} {'شراء' if p['side'] == 1 else 'مكشوف'}  {cs.NAMES_AR[p['strategy']]}  دخول {p['entry']:g}  "
                  f"وقف {p['stop']:.6g}  {hrs:.1f} ساعة  {'تلقائي' if p.get('auto') else 'تنبيه'}")
        if st.get("daily"):
            print("الربح/الخسارة اليومي:", json.dumps(st["daily"], ensure_ascii=False))
    elif a.cmd == "forget":
        sp = path("crypto", f"state_{cfg['market']}.json")
        st = load_json(sp, {"positions": {}})
        st["positions"].pop(a.symbol.upper(), None)
        save_json(sp, st)
        print(f"تم حذف {a.symbol.upper()} من المتابعة.")
    elif a.cmd == "stop":
        open(kill_path(), "w").close()
        print("⛔ مفتاح الإيقاف مفعّل. البوت بيقفل الصفقات ويوقف.")
    elif a.cmd == "resume":
        if os.path.exists(kill_path()):
            os.remove(kill_path())
        print("✅ تم إلغاء مفتاح الإيقاف.")


if __name__ == "__main__":
    run(main)
