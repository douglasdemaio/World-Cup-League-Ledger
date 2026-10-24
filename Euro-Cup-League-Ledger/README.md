# UEFA European Championship League Ledger

**Site will be live at:** <https://ecclubtracker.com/>

Every UEFA European Championship goal, assist and save, credited back to the **club league** the player earns his living in. Live tracking for **EURO 2028**, plus settled ledgers for **2024, 2020, 2016 and 2012** — the last five tournaments.

Sort by Premier League, La Liga, Bundesliga, Serie A, Ligue 1, Primeira Liga, Eredivisie, Süper Lig, Saudi Pro League, MLS, Liga MX, and more. Tap any league row to see exactly which players (and clubs) delivered.

## Files

- `index.html` — the single-page app (vanilla JS, no build step)
- `data/2028.json` — the live Euro 2028 ledger payload. The page polls this file every 60s. Schema: `{updated, goals[], assists[], saves[], notes{}}`, each stat an array of `{league, total, players:[{name, nat, n, club}]}`.
- `data/2024.json`, `data/2020.json`, `data/2016.json`, `data/2012.json` — settled ledgers for the four historical tournaments, same schema as the live file. Currently mirror the data embedded inline in `index.html` so the same data-API shape exists per tournament year.
- `data/players-2028.json` — index of tournament players, mapping the competition player ID to `{name, nat, club, clubnat}`. Seeded from the competition's squad endpoints joined with public club data.
- `data/leagues.json` — club-country → league-name defaults, plus per-club overrides for second-tier and unusual cases (Championship, 2. Bundesliga, etc.).
- `scripts/build_ledger.py` — hourly aggregator. Reads the match timeline feed (goals = event type 0, assists = 1, saves = 57), joins on the player index, buckets into leagues, writes the ledger payload. No secrets — public sources only.
- `scripts/seed_players.py` — rebuilds the player index. Run once and after each transfer window.
- `.github/workflows/update-2028.yml` — runs `build_ledger.py` every hour and commits the ledger payload back to `main` only when it changes.
- `euro-cup-league-tracker.html` — original prototype, kept for history.
- `poll-worker/` — optional Cloudflare Worker that aggregates the R16 predictions poll. Deploy with `cd poll-worker && npx wrangler deploy`, then paste the worker URL into `POLL_API_ENDPOINT` in `index.html`. Without it, the poll runs in local-only mode (each visitor sees only their own picks).
- `robots.txt`, `sitemap.xml` — SEO basics

## Hosting

GitHub Pages — set `Settings → Pages → Source` to `main / (root)`. The site is served from `index.html`.

## Contributing

Corrections welcome — especially for the historical tournaments (2012/2016/2020/2024), which list confirmed top contributors per league but aren't fully exhaustive. Open an issue or PR with a source link.

## License

MIT — see source. Not affiliated with UEFA.
