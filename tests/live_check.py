"""Live checks against a running Arena Monitor (default http://127.0.0.1:8090).

python tests/live_check.py sources     every keyless source has run OK at least once (GDELT may be rate-limited: reported, not failed)
python tests/live_check.py api         /api/state has every core key, /api/series returns points, static files serve
python tests/live_check.py sse         /api/stream delivers at least one data event within 150 s
python tests/live_check.py all
Exit code 0 = pass.
"""
import json
import os
import sys
import time
import urllib.request

BASE = os.environ.get("ARENA_URL", "http://127.0.0.1:8090")
CORE = ["news", "lol_schedule", "lol_live", "dota_live", "dota_results", "steam_players", "steam_top", "steam_store",
        "sdr", "deals", "epic_free", "status", "quakes"]  # markets is opt-in (ARENA_YAHOO=1)
SOFT = {"gdelt"}  # external rate limit; reported but not a failure


def get(path, timeout=30):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return r.status, r.read()


def check_sources():
    st = json.loads(get("/api/state")[1])
    bad = []
    for s in st["sources"]:
        mark = "OK " if s["ok"] else "ERR" if s["ok"] is False else ".. "
        print(mark, s["id"].ljust(16), s["count"], s["error"] or "")
        if not s["ok"] and s["id"] not in SOFT and not s["last_ok"]:
            bad.append(s["id"])
    print("FAIL: " + ", ".join(bad) if bad else "PASS: all keyless sources OK")
    return not bad


def check_api():
    code, body = get("/api/state")
    st = json.loads(body)
    missing = [k for k in CORE if k not in st]
    _, ser = get("/api/series?key=steam:730&hours=24")
    pts = json.loads(ser)
    statics = [get(p)[0] for p in ("/", "/app.js", "/style.css")]
    ok = code == 200 and not missing and len(pts) >= 1 and statics == [200, 200, 200]
    print("missing keys:", missing, "| series points:", len(pts), "| static:", statics)
    print("PASS" if ok else "FAIL")
    return ok


def check_sse():
    t0 = time.time()
    with urllib.request.urlopen(BASE + "/api/stream", timeout=160) as r:
        while time.time() - t0 < 150:
            line = r.readline().decode()
            if line.startswith("data: "):
                key = json.loads(line[6:])["key"]
                print("PASS: event '%s' after %.0f s" % (key, time.time() - t0))
                return True
    print("FAIL: no event in 150 s")
    return False


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    fns = {"sources": check_sources, "api": check_api, "sse": check_sse}
    todo = list(fns) if what == "all" else [what]
    sys.exit(0 if all([fns[w]() for w in todo]) else 1)
