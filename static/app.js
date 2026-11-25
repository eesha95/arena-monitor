/* Arena Monitor client: map + tile grid fed by /api/state and live /api/stream (SSE). */
'use strict';

const S = { data: {}, lexicon: { games: {}, signals: [] }, serverSkew: 0 };
const LS = {
  get(k, d) { try { const v = localStorage.getItem('am.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('am.' + k, JSON.stringify(v)); } catch {} },
};
const PREV_VISIT = LS.get('lastVisit', 0);
LS.set('lastVisit', Math.floor(Date.now() / 1000));

const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const now = () => Math.floor(Date.now() / 1000);
const fmtN = n => n == null ? '–' : n >= 1e6 ? (n / 1e6).toFixed(2) + 'M' : n >= 1e4 ? (n / 1e3).toFixed(1) + 'k' : n.toLocaleString();
const pct = (x, d = 1) => x == null ? '–' : (x >= 0 ? '+' : '') + (x * 100).toFixed(d) + '%';
const cls = x => x == null ? 'muted' : x >= 0 ? 'up' : 'down';
function rel(ts) {
  if (!ts) return '';
  const d = ts - now(), a = Math.abs(d);
  const s = a < 60 ? `${a}s` : a < 3600 ? `${Math.round(a / 60)}m` : a < 86400 ? `${Math.round(a / 3600)}h` : `${Math.round(a / 86400)}d`;
  return d > 0 ? `in ${s}` : `${s} ago`;
}
const dt = ts => ts ? new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
const cssVar = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
function spark(vals, w = 90, h = 22, color = cssVar('--accent')) {
  vals = (vals || []).filter(v => v != null);
  if (vals.length < 2) return '<span class="muted">–</span>';
  const mn = Math.min(...vals), mx = Math.max(...vals), r = mx - mn || 1;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1) * w).toFixed(1)},${(h - 2 - (v - mn) / r * (h - 4)).toFixed(1)}`).join(' ');
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline fill="none" stroke="${color}" stroke-width="1.5" points="${pts}"/></svg>`;
}

/* ------------------------------------------------------------------ filters + watchlist */
const F = { window: 86400, game: 'all', region: 'all', q: '' };
let watch = LS.get('watch', ['T1', 'Vitality', 'Team Spirit', 'Faker']);
const watchHit = text => { const t = (text || '').toLowerCase(); return watch.some(w => w && t.includes(w.toLowerCase())); };
function textOk(text) { return !F.q || (text || '').toLowerCase().includes(F.q.toLowerCase()); }
function newsFiltered(extraWindow) {
  const since = now() - (extraWindow || F.window);
  return (S.data.news || []).filter(it =>
    it.ts >= since &&
    (F.game === 'all' || it.games.includes(F.game)) &&
    (F.region === 'all' || it.places.some(p => p.region === F.region)) &&
    textOk(it.title + ' ' + it.summary));
}
function matchOk(m) {
  return (F.game === 'all' || m.game === F.game) && (F.region === 'all' || !m.region || m.region === F.region) &&
    textOk(matchText(m));
}
const matchText = m => m.teams ? m.teams.map(t => t.name + ' ' + t.code).join(' ') + ' ' + (m.league || '') : `${m.radiant} ${m.dire} ${m.league || ''}`;
const GAME_SHORT = { cs2: 'CS2', lol: 'LoL', valorant: 'VAL', dota2: 'Dota 2', r6: 'R6', rl: 'RL', ow: 'OW', apex: 'Apex', pubg: 'PUBG', fgc: 'FGC', fortnite: 'FN', mobile: 'Mobile', cod: 'CoD', marvel: 'Rivals', deadlock: 'Deadlock' };

/* ------------------------------------------------------------------ shared item renderers */
function newsRow(it, opts = {}) {
  const isNew = it.first_seen > PREV_VISIT && PREV_VISIT > 0;
  const w = watchHit(it.title);
  return `<div class="row"><div class="main">
    <a class="t" href="${esc(it.url)}" target="_blank" rel="noopener">${esc(it.title)}</a>
    <div class="m">${isNew && !opts.noNew ? '<span class="tag new">NEW</span>' : ''}${w ? '<span class="tag watch">WATCH</span>' : ''}
      <span>${esc(it.source)}</span><span>· ${rel(it.ts)}</span>
      ${it.games.map(g => `<span class="tag g">${esc(GAME_SHORT[g] || g)}</span>`).join('')}
      ${it.signals.map(s => `<span class="tag s">${esc(s)}</span>`).join('')}
      ${it.places.slice(0, 2).map(p => `<span class="tag">📍${esc(p.name)}</span>`).join('')}
    </div></div></div>`;
}
function teamHtml(t, right) {
  const img = t.image ? `<img src="${esc(t.image.replace('http://', 'https://'))}" alt="" loading="lazy">` : '';
  return right ? `${img}${esc(t.code || t.name)}` : `${esc(t.code || t.name)}${img}`;
}
function lolRow(m) { return matchRow(m); }
function matchRow(m) {
  const [a, b] = m.teams.length === 2 ? m.teams : [{ name: 'TBD' }, { name: 'TBD' }];
  const live = m.state === 'inProgress';
  const score = m.state === 'unstarted' ? 'vs' : `${a.wins ?? 0} – ${b.wins ?? 0}`;
  const w = watchHit(matchText(m));
  return `<div class="row"><div class="main">
    <div class="match"><span class="tn team r ${a.outcome === 'win' ? 'w' : ''}" title="${esc(a.name)}">${teamHtml(a)}</span>
      <span class="sc">${score}</span><span class="tn team ${b.outcome === 'win' ? 'w' : ''}" title="${esc(b.name)}">${teamHtml(b, true)}</span></div>
    <div class="m">${live ? '<span class="tag live">LIVE</span>' : ''}${w ? '<span class="tag watch">WATCH</span>' : ''}<span class="tag g">${esc(GAME_SHORT[m.game] || m.game_name || m.game || '')}</span>
      <span>${esc(m.league)}${m.block ? ' · ' + esc(m.block) : ''}${m.bestOf ? ' · Bo' + m.bestOf : ''}</span>
      <span>· ${m.state === 'unstarted' ? dt(m.start) + ' (' + rel(m.start) + ')' : rel(m.start)}</span>
      <a class="muted" href="${esc(m.url)}" target="_blank" rel="noopener">${esc(m.src || 'lolesports')} ↗</a></div></div></div>`;
}
function dotaRow(m, live) {
  const rw = m.radiant_win === true, dw = m.radiant_win === false;
  const w = watchHit(matchText(m));
  return `<div class="row"><div class="main">
    <div class="match"><span class="tn r ${rw ? 'w' : ''}">${esc(m.radiant)}</span><span class="sc">${m.rk ?? 0} – ${m.dk ?? 0}</span><span class="tn ${dw ? 'w' : ''}">${esc(m.dire)}</span></div>
    <div class="m">${live ? '<span class="tag live">LIVE</span>' : ''}${w ? '<span class="tag watch">WATCH</span>' : ''}<span class="tag g">Dota 2</span>
      ${m.league ? `<span>${esc(m.league)}</span>` : ''}
      ${live ? `<span>· ${Math.floor((m.clock || 0) / 60)}:${String((m.clock || 0) % 60).padStart(2, '0')}</span><span>· ${fmtN(m.spectators)} watching</span>` : `<span>· kill score · ${rel(m.end)}</span>`}
      <a class="muted" href="${esc(m.url)}" target="_blank" rel="noopener">OpenDota ↗</a></div></div></div>`;
}
function allMatches(kind) { // kind: 'live' | 'sched'
  const lol = kind === 'live' ? (S.data.lol_live || []) : (S.data.lol_schedule || []);
  const ps = (kind === 'live' ? S.data.ps_live : S.data.ps_sched) || [];
  return [...lol, ...ps.filter(m => m.game !== 'lol' || !lol.length)];
}
const empty = msg => `<div class="empty">${msg}</div>`;

