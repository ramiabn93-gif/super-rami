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
        self.cfg = autotrader.resolve(autotrader.DEFAULTS)
        self.state = {"trades": 0, "pnl": 0.0}
        self.t = dt.datetime(2026, 11, 5, 11, 0, tzinfo=strategy.NY)

    def test_allows_normal_trade(self):
        self.assertEqual(autotrader.gate(self.state, self.cfg, self.t, 1.9, 1.8, []), [])

    def test_blocks(self):
        g = autotrader.gate
        self.assertTrue(g(self.state, self.cfg, self.t.replace(hour=9, minute=35), 3.0, 2.9, []))
        self.assertTrue(g(self.state, self.cfg, self.t.replace(hour=15, minute=45), 3.0, 2.9, []))
        self.assertTrue(g({"trades": 3, "pnl": 0}, self.cfg, self.t, 3.0, 2.9, []))
        self.assertTrue(g({"trades": 0, "pnl": -100}, self.cfg, self.t, 3.0, 2.9, []))
        self.assertTrue(g(self.state, self.cfg, self.t, 2.5, 2.4, []))       # 250$ > 200$
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
        cfg = autotrader.resolve(dict(autotrader.DEFAULTS, max_premium_usd=5000, max_daily_loss_usd=10000))
        bars = synth.make(4) + synth.make(5)
        b = autotrader.SimBroker(bars)
        autotrader.trade_loop(b, cfg, common.load_config()["spx_strategy"], persist=False)
        self.assertEqual(len(b.closed), 2)
        self.assertEqual({t["day"] for t in b.closed}, {dt.date(2026, 11, 4), dt.date(2026, 11, 5)})
        self.assertFalse(b.open_positions())

    def test_sim_respects_premium_limit(self):
        b = autotrader.SimBroker(synth.make())
        autotrader.trade_loop(b, autotrader.resolve(dict(autotrader.DEFAULTS, max_premium_usd=100)),
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


# ---------------------------------------------------------------- crypto
import random  # noqa: E402

import binance_client as bn  # noqa: E402
import crypto_bot  # noqa: E402
import crypto_strategies as cs  # noqa: E402

T0 = 1_762_300_800_000  # 2025-11-05 00:00 UTC
STEP = 900_000           # 15m


def crypto_bars(n=600, seed=1, drift=0.0):
    rnd, p, out = random.Random(seed), 100.0, []
    for i in range(n):
        o = p
        p = max(p * (1 + drift + rnd.gauss(0, 0.004)), 0.01)
        out.append({"t": T0 + i * STEP, "o": o, "h": max(o, p) * 1.002, "l": min(o, p) * 0.998,
                    "c": p, "v": 1000 + rnd.random() * 200, "close_t": T0 + (i + 1) * STEP - 1})
    return out


class TestBinanceClient(unittest.TestCase):
    def test_signature_vector_from_binance_docs(self):
        secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
        q = "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559"
        self.assertEqual(bn.sign(secret, q), "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71")

    def test_floor_step(self):
        self.assertEqual(bn.floor_step(1.23456, 0.001), 1.234)
        self.assertEqual(bn.floor_step(123456.7, 1), 123456)

    def test_klines_parse_and_paging(self):
        calls = []

        def http(method, url, **k):
            calls.append(url)
            end = int(urllib_parse.parse_qs(urllib_parse.urlparse(url).query).get("endTime", [T0 + 2000 * STEP])[0])
            start = end - 1000 * STEP
            return [[t, "1", "2", "0.5", "1.5", "10", t + STEP - 1] for t in range(start, end, STEP)][-1000:]
        bars = bn.history("BTCUSDT", "15m", 1500, http=http)
        self.assertEqual(len(bars), 1500)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(b["t"] < c["t"] for b, c in zip(bars, bars[1:])))

    def test_no_keys_refused(self):
        os.environ.pop("BINANCE_API_KEY", None)
        os.environ.pop("BINANCE_API_SECRET", None)
        with self.assertRaises(common.RamiError):
            bn.Binance().signed("GET", "/api/v3/account")


import urllib.parse as urllib_parse  # noqa: E402


