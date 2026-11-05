"""Automatic trader for the SPX Smart Rally strategy (0DTE SPXW options).

Rami starts it himself. It watches 1-minute SPX bars, and when strategy.py
fires a signal it buys an OTM CALL/PUT with a stop-loss and take-profit
(bracket), subject to hard safety limits.

Modes:
  sim    replay a CSV of 1-minute bars with estimated option prices (no broker)
  ibkr   Interactive Brokers through TWS / IB Gateway (paper account by default)

Examples:
  python3 autotrader.py sim --csv spx_1m.csv
  python3 autotrader.py ibkr                 # paper account (port 7497)
  python3 autotrader.py status
  python3 autotrader.py stop                 # kill switch: flatten and stop

Safety (from config "spx_auto"):
  - paper account required unless mode is "live" AND env SUPER_RAMI_LIVE=I_UNDERSTAND
  - kill switch file ~/super-rami-data/STOP
  - trading window, last entry time, forced flatten before the close
  - max trades per day, max daily loss, max premium per trade, max spread
  - one position at a time
"""
import argparse
import datetime as dt
import json
import os
import sys

import strategy
from common import RamiError, load_config, load_json, path, run, save_json
from options_math import bs_price, round_tick

DEFAULTS = {
    "mode": "paper",              # "paper" or "live"
    "host": "127.0.0.1",
    "port": 7497,                 # TWS paper 7497, IB Gateway paper 4002
    "client_id": 17,
    "account_usd": 1000,          # Rami's trading capital for SPX
    "qty": 1,                     # contracts per trade
    "max_premium_pct": 20,        # max cost of one trade, % of account (1000 -> 200$)
    "max_daily_loss_pct": 10,     # stop for the day after losing this % (1000 -> 100$)
    "max_premium_usd": None,      # optional fixed override of max_premium_pct
    "max_daily_loss_usd": None,   # optional fixed override of max_daily_loss_pct
    "stop_loss_pct": 50,          # exit if premium falls this % from entry
    "take_profit_pct": 100,       # exit if premium rises this % from entry
    "max_trades_per_day": 3,
    "max_spread_pct": 15,         # skip if (ask-bid)/ask is wider than this
    "start": "09:45",             # New York time
    "last_entry": "15:30",
    "flatten": "15:50",
    "session_end": "16:00",
}


def resolve(cfg):
    """Turn % of account into dollar limits (fixed *_usd values win if set)."""
    cfg = dict(cfg)
    acct = float(cfg["account_usd"])
    if not cfg.get("max_premium_usd"):
        cfg["max_premium_usd"] = acct * cfg["max_premium_pct"] / 100
    if not cfg.get("max_daily_loss_usd"):
        cfg["max_daily_loss_usd"] = acct * cfg["max_daily_loss_pct"] / 100
    return cfg


def auto_cfg():
    cfg = dict(DEFAULTS)
    cfg.update(load_config().get("spx_auto", {}))
    return resolve(cfg)


def hhmm(s):
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def kill_switch_path():
    return path("STOP")


# ---------------------------------------------------------------- safety gate

def gate(state, cfg, now, ask, bid, open_positions):
    """Return a list of reasons NOT to enter. Empty list = allowed."""
    reasons = []
    if os.path.exists(kill_switch_path()):
        reasons.append("مفتاح الإيقاف مفعّل (ملف STOP).")
    t = now.time()
    if t < hhmm(cfg["start"]) or t > hhmm(cfg["last_entry"]):
        reasons.append(f"خارج وقت الدخول ({cfg['start']}–{cfg['last_entry']} نيويورك).")
    if state["trades"] >= cfg["max_trades_per_day"]:
        reasons.append(f"وصلنا الحد اليومي {cfg['max_trades_per_day']} صفقات.")
    if state["pnl"] <= -abs(cfg["max_daily_loss_usd"]):
        reasons.append(f"وصلنا حد الخسارة اليومي {cfg['max_daily_loss_usd']:,.0f}$.")
    if open_positions:
        reasons.append("فيه صفقة مفتوحة، صفقة وحدة بس في كل مرة.")
    if not ask or ask <= 0:
        reasons.append("ما فيه سعر طلب (ask) للعقد.")
    else:
        cost = ask * 100 * cfg["qty"]
        if cost > cfg["max_premium_usd"]:
            reasons.append(f"تكلفة الصفقة {cost:,.0f}$ أكبر من الحد {cfg['max_premium_usd']:,.0f}$.")
        if bid is not None and (ask - bid) / ask * 100 > cfg["max_spread_pct"]:
            reasons.append(f"الفرق بين bid و ask واسع ({bid:.2f}/{ask:.2f}).")
    return reasons