/* ------------------------------------------------------------------ tiles */
const SIG_W = { integrity: 3, outage: 3, business: 2.5, roster: 2, result: 1.5, event: 1.5, patch: 1.2, release: 1 };
const STOP = new Set('about after their there would could which these those where while with from that this have will what into over your more than just been they them when were also first new the and for'.split(' '));
const words = t => new Set((t.toLowerCase().match(/[a-z0-9]{5,}/g) || []).filter(w => !STOP.has(w)));

const TILES = {
  since: {
    title: 'Since your last visit', why: 'Everything that arrived after you last opened this page: new headlines, finished matches and items that hit your watchlist.',
    render() {
      if (!PREV_VISIT) return empty('First visit. From your next visit, this tile lists only what changed since then.');
      const items = (S.data.news || []).filter(i => i.first_seen > PREV_VISIT);
      const results = allMatches('sched').filter(m => m.state === 'completed' && m.start > PREV_VISIT);
      const dres = (S.data.dota_results || []).filter(m => m.end > PREV_VISIT);
      const watched = items.filter(i => watchHit(i.title));
      return `<div class="stat"><div><b>${items.length}</b><span>new headlines</span></div><div><b>${results.length + dres.length}</b><span>matches finished</span></div><div><b>${watched.length}</b><span>watchlist hits</span></div></div>
        <div class="muted" style="font-size:11px">Last visit ${dt(PREV_VISIT)} (${rel(PREV_VISIT)})</div>
        ${[...watched, ...items.filter(i => !watched.includes(i))].slice(0, 25).map(i => newsRow(i, { noNew: true })).join('') || empty('Nothing new yet.')}`;
    }, badge: () => PREV_VISIT ? (S.data.news || []).filter(i => i.first_seen > PREV_VISIT).length : '',
  },
  brief: {
    title: 'Brief: top 5 now', why: 'The five items that most likely matter now. Rules mode ranks headlines by signal type, recency, how many outlets carry the story, and your watchlist. AI mode (when the server has Anthropic credentials) is written by Claude from the same headlines, and every item lists the headlines it is based on.',
    mode: null,
    render(el) {
      const ai = S.data.ai_brief;
      const mode = this.mode || (ai && ai.items.length ? 'ai' : 'rules');
      setTimeout(() => el.querySelectorAll('[data-bm]').forEach(b => b.onclick = () => { this.mode = b.dataset.bm; renderTile('brief'); }));
      const sw = ai ? `<div class="chips"><button data-bm="ai" class="${mode === 'ai' ? 'on' : ''}">AI (Claude)</button><button data-bm="rules" class="${mode === 'rules' ? 'on' : ''}">Rules</button></div>` : '';
      if (mode === 'ai' && ai) {
        return sw + `<ol class="brief">${ai.items.map(it => `<li><b>${esc(it.headline)}</b> <span class="tag">${esc(it.audience)}</span>
          <div>${esc(it.why)}</div><div class="why">${it.cites.map(c => `<a href="${esc(c.url)}" target="_blank" rel="noopener">[${esc(c.source)}, ${dt(c.ts)}]</a>`).join(' ')}</div></li>`).join('')}</ol>
          <div class="muted" style="font-size:11px">Written by ${esc(ai.model)} ${rel(ai.at)} from the last 24 h of headlines. It uses only the cited headlines, but check the sources before you act.</div>`;
      }
      return sw + this.rules();
    },
    rules() {
      const items = newsFiltered();
      const ws = items.map(i => words(i.title));
      const scored = items.map((it, k) => {
        let corro = 0; const srcs = new Set([it.source]);
        items.forEach((o, j) => { if (j !== k && !srcs.has(o.source)) { let c = 0; ws[k].forEach(w => { if (ws[j].has(w)) c++; }); if (c >= 2) { srcs.add(o.source); corro++; } } });
        const age = (now() - it.ts) / 3600;
        const sig = it.signals.reduce((a, s) => a + (SIG_W[s] || 1), 0);
        const score = sig + Math.min(corro, 3) * 1.2 + (watchHit(it.title) ? 4 : 0) + (it.games.length ? .5 : 0) + 2 * Math.exp(-age / 12);
        return { it, score, corro: [...srcs].slice(1) };
      }).sort((a, b) => b.score - a.score);
      const picked = [], seen = [];
      for (const s of scored) {
        const w = words(s.it.title);
        if (seen.some(o => [...w].filter(x => o.has(x)).length >= 3)) continue;
        picked.push(s); seen.push(w); if (picked.length === 5) break;
      }
      if (!picked.length) return empty('No items in this window. Widen the time window or clear filters.');
      return `<ol class="brief">${picked.map(({ it, corro }) => `<li><a href="${esc(it.url)}" target="_blank" rel="noopener"><b>${esc(it.title)}</b></a>
        <div class="why">[${esc(it.source)}, ${dt(it.ts)}]${corro.length ? ' · also reported by ' + corro.map(esc).join(', ') : ''}${it.signals.length ? ' · signals: ' + it.signals.join(', ') : ''}</div></li>`).join('')}</ol>`;
    },
  },
  live: {
    title: 'Live now', why: 'Matches in progress: League of Legends from the official lolesports feed, and Dota 2 league games from OpenDota. When no pro games are live, the most-watched high-MMR Dota games show instead.',
    render() {
      const lol = allMatches('live').filter(m => m.state === 'inProgress' && !(m.game === 'dota2' && (S.data.dota_live || {}).pro?.length)).filter(matchOk);
      const dl = S.data.dota_live || { pro: [], top_pubs: [] };
      const dpro = dl.pro.filter(matchOk);
      let h = '';
      if (lol.length) h += `<div class="sec">Pro matches</div>` + lol.map(matchRow).join('');
      if (dpro.length && (F.game === 'all' || F.game === 'dota2')) h += `<div class="sec">Dota 2 · league games</div>` + dpro.map(m => dotaRow(m, true)).join('');
      if (!h) h = empty('No tracked pro matches are live right now.');
      if ((F.game === 'all' || F.game === 'dota2') && dl.top_pubs.length)
        h += `<div class="sec">Dota 2 · most-watched high-MMR games</div>` + dl.top_pubs.map(m => dotaRow({ ...m, radiant: 'Radiant', dire: `Dire · avg MMR ${m.mmr}` }, true)).join('');
      if (S.data.twitch) h += `<div class="sec">Twitch · top streams</div>` + S.data.twitch.filter(s => textOk(s.title + s.game + s.name)).slice(0, 10).map(s =>
        `<div class="row"><div class="main"><a class="t" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a> <span class="muted">${esc(s.game)}</span><div class="m">${fmtN(s.viewers)} viewers · ${esc(s.title).slice(0, 90)}</div></div></div>`).join('');
      return h;
    }, badge: () => allMatches('live').filter(m => m.state === 'inProgress').length + ((S.data.dota_live || {}).pro || []).length,
  },
  results: {
    title: 'Results', why: 'Finished pro matches: LoL from lolesports, with game wins, and Dota 2 from OpenDota, with kill score per map. Use these to follow team form and to check a story quickly.',
    render() {
      const since = now() - Math.max(F.window, 3 * 86400);
      const lol = allMatches('sched').filter(m => m.state === 'completed' && m.start >= since && !(m.game === 'dota2' && S.data.dota_results)).filter(matchOk).map(m => ({ t: m.start, h: matchRow(m) }));
      const dota = (S.data.dota_results || []).filter(m => m.end >= since).filter(matchOk).map(m => ({ t: m.end, h: dotaRow(m) }));
      const all = [...lol, ...dota].sort((a, b) => b.t - a.t);
      return all.map(x => x.h).join('') || empty('No finished matches in this window for the selected filters. LoL shows ≥3 days.');
    },
  },
  calendar: {
    title: 'Calendar', why: 'Upcoming LoL matches from the official schedule, and the free-game windows in the Epic Games Store. Plan content, watch parties and pushes around these times.',
    render() {
      const up = allMatches('sched').filter(m => m.state === 'unstarted' && m.start >= now() - 3600).filter(matchOk).map(m => ({ t: m.start, h: matchRow(m) }));
      const epic = (S.data.epic_free || []).filter(e => textOk(e.title) && F.game === 'all').map(e => ({
        t: e.kind === 'now' ? e.end : e.start,
        h: `<div class="row"><div class="main"><a class="t" href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.title)}</a>
          <div class="m"><span class="tag">Epic free</span><span>${e.kind === 'now' ? 'free until ' + dt(e.end) : 'free from ' + dt(e.start)}</span></div></div></div>`
      }));
      const all = [...up, ...epic].sort((a, b) => a.t - b.t);
      let h = '', day = '';
      for (const x of all) {
        const d = new Date(x.t * 1000).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
        if (d !== day) { h += `<div class="sec">${d}</div>`; day = d; }
        h += x.h;
      }
      return h || empty('No scheduled matches found. Leagues may be in the off-season.');
    },
  },
  news: {
    title: 'Esports & gaming news', why: 'Headlines from HLTV, VLR.gg, Dexerto, GamesIndustry.biz, Eurogamer, PC Gamer, Game Developer and GDELT. Each item is tagged by game, signal type and place mentioned.',
    render() { const it = newsFiltered(); return it.slice(0, 150).map(i => newsRow(i)).join('') || empty('No headlines match these filters.'); },
    badge: () => newsFiltered().length,
  },
  signals: {
    title: 'Signals', why: 'Headlines sorted into signal types: roster moves, results, events, patches, business (deals, funding, layoffs), releases, integrity (bans, cheating) and outages. Click a type to filter.',
    sel: 'all',
    render(el) {
      const items = newsFiltered().filter(i => i.signals.length);
      const counts = {}; items.forEach(i => i.signals.forEach(s => counts[s] = (counts[s] || 0) + 1));
      const sel = this.sel;
      const chips = ['all', ...Object.keys(SIG_W)].map(s => `<button data-sig="${s}" class="${s === sel ? 'on' : ''}">${s}${s === 'all' ? ` ${items.length}` : ` ${counts[s] || 0}`}</button>`).join('');
      const list = items.filter(i => sel === 'all' || i.signals.includes(sel));
      setTimeout(() => el.querySelectorAll('[data-sig]').forEach(b => b.onclick = () => { this.sel = b.dataset.sig; renderTile('signals'); }));
      return `<div class="chips">${chips}</div>` + (list.slice(0, 120).map(i => newsRow(i)).join('') || empty('No signals of this type in the window.'));
    },
  },
  players: {
    title: 'Live player counts (Steam)', why: 'Concurrent players right now on Steam for the main competitive titles, sampled every 5 minutes. It shows Steam only, so console, Riot and Epic launchers are not included.',
    render() {
      const rows = (S.data.steam_players || []).filter(r => (F.game === 'all' || r.game === F.game) && textOk(r.name)).sort((a, b) => (b.now || 0) - (a.now || 0));
      if (!rows.length) return empty('Waiting for the first sample…');
      return `<table><tr><th>Title</th><th class="num">Now</th><th class="num">1h</th><th class="num">24h peak</th><th>24h</th></tr>
        ${rows.map(r => `<tr><td><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.name)}</a></td><td class="num">${fmtN(r.now)}</td>
        <td class="num ${cls(r.chg1h)}">${pct(r.chg1h)}</td><td class="num">${fmtN(r.peak24)}</td><td>${spark(r.spark.map(p => p[1]), 80, 20, cssVar('--accent2'))}</td></tr>`).join('')}</table>`;
    },
  },
  steamtop: {
    title: 'Steam most played', why: 'The daily Steam chart: rank by peak concurrent players, and movement against last week. A fast climber is a new audience and a possible sponsorship or content opportunity.',
    render() {
      const d = S.data.steam_top; if (!d) return empty('Loading…');
      return `<div class="muted" style="font-size:11px">Rollup ${d.date ? new Date(d.date * 1000).toLocaleDateString() : ''}</div>
      <table><tr><th>#</th><th>Game</th><th class="num">Δ wk</th><th class="num">Peak</th></tr>
      ${d.ranks.filter(r => textOk(r.name)).map(r => { const mv = r.last_week ? r.last_week - r.rank : null;
        return `<tr><td class="mono">${r.rank}</td><td><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.name)}</a></td>
        <td class="num ${mv == null ? 'muted' : mv > 0 ? 'up' : mv < 0 ? 'down' : 'muted'}">${mv == null || !r.last_week ? 'new' : mv > 0 ? '▲' + mv : mv < 0 ? '▼' + -mv : '='}</td><td class="num">${fmtN(r.peak)}</td></tr>`; }).join('')}</table>`;
    },
  },
  trends: {
    title: 'Trends & forecasts', why: 'Player-count history that this monitor stores, with a forecast for the next hour. The model is a 24-hour seasonal-naive model, scaled to the current level. The range is the 80th percentile of its backtested error. It is a model estimate, not a fact.',
    sel: 730,
    render(el) {
      const rows = (S.data.steam_players || []).filter(r => F.game === 'all' || r.game === F.game);
      if (!rows.length) return empty('Waiting for history…');
      if (!rows.some(r => r.appid === this.sel)) this.sel = rows[0].appid;
      const r = rows.find(x => x.appid === this.sel);
      const f0 = r.forecast, f = f0 && !f0.hidden ? f0 : null;
      setTimeout(() => {
        const s = el.querySelector('#trendSel'); if (s) s.onchange = () => { this.sel = +s.value; renderTile('trends'); };
        loadBigChart(el.querySelector('.bigchart'), 'steam:' + this.sel, f);
      });
      return `<div class="inline-form"><select id="trendSel">${rows.map(x => `<option value="${x.appid}" ${x.appid === this.sel ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select></div>
        <div class="stat"><div><b>${fmtN(r.now)}</b><span>now</span></div>
        <div><b>${f ? fmtN(f.value) : '–'}</b><span>est. +1h ${f ? `(${fmtN(f.low)}–${fmtN(f.high)})` : ''}</span></div>
        <div><b>${f ? (f.mape * 100).toFixed(1) + '%' : '–'}</b><span>backtest MAPE${f ? ` · baseline ${(f.baseline_mape * 100).toFixed(1)}% · n=${f.n_backtest}` : ''}</span></div></div>
        <div class="bigchart muted">loading 7-day history…</div>
        <div class="muted" style="font-size:11px">${f ? 'Method: ' + esc(f.method) + '. It beats the "same as now" baseline in backtest. Model estimate.'
          : f0 && f0.hidden ? 'Forecast hidden: ' + esc(f0.reason) + '.' : 'The forecast starts after about 1 hour of samples. The seasonal model starts after 24 hours.'}</div>`;
    },
  },
  markets: {
    title: 'Markets', why: 'Share prices of publishers, platforms and peripheral makers. Earnings, layoffs and M&A in games often show here first. Quotes are delayed and are for personal monitoring only.',
    render() {
      if (!S.data.markets) return empty('Markets is off by default, because the Yahoo Finance terms ban automated collection. To turn it on at your own risk, start the server with ARENA_YAHOO=1.');
      const rows = S.data.markets.filter(r => textOk(r.sym + r.name));
      if (!rows.length) return empty('Loading…');
      return `<table><tr><th>Ticker</th><th class="num">Price</th><th class="num">1d</th><th class="num">1m</th><th>1m</th></tr>
      ${rows.map(r => r.error ? `<tr><td>${esc(r.sym)}</td><td colspan="4" class="muted">unavailable</td></tr>` :
        `<tr><td><a href="${esc(r.url)}" target="_blank" rel="noopener" title="${esc(r.name)}"><b>${esc(r.sym)}</b> <span class="muted">${esc(r.name)}</span></a></td>
        <td class="num">${r.price?.toFixed(2)} <span class="muted">${esc(r.cur || '')}</span></td><td class="num ${cls(r.chg)}">${pct(r.chg)}</td>
        <td class="num ${cls(r.chg1m)}">${pct(r.chg1m)}</td><td>${spark(r.spark, 70, 18, cssVar(r.chg1m >= 0 ? '--up' : '--down'))}</td></tr>`).join('')}</table>`;
    },
  },
  releases: {
    title: 'Releases (Steam)', why: 'New releases, top sellers and upcoming titles on the Steam store (US). Use it to see launches that can pull players and viewers away from your title.',
    tab: 'new_releases',
    render(el) {
      const d = S.data.steam_store; if (!d) return empty('Loading…');
      const tabs = { new_releases: 'New', top_sellers: 'Top sellers', coming_soon: 'Coming soon', specials: 'Specials' };
      setTimeout(() => el.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => { this.tab = b.dataset.tab; renderTile('releases'); }));
      return `<div class="chips">${Object.entries(tabs).map(([k, v]) => `<button data-tab="${k}" class="${k === this.tab ? 'on' : ''}">${v}</button>`).join('')}</div>
        ${(d[this.tab] || []).filter(i => textOk(i.name)).map(i => `<div class="row"><img src="${esc(i.img)}" alt="" width="92" height="35" loading="lazy" style="border-radius:3px;object-fit:cover">
        <div class="main"><a class="t" href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.name)}</a>
        <div class="m">${i.price ? '$' + i.price.toFixed(2) : 'Free / TBA'}${i.discount ? ` <span class="tag up">-${i.discount}%</span>` : ''}</div></div></div>`).join('') || empty('Empty')}`;
    },
  },
  deals: {
    title: 'Free games & deals', why: 'Games that are free on the Epic Games Store now and next, and the highest-rated Steam deals from CheapShark. Free weekends and big discounts bring player spikes.',
    render() {
      const ep = (S.data.epic_free || []).filter(e => textOk(e.title));
      const dl = (S.data.deals || []).filter(e => textOk(e.title));
      return `<div class="sec">Epic Games Store · free</div>${ep.map(e => `<div class="row"><div class="main"><a class="t" href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.title)}</a>
        <div class="m"><span class="tag ${e.kind === 'now' ? 'live' : ''}">${e.kind === 'now' ? 'FREE NOW' : 'NEXT'}</span>${e.kind === 'now' ? 'until ' + dt(e.end) : 'from ' + dt(e.start)}</div></div></div>`).join('') || empty('None listed')}
        <div class="sec">Steam deals · CheapShark</div>${dl.map(d => `<div class="row"><img src="${esc(d.thumb)}" alt="" width="70" height="26" loading="lazy" style="object-fit:cover;border-radius:3px">
        <div class="main"><a class="t" href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.title)}</a>
        <div class="m"><span class="up">$${d.sale.toFixed(2)}</span><s>$${d.normal.toFixed(2)}</s><span class="tag">-${d.savings}%</span>${d.steam_rating && d.steam_rating !== '0' ? `<span>${d.steam_rating}% positive</span>` : ''}</div></div></div>`).join('')}`;
    },
  },
  status: {
    title: 'Platform status & hazards', why: 'Live status pages for Discord, Epic Games and Twitch, and M4.5+ earthquakes in the last 24 h. A quake within 300 km of a Valve server relay is flagged, because it can cause regional lag or outages during matches.',
    render() {
      const st = S.data.status || [];
      const pops = S.data.sdr || [];
      const qk = (S.data.quakes || []).map(q => { const near = pops.map(p => ({ p, d: km(q.lat, q.lon, p.lat, p.lon) })).sort((a, b) => a.d - b.d)[0]; return { ...q, near }; }).sort((a, b) => b.mag - a.mag);
      const flagged = qk.filter(q => q.near && q.near.d < 300);
      return `${st.map(s => `<div class="row"><div class="main"><span class="status-dot status-${esc(s.indicator)}"></span><a class="t" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a>
        <span class="muted"> ${esc(s.desc)}</span>${s.degraded.length ? `<div class="m">Degraded: ${s.degraded.map(esc).join(', ')}</div>` : ''}
        ${s.incidents.map(i => `<div class="m"><span class="tag s">${esc(i.impact)}</span><a href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.name)}</a> · ${esc(i.status)}</div>`).join('')}</div></div>`).join('') || empty('Loading…')}
        <div class="sec">Earthquakes M4.5+ (24 h) · ${flagged.length} near server relays</div>
        ${qk.slice(0, 12).map(q => `<div class="row"><div class="main"><a class="t" href="${esc(q.url)}" target="_blank" rel="noopener">M${q.mag.toFixed(1)} ${esc(q.place)}</a>
        <div class="m">${rel(q.ts)}${q.near ? ` · nearest relay ${esc(q.near.p.desc)} ${Math.round(q.near.d)} km` : ''}${q.near && q.near.d < 300 ? ' <span class="tag live">NEAR RELAY</span>' : ''}</div></div></div>`).join('') || empty('No M4.5+ quakes in 24 h.')}`;
    }, badge: () => (S.data.status || []).filter(s => s.indicator !== 'none').length || '',
  },
  video: {
    title: 'Live video', why: 'Watch an official broadcast channel inside the monitor. The player shows the stream only when the channel is live. Type any Twitch channel name.',
    render(el) {
      const ch = LS.get('channel', 'riotgames');
      const presets = ['riotgames', 'valorant', 'eslcs', 'blastpremier', 'esl_dota2', 'pgl', 'rainbow6', 'rocketleague', 'evo'];
      setTimeout(() => {
        const set = c => { LS.set('channel', c); renderTile('video'); };
        el.querySelectorAll('[data-ch]').forEach(b => b.onclick = () => set(b.dataset.ch));
        const f = el.querySelector('form'); if (f) f.onsubmit = e => { e.preventDefault(); const v = f.c.value.trim(); if (v) set(v); };
      });
      const parent = location.hostname || 'localhost';
      return `<form class="inline-form"><input name="c" value="${esc(ch)}" placeholder="twitch channel"><button>Watch</button></form>
        <div class="chips">${presets.map(p => `<button data-ch="${p}" class="${p === ch ? 'on' : ''}">${p}</button>`).join('')}</div>
        <div class="video"><iframe src="https://player.twitch.tv/?channel=${encodeURIComponent(ch)}&parent=${encodeURIComponent(parent)}&muted=true" allowfullscreen></iframe></div>`;
    },
  },
  watch: {
    title: 'Watchlist', why: 'Teams, players, games or companies that you follow. Matches show across news and matches, and they get a WATCH tag. They also rank higher in the brief. The list is saved in this browser.',
    render(el) {
      setTimeout(() => {
        const f = el.querySelector('form');
        if (f) f.onsubmit = e => { e.preventDefault(); const v = f.w.value.trim(); if (v && !watch.includes(v)) { watch.push(v); LS.set('watch', watch); renderAll(); } };
        el.querySelectorAll('[data-rm]').forEach(b => b.onclick = () => { watch = watch.filter(w => w !== b.dataset.rm); LS.set('watch', watch); renderAll(); });
      });
      const since = now() - Math.max(F.window, 3 * 86400);
      const news = (S.data.news || []).filter(i => i.ts >= since && watchHit(i.title));
      const mats = [...allMatches('live'), ...allMatches('sched')].filter(m => watchHit(matchText(m)) && (m.state !== 'completed' || m.start >= since));
      const seenIds = new Set();
      const uniq = mats.filter(m => !seenIds.has(m.id) && seenIds.add(m.id)).sort((a, b) => (a.state === 'inProgress' ? -1 : 0) - (b.state === 'inProgress' ? -1 : 0) || b.start - a.start);
      return `<form class="inline-form"><input name="w" placeholder="add team, player, game, company…"><button>Add</button></form>
        <div class="chips">${watch.map(w => `<button data-rm="${esc(w)}" title="remove">${esc(w)} ✕</button>`).join('')}</div>
        ${uniq.length ? '<div class="sec">Matches</div>' + uniq.slice(0, 8).map(lolRow).join('') : ''}
        <div class="sec">Headlines (≥3 days)</div>${news.slice(0, 40).map(i => newsRow(i)).join('') || empty('No hits yet.')}`;
    }, badge: () => (S.data.news || []).filter(i => i.ts >= now() - 86400 && watchHit(i.title)).length || '',
  },
  sources: {
    title: 'Sources & health', why: 'The source register: each feed, its poll interval, last success, item count and terms note. A red row means the last fetch failed. The server tries again within 5 minutes.',
    render(el) {
      const src = S.data.sources || [];
      setTimeout(() => el.querySelectorAll('[data-refresh]').forEach(b => b.onclick = () => { apiFetch('/api/refresh?source=' + encodeURIComponent(b.dataset.refresh)); b.textContent = '…'; }));
      return `<table><tr><th>Source</th><th>Every</th><th>Last OK</th><th class="num">Items</th><th></th></tr>
        ${src.map(s => `<tr title="${esc(s.note)}${s.error ? '\n' + esc(s.error) : ''}"><td><span class="status-dot status-${s.ok === false ? 'major' : s.ok ? 'none' : 'unknown'}"></span>${esc(s.name)}
        ${s.error ? `<div class="muted" style="font-size:10px">${esc(s.error).slice(0, 90)}${s.next_try ? ` · retry ${rel(s.next_try)}` : ''}</div>` : `<div class="muted" style="font-size:10px">${esc(s.note)}</div>`}</td>
        <td class="mono muted">${s.interval >= 3600 ? s.interval / 3600 + 'h' : s.interval / 60 + 'm'}</td><td class="muted">${s.last_ok ? rel(s.last_ok) : '–'}</td>
        <td class="num">${s.count ?? '–'}</td><td><button data-refresh="${esc(s.id)}" title="refresh now">↻</button></td></tr>`).join('')}</table>`;
    },
  },
};

const ALL_TILES = Object.keys(TILES);
const VIEWS = {
  all: { label: 'All', tiles: ALL_TILES },
  fan: { label: 'Fan', tiles: ['since', 'live', 'results', 'calendar', 'video', 'brief', 'news', 'watch', 'players'] },
  team: { label: 'Team / org', tiles: ['since', 'brief', 'signals', 'results', 'calendar', 'watch', 'news', 'players', 'status'] },
  publisher: { label: 'Publisher / analyst', tiles: ['brief', 'players', 'trends', 'steamtop', 'signals', 'markets', 'releases', 'deals', 'status'] },
  sponsor: { label: 'Sponsor / brand', tiles: ['brief', 'players', 'steamtop', 'live', 'calendar', 'signals', 'markets', 'news', 'video'] },
  ops: { label: 'Ops', tiles: ['status', 'sources', 'live', 'players', 'trends', 'news'] },
};
let customViews = LS.get('customViews', {});
let currentView = LS.get('view', 'all');
let minimised = new Set(LS.get('min', []));

/* ------------------------------------------------------------------ helpers */
function km(a, b, c, d) {
  const R = 6371, r = Math.PI / 180, x = Math.sin((c - a) * r / 2) ** 2 + Math.cos(a * r) * Math.cos(c * r) * Math.sin((d - b) * r / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
}
async function loadBigChart(el, key, f) {
  if (!el) return;
  try {
    const pts = await (await apiFetch(`/api/series?key=${encodeURIComponent(key)}&hours=168`)).json();
    if (pts.length < 2) { el.textContent = 'Not enough history yet. Samples are stored every 5 minutes.'; return; }
    const W = 600, H = 150, xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
    if (f) { xs.push(f.at); ys.push(f.high, f.low); }
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys) * .95, y1 = Math.max(...ys) * 1.02;
    const X = t => ((t - x0) / (x1 - x0 || 1) * (W - 40) + 35).toFixed(1), Y = v => (H - 18 - (v - y0) / (y1 - y0 || 1) * (H - 28)).toFixed(1);
    const line = pts.map(p => `${X(p[0])},${Y(p[1])}`).join(' ');
    const last = pts[pts.length - 1];
    let fc = '';
    const cLine = cssVar('--accent2'), cMuted = cssVar('--muted'), cFc = cssVar('--warn');
    if (f) fc = `<polygon points="${X(last[0])},${Y(last[1])} ${X(f.at)},${Y(f.high)} ${X(f.at)},${Y(f.low)}" fill="${cFc}" fill-opacity=".2"/>
      <line x1="${X(last[0])}" y1="${Y(last[1])}" x2="${X(f.at)}" y2="${Y(f.value)}" stroke="${cFc}" stroke-dasharray="4 3"/>`;
    const ticks = [y0, (y0 + y1) / 2, y1].map(v => `<text x="2" y="${Y(v)}" fill="${cMuted}" font-size="10">${fmtN(Math.round(v))}</text>`).join('');
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${ticks}<polyline fill="none" stroke="${cLine}" stroke-width="1.5" points="${line}"/>${fc}
      <text x="35" y="${H - 3}" fill="${cMuted}" font-size="10">${dt(x0)}</text><text x="${W - 5}" y="${H - 3}" fill="${cMuted}" font-size="10" text-anchor="end">${f ? '+1h estimate' : dt(x1)}</text></svg>`;
  } catch (e) { el.textContent = 'Chart unavailable.'; }
}

