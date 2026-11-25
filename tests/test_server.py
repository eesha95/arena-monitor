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


class ClerkTokens(unittest.TestCase):
    """RS256 verification of Clerk session tokens, with a test RSA key generated here (offline)."""

    @staticmethod
    def _prime(bits, rnd):
        def probable(n):
            if n % 2 == 0:
                return False
            d, r = n - 1, 0
            while d % 2 == 0:
                d //= 2
                r += 1
            for _ in range(20):
                a = rnd.randrange(2, n - 1)
                x = pow(a, d, n)
                if x in (1, n - 1):
                    continue
                for _ in range(r - 1):
                    x = pow(x, 2, n)
                    if x == n - 1:
                        break
                else:
                    return False
            return True
        while True:
            c = rnd.getrandbits(bits) | (1 << (bits - 1)) | 1
            if probable(c):
                return c

    @classmethod
    def setUpClass(cls):
        import random
        rnd = random.Random(7)
        e = 65537
        while True:
            p, q = cls._prime(512, rnd), cls._prime(512, rnd)
            phi = (p - 1) * (q - 1)
            if p != q and phi % e:
                break
        cls.n, cls.e, cls.d = p * q, e, pow(e, -1, phi)
        cls.now = 1_800_000_000

    def _token(self, claims, alg="RS256", key=None):
        import base64
        import hashlib
        b = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()
        si = b({"alg": alg, "kid": "k1", "typ": "JWT"}) + "." + b(claims)
        k = (self.n.bit_length() + 7) // 8
        t = S._SHA256_PREFIX + hashlib.sha256(si.encode()).digest()
        em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
        n, d = key or (self.n, self.d)
        sig = pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")
        return si + "." + base64.urlsafe_b64encode(sig).rstrip(b"=").decode()

    def _claims(self, **kw):
        c = {"sub": "user_1", "exp": self.now + 60, "nbf": self.now - 5, "iat": self.now - 5,
             "azp": "https://arena.example"}
        c.update(kw)
        return c

    def _verify(self, tok, origin="https://arena.example"):
        return S.verify_clerk_token(tok, origin, key_lookup=lambda kid: (self.n, self.e) if kid == "k1" else None,
                                    now=self.now)

    def test_valid(self):
        self.assertEqual(self._verify(self._token(self._claims()))["sub"], "user_1")

    def test_tampered_payload(self):
        import base64
        h, _, sig = self._token(self._claims()).split(".")
        evil = base64.urlsafe_b64encode(json.dumps(self._claims(sub="admin")).encode()).rstrip(b"=").decode()
        with self.assertRaisesRegex(ValueError, "signature"):
            self._verify(h + "." + evil + "." + sig)

    def test_wrong_key(self):
        import random
        rnd = random.Random(99)
        p, q = self._prime(512, rnd), self._prime(512, rnd)
        other = (p * q, pow(65537, -1, (p - 1) * (q - 1)))
        with self.assertRaises(ValueError):
            self._verify(self._token(self._claims(), key=other))

    def test_expired_and_not_yet_valid(self):
        with self.assertRaisesRegex(ValueError, "expired"):
            self._verify(self._token(self._claims(exp=self.now - 60)))
        with self.assertRaisesRegex(ValueError, "not yet"):
            self._verify(self._token(self._claims(nbf=self.now + 60)))

    def test_wrong_origin(self):
        with self.assertRaisesRegex(ValueError, "authorized party"):
            self._verify(self._token(self._claims()), origin="https://evil.example")

    def test_alg_none_rejected(self):
        with self.assertRaisesRegex(ValueError, "alg"):
            self._verify(self._token(self._claims(), alg="none"))

    def test_pending_session(self):
        with self.assertRaisesRegex(ValueError, "pending"):
            self._verify(self._token(self._claims(sts="pending")))

    def test_frontend_api_from_publishable_key(self):
        import base64
        pk = "pk_test_" + base64.b64encode(b"sky-umbrella-1.clerk.accounts.dev$").decode()
        self.assertEqual(S.clerk_frontend_api(pk), "sky-umbrella-1.clerk.accounts.dev")
