# Arena Monitor — real-time esports & gaming world monitor

The world-monitor terminal from `worldmonitorgaming.docx`, made as a gaming and esports "vertical pack".
It has a world map with layers and filters. It has 17 tiles that you can minimise and maximise, with saved views per persona.
All feeds are cited and update live over Server-Sent Events.

## Run

```
start.cmd                 # or: python server.py, then open http://localhost:8090
```

It uses only the Python stdlib, with no API keys needed. Data (news items, player-count history) is stored in `%LOCALAPPDATA%\ArenaMonitor\monitor.db`, outside OneDrive, because it changes every few minutes. `ARENA_DATA` moves it.
Optional switches (environment variables; each one adds sources at start-up and shows them in the Sources tile):

| Variable | Adds |
|---|---|
| `PANDASCORE_TOKEN` | Live, upcoming and past matches for all games, including CS2 and Valorant, in Live now, Results, Calendar and Watchlist (free token at pandascore.co) |
| `ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`, or `ARENA_AI_BRIEF=1` with an `ant auth login` profile) | An **AI brief** that Claude (`claude-opus-5-5`, effort low) writes every 30 min from the last 24 h of headlines. It runs only when there are new headlines. Each item lists the headlines it is based on, and items with no valid citation are dropped. `ARENA_AI_MODEL` changes the model. Cost: about 10k input tokens per run |
| `TWITCH_CLIENT_ID` + `TWITCH_CLIENT_SECRET` | Twitch top streams in **Live now** |
| `PORT` | The port (default 8090) |

Example: `set PANDASCORE_TOKEN=... && start.cmd`. To keep it on every start, use `setx PANDASCORE_TOKEN ...` once and then open a new terminal.

## Layout

| Area | What it has |
|---|---|
| **Map layers** | LoL matches at the league's home studio (live, upcoming and finished); places mentioned in headlines; Valve server relay POPs (53, from Valve's SDR config); USGS M4.5+ earthquakes, flagged when within 300 km of a relay |
| **Filters** | time window, game (15 titles/families), region (Americas, EMEA, APAC), free-text search |
| **Views** | All, Fan, Team / org, Publisher / analyst, Sponsor / brand, Ops, and your own saved views (tiles, filters and minimised state) |
| **Tiles** | Since your last visit · Brief: top 5 (rule-ranked, cited) · Live now · Results · Calendar · News · Signals · Live player counts · Steam most played · Trends & forecasts · Markets · Releases · Free games & deals · Platform status & hazards · Live video (Twitch embed) · Watchlist · Sources & health |

## Sources and terms (checked 2026-11-23)

Use: private, single-user, local monitoring. The dashboard shows only headline, link and date (news) or small numbers (APIs).
Do not publish the dashboard or share it as a service: most publisher terms allow only personal, non-commercial use.
Each mode below comes from the source's terms page, where it could be read. Pages that could not be read are marked unverified.

