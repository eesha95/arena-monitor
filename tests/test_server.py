"""Offline unit tests for Arena Monitor. No network. Run: python -m unittest discover -s tests -v"""
import json
import os
import sys
import tempfile
import types
import unittest

os.environ["ARENA_DATA"] = tempfile.mkdtemp(prefix="arena-test-")
os.environ["ARENA_AI_BRIEF"] = "1"
os.environ["PANDASCORE_TOKEN"] = "test"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server as S  # noqa: E402


class Matcher(unittest.TestCase):
    def test_games_and_signals(self):
        g, sig, _ = S.classify("Vitality sign new AWPer ahead of the CS2 Major")
        self.assertIn("cs2", g)
        self.assertIn("roster", sig)

    def test_esports_signals_need_game_context(self):
        g, sig, _ = S.classify("Racehorse wins big at the track")
        self.assertEqual(g, [])
        self.assertNotIn("result", sig)

    def test_business_signal_without_game(self):
        _, sig, _ = S.classify("Publisher announces layoffs after weak earnings")
        self.assertIn("business", sig)

    def test_strict_drops_off_topic(self):
        self.assertIsNone(S.classify("Woman evicted after smart assistant error", strict=True))
        self.assertIsNotNone(S.classify("New Steam release tops the charts", strict=True))

    def test_places(self):
        _, _, p = S.classify("Grand final moves to Riyadh; fans travel from Seoul")
        names = {x["name"] for x in p}
        self.assertTrue({"riyadh", "seoul"} <= names)

    def test_word_boundaries(self):
        g, _, _ = S.classify("The overwatched lolly")  # no 'ow' or 'lol' false hits
        self.assertEqual(g, [])


