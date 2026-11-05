"""Interactive Brokers adapter for autotrader.py (via TWS or IB Gateway).

Needs:  pip install ib_async
And TWS / IB Gateway running with API enabled:
  Configure > API > Settings > "Enable ActiveX and Socket Clients",
  port 7497 (TWS paper) or 4002 (Gateway paper).
Real-time SPX data needs the CBOE index market-data subscription in IBKR.
"""
import datetime as dt
import math
import os

from common import RamiError
from strategy import NY


def _num(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) or x <= 0 else float(x)


class IBKRBroker:
    def __init__(self, cfg):
        try:
            import ib_async as ib_mod
        except ImportError:
            try:
                import ib_insync as ib_mod
            except ImportError:
                raise RamiError("ثبّت المكتبة أول:  pip install ib_async")
        self.m = ib_mod
        self.cfg = cfg
        self.ib = ib_mod.IB()
        try:
            self.ib.connect(cfg["host"], cfg["port"], clientId=cfg["client_id"], timeout=15)
        except Exception as e:  # noqa: BLE001 - surface any connection error to the user
            raise RamiError(f"ما قدرت أتصل بـ IBKR على {cfg['host']}:{cfg['port']}. "
                            f"تأكد إن TWS أو IB Gateway شغال والـ API مفعّل. ({e})")
        self.acct = self.ib.managedAccounts()[0]
        self.is_paper = self.acct.startswith("DU")
        self._safety_check()
        self.spx = ib_mod.Index("SPX", "CBOE", "USD")
        self.ib.qualifyContracts(self.spx)
        self.pnl = self.ib.reqPnL(self.acct)

    def _safety_check(self):
        mode = self.cfg["mode"]
        if mode == "paper" and not self.is_paper:
            self.ib.disconnect()
            raise RamiError(f"الحساب {self.acct} حقيقي والإعداد mode=paper. وقفت عشان سلامتك.")
        if mode == "live" and os.environ.get("SUPER_RAMI_LIVE") != "I_UNDERSTAND":
            self.ib.disconnect()
            raise RamiError("التداول الحقيقي يحتاج تكتب بنفسك: export SUPER_RAMI_LIVE=I_UNDERSTAND")
        if mode not in ("paper", "live"):
            raise RamiError("mode لازم يكون paper أو live")

    # ------------------------------------------------------------ data
    def account(self):
        return self.acct

    def now(self):
        return dt.datetime.now(NY)

    def bars(self):
        raw = self.ib.reqHistoricalData(self.spx, endDateTime="", durationStr="1 D",
                                        barSizeSetting="1 min", whatToShow="TRADES",
                                        useRTH=True, formatDate=2)
        now = dt.datetime.now(dt.timezone.utc).timestamp()
        out = [{"t": b.date.timestamp(), "o": b.open, "h": b.high, "l": b.low, "c": b.close} for b in raw]
        return [b for b in out if b["t"] + 60 <= now]          # completed bars only

    def contract(self, right, strike, expiry):
        c = self.m.Option("SPX", expiry, float(strike), right, "SMART",
                          multiplier="100", currency="USD", tradingClass="SPXW")
        if not self.ib.qualifyContracts(c):
            raise RamiError(f"ما لقيت العقد SPXW {expiry} {strike}{right}")
        return c

    def quote(self, c):
        t = self.ib.reqMktData(c, "", True, False)              # snapshot
        for _ in range(10):
            self.ib.sleep(0.5)
            if _num(t.ask):
                break
        return _num(t.bid), _num(t.ask)

    # ------------------------------------------------------------ orders
    def buy_bracket(self, c, qty, limit, tp, sl):
        orders = self.ib.bracketOrder("BUY", qty, limit, tp, sl)
        trades = []
        for o in orders:
            o.account, o.tif = self.acct, "DAY"
            trades.append(self.ib.placeOrder(c, o))
        parent = trades[0]
        for _ in range(60):                                      # wait up to 30s for the entry
            self.ib.sleep(0.5)
            if parent.orderStatus.status == "Filled":
                return parent.order.orderId
        self.ib.cancelOrder(parent.order)                        # children cancel with the parent
        raise RamiError("أمر الدخول ما تنفذ خلال 30 ثانية، ألغيته.")

    def open_positions(self):
        return [p for p in self.ib.positions(self.acct)
                if p.contract.symbol == "SPX" and p.contract.secType == "OPT" and p.position != 0]

    def flatten(self):
        for t in self.ib.openTrades():
            if t.contract.symbol == "SPX" and t.contract.secType == "OPT":
                self.ib.cancelOrder(t.order)
        self.ib.sleep(1)
        for p in self.open_positions():
            c = p.contract
            c.exchange = "SMART"
            side = "SELL" if p.position > 0 else "BUY"
            o = self.m.MarketOrder(side, abs(p.position))
            o.account = self.acct
            self.ib.placeOrder(c, o)
        self.ib.sleep(2)

    def daily_pnl(self):
        v = self.pnl.dailyPnL
        return 0.0 if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)

    def wait(self, seconds):
        self.ib.sleep(seconds)

    def disconnect(self):
        if self.ib.isConnected():
            self.ib.disconnect()
