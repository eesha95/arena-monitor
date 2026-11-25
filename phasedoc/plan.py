#!/usr/bin/env python3
"""Phasedoc plan script for the Arena Monitor arc (real-time esports & gaming monitor).

Owns the task tree (SQLite). Every leaf carries a runnable acceptance test.
Rule: `tick` a leaf only after its acceptance test has actually been run.

Project: C:/Users/eesha/OneDrive/Desktop/Obsidian/FDE projects/wm
Spec:    C:/Users/eesha/Downloads/worldmonitorgaming.docx (vertical-pack plan, gaming case)
Acceptance commands run from the project folder; live checks need the server up on :8090.

Retrospective seeding: P0-P4 work was built on 2026-11-23 before this phasedoc existed.
Tick timestamps are the seeding date; each tick was made only after its test ran that day.

Env:
  PLAN_DB       path to the SQLite DB           (default: ./plan.db next to this file)
  PLAN_MIRRORS  os.pathsep-separated mirror .md paths
                (default: ./BUILDPLAN.md and the Obsidian vault copy)
"""
import datetime as dt
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("PLAN_DB", os.path.join(HERE, "plan.db"))
VAULT_DIR = os.path.expanduser(
    "~/OneDrive/Desktop/Obsidian/FDE projects/eesha/phasedocs/arena-monitor")
MIRRORS = [p for p in os.environ.get(
    "PLAN_MIRRORS", os.pathsep.join([os.path.join(HERE, "BUILDPLAN.md"),
                                     os.path.join(VAULT_DIR, "BUILDPLAN.md")])
).split(os.pathsep) if p]
ARC = "Arena Monitor"

UT = "`python -m unittest tests.test_server.%s` passes"