class Feeds(unittest.TestCase):
    def test_rss_and_atom(self):
        rss = """<?xml version="1.0"?><rss><channel><item><title>A &amp; B</title><link>https://x/a</link>
                 <pubDate>Mon, 23 Nov 2026 10:00:00 GMT</pubDate><description>&lt;p&gt;Hi&lt;/p&gt;</description></item></channel></rss>"""
        atom = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>C</title><link href="https://x/c"/>
                  <updated>2026-11-23T10:00:00Z</updated></entry></feed>"""
        r, a = S.parse_feed(rss), S.parse_feed(atom)
        self.assertEqual(r[0]["title"], "A & B")
        self.assertEqual(r[0]["summary"], "Hi")
        self.assertEqual(a[0]["url"], "https://x/c")
        self.assertEqual(r[0]["ts"], a[0]["ts"])


class Forecast(unittest.TestCase):
    def series(self, f, hours=30, step=300):
        t0 = 1_800_000_000
        return [(t0 + i * step, f(i * step)) for i in range(hours * 3600 // step)]

    def test_seasonal_series_is_drawn_and_beats_baseline(self):
        import math
        pts = self.series(lambda t: 100000 + 40000 * math.sin(2 * math.pi * t / 86400))
        f = S.forecast(pts)
        self.assertFalse(f.get("hidden"), f)
        self.assertLess(f["mape"], f["baseline_mape"])
        self.assertLessEqual(f["low"], f["value"])
        self.assertLessEqual(f["value"], f["high"])

    def test_noise_is_hidden(self):
        import random
        rnd = random.Random(1)
        pts = self.series(lambda t: 100000 + rnd.uniform(-30000, 30000))
        f = S.forecast(pts)
        self.assertTrue(f.get("hidden"), f)
        self.assertIn("baseline", f["reason"])

    def test_short_history(self):
        self.assertIsNone(S.forecast([(1, 1)] * 5))


class PandaScore(unittest.TestCase):
    def test_match_mapping(self):
        m = {"id": 1, "status": "finished", "begin_at": "2026-11-23T10:00:00Z", "number_of_games": 3, "winner_id": 10,
             "videogame": {"slug": "valorant", "name": "Valorant"}, "league": {"name": "VCT"},
             "opponents": [{"opponent": {"id": 10, "name": "Paper Rex", "acronym": "PRX"}},
                           {"opponent": {"id": 11, "name": "Sentinels", "acronym": "SEN"}}],
             "results": [{"team_id": 10, "score": 2}, {"team_id": 11, "score": 1}]}
        r = S._ps_match(m)
        self.assertEqual((r["game"], r["state"]), ("valorant", "completed"))
        self.assertEqual([t["outcome"] for t in r["teams"]], ["win", "loss"])
        self.assertEqual([t["wins"] for t in r["teams"]], [2, 1])


class AIBrief(unittest.TestCase):
    def setUp(self):
        now = int(S.time.time())
        for i in range(3):
            S.q("INSERT OR REPLACE INTO items VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("t%d" % i, "HLTV news", "Headline %d" % i, "https://x/%d" % i, now - i * 60, now, "", "[]", "[]", "[]"),
                commit=True)
        self.calls = []
        test = self

        class Resp:
            stop_reason, model, stop_details = "end_turn", "claude-opus-5-5", None
            usage = types.SimpleNamespace(input_tokens=100, output_tokens=50)
            content = [types.SimpleNamespace(type="text", text=json.dumps({"items": [
                {"headline": "Cited", "why_it_matters": "w", "audience": "fans", "sources": [1, 2]},
                {"headline": "Uncited", "why_it_matters": "w", "audience": "fans", "sources": [99]}]}))]

        class Msgs:
            def create(self, **kw):
                test.calls.append(kw)
                return Resp()

        fake = types.ModuleType("anthropic")
        fake.Anthropic = lambda: types.SimpleNamespace(beta=types.SimpleNamespace(messages=Msgs()))
        sys.modules["anthropic"] = fake
        S._ai_last["sig"] = None

    def test_cited_only_and_no_repeat_spend(self):
        self.assertEqual(S.job_ai_brief(), 1)
        b = S.STATE["ai_brief"]
        self.assertEqual(b["items"][0]["headline"], "Cited")
        self.assertEqual(len(b["items"][0]["cites"]), 2)
        req = self.calls[0]
        self.assertEqual(req["model"], "claude-opus-5-5")
        self.assertEqual(req["fallbacks"], "default")
        self.assertEqual(req["output_config"]["format"]["type"], "json_schema")
        S.job_ai_brief()
        self.assertEqual(len(self.calls), 1, "second run with no new headlines must not call the API")


class Backoff(unittest.TestCase):
    def test_429_backs_off(self):
        sid = "test_rl"
        S.SOURCES[sid] = {"id": sid, "name": "t", "url": "", "interval": 1800, "note": "", "kind": "feed",
                          "fn": lambda: (_ for _ in ()).throw(RuntimeError("HTTP Error 429: Too Many Requests"))}
        S.STATUS[sid] = {"ok": None, "last_ok": None, "last_try": None, "error": None, "count": 0, "ms": None,
                         "fails": 0, "next_try": None}
        slept = []
        orig = S.time.sleep

        def fake_sleep(x):
            slept.append(x)
            if len(slept) >= 4:
                raise StopIteration
        S.time.sleep = fake_sleep
        try:
            with self.assertRaises(StopIteration):
                S.loop(sid, 0)
        finally:
            S.time.sleep = orig
            del S.SOURCES[sid], S.STATUS[sid]
        self.assertEqual(slept[1:4], [900, 1800, 3600])


class DataDir(unittest.TestCase):
    def test_data_outside_vault(self):
        self.assertNotIn("OneDrive", S.DB_PATH.replace("\\", "/").split("/ArenaMonitor")[0]
                         if "ARENA_DATA" not in os.environ else "")
        self.assertTrue(S.DB_PATH.startswith(os.environ["ARENA_DATA"]))


if __name__ == "__main__":
    unittest.main()
