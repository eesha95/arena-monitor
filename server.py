#!/usr/bin/env python3
"""Arena Monitor - a real-time, world-monitor style dashboard for esports and gaming.

Stdlib only. Background jobs poll public sources on fixed intervals, keep the latest
payload in memory, store history (news items, player-count series) in SQLite, and push
every update to browsers over Server-Sent Events.

Run:  python server.py            (then open http://127.0.0.1:8090)
Env:  PORT=8090
      TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET   optional, enables the "Top streams" feed
"""
import gzip
import hashlib
import html
import json
import math
import os
import queue
import re
import sqlite3
import threading
import time
import traceback
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
# Runtime data churns every few minutes, so it lives outside the OneDrive-synced vault by default.
DATA = os.environ.get("ARENA_DATA") or os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "ArenaMonitor")
os.makedirs(DATA, exist_ok=True)
DB_PATH = os.path.join(DATA, "monitor.db")
PORT = int(os.environ.get("PORT", "8090"))
# Serverless mode (Vercel): no background threads; sources refresh on request when due, and the page polls.
SERVERLESS = os.environ.get("ARENA_SERVERLESS") == "1"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ArenaMonitor/0.1 (local dashboard)"

# Riot's public web key, embedded in lolesports.com and used by its own front end.
LOL_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"