class TestCryptoStrategies(unittest.TestCase):
    def setUp(self):
        self.p = dict(cs.DEFAULT_PARAMS)

    def test_breakout_signal(self):
        bars = crypto_bars(400)
        last = bars[-1]
        bars.append({"t": last["t"] + STEP, "o": last["c"], "h": last["c"] * 1.06, "l": last["c"],
                     "c": last["c"] * 1.05, "v": 10000, "close_t": last["t"] + 2 * STEP - 1})
        ind = cs.Ind(bars, self.p)
        self.assertEqual(cs.signal("breakout", bars, ind, len(bars) - 1, self.p), 1)
        self.assertEqual(cs.signal("momentum", bars, ind, len(bars) - 1, self.p), 1)

    def test_all_strategies_backtest_run(self):
        bars = crypto_bars(2000, seed=3, drift=0.0003)
        for n in cs.STRATEGIES:
            r = cs.backtest(bars, n, self.p, 0.1, True)
            self.assertGreaterEqual(r["trades"], 0)
            self.assertLessEqual(r["max_dd"], 100)

    def test_stop_and_trailing(self):
        bars = crypto_bars(300)
        ind = cs.Ind(bars, self.p)
        pos = cs.new_position("X", "breakout", 1, 100.0, 1.0, bars[250]["t"], self.p)
        self.assertEqual(pos["stop"], 98.0)
        self.assertEqual(cs.stop_hit(pos, {"o": 99, "h": 99, "l": 97.5, "c": 98}), 98.0)
        self.assertEqual(cs.stop_hit(pos, {"o": 96, "h": 96, "l": 95, "c": 95}), 96)   # gap
        pos["best"] = 110.0
        ind.atr[260] = 1.0
        cs.manage(pos, bars, ind, 260, self.p)
        self.assertEqual(pos["stop"], 108.0)                                              # trailed

    def test_max_hold(self):
        bars = crypto_bars(700)
        ind = cs.Ind(bars, self.p)
        pos = cs.new_position("X", "breakout", 1, bars[300]["c"], 0.0001, bars[300]["t"], self.p)
        pos["stop"] = 0
        why = None
        for i in range(301, 700):
            why = cs.manage(pos, bars, ind, i, self.p)
            if why and "أقصى" in why:
                break
        self.assertLessEqual((bars[i]["t"] - bars[300]["t"]) / 86400e3, 3.01)


class FakeClient:
    def __init__(self):
        self.orders = []
        self.px = 100.0

    def price(self, s):
        return self.px

    def open(self, s, side, usdt, lev=1):
        self.orders.append(("open", s, side, usdt))
        return usdt / self.px, self.px

    def close(self, s, side, qty):
        self.orders.append(("close", s, side, qty))
        return self.px

    def set_stop(self, *a):
        self.orders.append(("stop",) + a)


class TestCryptoBot(unittest.TestCase):
    def make(self, **over):
        cfg = crypto_bot.ccfg()
        cfg.update(strong=["BTCUSDT"], memes=["PEPEUSDT"], **over)
        self.bars = crypto_bars(400)
        self.t = [self.bars[-1]["close_t"] + 1]
        self.client = FakeClient()
        self.client.px = self.bars[-1]["c"]
        bot = crypto_bot.Bot(cfg, client=self.client, fetch=lambda s: self.bars, now=lambda: self.t[0])
        bot.state = {"positions": {}, "daily": {}, "last_bar": {}}
        return bot

    def force_signal(self, side):
        orig = cs.signal
        cs.signal = lambda *a, **k: side
        self.addCleanup(setattr, cs, "signal", orig)

    def test_alert_mode_never_orders(self):
        bot = self.make(entry_mode="alert", exit_mode="alert")
        self.force_signal(1)
        bot.step()
        self.assertIn("BTCUSDT", bot.state["positions"])
        self.assertFalse(bot.state["positions"]["BTCUSDT"]["auto"])
        self.assertEqual(self.client.orders, [])

    def test_auto_without_budget_falls_back_to_alert(self):
        bot = self.make(entry_mode="auto", per_trade_usdt=None)
        self.force_signal(1)
        bot.step()
        self.assertEqual(self.client.orders, [])

    def test_auto_entry_and_auto_stop_exit(self):
        bot = self.make(entry_mode="auto", exit_mode="auto", per_trade_usdt=100)
        self.force_signal(1)
        bot.step()
        opens = [o for o in self.client.orders if o[0] == "open"]
        self.assertEqual([(o[1], o[3]) for o in opens], [("BTCUSDT", 100.0), ("PEPEUSDT", 50.0)])  # memes half
        stop = bot.state["positions"]["BTCUSDT"]["stop"]
        self.client.px = stop * 0.99                     # price falls through the stop
        self.t[0] += 60_000
        bot.step()
        self.assertNotIn("BTCUSDT", bot.state["positions"])
        self.assertTrue(any(o[0] == "close" and o[1] == "BTCUSDT" for o in self.client.orders))
        self.assertLess(bot.state["daily"][bot.today()], 0)

    def test_spot_ignores_shorts(self):
        bot = self.make(entry_mode="auto", per_trade_usdt=100, market="spot")
        self.force_signal(-1)
        bot.step()
        self.assertEqual(bot.state["positions"], {})

    def test_max_open_positions(self):
        bot = self.make(entry_mode="auto", per_trade_usdt=100, max_open_positions=1)
        self.force_signal(1)
        bot.step()
        self.assertEqual(len([o for o in self.client.orders if o[0] == "open"]), 1)

    def test_kill_switch_flattens(self):
        bot = self.make(entry_mode="auto", exit_mode="auto", per_trade_usdt=100)
        self.force_signal(1)
        bot.step()
        open(crypto_bot.kill_path(), "w").close()
        try:
            self.assertFalse(bot.step())
            self.assertEqual(bot.state["positions"], {})
        finally:
            os.remove(crypto_bot.kill_path())

    def test_live_requires_env(self):
        cfg = crypto_bot.ccfg()
        with self.assertRaises(common.RamiError):
            crypto_bot.check_config(dict(cfg, testnet=False))
        with self.assertRaises(common.RamiError):
            crypto_bot.check_config(dict(cfg, leverage=20))


