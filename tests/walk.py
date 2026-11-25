"""Headless browser walk (agent-browser) at 1280 px and 390 px.

For each width and each built-in view: zero JS errors, zero horizontal overflow, every tile renders
(no 'Render error'), the map has tiles. Also exercises maximise / Escape / minimise once per width.
Run with the server up: python tests/walk.py     Exit code 0 = pass.
"""
import json
import os
import subprocess
import sys

URL = os.environ.get("ARENA_URL", "http://localhost:8090")
# Call the native binary directly: going through agent-browser.cmd lets cmd.exe mangle the JS quoting.
_NATIVE = os.path.join(os.environ.get("APPDATA", ""), "npm", "node_modules", "agent-browser", "bin",
                       "agent-browser-win32-x64.exe")
AB = _NATIVE if os.name == "nt" and os.path.exists(_NATIVE) else "agent-browser"
VIEWS = ["all", "fan", "team", "publisher", "sponsor", "ops"]


def ab(*args):
    # Output goes to a temp file, not a pipe: the first agent-browser call starts a background daemon that
    # inherits the child's stdout, and a pipe would then never reach EOF (the walk hung on a cold start).
    import tempfile
    with tempfile.TemporaryFile() as out:
        try:
            subprocess.run([AB, *args], stdout=out, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=60)
        except subprocess.TimeoutExpired:
            print("  timeout:", args[0], flush=True)
            return ""
        out.seek(0)
        return out.read().decode("utf-8", "replace").strip()


def ev(js):
    out = ab("eval", js)
    try:
        return json.loads(out)
    except Exception:
        return out


PROBE = """(() => {
  const tiles = [...document.querySelectorAll('#grid .tile')];
  return {
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    tiles: tiles.length,
    renderErrors: tiles.filter(t => t.innerText.includes('Render error')).map(t => t.id),
    mapTiles: document.querySelectorAll('#map .leaflet-tile-loaded').length,
  };
})()"""


def main():
    fails = []
    for width in (1280, 390):
        ab("set", "viewport", str(width), "900")
        ab("open", URL)
        ab("wait", "3500")
        for v in VIEWS:
            ev(f"document.querySelector('[data-view={v}]').click(); true")
            ab("wait", "600")
            p = ev(PROBE)
            ok = isinstance(p, dict) and p["overflow"] <= 0 and not p["renderErrors"] and p["tiles"] > 0 and p["mapTiles"] > 0
            print(f"{width}px {v:<10} {'ok ' if ok else 'BAD'} {p}", flush=True)
            if not ok:
                fails.append((width, v, p))
        inter = ev("""(() => { const id = document.querySelector('#grid .tile').id;
          document.querySelector('#' + id + ' .bmax').click(); const a = !document.querySelector('#overlay').hidden;
          document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})); const b = document.querySelector('#overlay').hidden;
          document.querySelector('#' + id + ' .bmin').click(); const c = document.querySelector('#' + id).classList.contains('min');
          document.querySelector('#' + id + ' .bmin').click(); return [a, b, c]; })()""")
        print(f"{width}px maximise/escape/minimise {inter}")
        side = ev("""(() => { const q = document.querySelector('#qs'); q.value = 'news'; q.dispatchEvent(new Event('input'));
          const hits = document.querySelectorAll('#qsResults .qs-item').length; q.value = ''; q.dispatchEvent(new Event('input'));
          document.querySelector('[data-nav=signals]').click();
          return [document.querySelectorAll('#sideNav a').length, hits > 0, document.querySelector('.side-nav a.active')?.dataset.nav]; })()""")
        print(f"{width}px sidebar nav/search/active {side}")
        if side != [18, True, "signals"]:
            fails.append((width, "sidebar", side))
        theme = ev("""(() => { const r = []; for (const t of ['dark', 'light']) {
            document.querySelector('[data-theme-pref=' + t + ']').click();
            r.push(document.documentElement.dataset.theme === t && document.documentElement.scrollWidth <= document.documentElement.clientWidth);
          } return r; })()""")
        print(f"{width}px theme dark/light {theme}")
        if theme != [True, True]:
            fails.append((width, "theme", theme))
        if inter != [True, True, True]:
            fails.append((width, "interactions", inter))
        errs = ab("errors")
        if errs and "No errors" not in errs:
            print("JS errors:", errs)
            fails.append((width, "errors", errs))
    ev("document.querySelector('[data-view=all]').click(); true")
    ab("close")
    print("FAIL" if fails else "PASS")
    return not fails


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
