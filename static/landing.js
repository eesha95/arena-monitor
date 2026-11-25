/* Landing page: theme toggle (shares the dashboard's saved choice) and live numbers from /api/public. */
'use strict';
(function () {
  const root = document.documentElement;
  function save(v) { try { localStorage.setItem('am.theme', JSON.stringify(v)); } catch (e) {} }
  function syncPreview() {
    // the <picture> follows the OS setting; force it to follow the chosen theme instead
    const img = document.getElementById('prevImg'), src = document.getElementById('prevDark');
    if (!img) return;
    const dark = root.getAttribute('data-theme') === 'dark';
    if (src) src.remove();
    img.src = dark ? '/img/preview-dark.png' : '/img/preview-light.png';
  }
  document.getElementById('lpTheme').onclick = () => {
    const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next); save(next); syncPreview();
  };
  syncPreview();

  const fmt = n => n == null ? '–' : n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e4 ? Math.round(n / 1e3) + 'k' : n.toLocaleString();
  async function load() {
    const note = document.getElementById('statsNote');
    try {
      const d = await (await fetch('/api/public')).json();
      const set = (k, v) => { const el = document.querySelector(`[data-k="${k}"]`); if (el) el.textContent = v; };
      set('headlines_24h', fmt(d.headlines_24h));
      set('matches_24h', fmt(d.matches_24h));
      set('steam_players_tracked', fmt(d.steam_players_tracked));
      set('sources', `${d.sources_ok}/${d.sources_total}`);
      note.textContent = 'Live numbers, updated ' + new Date(d.updated * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) +
        (d.top_game ? `. Most played right now: ${d.top_game.name} (${fmt(d.top_game.players)}).` : '.');
    } catch (e) {
      note.textContent = 'Live numbers are unavailable right now.';
    }
  }
  load();
  setInterval(load, 120000);
})();