class TestSpxCapital(unittest.TestCase):
    def test_limits_from_1000_account(self):
        c = autotrader.resolve(autotrader.DEFAULTS)
        self.assertEqual((c["max_premium_usd"], c["max_daily_loss_usd"]), (200, 100))
        c = autotrader.resolve(dict(autotrader.DEFAULTS, account_usd=5000))
        self.assertEqual((c["max_premium_usd"], c["max_daily_loss_usd"]), (1000, 500))


class TestPickContract(unittest.TestCase):
    def test_moves_further_otm_when_expensive(self):
        cfg = autotrader.resolve(autotrader.DEFAULTS)            # 200$ limit -> ask <= 2.00
        b = autotrader.SimBroker(synth.make())
        prices = {5015: 3.5, 5020: 2.6, 5025: 1.8, 5030: 1.2}
        b.quote = lambda c: (prices[c["strike"]] - 0.1, prices[c["strike"]])
        _, strike, _, ask = autotrader.pick_contract(b, cfg, {"strike": 5015}, "C", "20261104")
        self.assertEqual((strike, ask), (5025, 1.8))

    def test_stops_at_extra_otm_limit(self):
        cfg = autotrader.resolve(dict(autotrader.DEFAULTS, extra_otm_pts=5))
        b = autotrader.SimBroker(synth.make())
        b.quote = lambda c: (4.9, 5.0)
        _, strike, _, _ = autotrader.pick_contract(b, cfg, {"strike": 4985}, "P", "20261104")
        self.assertEqual(strike, 4980)                            # puts move down


# ---------------------------------------------------------------- auto pick
import picker  # noqa: E402


class TestStockPicker(unittest.TestCase):
    def test_uptrend_only_and_weights(self):
        up = [100 * (1.002 ** i) for i in range(260)]
        down = [100 * (0.998 ** i) for i in range(260)]
        rnd = random.Random(5)
        choppy_up = [100 * (1.0015 ** i) * (1 + rnd.gauss(0, 0.02)) for i in range(260)]
        smooth_up = [p * (1 + 0.003 * ((-1) ** i)) for i, p in enumerate(up)]  # tiny noise so vol > 0
        ms = [picker.evaluate("A", smooth_up), picker.evaluate("B", down), picker.evaluate("C", choppy_up)]
        self.assertFalse(ms[1]["uptrend"])
        chosen = picker.pick(ms, 5)
        self.assertNotIn("B", [m["symbol"] for m in chosen])
        self.assertAlmostEqual(sum(m["weight"] for m in chosen), 100, delta=0.2)
        self.assertEqual(chosen[0]["symbol"], "A")                 # smoother trend ranks first

    def test_not_enough_data(self):
        self.assertIsNone(picker.evaluate("X", [1.0] * 100))


class TestCryptoAutopick(unittest.TestCase):
    def test_candidates_split_and_filters(self):
        t = [{"symbol": "BTCUSDT", "quoteVolume": "9e9"}, {"symbol": "PEPEUSDT", "quoteVolume": "5e8"},
             {"symbol": "USDCUSDT", "quoteVolume": "9e9"}, {"symbol": "ETHBTC", "quoteVolume": "9e9"},
             {"symbol": "TINYUSDT", "quoteVolume": "1e5"}, {"symbol": "SOLUSDT", "quoteVolume": "2e9"}]
        strong, memes = crypto_bot.candidates(t)
        self.assertEqual(strong, ["BTCUSDT", "SOLUSDT"])
        self.assertEqual(memes, ["PEPEUSDT"])

    def test_autopick_keeps_only_profitable(self):
        cfg = crypto_bot.ccfg()
        cfg.update(autopick_strong=1, autopick_memes=1)
        t = [{"symbol": s, "quoteVolume": "1e9"} for s in ("BTCUSDT", "ETHUSDT", "DOGEUSDT")]
        data = {"BTCUSDT": crypto_bars(3000, seed=11, drift=0.0004), "ETHUSDT": crypto_bars(3000, seed=12),
                "DOGEUSDT": crypto_bars(3000, seed=13, drift=-0.0004)}
        pk = crypto_bot.autopick(cfg, t, lambda s: data[s])
        self.assertLessEqual(len(pk["strong"]), 1)
        self.assertLessEqual(len(pk["memes"]), 1)
        for s in pk["strong"] + pk["memes"]:
            r = next(r for r in pk["report"] if r["symbol"] == s)
            self.assertTrue(r["ok"] and r["return"] > 0)
            self.assertIn(pk["assign"][s], cs.STRATEGIES)
        self.assertEqual(len(pk["report"]), 3)