| Source | Poll | Terms | Mode | Notes |
|---|---|---|---|---|
| HLTV RSS | 60 min | hltv.org/terms (403, unread) | unverified, treated as link-only | feed `ttl` is 60 min |
| VLR.gg RSS | 15 min | https://www.vlr.gg/terms | personal, non-commercial | terms ban bots and building a database |
| Dexerto RSS | 10 min | https://www.dexerto.com/terms-and-conditions/ | domestic and private use | credit the site and authors |
| GamesIndustry.biz RSS | 10 min | https://www.gamesindustry.biz/terms-and-conditions | private, non-commercial | |
| Eurogamer RSS | 10 min | https://www.eurogamer.net/terms-and-conditions | private, non-commercial | no showing "for the benefit of any third party" |
| PC Gamer RSS | 15 min | https://futureplc.com/terms-and-conditions-us | personal use | terms ban robots; the RSS feed is offered anyway |
| Game Developer RSS | 15 min | https://www.informa.com/generic-content/terms--conditions/ | personal, non-commercial | keep copyright notices |
| GDELT DOC 2.0 | 30 min | https://www.gdeltproject.org/about.html | free use with citation | cite and link GDELT (in the footer); 1 request / 5 s |
| LoL Esports API | 1 / 10 min | https://developer.riotgames.com/terms | **unofficial** | uses the lolesports site key, not a Riot-issued key; may be revoked |
| OpenDota | 1 / 5 min | https://api.opendota.com/api/ | no data licence published | 60 / min, 3,000 / day |
| Steam Web API | 5 min / 1 h / 1 day | https://steamcommunity.com/dev/apiterms | personal use, no Valve endorsement | 100,000 calls / day |
| Steam store `featuredcategories` / `appdetails` | 30 min | none (undocumented) | unverified | not covered by the Web API terms |
| CheapShark | 30 min | https://apidocs.cheapshark.com/ | free; deal links must be CheapShark links | descriptive User-Agent sent |
| Epic free games | 1 h | legal.epicgames.com (login wall) | unverified | undocumented endpoint |
| Statuspage summaries (Discord, Epic, Twitch) | 2 min | none published | public endpoint made for this use | |
| USGS earthquakes | 15 min | https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php | public domain | credit USGS (in the footer) |
| OpenStreetMap tiles (map) | browser | https://operations.osmfoundation.org/policies/tiles/ | attribution | light use only; credit shown on the map |
| Yahoo Finance chart (Markets tile) | **off** | https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html | terms ban automated collection | opt-in only with `ARENA_YAHOO=1`, at your own risk |
| PandaScore (optional) | 90 s / 15 min | https://www.pandascore.co/terms-and-condition | free Fixtures plan | 1,000 / h; no betting use |
| Liquipedia (not connected) | — | https://liquipedia.net/api-terms-of-use | CC BY-SA 3.0 | needs a User-Agent with contact details; parse at most 1 / 30 s |

Changes made after the check: Markets turned off by default (Yahoo terms). Map moved from Esri tiles to OpenStreetMap, because the Esri licence assumes an ArcGIS account. HLTV polled hourly. Credits added to the footer.

## Matcher

`classify()` in `server.py` tags each headline with games (alias lexicon) and signal types:
roster, result, event, patch, business, release, integrity and outage. It also adds the places mentioned (cities, countries and org home cities).
Roster, result and event tags apply only when the item is about a game or esports.

## Forecasts

There is a next-hour player-count estimate for each tracked title. The model is seasonal-naive at 24 h, scaled by the current level.
It falls back to a 1 h linear trend when there is less than 24 h of history. The model is backtested on the stored history, and the range is the 80th percentile of the absolute % error.
The forecast is drawn only when its backtest error is lower than the "same as now" baseline. Otherwise the tile says why it is hidden.
It is labelled as a model estimate in the UI.

## Deploy on Vercel

`vercel.json` + `api/index.py` run the same request handler as a Vercel Python Function in serverless mode
(`ARENA_SERVERLESS=1`):

- There are no background threads. Each `/api/state` request refreshes the sources that are due, in parallel, within about 25 s.
  The first request after a cold start is slow (around 15 s). Later requests return cached data.
- The page polls `/api/state` every 60 s instead of the live SSE stream. The sidebar shows "polling · 60 s".
- Runtime data is in `/tmp` on each instance and is not kept. Player-count sparklines, trends and forecasts need
  history, so they fill only while an instance stays warm. Use the local app (`start.cmd`) for full history.
- Static files are served from `static/` through rewrites.

Deploy: `vercel deploy` (preview), then `vercel deploy --prod`.

**Terms note:** the deployment is public by the owner's choice. Most publisher feeds allow only private,
personal use (see "Sources and terms"). Anyone who reuses this code is responsible for checking those terms first.

## Tests

```
python -m unittest discover -s tests -v   # 14 offline tests: matcher, feeds, forecast gate, PandaScore, AI brief, backoff, data dir
python tests/live_check.py all            # against the running server: sources, API, SSE
python tests/walk.py                      # agent-browser walk, 6 views at 1280 px and 390 px
```

The phasedoc for this arc (`phasedoc/plan.py`, cockpit at http://127.0.0.1:6793) uses these as its acceptance tests.

## Known gaps

- Without `PANDASCORE_TOKEN`, CS2 and Valorant appear through news only. HLTV has no API, and vlrggapi now returns 402.
  The Liquipedia API is not connected, because it needs a contact email in the User-Agent.
- Without Anthropic credentials, the brief is rule-ranked only.
- GDELT rate-limits strictly. On a 429 the server waits 15, then 30, then 60 minutes, up to 2 h.
- In the off-season (now, after Worlds), the LoL calendar is empty.
