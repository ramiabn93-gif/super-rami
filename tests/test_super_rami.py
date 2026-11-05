"""Offline tests: python3 -m unittest discover -s tests -v"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "skills", "super-rami", "scripts"))
sys.path.insert(0, HERE)
os.environ["SUPER_RAMI_HOME"] = tempfile.mkdtemp()

import analyze, budget, common, copycat, paper, risk, strategy, synth  # noqa: E402


class TestCommon(unittest.TestCase):
    def test_symbols(self):
        self.assertEqual(common.normalize_symbol("2222"), "2222.SR")
        self.assertEqual(common.normalize_symbol(" aapl "), "AAPL")
        self.assertEqual(common.currency_of("1120"), "SAR")
        self.assertFalse(common.allows_fractional("2222.SR"))

    def test_parse_chart(self):
        raw = {"chart": {"result": [{"meta": {"currency": "USD", "regularMarketPrice": 11},
                                      "timestamp": [1, 2, 3],
                                      "indicators": {"quote": [{"close": [10, None, 11], "volume": [5, 5, 5],
                                                                "high": [10.5, None, 11.2], "low": [9.5, None, 10.8],
                                                                "open": [10, None, 10.9]}]}}]}}
        rows, meta = common.parse_chart(raw, "X")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][3], 11.2)
        self.assertEqual(meta["price"], 11)


class TestCopycat(unittest.TestCase):
    def test_parse_and_weights(self):
        with open(os.path.join(HERE, "fixtures", "infotable.xml"), "rb") as f:
            h = copycat.parse_info_table(f.read())
        self.assertEqual([x["name"] for x in h], ["APPLE INC", "COCA COLA CO"])  # option skipped
        self.assertEqual(h[0]["shares"], 400000)
        self.assertAlmostEqual(h[0]["weight"], 80.0)
        copycat.attach_tickers(h, {"0": {"title": "Apple Inc.", "ticker": "AAPL"},
                                   "1": {"title": "COCA COLA CO", "ticker": "KO"}})
        self.assertEqual([x["ticker"] for x in h], ["AAPL", "KO"])

    def test_compare(self):
        new = [{"cusip": "a", "name": "A", "shares": 10}, {"cusip": "b", "name": "B", "shares": 5}]
        old = [{"cusip": "a", "name": "A", "shares": 20}, {"cusip": "c", "name": "C", "shares": 1}]
        acts = {c[1]: c[0] for c in copycat.compare(new, old)}
        self.assertEqual(acts, {"A": "قلّل", "B": "جديد", "C": "باع كامل"})


class TestAnalyze(unittest.TestCase):
    def test_indicators(self):
        up = [float(i) for i in range(1, 260)]
        self.assertEqual(analyze.rsi(up), 100.0)
        self.assertAlmostEqual(analyze.sma(up, 3), 258.0)
        rows = [(i, c, 0) for i, c in enumerate(up)]
        m = analyze.analyze(rows, {"symbol": "X", "name": "X", "currency": "USD", "price": 259})
        self.assertIn("صاعد", m["notes"][0])


class TestBudgetRisk(unittest.TestCase):
    def setUp(self):
        self.cfg = common.load_config()

    def test_plan_caps_and_whole_shares(self):
        plan = budget.make_plan(10000, {"AAPL": 80, "MSFT": 20}, {"AAPL": 100.0, "MSFT": 50.0}, self.cfg)
        aapl = next(l for l in plan["lines"] if l["symbol"] == "AAPL")
        self.assertLessEqual(aapl["cost"], 10000 * 0.20 + 1e-6)          # capped at 20%
        self.assertIn("AAPL", plan["capped"])
        sar = budget.make_plan(10000, {"2222.SR": 100}, {"2222.SR": 27.3}, self.cfg)
        self.assertEqual(sar["lines"][0]["qty"], int(sar["lines"][0]["qty"]))

    def test_mixed_markets_rejected(self):
        with self.assertRaises(common.RamiError):
            budget.make_plan(1000, {"AAPL": 50, "2222.SR": 50}, {"AAPL": 1, "2222.SR": 1}, self.cfg)

    def test_risk_rules(self):
        pf = {"cash": {"USD": 10000.0}, "positions": {}}
        self.assertEqual(risk.verdict(risk.check_order(pf, "AAPL", "buy", 10, 100, "USD", self.cfg)), "PASS")
        self.assertEqual(risk.verdict(risk.check_order(pf, "AAPL", "buy", 30, 100, "USD", self.cfg)), "BLOCK")
        self.assertEqual(risk.verdict(risk.check_order(pf, "PENY", "buy", 10, 0.5, "USD", self.cfg)), "BLOCK")
        self.assertEqual(risk.verdict(risk.check_order(pf, "AAPL", "sell", 1, 100, "USD", self.cfg)), "BLOCK")


class TestPaper(unittest.TestCase):
    def test_buy_sell_cycle(self):
        cfg = common.load_config()
        pf = {"cash": {"USD": 10000.0, "SAR": 0.0}, "positions": {}, "trades": [], "realized": {}}
        ok, _ = paper.execute(pf, "AAPL", "buy", 10, 100.0, cfg, confirm=False)
        self.assertFalse(ok)                                   # preview changes nothing
        self.assertEqual(pf["cash"]["USD"], 10000.0)
        ok, _ = paper.execute(pf, "AAPL", "buy", 10, 100.0, cfg, confirm=True)
        self.assertTrue(ok)
        self.assertEqual(pf["cash"]["USD"], 9000.0)
        ok, _ = paper.execute(pf, "AAPL", "sell", 10, 110.0, cfg, confirm=True)
        self.assertTrue(ok)
        self.assertNotIn("AAPL", pf["positions"])
        self.assertAlmostEqual(pf["realized"]["USD"], 100.0)


class TestStrategy(unittest.TestCase):
    def setUp(self):
        self.s = common.load_config()["spx_strategy"]

    def test_call_signal(self):
        sig = strategy.find_signals(synth.make(), self.s)
        self.assertEqual([x["side"] for x in sig], ["CALL"])
        self.assertEqual(sig[0]["strike"] % 5, 0)
        self.assertGreater(sig[0]["strike"], sig[0]["price"])   # OTM call

    def test_put_signal_mirror(self):
        sig = strategy.find_signals(synth.mirror(synth.make()), self.s)
        self.assertEqual([x["side"] for x in sig], ["PUT"])
        self.assertLess(sig[0]["strike"], sig[0]["price"])      # OTM put

    def test_no_signal_without_rally(self):
        s = dict(self.s, min_rally_pts=50)
        self.assertEqual(strategy.find_signals(synth.make(), s), [])

    def test_no_look_ahead(self):
        bars = synth.make()
        full = strategy.find_signals(bars, self.s)
        cut = strategy.find_signals(bars[:full[0]["i"] + 1], self.s)
        self.assertEqual(cut[-1]["time"], full[0]["time"])

    def test_zones_reset_each_day(self):
        bars = synth.make(4)[:110] + synth.make(5)[110:]      # setup split across two days
        self.assertEqual(strategy.find_signals(bars, self.s), [])

    def test_backtest(self):
        bars = synth.make()
        bt = strategy.backtest(bars, strategy.find_signals(bars, self.s), self.s)
        self.assertEqual(bt["trades"], 1)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- auto trading
import datetime as dt  # noqa: E402
import types  # noqa: E402

import autotrader  # noqa: E402
import options_math  # noqa: E402


class TestOptionsMath(unittest.TestCase):
    def test_bs(self):
        c = options_math.bs_price(5000, 5000, 120, "C")
        p = options_math.bs_price(5000, 5000, 120, "P")
        self.assertGreater(c, 0)
        self.assertAlmostEqual(c, p, delta=1.0)               # ATM call ~ put
        self.assertEqual(options_math.bs_price(5010, 5000, 0, "C"), 10)
        self.assertLess(options_math.bs_price(5000, 5020, 30, "C"), options_math.bs_price(5000, 5020, 300, "C"))

    def test_ticks(self):
        self.assertEqual(options_math.round_tick(2.43), 2.40)
        self.assertEqual(options_math.round_tick(2.43, up=True), 2.45)
        self.assertEqual(options_math.round_tick(11.04, up=True), 11.10)


class TestAutoGate(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(autotrader.DEFAULTS)
        self.state = {"trades": 0, "pnl": 0.0}
        self.t = dt.datetime(2026, 11, 5, 11, 0, tzinfo=strategy.NY)

    def test_allows_normal_trade(self):
        self.assertEqual(autotrader.gate(self.state, self.cfg, self.t, 3.0, 2.9, []), [])

    def test_blocks(self):
        g = autotrader.gate
        self.assertTrue(g(self.state, self.cfg, self.t.replace(hour=9, minute=35), 3.0, 2.9, []))
        self.assertTrue(g(self.state, self.cfg, self.t.replace(hour=15, minute=45), 3.0, 2.9, []))
        self.assertTrue(g({"trades": 3, "pnl": 0}, self.cfg, self.t, 3.0, 2.9, []))
        self.assertTrue(g({"trades": 0, "pnl": -300}, self.cfg, self.t, 3.0, 2.9, []))
        self.assertTrue(g(self.state, self.cfg, self.t, 6.0, 5.9, []))       # 600$ > 500$
        self.assertTrue(g(self.state, self.cfg, self.t, 3.0, 2.0, []))       # wide spread
        self.assertTrue(g(self.state, self.cfg, self.t, 3.0, 2.9, ["pos"]))  # one at a time
        self.assertTrue(g(self.state, self.cfg, self.t, None, None, []))

    def test_kill_switch(self):
        open(autotrader.kill_switch_path(), "w").close()
        try:
            self.assertTrue(autotrader.gate(self.state, self.cfg, self.t, 3.0, 2.9, []))
        finally:
            os.remove(autotrader.kill_switch_path())

    def test_bracket(self):
        self.assertEqual(autotrader.bracket_prices(4.0, self.cfg), (8.0, 2.0))


class TestSimLoop(unittest.TestCase):
    def test_sim_trades_once_per_day_and_closes(self):
        cfg = dict(autotrader.DEFAULTS, max_premium_usd=5000, max_daily_loss_usd=10000)
        bars = synth.make(4) + synth.make(5)
        b = autotrader.SimBroker(bars)
        autotrader.trade_loop(b, cfg, common.load_config()["spx_strategy"], persist=False)
        self.assertEqual(len(b.closed), 2)
        self.assertEqual({t["day"] for t in b.closed}, {dt.date(2026, 11, 4), dt.date(2026, 11, 5)})
        self.assertFalse(b.open_positions())

    def test_sim_respects_premium_limit(self):
        b = autotrader.SimBroker(synth.make())
        autotrader.trade_loop(b, dict(autotrader.DEFAULTS, max_premium_usd=100),
                              common.load_config()["spx_strategy"], persist=False)
        self.assertEqual(b.closed, [])


class _FakeIB:
    account = "U1234567"

    def connect(self, *a, **k):
        pass

    def managedAccounts(self):
        return [self.account]

    def disconnect(self):
        self.disconnected = True

    def qualifyContracts(self, *c):
        return list(c)

    def reqPnL(self, acct):
        return types.SimpleNamespace(dailyPnL=float("nan"))

    def isConnected(self):
        return True


class TestIBKRSafety(unittest.TestCase):
    def setUp(self):
        fake = types.ModuleType("ib_async")
        fake.IB = _FakeIB
        fake.Index = lambda *a, **k: object()
        sys.modules["ib_async"] = fake
        import broker_ibkr
        self.mod = broker_ibkr

    def tearDown(self):
        sys.modules.pop("ib_async", None)
        os.environ.pop("SUPER_RAMI_LIVE", None)

    def test_real_account_refused_in_paper_mode(self):
        _FakeIB.account = "U1234567"
        with self.assertRaises(common.RamiError):
            self.mod.IBKRBroker(dict(autotrader.DEFAULTS))

    def test_paper_account_ok(self):
        _FakeIB.account = "DU1234567"
        b = self.mod.IBKRBroker(dict(autotrader.DEFAULTS))
        self.assertTrue(b.is_paper)
        self.assertEqual(b.daily_pnl(), 0.0)

    def test_live_needs_env(self):
        _FakeIB.account = "U1234567"
        with self.assertRaises(common.RamiError):
            self.mod.IBKRBroker(dict(autotrader.DEFAULTS, mode="live"))
        os.environ["SUPER_RAMI_LIVE"] = "I_UNDERSTAND"
        self.assertFalse(self.mod.IBKRBroker(dict(autotrader.DEFAULTS, mode="live")).is_paper)