/* ------------------------------------------------------------------ grid */
function viewTiles() { return (VIEWS[currentView] || customViews[currentView] || VIEWS.all).tiles.filter(t => TILES[t]); }
function buildGrid() {
  const g = $('#grid'); g.innerHTML = '';
  for (const id of viewTiles()) {
    const t = TILES[id];
    const el = document.createElement('section');
    el.className = 'tile' + (minimised.has(id) ? ' min' : ''); el.id = 'tile-' + id;
    el.innerHTML = `<div class="hd"><h2>${esc(t.title)}</h2><span class="badge"></span><span class="why" data-why="${esc(t.why)}">?</span>
      <button class="bmin" title="minimise / restore">${minimised.has(id) ? '▢' : '–'}</button><button class="bmax" title="maximise">⤢</button></div><div class="body"></div>`;
    el.querySelector('.bmin').onclick = () => { minimised.has(id) ? minimised.delete(id) : minimised.add(id); LS.set('min', [...minimised]); buildGrid(); };
    el.querySelector('.bmax').onclick = () => maximise(id);
    g.appendChild(el);
    renderTile(id);
  }
  renderViews(); renderSideNav();
}
function maximise(id) {
  const ov = $('#overlay'), el = $('#tile-' + id);
  if (!el) return;
  if (!ov.hidden) { closeOverlay(); return; }
  el.dataset.placeholder = '1';
  const ph = document.createElement('div'); ph.id = 'ph-' + id; el.before(ph);
  el.classList.remove('min');
  ov.appendChild(el); ov.hidden = false;
  el.querySelector('.bmax').textContent = '✕';
  renderTile(id);
}
function closeOverlay() {
  const ov = $('#overlay'); const el = ov.querySelector('.tile'); if (!el) { ov.hidden = true; return; }
  const id = el.id.slice(5); const ph = $('#ph-' + id);
  if (ph) { ph.replaceWith(el); } ov.hidden = true;
  if (minimised.has(id)) el.classList.add('min');
  el.querySelector('.bmax').textContent = '⤢';
}
$('#overlay').addEventListener('click', e => { if (e.target.id === 'overlay') closeOverlay(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeOverlay(); });

function renderTile(id) {
  const el = $('#tile-' + id); if (!el) return;
  const t = TILES[id];
  const body = el.querySelector('.body');
  if (el.classList.contains('min')) { const b = t.badge ? t.badge() : ''; el.querySelector('.badge').textContent = b === 0 ? '' : b; return; }
  const st = body.scrollTop;
  try { body.innerHTML = t.render.call(t, body); } catch (e) { body.innerHTML = empty('Render error: ' + esc(e.message)); console.error(id, e); }
  body.scrollTop = st;
  const b = t.badge ? t.badge() : '';
  el.querySelector('.badge').textContent = b === 0 ? '' : b;
}
function renderAll() { viewTiles().forEach(renderTile); renderMap(); renderHealth(); renderSideNav(); }

/* which tiles depend on which state keys */
const DEPS = {
  news: ['since', 'brief', 'news', 'signals', 'watch'], ai_brief: ['brief'], ps_live: ['live', 'watch'], ps_sched: ['results', 'calendar', 'since', 'watch'], lol_live: ['live', 'watch'], lol_schedule: ['results', 'calendar', 'since', 'watch'],
  dota_live: ['live'], dota_results: ['results', 'since'], steam_players: ['players', 'trends'], steam_top: ['steamtop'],
  steam_store: ['releases'], markets: ['markets'], deals: ['deals'], epic_free: ['deals', 'calendar'], status: ['status'],
  quakes: ['status'], sdr: ['status'], sources: ['sources'], twitch: ['live'],
};
const MAP_KEYS = new Set(['news', 'lol_live', 'lol_schedule', 'quakes', 'sdr']);

function renderViews() {
  const nav = $('#views');
  const all = { ...Object.fromEntries(Object.entries(VIEWS).map(([k, v]) => [k, v.label])), ...Object.fromEntries(Object.keys(customViews).map(k => [k, k])) };
  nav.innerHTML = Object.entries(all).map(([k, v]) => `<button data-view="${esc(k)}" class="${k === currentView ? 'on' : ''}">${esc(v)}${customViews[k] ? '<span class="x" data-del="' + esc(k) + '">✕</span>' : ''}</button>`).join('');
  nav.querySelectorAll('[data-view]').forEach(b => b.onclick = e => {
    const del = e.target.dataset.del;
    if (del) { delete customViews[del]; LS.set('customViews', customViews); if (currentView === del) currentView = 'all'; }
    else { currentView = b.dataset.view; const cv = customViews[currentView]; if (cv && cv.filters) applyFilters(cv.filters); }
    LS.set('view', currentView); buildGrid(); renderMap();
  });
}
function applyFilters(f) {
  Object.assign(F, f);
  $('#fWindow').value = F.window; $('#fGame').value = F.game; $('#fRegion').value = F.region; $('#fSearch').value = F.q;
}
$('#saveView').onclick = () => {
  const name = prompt('Name for this view (saves the current tiles, filters and minimised tiles):');
  if (!name) return;
  customViews[name] = { tiles: viewTiles().filter(t => !minimised.has(t)).concat(viewTiles().filter(t => minimised.has(t))), filters: { ...F } };
  LS.set('customViews', customViews); currentView = name; LS.set('view', name); buildGrid();
};
$('#resetView').onclick = () => { minimised.clear(); LS.set('min', []); applyFilters({ window: 86400, game: 'all', region: 'all', q: '' }); buildGrid(); renderMap(); };
$('#fWindow').onchange = e => { F.window = +e.target.value; renderAll(); };
$('#fGame').onchange = e => { F.game = e.target.value; renderAll(); };
$('#fRegion').onchange = e => { F.region = e.target.value; renderAll(); };
let qT; $('#fSearch').oninput = e => { clearTimeout(qT); qT = setTimeout(() => { F.q = e.target.value.trim(); renderAll(); }, 200); };

function renderHealth() {
  const src = S.data.sources || []; const bad = src.filter(s => s.ok === false).length, ok = src.filter(s => s.ok).length;
  const h = $('#health'); h.textContent = `sources ${ok}/${src.length} ok${bad ? ` · ${bad} failing` : ''}`; h.classList.toggle('bad', bad > 0);
}
$('#health').onclick = () => { if (!viewTiles().includes('sources')) { currentView = 'ops'; LS.set('view', 'ops'); buildGrid(); } $('#tile-sources')?.scrollIntoView({ behavior: 'smooth', block: 'center' }); };

/* ------------------------------------------------------------------ sidebar: nav, search, drawer */
const NAV = [
  { items: ['map', 'brief', 'since'] },
  { group: 'Live', items: ['live', 'results', 'calendar', 'video'] },
  { group: 'News', items: ['news', 'signals', 'watch'] },
  { group: 'Games & market', items: ['players', 'steamtop', 'trends', 'releases', 'deals', 'markets'] },
  { group: 'Ops', items: ['status', 'sources'] },
];
const NAV_LABEL = { map: 'Map', since: 'Since last visit', brief: 'Brief', live: 'Live now', results: 'Results', calendar: 'Calendar',
  video: 'Live video', news: 'News', signals: 'Signals', watch: 'Watchlist', players: 'Player counts', steamtop: 'Steam charts',
  trends: 'Trends & forecasts', releases: 'Releases', deals: 'Free games & deals', markets: 'Markets', status: 'Status & hazards',
  sources: 'Sources & health' };
let navTimer, activeNav = 'map';
function renderSideNav() {
  const shown = new Set(viewTiles());
  $('#sideNav').innerHTML = NAV.map(g => (g.group ? `<div class="side-group">${esc(g.group)}</div>` : '') + g.items.map(id => {
    let cnt = '';
    if (id !== 'map' && TILES[id].badge) { try { const b = TILES[id].badge(); cnt = b === 0 || b == null ? '' : b; } catch (e) { /* ignore */ } }
    const off = id !== 'map' && !shown.has(id);
    return `<a href="#${id === 'map' ? 'map' : 'tile-' + id}" data-nav="${id}" class="${id === activeNav ? 'active' : ''}${off ? ' hidden-tile' : ''}"` +
      `${off ? ' title="Not in this view: opens the All view"' : ''}>${esc(NAV_LABEL[id])}<span class="cnt">${cnt}</span></a>`;
  }).join('')).join('');
  $('#sideNav').querySelectorAll('[data-nav]').forEach(a => a.onclick = e => { e.preventDefault(); goTo(a.dataset.nav); });
}
function goTo(id) {
  closeDrawer();
  if (id !== 'map' && !viewTiles().includes(id)) { currentView = 'all'; LS.set('view', 'all'); buildGrid(); renderMap(); }
  const el = id === 'map' ? $('#map') : $('#tile-' + id);
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  if (id !== 'map') { el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash'); }
  activeNav = id; renderSideNav();
}
// highlight the section nearest the top of the screen
let scrollTimer;
window.addEventListener('scroll', () => {
  clearTimeout(scrollTimer);
  scrollTimer = setTimeout(() => {
    let best = 'map', bestTop = -Infinity;
    const cands = [['map', $('#map')], ...viewTiles().map(id => [id, $('#tile-' + id)])];
    for (const [id, el] of cands) {
      if (!el) continue;
      const top = el.getBoundingClientRect().top;
      if (top <= 140 && top > bestTop) { best = id; bestTop = top; }
    }
    if (best !== activeNav) { activeNav = best; renderSideNav(); }
  }, 80);
}, { passive: true });

// drawer (phones)
const side = $('#side'), backdrop = $('#sideBackdrop'), menuBtn = $('#menuBtn');
function openDrawer() { side.classList.add('open'); backdrop.hidden = false; menuBtn.setAttribute('aria-expanded', 'true'); }
function closeDrawer() { side.classList.remove('open'); backdrop.hidden = true; menuBtn.setAttribute('aria-expanded', 'false'); }
menuBtn.onclick = () => side.classList.contains('open') ? closeDrawer() : openDrawer();
backdrop.onclick = closeDrawer;

// search: sections, views and headlines; Enter opens the selected result
const qs = $('#qs'), qsBox = $('#qsResults');
if (/Mac|iPhone|iPad/.test(navigator.platform)) $('#qsKey').textContent = '⌘K';
let qsItems = [], qsSel = 0;
function qsRender() {
  const q = qs.value.trim().toLowerCase();
  if (!q) { qsBox.hidden = true; return; }
  const secs = Object.keys(NAV_LABEL).filter(id => (NAV_LABEL[id] + ' ' + (TILES[id] ? TILES[id].title + ' ' + TILES[id].why : '')).toLowerCase().includes(q))
    .slice(0, 6).map(id => ({ kind: 'Sections', label: NAV_LABEL[id], sub: id === 'map' ? 'World map and layers' : TILES[id].title, run: () => goTo(id) }));
  const allViews = { ...Object.fromEntries(Object.entries(VIEWS).map(([k, v]) => [k, v.label])), ...Object.fromEntries(Object.keys(customViews).map(k => [k, k])) };
  const views = Object.entries(allViews).filter(([, v]) => v.toLowerCase().includes(q)).slice(0, 3)
    .map(([k, v]) => ({ kind: 'Views', label: v, sub: 'switch view', run: () => { document.querySelector(`[data-view="${CSS.escape(k)}"]`)?.click(); closeDrawer(); } }));
  const news = (S.data.news || []).filter(i => (i.title + ' ' + i.source).toLowerCase().includes(q)).slice(0, 6)
    .map(i => ({ kind: 'Headlines', label: i.title, sub: `${i.source} · ${rel(i.ts)}`, run: () => window.open(i.url, '_blank', 'noopener') }));
  const typed = qs.value.trim();
  const filt = [{ kind: 'Filter', label: `Filter every tile by “${typed}”`, sub: 'sets the Filter box above the map',
    run: () => { $('#fSearch').value = typed; F.q = typed; renderAll(); closeDrawer(); } }];
  qsItems = [...secs, ...views, ...news, ...filt];
  qsSel = Math.min(qsSel, qsItems.length - 1);
  let h = '', last = '';
  qsItems.forEach((it, n) => {
    if (it.kind !== last) { h += `<div class="qs-sec">${esc(it.kind)}</div>`; last = it.kind; }
    h += `<button class="qs-item${n === qsSel ? ' sel' : ''}" role="option" data-n="${n}">${esc(it.label)}<small>${esc(it.sub)}</small></button>`;
  });
  qsBox.innerHTML = h; qsBox.hidden = false;
  qsBox.querySelectorAll('[data-n]').forEach(b => b.onmousedown = e => { e.preventDefault(); qsRun(+b.dataset.n); });
}
function qsRun(n) { const it = qsItems[n]; if (!it) return; qs.value = ''; qsBox.hidden = true; qs.blur(); it.run(); }
qs.addEventListener('input', () => { qsSel = 0; qsRender(); });
qs.addEventListener('focus', qsRender);
qs.addEventListener('blur', () => setTimeout(() => { qsBox.hidden = true; }, 150));
qs.addEventListener('keydown', e => {
  if (e.key === 'ArrowDown') { e.preventDefault(); qsSel = Math.min(qsSel + 1, qsItems.length - 1); qsRender(); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); qsSel = Math.max(qsSel - 1, 0); qsRender(); }
  else if (e.key === 'Enter') { e.preventDefault(); qsRun(qsSel); }
  else if (e.key === 'Escape') { qs.value = ''; qsBox.hidden = true; qs.blur(); }
});
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault(); if (window.innerWidth <= 900) openDrawer(); qs.focus(); qs.select();
  } else if (e.key === 'Escape') closeDrawer();
});