def bracket_prices(entry, cfg):
    tp = round_tick(entry * (1 + cfg["take_profit_pct"] / 100), up=True)
    sl = round_tick(entry * (1 - cfg["stop_loss_pct"] / 100))
    return tp, sl


# ---------------------------------------------------------------- simulator

class SimBroker:
    """Replays 1-minute bars; option prices from Black-Scholes (estimate only)."""

    is_paper = True
    spread = 0.10

    def __init__(self, bars, vol=0.15):
        self.all = bars
        self.ptr = 0
        self.vol = vol
        self.pos = None           # {contract, qty, entry, tp, sl}
        self.realized = 0.0
        self.closed = []

    def account(self):
        return "SIM"

    def now(self):
        return strategy.ny_time(self.all[self.ptr]["t"]) + dt.timedelta(minutes=1)

    def done(self):
        return self.ptr >= len(self.all) - 1

    def bars(self):
        day = strategy.ny_time(self.all[self.ptr]["t"]).date()
        return [b for b in self.all[:self.ptr + 1] if strategy.ny_time(b["t"]).date() == day]

    def _minutes_left(self, ts):
        t = strategy.ny_time(ts)
        close = t.replace(hour=16, minute=0, second=0)
        return max((close - t).total_seconds() / 60, 0)

    def _value(self, c, spot, ts):
        return bs_price(spot, c["strike"], self._minutes_left(ts), c["right"], self.vol)

    def contract(self, right, strike, expiry):
        return {"right": right, "strike": strike, "expiry": expiry}

    def quote(self, c):
        b = self.all[self.ptr]
        mid = self._value(c, b["c"], b["t"])
        return max(mid - self.spread / 2, 0.0), mid + self.spread / 2

    def buy_bracket(self, c, qty, limit, tp, sl):
        self.pos = {"contract": c, "qty": qty, "entry": limit, "tp": tp, "sl": sl,
                    "opened": self.now().strftime("%H:%M")}
        return "sim-1"

    def open_positions(self):
        return [self.pos] if self.pos else []

    def _close(self, price, why):
        p = self.pos
        pnl = (price - p["entry"]) * 100 * p["qty"]
        self.realized += pnl
        self.closed.append(dict(p, exit=round(price, 2), why=why, pnl=round(pnl, 2),
                                closed=self.now().strftime("%H:%M"), day=self.now().date()))
        self.pos = None

    def flatten(self):
        if self.pos:
            bid, _ = self.quote(self.pos["contract"])
            self._close(bid, "إغلاق إجباري")

    def daily_pnl(self):
        today = self.now().date()
        return sum(t["pnl"] for t in self.closed if t["day"] == today)

    def wait(self, _seconds):
        prev = self.all[self.ptr]
        self.ptr += 1
        if not self.pos:
            return
        if strategy.ny_time(self.all[self.ptr]["t"]).date() != strategy.ny_time(prev["t"]).date():
            c = self.pos["contract"]       # data ended for the day: 0DTE settles at intrinsic
            intrinsic = max(prev["c"] - c["strike"], 0) if c["right"] == "C" else max(c["strike"] - prev["c"], 0)
            self.ptr -= 1
            self._close(intrinsic, "انتهاء العقد")
            self.ptr += 1
            return
        b, c = self.all[self.ptr], self.pos["contract"]
        hi = self._value(c, b["h"] if c["right"] == "C" else b["l"], b["t"])
        lo = self._value(c, b["l"] if c["right"] == "C" else b["h"], b["t"])
        if lo <= self.pos["sl"]:                 # assume the stop hits first
            self._close(self.pos["sl"], "وقف خسارة")
        elif hi >= self.pos["tp"]:
            self._close(self.pos["tp"], "جني ربح")

    def disconnect(self):
        pass


# ---------------------------------------------------------------- main loop