# ----------------------------------------------------------------------------- HTTP fetch
def fetch(url, headers=None, timeout=25, data=None):
    h = {"User-Agent": UA, "Accept-Encoding": "gzip", "Accept": "*/*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    return body.decode("utf-8", "replace")


def fetch_json(url, **kw):
    return json.loads(fetch(url, **kw))


# ----------------------------------------------------------------------------- storage
_db_lock = threading.Lock()


def db():
    con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


DBC = db()
with _db_lock:
    DBC.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS items(
            id TEXT PRIMARY KEY, source TEXT, title TEXT, url TEXT, ts INTEGER,
            first_seen INTEGER, summary TEXT, games TEXT, signals TEXT, places TEXT);
        CREATE INDEX IF NOT EXISTS items_ts ON items(ts);
        CREATE TABLE IF NOT EXISTS series(key TEXT, ts INTEGER, value REAL);
        CREATE INDEX IF NOT EXISTS series_key_ts ON series(key, ts);
        CREATE TABLE IF NOT EXISTS apps(appid INTEGER PRIMARY KEY, name TEXT);
        """
    )
    DBC.commit()


def q(sql, args=(), commit=False):
    with _db_lock:
        cur = DBC.execute(sql, args)
        rows = cur.fetchall()
        if commit:
            DBC.commit()
        return rows


# ----------------------------------------------------------------------------- live state + SSE
STATE = {}
STATUS = {}
LOCK = threading.Lock()
SUBSCRIBERS = set()


def publish(key, payload):
    with LOCK:
        STATE[key] = payload
        subs = list(SUBSCRIBERS)
    msg = json.dumps({"key": key, "data": payload, "at": int(time.time())})
    for sq in subs:
        try:
            sq.put_nowait(msg)
        except queue.Full:
            pass


# ----------------------------------------------------------------------------- lexicons (matcher)
GAMES = {
    "cs2": ("Counter-Strike 2", ["counter-strike", "cs2", "csgo", "cs:go", "hltv", "iem", "blast premier",
                                 "blast open", "esl pro league", "pgl", "cs major", "austin major", "cologne major"]),
    "lol": ("League of Legends", ["league of legends", "lol esports", "lck", "lpl", "lec", "lcs", "lta", "lcp",
                                  "worlds 20", "msi 20", "first stand", "summoner", "faker"]),
    "valorant": ("VALORANT", ["valorant", "vct", "champions tour", "masters toronto", "masters bangkok"]),
    "dota2": ("Dota 2", ["dota", "the international", "riyadh masters", "dreamleague", "blast slam"]),
    "r6": ("Rainbow Six Siege", ["rainbow six", "r6 siege", "siege x", "six invitational"]),
    "rl": ("Rocket League", ["rocket league", "rlcs"]),
    "ow": ("Overwatch", ["overwatch", "owcs"]),
    "apex": ("Apex Legends", ["apex legends", "algs"]),
    "pubg": ("PUBG", ["pubg", "battlegrounds"]),
    "fgc": ("Fighting games", ["street fighter", "tekken", "evo", "fighting game", "capcom cup", "fatal fury",
                               "mortal kombat", "smash bros"]),
    "fortnite": ("Fortnite", ["fortnite", "fncs"]),
    "mobile": ("Mobile esports", ["mobile legends", "mlbb", "honor of kings", "free fire", "pubg mobile"]),
    "cod": ("Call of Duty", ["call of duty", "cdl", "warzone", "black ops"]),
    "marvel": ("Marvel Rivals", ["marvel rivals"]),
    "deadlock": ("Deadlock", ["deadlock"]),
}

SIGNALS = {
    "roster": ["roster", "sign", "signs", "signed", "signing", "re-sign", "picks up", "pick up", "bench", "joins", "benched", "transfer", "parts ways", "part ways", "stand-in",
               "free agent", "on loan", "departs", "retires", "retirement", "new coach", "head coach", "acquires player"],
    "result": ["wins", "beat", "beats", "defeat", "defeats", "eliminated", "eliminate", "champion", "grand final",
               "advance", "advances", "qualify", "qualifies", "upset", "sweep", "sweeps", "lift the trophy"],
    "event": ["tournament", "prize pool", "bracket", "venue", "tickets", "lan", "qualifier", "circuit",
              "invitational", "playoffs", "group stage", "format revealed", "dates revealed", "host city"],
    "patch": ["patch", "nerf", "buff", "balance changes", "hotfix", "map pool", "new agent", "new hero",
              "new champion", "update notes", "operation"],
    "business": ["acquire", "acquires", "acquisition", "funding", "raises", "investment", "layoff", "layoffs",
                 "laid off", "revenue", "earnings", "partnership", "sponsor", "sponsorship", "merger", "ipo",
                 "shut down", "shuts down", "closure", "valuation", "stake"],
    "release": ["release date", "launches", "early access", "trailer", "revealed", "out now", "delayed",
                "delay", "coming to", "launch date", "beta"],
    "integrity": ["ban", "banned", "cheat", "cheating", "match-fixing", "match fixing", "suspended",
                  "investigation", "esic", "lawsuit", "fined", "doping", "betting violation"],
    "outage": ["outage", "servers down", "server issues", "ddos", "disruption", "downtime", "maintenance"],
}

# name -> (lat, lon, region). Countries, esports hubs, and org home cities (headquarters, approximate).
PLACES = {
    # cities
    "seoul": (37.57, 126.98, "APAC"), "busan": (35.18, 129.08, "APAC"), "shanghai": (31.23, 121.47, "APAC"),
    "beijing": (39.9, 116.4, "APAC"), "chengdu": (30.66, 104.07, "APAC"), "shenzhen": (22.54, 114.06, "APAC"),
    "hangzhou": (30.27, 120.15, "APAC"), "guangzhou": (23.13, 113.26, "APAC"), "tokyo": (35.68, 139.69, "APAC"),
    "osaka": (34.69, 135.5, "APAC"), "taipei": (25.03, 121.56, "APAC"), "hong kong": (22.32, 114.17, "APAC"),
    "bangkok": (13.76, 100.5, "APAC"), "jakarta": (-6.2, 106.85, "APAC"), "manila": (14.6, 120.98, "APAC"),
    "singapore": (1.35, 103.82, "APAC"), "kuala lumpur": (3.14, 101.69, "APAC"), "sydney": (-33.87, 151.21, "APAC"),
    "melbourne": (-37.81, 144.96, "APAC"), "ho chi minh": (10.82, 106.63, "APAC"), "mumbai": (19.08, 72.88, "APAC"),
    "ulaanbaatar": (47.89, 106.91, "APAC"),
    "berlin": (52.52, 13.4, "EMEA"), "cologne": (50.94, 6.96, "EMEA"), "hamburg": (53.55, 9.99, "EMEA"),
    "katowice": (50.26, 19.02, "EMEA"), "paris": (48.86, 2.35, "EMEA"), "london": (51.51, -0.13, "EMEA"),
    "copenhagen": (55.68, 12.57, "EMEA"), "stockholm": (59.33, 18.07, "EMEA"), "malmö": (55.6, 13.0, "EMEA"),
    "malmo": (55.6, 13.0, "EMEA"), "madrid": (40.42, -3.7, "EMEA"), "barcelona": (41.39, 2.17, "EMEA"),
    "lisbon": (38.72, -9.14, "EMEA"), "amsterdam": (52.37, 4.9, "EMEA"), "rotterdam": (51.92, 4.48, "EMEA"),
    "utrecht": (52.09, 5.12, "EMEA"), "antwerp": (51.22, 4.4, "EMEA"), "belgrade": (44.79, 20.45, "EMEA"),
    "kyiv": (50.45, 30.52, "EMEA"), "istanbul": (41.01, 28.98, "EMEA"), "riyadh": (24.71, 46.68, "EMEA"),
    "dubai": (25.2, 55.27, "EMEA"), "abu dhabi": (24.45, 54.38, "EMEA"), "warsaw": (52.23, 21.01, "EMEA"),
    "helsinki": (60.17, 24.94, "EMEA"), "oslo": (59.91, 10.75, "EMEA"), "astana": (51.17, 71.45, "EMEA"),
    "cairo": (30.04, 31.24, "EMEA"), "johannesburg": (-26.2, 28.05, "EMEA"),
    "los angeles": (34.05, -118.24, "Americas"), "las vegas": (36.17, -115.14, "Americas"),
    "dallas": (32.78, -96.8, "Americas"), "austin": (30.27, -97.74, "Americas"), "atlanta": (33.75, -84.39, "Americas"),
    "new york": (40.71, -74.01, "Americas"), "chicago": (41.88, -87.63, "Americas"), "seattle": (47.61, -122.33, "Americas"),
    "san francisco": (37.77, -122.42, "Americas"), "toronto": (43.65, -79.38, "Americas"),
    "montreal": (45.5, -73.57, "Americas"), "vancouver": (49.28, -123.12, "Americas"),
    "mexico city": (19.43, -99.13, "Americas"), "são paulo": (-23.55, -46.63, "Americas"),
    "sao paulo": (-23.55, -46.63, "Americas"), "rio de janeiro": (-22.91, -43.17, "Americas"),
    "buenos aires": (-34.6, -58.38, "Americas"), "santiago": (-33.45, -70.67, "Americas"), "lima": (-12.05, -77.04, "Americas"),
    # countries (centroid-ish)
    "south korea": (36.5, 127.8, "APAC"), "korea": (36.5, 127.8, "APAC"), "korean": (36.5, 127.8, "APAC"),
    "china": (35.0, 104.0, "APAC"), "chinese": (35.0, 104.0, "APAC"), "japan": (36.2, 138.3, "APAC"),
    "japanese": (36.2, 138.3, "APAC"), "taiwan": (23.7, 121.0, "APAC"), "vietnam": (14.1, 108.3, "APAC"),
    "thailand": (15.9, 100.99, "APAC"), "indonesia": (-2.5, 118.0, "APAC"), "philippines": (12.9, 121.8, "APAC"),
    "malaysia": (4.2, 102.0, "APAC"), "india": (21.0, 78.0, "APAC"), "australia": (-25.3, 133.8, "APAC"),
    "mongolia": (46.9, 103.8, "APAC"), "germany": (51.2, 10.4, "EMEA"), "france": (46.6, 2.2, "EMEA"),
    "united kingdom": (54.0, -2.0, "EMEA"), "uk": (54.0, -2.0, "EMEA"), "spain": (40.4, -3.7, "EMEA"),
    "poland": (52.1, 19.1, "EMEA"), "denmark": (56.0, 10.0, "EMEA"), "sweden": (62.0, 15.0, "EMEA"),
    "finland": (64.0, 26.0, "EMEA"), "norway": (61.0, 9.0, "EMEA"), "netherlands": (52.2, 5.3, "EMEA"),
    "belgium": (50.6, 4.6, "EMEA"), "italy": (42.8, 12.5, "EMEA"), "portugal": (39.6, -8.0, "EMEA"),
    "ukraine": (49.0, 31.0, "EMEA"), "russia": (56.0, 38.0, "EMEA"), "turkey": (39.0, 35.0, "EMEA"),
    "saudi arabia": (24.0, 45.0, "EMEA"), "saudi": (24.0, 45.0, "EMEA"), "kazakhstan": (48.0, 67.0, "EMEA"),
    "serbia": (44.0, 21.0, "EMEA"), "israel": (31.0, 35.0, "EMEA"), "egypt": (26.8, 30.8, "EMEA"),
    "south africa": (-30.6, 22.9, "EMEA"), "united states": (39.8, -98.6, "Americas"), "usa": (39.8, -98.6, "Americas"),
    "canada": (56.0, -106.0, "Americas"), "mexico": (23.6, -102.5, "Americas"), "brazil": (-14.2, -51.9, "Americas"),
    "brazilian": (-14.2, -51.9, "Americas"), "argentina": (-38.4, -63.6, "Americas"), "chile": (-35.7, -71.5, "Americas"),
    "peru": (-9.2, -75.0, "Americas"),
    # orgs -> home city (approximate headquarters; plotted as "org mention", not event location)
    "t1": (37.57, 126.98, "APAC"), "gen.g": (37.57, 126.98, "APAC"), "hanwha life": (37.57, 126.98, "APAC"),
    "bilibili gaming": (31.23, 121.47, "APAC"), "paper rex": (1.35, 103.82, "APAC"),
    "team vitality": (48.86, 2.35, "EMEA"), "g2 esports": (52.52, 13.4, "EMEA"),
    "fnatic": (51.51, -0.13, "EMEA"), "natus vincere": (50.45, 30.52, "EMEA"), "navi": (50.45, 30.52, "EMEA"),
    "mouz": (53.55, 9.99, "EMEA"), "astralis": (55.68, 12.57, "EMEA"),
    "team liquid": (52.09, 5.12, "EMEA"), "team falcons": (24.71, 46.68, "EMEA"), "furia": (-23.55, -46.63, "Americas"),
    "cloud9": (34.05, -118.24, "Americas"), "100 thieves": (34.05, -118.24, "Americas"),
}

_regex_cache = {}


def _rx(term):
    r = _regex_cache.get(term)
    if r is None:
        # word-ish boundaries; short aliases need exact token match
        r = re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", re.I)
        _regex_cache[term] = r
    return r


GAMING_TERMS = ["game", "games", "gaming", "gamer", "gamers", "esports", "e-sports", "esport", "steam", "xbox",
                "playstation", "ps5", "nintendo", "switch 2", "console", "studio", "publisher", "developer", "twitch",
                "streamer", "kick", "gta", "dlc", "fps", "mmo", "rpg", "roguelike", "speedrun", "pro player",
                "tournament", "valve", "riot", "blizzard", "ubisoft", "epic games", "activision", "rockstar",
                "minecraft", "roblox", "pokemon", "pokémon"]
ESPORTS_SIGNALS = {"roster", "result", "event"}  # only meaningful when the item is about a game / esports


def classify(text, default_games=(), strict=False):
    """Tag games, signal types and places. strict=True drops items with no gaming term (broad outlets)."""
    t = text.lower()
    games = set(default_games)
    for gid, (_, aliases) in GAMES.items():
        if any(_rx(a).search(t) for a in aliases):
            games.add(gid)
    if strict and not games and not any(_rx(w).search(t) for w in GAMING_TERMS):
        return None
    esports_ctx = bool(games) or any(_rx(w).search(t) for w in ("esports", "e-sports", "esport", "pro player"))
    sig = [s for s, words in SIGNALS.items()
           if (s not in ESPORTS_SIGNALS or esports_ctx) and any(_rx(w).search(t) for w in words)]
    places, seen = [], set()
    for name, (lat, lon, reg) in PLACES.items():
        if (lat, lon) in seen:
            continue
        if _rx(name).search(t):
            seen.add((lat, lon))
            places.append({"name": name, "lat": lat, "lon": lon, "region": reg})
    return sorted(games), sig, places[:4]


# ----------------------------------------------------------------------------- source register
# Every feed shows headline + link + date only. Licence/terms must be checked before any
# redistribution beyond personal monitoring.
SOURCES = {}


def source(sid, name, url, interval, note, kind="feed"):
    def deco(fn):
        SOURCES[sid] = {"id": sid, "name": name, "url": url, "interval": interval, "note": note,
                        "kind": kind, "fn": fn}
        STATUS[sid] = {"ok": None, "last_ok": None, "last_try": None, "error": None, "count": 0, "ms": None,
                       "fails": 0, "next_try": None}
        return fn
    return deco


# (id, name, url, default game tags, strict relevance filter, poll seconds, terms mode)
# Terms checked 2026-11-23 (see README "Sources"). Publisher feeds allow personal, non-commercial use:
# keep the dashboard private and show headline + link + date only.
RSS_FEEDS = [
    ("hltv", "HLTV news", "https://www.hltv.org/rss/news", ["cs2"], False, 3600,
     "terms page unreadable (403); treat as link-only; feed ttl 60 min"),
    ("vlr", "VLR.gg news", "https://www.vlr.gg/rss", ["valorant"], False, 900, "personal, non-commercial use only"),
    ("dexerto", "Dexerto", "https://www.dexerto.com/feed/", [], True, 600, "domestic and private use only"),
    ("gi_biz", "GamesIndustry.biz", "https://www.gamesindustry.biz/feed", [], False, 600,
     "private, non-commercial use only"),
    ("eurogamer", "Eurogamer", "https://www.eurogamer.net/feed", [], True, 600, "private, non-commercial use only"),
    ("pcgamer", "PC Gamer", "https://www.pcgamer.com/rss/", [], True, 900, "Future plc: personal use only"),
    ("gamedev", "Game Developer", "https://www.gamedeveloper.com/rss.xml", [], False, 900,
     "Informa: personal, non-commercial use"),
]


def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        return int(parsedate_to_datetime(s).timestamp())
    except Exception:
        pass
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def parse_feed(xml_text):
    xml_text = re.sub(r"^[^<]+", "", xml_text)
    root = ET.fromstring(xml_text.encode("utf-8"))
    out = []
    atom = "{http://www.w3.org/2005/Atom}"
    for it in root.iter("item"):
        out.append({
            "title": strip_html(it.findtext("title")),
            "url": (it.findtext("link") or "").strip(),
            "ts": parse_date(it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date")),
            "summary": strip_html(it.findtext("description"))[:260],
        })
    for it in root.iter(atom + "entry"):
        link = it.find(atom + "link")
        out.append({
            "title": strip_html(it.findtext(atom + "title")),
            "url": link.get("href") if link is not None else "",
            "ts": parse_date(it.findtext(atom + "updated") or it.findtext(atom + "published")),
            "summary": strip_html(it.findtext(atom + "summary") or it.findtext(atom + "content"))[:260],
        })
    return [o for o in out if o["title"] and o["url"]]


def store_items(src_name, items, default_games=(), strict=False):
    now = int(time.time())
    kept = 0
    for it in items:
        iid = hashlib.sha1(it["url"].encode()).hexdigest()[:16]
        tags = classify(it["title"] + " " + it.get("summary", ""), default_games, strict)
        if tags is None:
            continue
        games, sig, places = tags
        if it.get("country"):
            p = PLACES.get(it["country"].lower())
            if p and not places:
                places = [{"name": it["country"], "lat": p[0], "lon": p[1], "region": p[2], "via": "source country"}]
        ts = it.get("ts") or now
        cur = q("INSERT OR IGNORE INTO items(id,source,title,url,ts,first_seen,summary,games,signals,places) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (iid, src_name, it["title"], it["url"], min(ts, now), now, it.get("summary", ""),
                 json.dumps(games), json.dumps(sig), json.dumps(places)), commit=True)
        kept += 1
    return kept


def publish_news():
    since = int(time.time()) - 7 * 86400
    rows = q("SELECT * FROM items WHERE ts>=? ORDER BY ts DESC LIMIT 600", (since,))
    items = [{
        "id": r["id"], "source": r["source"], "title": r["title"], "url": r["url"], "ts": r["ts"],
        "first_seen": r["first_seen"], "summary": r["summary"], "games": json.loads(r["games"]),
        "signals": json.loads(r["signals"]), "places": json.loads(r["places"]),
    } for r in rows]
    publish("news", items)


def make_rss_job(sid, name, url, games, strict, interval, terms):
    note = "Headline + link only; " + terms + ("; gaming-relevance filter on" if strict else "")

    @source("rss:" + sid, name, url, interval, note)
    def job():
        items = parse_feed(fetch(url))
        kept = store_items(name, items, games, strict)
        publish_news()
        return kept
    return job


for _f in RSS_FEEDS:
    make_rss_job(*_f)


@source("gdelt", "GDELT global news (esports)", "https://api.gdeltproject.org/api/v2/doc/doc", 1800,
        "GDELT: free use with citation and link to gdeltproject.org; max 1 request / 5 s")
def job_gdelt():
    qs = urllib.parse.urlencode({"query": '(esports OR "e-sports" OR "esport")', "mode": "artlist",
                                 "format": "json", "maxrecords": "75", "timespan": "24h", "sort": "datedesc"})
    txt = fetch("https://api.gdeltproject.org/api/v2/doc/doc?" + qs, timeout=40)
    if not txt.lstrip().startswith("{"):
        raise RuntimeError(txt.strip()[:120])
    arts = json.loads(txt).get("articles", [])
    items = []
    for a in arts:
        sd = a.get("seendate", "")
        try:
            ts = int(datetime.strptime(sd, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            ts = None
        items.append({"title": a.get("title", ""), "url": a.get("url", ""), "ts": ts,
                      "summary": a.get("domain", ""), "country": a.get("sourcecountry", "")})
    kept = store_items("GDELT", [i for i in items if i["title"]], strict=True)
    publish_news()
    return kept


# ---- League of Legends (official lolesports API)
LOL_LEAGUE_HOME = {  # league slug -> (lat, lon, region, label) ; studio city, approximate
    "lck": (37.57, 126.98, "APAC", "Seoul"), "lpl": (31.23, 121.47, "APAC", "Shanghai"),
    "lec": (52.52, 13.4, "EMEA", "Berlin"), "lta_n": (34.05, -118.24, "Americas", "Los Angeles"),
    "lta_s": (-23.55, -46.63, "Americas", "São Paulo"), "lta_north": (34.05, -118.24, "Americas", "Los Angeles"),
    "lta_south": (-23.55, -46.63, "Americas", "São Paulo"), "lcs": (34.05, -118.24, "Americas", "Los Angeles"),
    "cblol-brazil": (-23.55, -46.63, "Americas", "São Paulo"), "cblol": (-23.55, -46.63, "Americas", "São Paulo"),
    "lcp": (25.03, 121.56, "APAC", "Taipei"), "vcs": (10.82, 106.63, "APAC", "Ho Chi Minh City"),
    "ljl-japan": (35.68, 139.69, "APAC", "Tokyo"), "lfl": (48.86, 2.35, "EMEA", "Paris"),
    "primeleague": (50.94, 6.96, "EMEA", "Cologne"), "lla": (19.43, -99.13, "Americas", "Mexico City"),
    "turkiye-sampiyonluk-ligi": (41.01, 28.98, "EMEA", "Istanbul"), "superliga": (40.42, -3.7, "EMEA", "Madrid"),
    "nlc": (51.51, -0.13, "EMEA", "London"), "lck_challengers_league": (37.57, 126.98, "APAC", "Seoul"),
    "ldl": (31.23, 121.47, "APAC", "Shanghai"), "emea_masters": (52.52, 13.4, "EMEA", "Berlin"),
}


def _lol_event(e):
    m = e.get("match") or {}
    teams = [{"name": t.get("name"), "code": t.get("code"), "image": t.get("image"),
              "wins": (t.get("result") or {}).get("gameWins"), "outcome": (t.get("result") or {}).get("outcome")}
             for t in m.get("teams", [])]
    lg = e.get("league") or {}
    home = LOL_LEAGUE_HOME.get(lg.get("slug", ""))
    return {
        "game": "lol", "id": m.get("id") or e.get("id"), "start": parse_date(e.get("startTime")),
        "state": e.get("state"), "league": lg.get("name"), "slug": lg.get("slug"), "block": e.get("blockName"),
        "bestOf": (m.get("strategy") or {}).get("count"), "teams": teams,
        "region": home[2] if home else None,
        "geo": {"lat": home[0], "lon": home[1], "label": home[3]} if home else None,
        "url": "https://lolesports.com/schedule",
    }


@source("lol_schedule", "LoL Esports schedule", "https://esports-api.lolesports.com/persisted/gw/getSchedule", 600,
        "UNOFFICIAL: lolesports site key, not a Riot-issued key; Riot terms do not cover it and it may be revoked")
def job_lol_schedule():
    h = {"x-api-key": LOL_KEY}
    d = fetch_json("https://esports-api.lolesports.com/persisted/gw/getSchedule?hl=en-US", headers=h)
    sched = d["data"]["schedule"]
    events = [e for e in sched["events"] if e.get("type") == "match"]
    older = (sched.get("pages") or {}).get("older")
    if older:  # one more page of results for context
        try:
            d2 = fetch_json("https://esports-api.lolesports.com/persisted/gw/getSchedule?hl=en-US&pageToken="
                            + urllib.parse.quote(older), headers=h)
            events = [e for e in d2["data"]["schedule"]["events"] if e.get("type") == "match"] + events
        except Exception:
            pass
    out = [_lol_event(e) for e in events]
    publish("lol_schedule", out)
    return len(out)


@source("lol_live", "LoL Esports live", "https://esports-api.lolesports.com/persisted/gw/getLive", 60,
        "UNOFFICIAL: lolesports site key, not a Riot-issued key; Riot terms do not cover it and it may be revoked")
def job_lol_live():
    d = fetch_json("https://esports-api.lolesports.com/persisted/gw/getLive?hl=en-US", headers={"x-api-key": LOL_KEY})
    evs = ((d.get("data") or {}).get("schedule") or {}).get("events") or []
    out = [_lol_event(e) for e in evs if e.get("type") == "match"]
    publish("lol_live", out)
    return len(out)


# ---- Dota 2 (OpenDota)
@source("dota_live", "Dota 2 live games (OpenDota)", "https://api.opendota.com/api/live", 60,
        "OpenDota: no data licence published; free tier 60/min, 3000/day")
def job_dota_live():
    d = fetch_json("https://api.opendota.com/api/live")
    pro, pubs = [], []
    for g in d:
        row = {"game": "dota2", "id": g.get("match_id"), "league_id": g.get("league_id"),
               "radiant": g.get("team_name_radiant") or "Radiant", "dire": g.get("team_name_dire") or "Dire",
               "rk": g.get("radiant_score"), "dk": g.get("dire_score"), "lead": g.get("radiant_lead"),
               "clock": g.get("game_time"), "spectators": g.get("spectators"), "mmr": g.get("average_mmr"),
               "url": "https://www.opendota.com/matches/%s" % g.get("match_id")}
        (pro if (g.get("league_id") or g.get("team_name_radiant")) else pubs).append(row)
    pubs.sort(key=lambda r: -(r["spectators"] or 0))
    publish("dota_live", {"pro": pro, "top_pubs": pubs[:6]})
    return len(pro)


@source("dota_results", "Dota 2 pro results (OpenDota)", "https://api.opendota.com/api/proMatches", 300,
        "OpenDota: no data licence published; free tier 60/min, 3000/day")
def job_dota_results():
    d = fetch_json("https://api.opendota.com/api/proMatches")
    out = [{"game": "dota2", "id": m["match_id"], "end": (m.get("start_time") or 0) + (m.get("duration") or 0),
            "league": m.get("league_name"), "radiant": m.get("radiant_name") or "Radiant",
            "dire": m.get("dire_name") or "Dire", "rk": m.get("radiant_score"), "dk": m.get("dire_score"),
            "radiant_win": m.get("radiant_win"), "url": "https://www.opendota.com/matches/%s" % m["match_id"]}
           for m in d[:40]]
    publish("dota_results", out)
    return len(out)


# ---- Steam
TRACKED = [
    (730, "Counter-Strike 2", "cs2"), (570, "Dota 2", "dota2"), (578080, "PUBG: BATTLEGROUNDS", "pubg"),
    (1172470, "Apex Legends", "apex"), (2357570, "Overwatch 2", "ow"), (2767030, "Marvel Rivals", "marvel"),
    (359550, "Rainbow Six Siege", "r6"), (252950, "Rocket League", "rl"), (1364780, "Street Fighter 6", "fgc"),
    (1778820, "TEKKEN 8", "fgc"), (1422450, "Deadlock", "deadlock"), (252490, "Rust", None),
]


def app_name(appid, fallback=None):
    r = q("SELECT name FROM apps WHERE appid=?", (appid,))
    if r:
        return r[0]["name"]
    try:
        d = fetch_json("https://store.steampowered.com/api/appdetails?appids=%d&filters=basic" % appid)
        name = d[str(appid)]["data"]["name"]
        q("INSERT OR REPLACE INTO apps VALUES(?,?)", (appid, name), commit=True)
        time.sleep(0.15 if SERVERLESS else 1.2)
        return name
    except Exception:
        return fallback or "App %d" % appid


def forecast(points, step=300):
    """Seasonal-naive (24 h) forecast 1 h ahead, scaled by the latest level ratio.
    Backtested on the stored history; band = 80th percentile of absolute % errors."""
    if len(points) < 12:
        return None
    ts = [p[0] for p in points]
    vs = [p[1] for p in points]

    def at(t):  # nearest sample within 20 min
        import bisect
        i = bisect.bisect_left(ts, t)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(ts) and abs(ts[j] - t) <= 1200:
                if best is None or abs(ts[j] - t) < abs(ts[best] - t):
                    best = j
        return vs[best] if best is not None else None

    now_t, now_v = ts[-1], vs[-1]
    day = 86400
    method = "seasonal-naive 24h x level ratio"
    base_prev, base_now, target_prev = at(now_t - day), now_v, at(now_t + 3600 - day)
    if base_prev and target_prev:
        est = target_prev * (base_now / base_prev)
    else:
        # fallback: linear trend over the last hour
        h = at(now_t - 3600)
        if not h:
            return None
        est = now_v + (now_v - h)
        method = "linear 1h trend (under 24h of history)"
    # backtest: replay the same rule over history
    errs, base_errs = [], []
    for k in range(len(ts)):
        t0 = ts[k]
        if t0 + 3600 > now_t:
            break
        actual = at(t0 + 3600)
        if not actual:
            continue
        if base_prev and target_prev:
            a, b = at(t0 - day), at(t0 + 3600 - day)
            if not (a and b):
                continue
            pred = b * (vs[k] / a)
        else:
            h = at(t0 - 3600)
            if not h:
                continue
            pred = vs[k] + (vs[k] - h)
        errs.append(abs(pred - actual) / max(actual, 1))
        base_errs.append(abs(vs[k] - actual) / max(actual, 1))  # persistence baseline: "same as now"
    n = len(errs)
    if n < 6:
        return {"hidden": True, "reason": "backtest needs at least 6 points (has %d)" % n, "n_backtest": n,
                "method": method}
    mape, base = sum(errs) / n, sum(base_errs) / n
    if mape >= base:  # quality gate: a forecast that does not beat its baseline is not drawn
        return {"hidden": True, "n_backtest": n, "method": method, "mape": round(mape, 4),
                "baseline_mape": round(base, 4),
                "reason": "does not beat the 'same as now' baseline in backtest (%.1f%% vs %.1f%%)"
                          % (mape * 100, base * 100)}
    band = sorted(errs)[int(0.8 * (n - 1))]
    return {"at": now_t + 3600, "value": round(est), "low": round(est * (1 - band)), "high": round(est * (1 + band)),
            "mape": round(mape, 4), "baseline_mape": round(base, 4), "n_backtest": n, "method": method}


@source("steam_players", "Steam concurrent players (tracked titles)",
        "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/", 300,
        "Steam Web API terms: personal use, no Valve endorsement; Steam only, excludes other platforms")
def job_steam_players():
    now = int(time.time())
    rows = []
    for appid, label, gid in TRACKED:
        try:
            d = fetch_json("https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid=%d" % appid)
            n = d["response"].get("player_count")
        except Exception:
            n = None
        if n is not None:
            q("INSERT INTO series VALUES(?,?,?)", ("steam:%d" % appid, now, n), commit=True)
        rows.append((appid, label, gid, n))
        time.sleep(0.3)
    q("DELETE FROM series WHERE ts<?", (now - 21 * 86400,), commit=True)
    out = []
    for appid, label, gid, n in rows:
        pts = [(r["ts"], r["value"]) for r in
               q("SELECT ts,value FROM series WHERE key=? AND ts>=? ORDER BY ts", ("steam:%d" % appid, now - 8 * 86400))]
        last24 = [p for p in pts if p[0] >= now - 86400]
        prev = [p for p in pts if now - 7200 <= p[0] <= now - 3000]
        out.append({
            "appid": appid, "name": label, "game": gid, "now": n,
            "peak24": max((p[1] for p in last24), default=None),
            "chg1h": (n - prev[-1][1]) / prev[-1][1] if (n and prev and prev[-1][1]) else None,
            "spark": [[p[0], p[1]] for p in last24][::2],
            "forecast": forecast(pts),
            "url": "https://steamdb.info/app/%d/charts/" % appid,
        })
    publish("steam_players", out)
    return len(out)


@source("steam_top", "Steam most played (daily rollup)",
        "https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/", 3600,
        "Steam Web API terms: personal use, no Valve endorsement")
def job_steam_top():
    d = fetch_json("https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/")
    r = d["response"]
    out = []
    for x in r["ranks"][:25]:
        out.append({"rank": x["rank"], "last_week": x.get("last_week_rank"), "appid": x["appid"],
                    "peak": x.get("peak_in_game"), "name": app_name(x["appid"]),
                    "url": "https://store.steampowered.com/app/%d" % x["appid"]})
    publish("steam_top", {"date": r.get("rollup_date"), "ranks": out})
    return len(out)


@source("steam_store", "Steam store: new, top sellers, coming soon",
        "https://store.steampowered.com/api/featuredcategories", 1800, "Undocumented Steam store endpoint; not covered by the Web API terms (unverified)")
def job_steam_store():
    d = fetch_json("https://store.steampowered.com/api/featuredcategories?cc=us&l=english")
    out = {}
    for key in ("new_releases", "top_sellers", "coming_soon", "specials"):
        items = (d.get(key) or {}).get("items") or []
        seen, rows = set(), []
        for it in items:
            if it["id"] in seen:
                continue
            seen.add(it["id"])
            rows.append({"id": it["id"], "name": it.get("name"), "discount": it.get("discount_percent"),
                         "price": (it.get("final_price") or 0) / 100, "currency": it.get("currency"),
                         "img": it.get("small_capsule_image") or it.get("header_image"),
                         "url": "https://store.steampowered.com/app/%s" % it["id"]})
        out[key] = rows[:15]
    publish("steam_store", out)
    return sum(len(v) for v in out.values())


@source("sdr", "Valve server relay POPs (map layer)", "https://api.steampowered.com/ISteamApps/GetSDRConfig/v1/",
        86400, "Steam Datagram Relay config for CS2; locations published by Valve")
def job_sdr():
    d = fetch_json("https://api.steampowered.com/ISteamApps/GetSDRConfig/v1/?appid=730")
    out = [{"code": k, "desc": v.get("desc"), "lon": v["geo"][0], "lat": v["geo"][1],
            "relays": len(v.get("relays") or [])} for k, v in d["pops"].items() if v.get("geo")]
    publish("sdr", out)
    return len(out)


# ---- deals and free games
@source("deals", "CheapShark deals (Steam)", "https://www.cheapshark.com/api/1.0/deals", 1800,
        "CheapShark: free; deal links must be CheapShark links; credit appreciated")
def job_deals():
    d = fetch_json("https://www.cheapshark.com/api/1.0/deals?storeID=1&sortBy=Deal%20Rating&pageSize=20")
    out = [{"title": x["title"], "sale": float(x["salePrice"]), "normal": float(x["normalPrice"]),
            "savings": round(float(x["savings"])), "meta": x.get("metacriticScore"),
            "steam_rating": x.get("steamRatingPercent"), "thumb": x.get("thumb"),
            "url": "https://www.cheapshark.com/redirect?dealID=" + x["dealID"]} for x in d]
    publish("deals", out)
    return len(out)


@source("epic_free", "Epic Games Store free games",
        "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions", 3600, "Undocumented Epic endpoint; terms unverified (legal page needs login)")
def job_epic():
    d = fetch_json("https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale=en-US&country=US")
    els = d["data"]["Catalog"]["searchStore"]["elements"]
    out = []
    for e in els:
        promo = e.get("promotions") or {}
        for kind in ("promotionalOffers", "upcomingPromotionalOffers"):
            for grp in promo.get(kind) or []:
                for off in grp.get("promotionalOffers") or []:
                    if (off.get("discountSetting") or {}).get("discountPercentage") != 0:
                        continue
                    slug = (e.get("productSlug") or "") or next(
                        (m.get("pageSlug") for m in (e.get("catalogNs") or {}).get("mappings") or []), "")
                    out.append({"title": e["title"], "kind": "now" if kind == "promotionalOffers" else "next",
                                "start": parse_date(off.get("startDate")), "end": parse_date(off.get("endDate")),
                                "url": "https://store.epicgames.com/p/" + slug if slug else
                                "https://store.epicgames.com/free-games"})
    publish("epic_free", out)
    return len(out)


# ---- markets
TICKERS = [("TTWO", "Take-Two"), ("EA", "Electronic Arts"), ("RBLX", "Roblox"), ("U", "Unity"),
           ("NTES", "NetEase"), ("SONY", "Sony"), ("NTDOY", "Nintendo ADR"), ("TCEHY", "Tencent ADR"),
           ("UBI.PA", "Ubisoft"), ("CDR.WA", "CD Projekt"), ("CRSR", "Corsair"), ("LOGI", "Logitech")]


# Yahoo's terms (2(d)(ix), 2(e)) ban automated collection, so this source is OFF unless ARENA_YAHOO=1.
YAHOO_ON = os.environ.get("ARENA_YAHOO") == "1"


def job_markets():
    out = []
    for sym, label in TICKERS:
        try:
            d = fetch_json("https://query1.finance.yahoo.com/v8/finance/chart/%s?range=1mo&interval=1d"
                           % urllib.parse.quote(sym))
            r = d["chart"]["result"][0]
            meta = r["meta"]
            closes = [c for c in (r["indicators"]["quote"][0].get("close") or []) if c is not None]
            price = meta.get("regularMarketPrice")
            prev = closes[-2] if len(closes) >= 2 else meta.get("chartPreviousClose")
            out.append({"sym": sym, "name": label, "price": price, "cur": meta.get("currency"),
                        "chg": (price - prev) / prev if price and prev else None,
                        "chg1m": (price - closes[0]) / closes[0] if price and closes else None,
                        "spark": [round(c, 2) for c in closes],
                        "time": meta.get("regularMarketTime"),
                        "url": "https://finance.yahoo.com/quote/" + sym})
        except Exception as e:
            out.append({"sym": sym, "name": label, "error": str(e)[:80]})
        time.sleep(0.8)
    publish("markets", out)
    if all("error" in o for o in out):
        raise RuntimeError("all tickers failed")
    return len(out)


if YAHOO_ON:
    source("markets", "Gaming equities (Yahoo Finance chart)", "https://query1.finance.yahoo.com/v8/finance/chart/",
           600, "OPT-IN: Yahoo terms ban automated collection; delayed quotes; your own risk")(job_markets)


# ---- platform status and hazards
STATUS_PAGES = [("Discord", "https://discordstatus.com"), ("Epic Games", "https://status.epicgames.com"),
                ("Twitch", "https://status.twitch.com")]


@source("status", "Platform status pages", "statuspage.io /api/v2/summary.json", 120,
        "Public Statuspage summaries (made for this use); no published terms")
def job_status():
    out = []
    for name, base in STATUS_PAGES:
        try:
            d = fetch_json(base + "/api/v2/summary.json")
            bad = [c["name"] for c in d.get("components", []) if c.get("status") != "operational" and not c.get("group")]
            out.append({"name": name, "indicator": d["status"]["indicator"], "desc": d["status"]["description"],
                        "degraded": bad[:6], "incidents": [{"name": i["name"], "impact": i.get("impact"),
                                                            "status": i.get("status"), "url": i.get("shortlink")}
                                                           for i in d.get("incidents", [])[:3]], "url": base})
        except Exception as e:
            out.append({"name": name, "indicator": "unknown", "desc": "unreachable: %s" % str(e)[:60], "url": base,
                        "degraded": [], "incidents": []})
    publish("status", out)
    return len(out)


@source("quakes", "USGS earthquakes M4.5+ (24 h)",
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson", 900, "USGS: public domain; credit USGS")
def job_quakes():
    d = fetch_json("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson")
    out = [{"mag": f["properties"]["mag"], "place": f["properties"]["place"], "ts": f["properties"]["time"] // 1000,
            "lon": f["geometry"]["coordinates"][0], "lat": f["geometry"]["coordinates"][1],
            "url": f["properties"]["url"]} for f in d["features"]]
    publish("quakes", out)
    return len(out)


# ---- optional: Twitch (needs app credentials)
_twitch_token = {"v": None, "exp": 0}
TW_ID, TW_SECRET = os.environ.get("TWITCH_CLIENT_ID"), os.environ.get("TWITCH_CLIENT_SECRET")
if TW_ID and TW_SECRET:
    @source("twitch", "Twitch top streams", "https://api.twitch.tv/helix/streams", 180, "Twitch Helix API (app token)")
    def job_twitch():
        if time.time() > _twitch_token["exp"] - 300:
            body = urllib.parse.urlencode({"client_id": TW_ID, "client_secret": TW_SECRET,
                                           "grant_type": "client_credentials"}).encode()
            t = json.loads(fetch("https://id.twitch.tv/oauth2/token", data=body))
            _twitch_token.update(v=t["access_token"], exp=time.time() + t["expires_in"])
        h = {"Client-Id": TW_ID, "Authorization": "Bearer " + _twitch_token["v"]}
        d = fetch_json("https://api.twitch.tv/helix/streams?first=30", headers=h)
        out = [{"user": s["user_login"], "name": s["user_name"], "game": s["game_name"], "viewers": s["viewer_count"],
                "title": s["title"], "lang": s["language"], "url": "https://twitch.tv/" + s["user_login"]}
               for s in d["data"]]
        publish("twitch", out)
        return len(out)


# ---- optional: PandaScore (all games incl. CS2 / Valorant; free token at pandascore.co)
PS_TOKEN = os.environ.get("PANDASCORE_TOKEN")
PS_GAME = {"cs-go": "cs2", "cs-2": "cs2", "csgo": "cs2", "league-of-legends": "lol", "lol": "lol", "valorant": "valorant",
           "dota-2": "dota2", "dota2": "dota2", "r6-siege": "r6", "rl": "rl", "rocket-league": "rl", "ow": "ow",
           "overwatch": "ow", "pubg": "pubg", "cod-mw": "cod", "call-of-duty": "cod", "mobile-legends": "mobile",
           "kog": "mobile", "fifa": None, "starcraft-2": None}


def _ps_match(m):
    vg = (m.get("videogame") or {})
    scores = {r.get("team_id"): r.get("score") for r in m.get("results") or []}
    teams = []
    for o in m.get("opponents") or []:
        t = o.get("opponent") or {}
        outcome = None
        if m.get("winner_id") is not None and m.get("status") == "finished":
            outcome = "win" if t.get("id") == m.get("winner_id") else "loss"
        teams.append({"name": t.get("name"), "code": t.get("acronym") or t.get("name"), "image": t.get("image_url"),
                      "wins": scores.get(t.get("id")), "outcome": outcome})
    state = {"running": "inProgress", "not_started": "unstarted", "finished": "completed"}.get(m.get("status"),
                                                                                                m.get("status"))
    stream = next((s.get("raw_url") for s in m.get("streams_list") or [] if s.get("main")), None)
    league = " · ".join(x for x in [(m.get("league") or {}).get("name"), (m.get("serie") or {}).get("full_name")] if x)
    return {"game": PS_GAME.get(vg.get("slug"), vg.get("slug")), "game_name": vg.get("name"), "src": "PandaScore",
            "id": "ps%s" % m.get("id"), "start": parse_date(m.get("begin_at") or m.get("scheduled_at")),
            "state": state, "league": league, "block": (m.get("tournament") or {}).get("name"),
            "bestOf": m.get("number_of_games"), "teams": teams, "region": None, "geo": None,
            "url": stream or "https://www.pandascore.co"}


def _ps_get(path):
    return fetch_json("https://api.pandascore.co" + path, headers={"Authorization": "Bearer " + PS_TOKEN,
                                                                   "Accept": "application/json"})


if PS_TOKEN:
    @source("ps_live", "PandaScore live matches (all games)", "https://api.pandascore.co/matches/running", 90,
            "PandaScore API, free tier (1000 req/h)")
    def job_ps_live():
        out = [_ps_match(m) for m in _ps_get("/matches/running?per_page=50")]
        publish("ps_live", out)
        return len(out)

    @source("ps_sched", "PandaScore upcoming + past (all games)", "https://api.pandascore.co/matches", 900,
            "PandaScore API, free tier (1000 req/h)")
    def job_ps_sched():
        up = _ps_get("/matches/upcoming?per_page=100&sort=begin_at")
        past = _ps_get("/matches/past?per_page=100&sort=-end_at")
        out = [_ps_match(m) for m in up + past]
        publish("ps_sched", out)
        return len(out)


# ---- optional: AI brief written by Claude from the cited headlines (needs Anthropic credentials)
AI_ON = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
             or os.environ.get("ARENA_AI_BRIEF"))
AI_MODEL = os.environ.get("ARENA_AI_MODEL", "claude-opus-5-5")
_ai_last = {"sig": None}

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "why_it_matters": {"type": "string"},
                "audience": {"type": "string", "enum": ["fans", "teams", "publishers", "sponsors", "everyone"]},
                "sources": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["headline", "why_it_matters", "audience", "sources"],
            "additionalProperties": False}},
    },
    "required": ["items"],
    "additionalProperties": False,
}
BRIEF_SYSTEM = (
    "You write a short brief for an esports and gaming monitoring dashboard. You get numbered headlines from the "
    "last 24 hours, each with its outlet and time. Pick the five items that matter most to people who follow or "
    "work in esports and games: results and roster moves in top-tier competition, business events (deals, funding, "
    "layoffs, earnings), integrity cases, big releases and outages. Merge items that report the same story. "
    "Use only facts stated in the headlines and summaries. Do not add facts, numbers or names that are not there. "
    "Write each headline in at most 14 words and each 'why it matters' line in at most 30 words, in plain English. "
    "List in 'sources' the numbers of every headline that supports the item."
)

if AI_ON:
    @source("ai_brief", "AI brief (Claude, cited)", "Anthropic Messages API", 1800,
            "Written by %s from the numbered headlines; each item lists its sources" % AI_MODEL)
    def job_ai_brief():
        import anthropic  # imported lazily so the server runs without the SDK
        since = int(time.time()) - 86400
        rows = q("SELECT id,source,title,url,ts,summary FROM items WHERE ts>=? ORDER BY ts DESC LIMIT 80", (since,))
        if not rows:
            return 0
        sig = hashlib.sha1("".join(r["id"] for r in rows).encode()).hexdigest()
        if sig == _ai_last["sig"] and STATE.get("ai_brief"):
            return len(STATE["ai_brief"]["items"])  # no new headlines: keep the last brief, spend nothing
        lines = ["[%d] %s | %s | %s | %s" % (i + 1, r["source"], datetime.fromtimestamp(r["ts"], timezone.utc)
                                              .strftime("%Y-%m-%d %H:%M UTC"), r["title"], (r["summary"] or "")[:160])
                 for i, r in enumerate(rows)]
        client = anthropic.Anthropic()
        resp = client.beta.messages.create(
            model=AI_MODEL, max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": BRIEF_SCHEMA}},
            system=BRIEF_SYSTEM,
            messages=[{"role": "user", "content": "Headlines:\n" + "\n".join(lines)}],
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("model declined (%s)" % getattr(resp.stop_details, "category", None))
        text = next(b.text for b in resp.content if b.type == "text")
        data = json.loads(text)
        items = []
        for it in data["items"][:5]:
            cites = [{"source": rows[n - 1]["source"], "title": rows[n - 1]["title"], "url": rows[n - 1]["url"],
                      "ts": rows[n - 1]["ts"]} for n in it["sources"] if 1 <= n <= len(rows)]
            if not cites:  # an item with no valid citation is dropped, never shown uncited
                continue
            items.append({"headline": it["headline"], "why": it["why_it_matters"], "audience": it["audience"],
                          "cites": cites})
        _ai_last["sig"] = sig
        publish("ai_brief", {"at": int(time.time()), "model": resp.model, "items": items,
                             "usage": {"in": resp.usage.input_tokens, "out": resp.usage.output_tokens}})
        return len(items)


# ----------------------------------------------------------------------------- scheduler
def run_job(sid):
    s = SOURCES[sid]
    st = STATUS[sid]
    t0 = time.time()
    st["last_try"] = int(t0)
    try:
        n = s["fn"]()
        st.update(ok=True, last_ok=int(time.time()), error=None, count=n, ms=int((time.time() - t0) * 1000),
                  fails=0, next_try=None)
    except Exception as e:
        st["fails"] = st.get("fails", 0) + 1
        st.update(ok=False, error="%s: %s" % (type(e).__name__, str(e)[:160]), ms=int((time.time() - t0) * 1000))
        if os.environ.get("DEBUG"):
            traceback.print_exc()
    publish("sources", sources_payload())


def loop(sid, delay):
    time.sleep(delay)
    while True:
        run_job(sid)
        interval = SOURCES[sid]["interval"]
        st = STATUS[sid]
        if st["ok"] is False:
            if "429" in (st["error"] or "") or "limit requests" in (st["error"] or ""):
                # rate-limited: back off 15, 30, 60 ... minutes, capped at 2 h
                interval = min(900 * 2 ** (st["fails"] - 1), 7200)
            else:
                interval = min(interval, 300)  # transient error: retry sooner
            st["next_try"] = int(time.time() + interval)
        time.sleep(interval)


def sources_payload():
    return [{k: v for k, v in s.items() if k != "fn"} | STATUS[sid] for sid, s in SOURCES.items()]


_due_lock = threading.Lock()
_running = set()


def _next_due(sid):
    st, interval = STATUS[sid], SOURCES[sid]["interval"]
    if st["last_try"] is None:
        return 0
    if st["ok"] is False:
        if "429" in (st["error"] or "") or "limit requests" in (st["error"] or ""):
            interval = min(900 * 2 ** max(st["fails"] - 1, 0), 7200)
        else:
            interval = min(interval, 300)
    return st["last_try"] + interval


def ensure_fresh(budget=25.0):
    """Serverless: run every due source in parallel threads; wait at most `budget` seconds.
    Sources still running when the budget ends keep going while the instance stays warm."""
    now = time.time()
    started = []
    with _due_lock:
        if not STATE.get("news"):
            publish_news()
        for sid in SOURCES:
            if sid in _running or sid == "ai_brief" and not STATE.get("news"):
                continue
            if _next_due(sid) <= now:
                _running.add(sid)
                t = threading.Thread(target=_run_tracked, args=(sid,), daemon=True)
                t.start()
                started.append(t)
    end = now + budget
    for t in started:
        t.join(max(0.0, end - time.time()))


def _run_tracked(sid):
    try:
        run_job(sid)
    finally:
        with _due_lock:
            _running.discard(sid)


def start_jobs():
    publish_news()
    for i, sid in enumerate(SOURCES):
        delay = 90 if sid == "gdelt" else 120 if sid == "ai_brief" else i * 0.7  # GDELT is strict; brief waits for news
        threading.Thread(target=loop, args=(sid, delay), daemon=True, name=sid).start()


# ----------------------------------------------------------------------------- HTTP server
MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
        ".json": "application/json", ".jpg": "image/jpeg", ".webp": "image/webp"}


PAGES = {"/": "/landing.html", "/app": "/index.html", "/sign-in": "/sign-in.html"}


def public_stats():
    """Counts only (no feed content) for the public landing page. Every number comes from live data."""
    if SERVERLESS:
        ensure_fresh(budget=8)
    with LOCK:
        st = dict(STATE)
    now = int(time.time())
    news = st.get("news") or []
    players = [r for r in (st.get("steam_players") or []) if r.get("now")]
    top = max(players, key=lambda r: r["now"]) if players else None
    lol = [m for m in (st.get("lol_schedule") or []) if m.get("state") == "completed" and (m.get("start") or 0) >= now - 86400]
    dota = [m for m in (st.get("dota_results") or []) if (m.get("end") or 0) >= now - 86400]
    dl = st.get("dota_live") or {}
    srcs = sources_payload()
    return {
        "sources_ok": sum(1 for x in srcs if x["ok"]), "sources_total": len(srcs),
        "headlines_24h": sum(1 for i in news if i["ts"] >= now - 86400),
        "signals_24h": sum(1 for i in news if i["ts"] >= now - 86400 and i["signals"]),
        "matches_24h": len(lol) + len(dota),
        "live_games": len(dl.get("pro") or []) + len(dl.get("top_pubs") or []),
        "steam_players_tracked": sum(r["now"] for r in players),
        "top_game": {"name": top["name"], "players": top["now"]} if top else None,
        "updated": now,
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("DEBUG"):
            super().log_message(fmt, *args)

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        p = u.path
        if p == "/api/state":
            if SERVERLESS:
                ensure_fresh()
            with LOCK:
                snap = dict(STATE)
            snap["sources"] = sources_payload()
            snap["lexicon"] = {"games": {k: v[0] for k, v in GAMES.items()}, "signals": list(SIGNALS)}
            snap["server_time"] = int(time.time())
            snap["mode"] = "serverless" if SERVERLESS else "server"
            return self._send(200, json.dumps(snap))
        if p == "/api/stream":
            if SERVERLESS:
                return self._send(404, '{"error":"no stream in serverless mode; poll /api/state"}')
            return self.stream()
        if p == "/api/series":
            qs = urllib.parse.parse_qs(u.query)
            key = qs.get("key", [""])[0]
            hours = min(int(qs.get("hours", ["48"])[0]), 24 * 21)
            rows = q("SELECT ts,value FROM series WHERE key=? AND ts>=? ORDER BY ts", (key, int(time.time()) - hours * 3600))
            return self._send(200, json.dumps([[r["ts"], r["value"]] for r in rows]))
        if p == "/api/refresh":
            sid = urllib.parse.parse_qs(u.query).get("source", [""])[0]
            if sid in SOURCES:
                if SERVERLESS:
                    run_job(sid)
                    return self._send(200, '{"done":true}')
                threading.Thread(target=run_job, args=(sid,), daemon=True).start()
                return self._send(202, '{"queued":true}')
            return self._send(404, '{"error":"unknown source"}')
        if p == "/api/public":
            return self._send(200, json.dumps(public_stats()))
        # pages: landing at /, dashboard at /app, sign-in at /sign-in
        p = PAGES.get(p.rstrip("/") or "/", p)
        fp = os.path.normpath(os.path.join(STATIC, p.lstrip("/")))
        if not fp.startswith(STATIC) or not os.path.isfile(fp):
            return self._send(404, "not found", "text/plain")
        with open(fp, "rb") as f:
            self._send(200, f.read(), MIME.get(os.path.splitext(fp)[1], "application/octet-stream"))

    def stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        sq = queue.Queue(maxsize=200)
        with LOCK:
            SUBSCRIBERS.add(sq)
        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            while True:
                try:
                    msg = sq.get(timeout=15)
                    self.wfile.write(b"data: " + msg.encode("utf-8") + b"\n\n")
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            with LOCK:
                SUBSCRIBERS.discard(sq)
            self.close_connection = True


class Server(ThreadingHTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    start_jobs()
    srv = Server(("127.0.0.1", PORT), Handler)
    print("Arena Monitor on http://localhost:%d  (%d sources)" % (PORT, len(SOURCES)), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