/* ------------------------------------------------------------------ map */
const map = L.map('map', { worldCopyJump: true, zoomControl: true, minZoom: 2 }).setView([25, 15], 2);
// OpenStreetMap standard tiles (tile usage policy: light use, attribution required), darkened in CSS (.osm-dark).
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors', maxZoom: 18, className: 'osm-dark',
}).addTo(map);
const layers = {
  matches: L.layerGroup().addTo(map), news: L.layerGroup().addTo(map), pops: L.layerGroup(), quakes: L.layerGroup().addTo(map),
};
const layerNames = { matches: 'LoL matches (league home)', news: 'News mentions', pops: 'Valve server relays', quakes: 'Earthquakes M4.5+ (24h)' };
const savedLayers = LS.get('layers', null);
if (savedLayers) Object.entries(layers).forEach(([k, l]) => savedLayers.includes(k) ? l.addTo(map) : map.removeLayer(l));
L.control.layers(null, Object.fromEntries(Object.entries(layers).map(([k, l]) => [layerNames[k], l])), { collapsed: window.innerWidth < 760 }).addTo(map);
map.on('overlayadd overlayremove', () => LS.set('layers', Object.entries(layers).filter(([, l]) => map.hasLayer(l)).map(([k]) => k)));
const legend = L.control({ position: 'bottomleft' });
legend.onAdd = () => { const d = L.DomUtil.create('div', 'map-legend'); d.innerHTML =
  '<i style="background:#ff3b5c"></i>Live match<br><i style="background:#7c5cff"></i>Upcoming match<br><i style="background:#64748b"></i>Finished match<br><i style="background:var(--accent2)"></i>News mentions<br><i style="background:#34d399;border-radius:2px"></i>Server relay<br><i style="background:#f59e0b"></i>Earthquake'; return d; };