def log(msg, now=None):
    stamp = (now or dt.datetime.now()).strftime("%Y-%m-%d %H:%M")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    with open(path("auto", "log.txt"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def trade_loop(broker, cfg, scfg, persist=True):
    state = None
    last_bar = None
    flattened_day = None
    while True:
        now = broker.now()
        day = now.date().isoformat()
        if state is None or state["day"] != day:
            state_file = path("auto", f"state_{day}.json")
            state = load_json(state_file, None) if persist else None
            state = state or {"day": day, "trades": 0, "pnl": 0.0, "acted": []}
        state["pnl"] = broker.daily_pnl()

        if os.path.exists(kill_switch_path()):
            broker.flatten()
            log("⛔ مفتاح الإيقاف مفعّل. قفلت الصفقات ووقفت.", now)
            break
        if now.time() >= hhmm(cfg["flatten"]) and flattened_day != day:
            if broker.open_positions():
                broker.flatten()
                log("🔒 قفلت الصفقات المفتوحة قبل الإغلاق.", now)
            flattened_day = day

        bars = broker.bars()
        if bars and bars[-1]["t"] != last_bar:
            last_bar = bars[-1]["t"]
            sigs = strategy.find_signals(bars, scfg)
            sig = sigs[-1] if sigs and sigs[-1]["i"] == len(bars) - 1 else None
            if sig and sig["time"] not in state["acted"]:
                state["acted"].append(sig["time"])
                handle_signal(broker, cfg, state, sig, now)
        if persist:
            save_json(path("auto", f"state_{day}.json"), state)

        if getattr(broker, "done", lambda: False)():
            broker.flatten()
            break
        if now.time() >= hhmm(cfg["session_end"]) and not isinstance(broker, SimBroker):
            log("🏁 انتهت الجلسة.", now)
            break
        broker.wait(5)
    return state


def handle_signal(broker, cfg, state, sig, now):
    right = "C" if sig["side"] == "CALL" else "P"
    head = f"إشارة {sig['side']} عند {sig['price']:,.2f} → SPXW {sig['strike']}{right}"
    try:
        c = broker.contract(right, sig["strike"], now.strftime("%Y%m%d"))
        bid, ask = broker.quote(c)
    except RamiError as e:
        log(f"⚠️ {head} — {e}", now)
        return
    reasons = gate(state, cfg, now, ask, bid, broker.open_positions())
    if reasons:
        log(f"⏭️ {head} — تجاهلتها: " + " ".join(reasons), now)
        return
    limit = round_tick(ask, up=True)
    tp, sl = bracket_prices(limit, cfg)
    state["trades"] += 1             # count the attempt so a failing order can't loop
    try:
        broker.buy_bracket(c, cfg["qty"], limit, tp, sl)
    except RamiError as e:
        log(f"⚠️ {head} — {e}", now)
        return
    log(f"✅ {head} — شراء {cfg['qty']} عقد بـ {limit:.2f} (هدف {tp:.2f} / وقف {sl:.2f})", now)


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description="التنفيذ التلقائي لاستراتيجية SPX")
    ap.add_argument("cmd", choices=["sim", "ibkr", "status", "stop", "resume", "rules"])
    ap.add_argument("--csv", help="sim: ملف دقيقة time,open,high,low,close")
    ap.add_argument("--vol", type=float, default=0.15, help="sim: التذبذب المفترض لتسعير العقود")
    a = ap.parse_args()
    cfg, scfg = auto_cfg(), load_config()["spx_strategy"]

    if a.cmd == "rules":
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
    elif a.cmd == "stop":
        open(kill_switch_path(), "w").close()
        print("⛔ تم تفعيل مفتاح الإيقاف. البوت بيقفل الصفقات ويوقف خلال ثواني.")
    elif a.cmd == "resume":
        if os.path.exists(kill_switch_path()):
            os.remove(kill_switch_path())
        print("✅ تم إلغاء مفتاح الإيقاف.")
    elif a.cmd == "status":
        day = dt.datetime.now(strategy.NY).date().isoformat()
        st = load_json(path("auto", f"state_{day}.json"), None)
        print(f"مفتاح الإيقاف: {'مفعّل ⛔' if os.path.exists(kill_switch_path()) else 'غير مفعّل'}")
        print(json.dumps(st, ensure_ascii=False, indent=2) if st else "ما فيه نشاط اليوم.")
        lp = path("auto", "log.txt")
        if os.path.exists(lp):
            print("\nآخر السجل:")
            with open(lp, encoding="utf-8") as f:
                print("".join(f.readlines()[-10:]), end="")
    elif a.cmd == "sim":
        if not a.csv:
            raise RamiError("وضع المحاكاة يحتاج --csv")
        b = SimBroker(strategy.bars_from_csv(a.csv), a.vol)
        trade_loop(b, cfg, scfg, persist=False)
        print("\n— نتائج المحاكاة (أسعار العقود تقديرية) —")
        for t in b.closed:
            c = t["contract"]
            print(f"  {t['day']} {t['opened']}→{t['closed']}  SPXW {c['strike']}{c['right']}  "
                  f"{t['entry']:.2f} → {t['exit']:.2f}  {t['why']}  {t['pnl']:+,.0f}$")
        print(f"الإجمالي: {b.realized:+,.0f}$ على {len(b.closed)} صفقات")
    else:
        from broker_ibkr import IBKRBroker
        broker = IBKRBroker(cfg)
        try:
            log(f"🚀 بدأ البوت — الحساب {broker.account()} — {'تجريبي' if broker.is_paper else 'حقيقي ⚠️'}")
            trade_loop(broker, cfg, scfg)
        except KeyboardInterrupt:
            log("⏹️ أوقفته يدوياً. الصفقات المفتوحة باقية بأوامر الوقف والهدف حقتها.")
        finally:
            broker.disconnect()


if __name__ == "__main__":
    run(main)