# (id, parent, title, acceptance, component). Parents leave acceptance empty.
# Component keys: lowercase letters only; must match comp-<key> in the SVG.
TASKS = [
    ("P0", "", "Pack and sources", "", ""),
    ("P0.1", "P0", "Source register", "", ""),
    ("P0.1.1", "P0.1", "21 keyless sources polled on fixed intervals, each with a terms note",
     "`python tests/live_check.py sources` prints PASS (GDELT may be rate-limited: reported, not failed)", "sources"),
    ("P0.1.2", "P0.1", "Licence and redistribution mode checked for every source",
     "every row in the Sources section of README.md links the source's terms page and states a redistribution mode; reviewed by the operator", "sources"),
    ("P0.2", "P0", "Personas and saved views", "", ""),
    ("P0.2.1", "P0.2", "Fan, Team/org, Publisher/analyst, Sponsor/brand and Ops views; custom views saved per browser",
     "`python tests/walk.py` prints PASS: all 6 views render at 1280 px and 390 px", "views"),
    ("P1", "", "Matcher", "", ""),
    ("P1.1", "P1", "Tagging", "", ""),
    ("P1.1.1", "P1.1", "Game, signal and place lexicon with word boundaries",
     UT % "Matcher", "matcher"),
    ("P1.1.2", "P1.1", "Relevance filter for broad outlets; esports-only signals need game context",
     UT % "Matcher" + " (strict and context tests)", "matcher"),
    ("P1.1.3", "P1.1", "Measured matcher precision on a labelled sample",
     "a 400-headline stratified sample labelled by a person gives precision >= 95 % with a Wilson lower bound >= 90 % for game tags and for signal tags", "matcher"),
    ("P2", "", "Rooms: map, tiles, live push", "", ""),
    ("P2.1", "P2", "Map and tiles", "", ""),
    ("P2.1.1", "P2.1", "Map layers: LoL matches, news mentions, Valve server relays, M4.5+ quakes",
     "`python tests/walk.py` reports mapTiles > 0 on every view and `python tests/live_check.py api` shows sdr and quakes present", "map"),
    ("P2.1.2", "P2.1", "17 tiles with purpose notes and honest empty states",
     "`python tests/walk.py` reports no 'Render error' tile in any view at either width", "tiles"),
    ("P2.1.3", "P2.1", "Headless walk at 1280 px and 390 px",
     "`python tests/walk.py` prints PASS: 0 JS errors, 0 horizontal overflow, maximise/Escape/minimise work", "tiles"),
    ("P2.2", "P2", "Live push", "", ""),
    ("P2.2.1", "P2.2", "Server-Sent Events push every source update to open pages",
     "`python tests/live_check.py sse` prints PASS within 150 s", "stream"),
    ("P3", "", "Forecasts and storage", "", ""),
    ("P3.1", "P3", "Player-count forecast", "", ""),
    ("P3.1.1", "P3.1", "Next-hour forecast, backtested, drawn only when it beats the 'same as now' baseline",
     UT % "Forecast", "forecast"),
    ("P3.2", "P3", "Storage", "", ""),
    ("P3.2.1", "P3.2", "Runtime DB outside the OneDrive vault (LOCALAPPDATA/ArenaMonitor)",
     UT % "DataDir" + " and the live server's DB is %LOCALAPPDATA%/ArenaMonitor/monitor.db", "store"),
    ("P4", "", "Keyed and rate-limited sources", "", ""),
    ("P4.1", "P4", "Rate limits", "", ""),
    ("P4.1.1", "P4.1", "429 backoff 15/30/60 min up to 2 h; next retry shown in the Sources tile",
     UT % "Backoff", "scheduler"),
    ("P4.2", "P4", "All-games matches (PandaScore)", "", ""),
    ("P4.2.1", "P4.2", "PandaScore match mapping into the shared match model",
     UT % "PandaScore", "sources"),
    ("P4.2.2", "P4.2", "PandaScore live with a real token",
     "with PANDASCORE_TOKEN set, `python tests/live_check.py sources` shows ps_live and ps_sched OK and Live now lists a CS2 or Valorant match when one is running", "sources"),
    ("P4.3", "P4", "AI brief (Claude)", "", ""),
    ("P4.3.1", "P4.3", "AI brief job: cited items only, no API call without new headlines",
     UT % "AIBrief", "brief"),
    ("P4.3.2", "P4.3", "AI brief live with real Anthropic credentials",
     "with ANTHROPIC_API_KEY set, the ai_brief source is OK and the Brief tile shows AI items, each with at least one working source link", "brief"),
    ("P4.4", "P4", "Liquipedia matches", "", ""),
    ("P4.4.1", "P4.4", "Liquipedia API with operator-approved contact User-Agent and rate limits",
     "operator approves the contact string; `python tests/live_check.py sources` shows the liquipedia source OK at <= 1 parse request per 30 s", "sources"),
    ("P5", "", "Operations", "", ""),
    ("P5.1", "P5", "Running and tracking", "", ""),
    ("P5.1.1", "P5.1", "Server runs outside the Claude session (own window via start.cmd)",
     "after the Claude session that started it ends, `python tests/live_check.py api` still prints PASS", "ops"),
    ("P5.1.2", "P5.1", "Project and port 8090 registered on the trackdoc hub",
     "`trackdoc port list` shows 8090 arena-monitor and http://127.0.0.1:7000/?tab=projects lists Arena Monitor", "ops"),
    ("P5.1.3", "P5.1", "Server starts at logon (scheduled task)",
     "operator approves; `Get-ScheduledTask arena-monitor` exists and after a re-logon `python tests/live_check.py api` prints PASS", "ops"),
]


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks(
  id TEXT PRIMARY KEY, parent TEXT NOT NULL, title TEXT NOT NULL,
  acceptance TEXT NOT NULL, component TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', updated TEXT);
CREATE TABLE IF NOT EXISTS notes(
  task TEXT NOT NULL, at TEXT NOT NULL, text TEXT NOT NULL);
"""


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def rows(c):
    return c.execute("SELECT * FROM tasks ORDER BY id").fetchall()


def is_leaf(c, tid):
    return c.execute("SELECT 1 FROM tasks WHERE parent=?", (tid,)).fetchone() is None


def must_get(c, tid):
    r = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not r:
        sys.exit(f"no such task: {tid}")
    return r


def mark(c, tid, status):
    must_get(c, tid)
    if not is_leaf(c, tid):
        sys.exit(f"{tid} is not a leaf; tick its leaves instead")
    c.execute("UPDATE tasks SET status=?, updated=? WHERE id=?", (status, now(), tid))
    c.commit()


def rollup(c):
    comp = {}
    for r in rows(c):
        if not r["component"] or not is_leaf(c, r["id"]):
            continue
        d = comp.setdefault(r["component"], {"total": 0, "done": 0, "partial": 0})
        d["total"] += 1
        if r["status"] in ("done", "partial"):
            d[r["status"]] += 1
    for d in comp.values():
        d["state"] = ("done" if d["done"] == d["total"]
                      else "progress" if d["done"] or d["partial"] else "untouched")
    return comp


BOX = {"done": "[x]", "partial": "[~]", "open": "[ ]"}


def mirror(c):
    all_rows = rows(c)
    leaves = [r for r in all_rows if is_leaf(c, r["id"])]
    done = sum(r["status"] == "done" for r in leaves)
    out = [f"# Buildplan: {ARC}", "",
           f"_Generated by plan.py at {now()}. Do not edit by hand._", "",
           f"**{done}/{len(leaves)} leaves done** "
           f"({round(100 * done / max(len(leaves), 1))}%). "
           "`[x]` done and verified, `[~]` partial, `[ ]` open.", ""]
    for r in all_rows:
        depth = r["id"].count(".")
        if depth == 0:
            out += ["", f"## {r['id']} {r['title']}", ""]
        elif is_leaf(c, r["id"]):
            out.append(f"{'  ' * (depth - 1)}- {BOX[r['status']]} **{r['id']}** {r['title']}"
                       f" `({r['component']})` — accept: {r['acceptance']}")
            for n in c.execute("SELECT * FROM notes WHERE task=? ORDER BY at", (r["id"],)):
                out.append(f"{'  ' * depth}- note {n['at']}: {n['text']}")
        else:
            out.append(f"{'  ' * (depth - 1)}- **{r['id']}** {r['title']}")
    text = "\n".join(out) + "\n"
    for p in MIRRORS:
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    return MIRRORS


def tree(c):
    for r in rows(c):
        depth = r["id"].count(".")
        mark_ = BOX[r["status"]] if is_leaf(c, r["id"]) else "   "
        print(f"{'  ' * depth}{mark_} {r['id']:<8} {r['title']}")


def main(argv):
    if not argv:
        sys.exit(__doc__ + "\nverbs: init tick part note add tree components mirror")
    verb, args = argv[0], argv[1:]
    if verb == "init":
        if os.path.exists(DB):
            sys.exit(f"{DB} exists; refusing to re-seed")
        c = conn()
        c.executemany("INSERT INTO tasks(id,parent,title,acceptance,component,updated)"
                      " VALUES(?,?,?,?,?,?)", [t + (now(),) for t in TASKS])
        c.commit()
        print(f"seeded {len(TASKS)} tasks into {DB}")
        print("mirror:", *mirror(c))
        return
    c = conn()
    if verb in ("tick", "part"):
        mark(c, args[0], "done" if verb == "tick" else "partial")
        print(f"{verb} {args[0]}; mirror:", *mirror(c))
    elif verb == "note":
        must_get(c, args[0])
        c.execute("INSERT INTO notes VALUES(?,?,?)", (args[0], now(), " ".join(args[1:])))
        c.commit()
        mirror(c)
    elif verb == "add":
        if len(args) != 5:
            sys.exit("add <id> <parent> <title> <acceptance> <component>")
        tid, parent, title, acc, comp = args
        must_get(c, parent)
        if not acc.strip():
            sys.exit("a leaf needs a runnable acceptance test; decompose further")
        if comp and (not comp.isalpha() or comp != comp.lower()):
            sys.exit("component must be lowercase letters only")
        c.execute("INSERT INTO tasks(id,parent,title,acceptance,component,updated)"
                  " VALUES(?,?,?,?,?,?)", (tid, parent, title, acc, comp, now()))
        c.commit()
        print(f"added {tid}; mirror:", *mirror(c))
    elif verb == "tree":
        tree(c)
    elif verb == "components":
        print(json.dumps(rollup(c), indent=2))
    elif verb == "mirror":
        print("mirror:", *mirror(c))
    else:
        sys.exit(f"unknown verb: {verb}")


if __name__ == "__main__":
    main(sys.argv[1:])