legend.addTo(map);

function renderMap() {
  Object.values(layers).forEach(l => l.clearLayers());
  // matches at league home studio
  const since = now() - Math.max(F.window, 86400), until = now() + 7 * 86400;
  const byLoc = {};
  const seen = new Set();
  [...(S.data.lol_live || []), ...(S.data.lol_schedule || [])].filter(m => m.geo && !seen.has(m.id) && seen.add(m.id) && matchOk(m) && m.start >= since && m.start <= until)
    .forEach(m => { const k = m.geo.lat + ',' + m.geo.lon; (byLoc[k] = byLoc[k] || { geo: m.geo, ms: [] }).ms.push(m); });
  for (const { geo, ms } of Object.values(byLoc)) {
    const live = ms.some(m => m.state === 'inProgress'), up = ms.some(m => m.state === 'unstarted');
    const color = live ? '#ff3b5c' : up ? '#7c5cff' : '#64748b';
    const mk = L.circleMarker([geo.lat, geo.lon], { radius: 6 + Math.min(ms.length, 10), color, fillColor: color, fillOpacity: .55, weight: 2, className: live ? 'pulse-live' : '' });
    ms.sort((a, b) => (a.state === 'inProgress' ? -1 : 1) - (b.state === 'inProgress' ? -1 : 1) || b.start - a.start);
    mk.bindPopup(`<b>${esc(ms[0].league)} · ${esc(geo.label)}</b> <span class="muted">(home studio, approx.)</span>${ms.slice(0, 12).map(lolRow).join('')}`);
    mk.addTo(layers.matches);
  }
  // news mentions
  const agg = {};
  newsFiltered().forEach(it => it.places.forEach(p => { const k = p.lat + ',' + p.lon; (agg[k] = agg[k] || { p, items: [] }).items.push(it); }));
  for (const { p, items } of Object.values(agg)) {
    const w = items.some(i => watchHit(i.title));
    L.circleMarker([p.lat, p.lon], { radius: 4 + Math.min(Math.sqrt(items.length) * 3, 16), color: w ? cssVar('--text') : cssVar('--accent2'), weight: w ? 2 : 1, fillColor: cssVar('--accent2'), fillOpacity: .35 })
      .bindPopup(`<b>📍 ${esc(p.name)}</b> <span class="muted">${items.length} headline${items.length > 1 ? 's' : ''} mention this place</span>${items.slice(0, 15).map(i => newsRow(i)).join('')}`)
      .addTo(layers.news);
  }
  // server relays
  (S.data.sdr || []).forEach(p => L.circleMarker([p.lat, p.lon], { radius: 3, color: '#34d399', fillColor: '#34d399', fillOpacity: .9, weight: 1 })
    .bindTooltip(`${esc(p.desc)} · ${esc(p.code)} · ${p.relays} relays`).addTo(layers.pops));
  // quakes
  (S.data.quakes || []).forEach(q => {
    const near = (S.data.sdr || []).some(p => km(q.lat, q.lon, p.lat, p.lon) < 300);
    L.circleMarker([q.lat, q.lon], { radius: Math.max(3, (q.mag - 4) * 5), color: near ? '#ff3b5c' : '#f59e0b', fillColor: '#f59e0b', fillOpacity: .45, weight: near ? 2 : 1 })
      .bindPopup(`<b>M${q.mag.toFixed(1)}</b> ${esc(q.place)}<br>${rel(q.ts)}${near ? '<br><span class="tag live">within 300 km of a server relay</span>' : ''}<br><a href="${esc(q.url)}" target="_blank" rel="noopener">USGS ↗</a>`)
      .addTo(layers.quakes);
  });
}

