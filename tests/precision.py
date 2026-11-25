"""Matcher precision on a hand-labelled sample (phasedoc leaf P1.1.3).

1. python tests/precision.py sample [N]   writes tests/labels.csv: N stratified headlines (default 400) with the
                                          matcher's game and signal tags, plus empty label columns.
2. Open labels.csv (Excel, LibreOffice, a text editor) and fill, for each row:
       games_ok    y / n   are ALL predicted game tags correct? (leave blank if the row has no game tags)
       signals_ok  y / n   are ALL predicted signal tags correct? (blank if no signal tags)
3. python tests/precision.py score        precision per tag family with a 95 % Wilson interval.
                                          PASS = precision >= 95 % and Wilson lower bound >= 90 % for both families.

The sample reads the live DB (read-only). It is stratified by source and tag family, so small sources and
rare signal types are in it. It is not an accuracy claim until a person has filled the labels.
"""
import csv
import json
import math
import os
import random
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "labels.csv")
DATA = os.environ.get("ARENA_DATA") or os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                                                    "ArenaMonitor")
DB = os.path.join(DATA, "monitor.db")
COLS = ["id", "source", "title", "games_pred", "signals_pred", "games_ok", "signals_ok", "url"]


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def sample(n):
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    con.row_factory = sqlite3.Row
    rows = [r for r in con.execute("SELECT id,source,title,url,games,signals FROM items")
            if json.loads(r["games"]) or json.loads(r["signals"])]
    rnd = random.Random(20261123)
    # strata: (source, has game tag, has signal tag); take round-robin so every stratum is represented
    strata = {}
    for r in rows:
        key = (r["source"], bool(json.loads(r["games"])), bool(json.loads(r["signals"])))
        strata.setdefault(key, []).append(r)
    for v in strata.values():
        rnd.shuffle(v)
    picked, pools = [], list(strata.values())
    while len(picked) < n and any(pools):
        for pool in pools:
            if pool and len(picked) < n:
                picked.append(pool.pop())
    rnd.shuffle(picked)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        for r in picked:
            w.writerow([r["id"], r["source"], r["title"], " ".join(json.loads(r["games"])),
                        " ".join(json.loads(r["signals"])), "", "", r["url"]])
    print("wrote %d rows (of %d tagged items, %d strata) to %s" % (len(picked), len(rows), len(strata), OUT))
    if len(picked) < n:
        print("note: only %d tagged items exist yet; re-run later for a full %d-row sample" % (len(picked), n))


def score():
    with open(OUT, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    ok_all = True
    for fam in ("games", "signals"):
        lab = [r[fam + "_ok"].strip().lower() for r in rows if r[fam + "_pred"].strip()]
        done = [x for x in lab if x in ("y", "n")]
        k, n = done.count("y"), len(done)
        lo, hi = wilson(k, n)
        p = k / n if n else 0
        passed = n > 0 and p >= 0.95 and lo >= 0.90
        ok_all &= passed and len(done) == len(lab)
        print("%-8s labelled %d/%d  precision %.1f%%  Wilson 95%% [%.1f%%, %.1f%%]  %s"
              % (fam, n, len(lab), p * 100, lo * 100, hi * 100, "PASS" if passed else "FAIL"))
    print("PASS" if ok_all else "FAIL (or labels incomplete)")
    return ok_all


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "sample":
        sample(int(sys.argv[2]) if len(sys.argv) > 2 else 400)
    elif cmd == "score":
        sys.exit(0 if score() else 1)
    else:
        sys.exit(__doc__)