/* ------------------------------------------------------------------ data + live stream */
function fillGameSelect() {
  const sel = $('#fGame'); const cur = sel.value;
  sel.innerHTML = '<option value="all">All games</option>' + Object.entries(S.lexicon.games).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('');
  sel.value = cur || 'all';
}
let mapTimer;
function onUpdate(key, data) {
  clearTimeout(navTimer); navTimer = setTimeout(renderSideNav, 600);
  S.data[key] = data;
  (DEPS[key] || []).forEach(id => { renderTile(id); const el = $('#tile-' + id); if (el && key !== 'sources') { el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash'); } });
  if (MAP_KEYS.has(key)) { clearTimeout(mapTimer); mapTimer = setTimeout(renderMap, 400); }
  if (key === 'sources') renderHealth();
}
/* ------------------------------------------------------------------ theme: light (default) / dark / auto */
const darkMq = window.matchMedia ? matchMedia('(prefers-color-scheme: dark)') : null;
function themePref() { return LS.get('theme', 'light'); }
function applyTheme(pref) {
  const dark = pref === 'dark' || (pref === 'auto' && darkMq && darkMq.matches);
  const want = dark ? 'dark' : 'light';
  document.querySelectorAll('[data-theme-pref]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.themePref === pref)));
  if (document.documentElement.getAttribute('data-theme') === want) return;
  document.documentElement.setAttribute('data-theme', want);
  if (S.data && Object.keys(S.data).length) renderAll(); // charts and map markers read theme colours at render time
}
document.querySelectorAll('[data-theme-pref]').forEach(b => b.onclick = () => { LS.set('theme', b.dataset.themePref); applyTheme(b.dataset.themePref); });
if (darkMq) darkMq.addEventListener('change', () => { if (themePref() === 'auto') applyTheme('auto'); });
applyTheme(themePref());

// Sign-in: every data request carries the Clerk session token when sign-in is on (see auth.js).
let AUTH = { enabled: false, token: async () => null };
async function apiFetch(url, opts = {}) {
  const t = await AUTH.token();
  const headers = Object.assign({}, opts.headers || {}, t ? { Authorization: 'Bearer ' + t } : {});
  const r = await fetch(url, Object.assign({}, opts, { headers }));
  if (r.status === 401 && AUTH.enabled) { location.replace('/sign-in'); throw new Error('signed out'); }
  return r;
}
function renderAccount() {
  const box = $('#account'); if (!box) return;
  if (!AUTH.enabled || !AUTH.clerk.user) { box.hidden = true; return; }
  const u = AUTH.clerk.user;
  const email = u.primaryEmailAddress ? u.primaryEmailAddress.emailAddress : '';
  box.hidden = false;
  box.innerHTML = `<div class="acct-name">${esc(u.fullName || u.username || email || 'Signed in')}</div>` +
    (email && (u.fullName || u.username) ? `<div class="acct-mail">${esc(email)}</div>` : '') +
    `<button type="button" id="signOut" class="acct-out">Sign out</button>`;
  $('#signOut').onclick = () => AUTH.signOut();
}
async function boot() {
  AUTH = await window.AM_AUTH;
  if (AUTH.enabled && !AUTH.clerk.isSignedIn) { location.replace('/sign-in'); return; }
  renderAccount();
  const st = await (await apiFetch('/api/state')).json();
  S.lexicon = st.lexicon; delete st.lexicon;
  S.data = st;
  fillGameSelect();
  buildGrid(); renderMap(); renderHealth();
  if (st.mode === 'serverless') poll(); else connect();
}
// Serverless hosting (Vercel) has no live stream: fetch the whole state every 60 s instead.
const SKIP_KEYS = new Set(['lexicon', 'server_time', 'mode']);
function poll() {
  const live = $('#live'), txt = $('#liveText'), ml = $('#mlive');
  live.className = 'live on'; ml.className = 'mlive on'; txt.textContent = 'polling · 60 s';
  setInterval(async () => {
    try {
      const st = await (await apiFetch('/api/state')).json();
      for (const [k, v] of Object.entries(st)) if (!SKIP_KEYS.has(k) && JSON.stringify(v) !== JSON.stringify(S.data[k])) onUpdate(k, v);
      live.className = 'live on'; ml.className = 'mlive on'; txt.textContent = 'polling · 60 s';
    } catch (e) { live.className = 'live off'; ml.className = 'mlive off'; txt.textContent = 'offline, retrying'; }
  }, 60000);
}
async function connect() {
  const live = $('#live'), txt = $('#liveText');
  // EventSource cannot send headers, so the (short-lived) token rides in the URL; reconnects fetch a fresh one.
  const t = await AUTH.token();
  const es = new EventSource('/api/stream' + (t ? '?token=' + encodeURIComponent(t) : ''));
  const ml = $('#mlive');
  es.onopen = () => { live.className = 'live on'; ml.className = 'mlive on'; txt.textContent = 'live'; };
  es.onerror = () => {
    live.className = 'live off'; ml.className = 'mlive off'; txt.textContent = 'reconnecting';
    if (AUTH.enabled) { es.close(); setTimeout(connect, 3000); }
  };
  es.onmessage = ev => { try { const m = JSON.parse(ev.data); onUpdate(m.key, m.data); } catch (e) { console.error(e); } };
}
setInterval(() => {
  const d = new Date();
  $('#clock').textContent = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ' · ' + d.toISOString().slice(11, 16) + ' UTC';
}, 1000);
setInterval(() => viewTiles().forEach(id => ['since', 'live', 'calendar'].includes(id) && renderTile(id)), 60000); // refresh relative times
boot().catch(e => { $('#grid').innerHTML = empty('Could not reach the server: ' + esc(e.message)); });
