# World Cup League Ledger

**Live site:** <https://wcclubtracker.com/>

Every FIFA World Cup goal, assist and save, credited back to the **club league** the player earns his living in. Live tracking for **2026**, plus settled ledgers for **2022, 2018, 2014 and 2010** — the last five tournaments.

Sort by Premier League, La Liga, Bundesliga, Serie A, Ligue 1, MLS, Saudi Pro League, Eredivisie, Primeira Liga, Liga MX, K League, J League, Süper Lig, HNL, A-League, and more. Tap any league row to see exactly which players (and clubs) delivered.

## Files

- `index.html` — the single-page app (vanilla JS, no build step)
- `data/2026.json` — the live 2026 ledger. The page polls this file every 60s. Schema: `{updated, goals[], assists[], saves[], notes{}}`, each stat an array of `{league, total, players:[{name, nat, n, club}]}`.
- `data/players-2026.json` — index of all ~1,248 tournament players, mapping FIFA `IdPlayer` → `{name, nat, club, clubnat}`. Seeded from FIFA's squad endpoints joined to Wikipedia's `2026 FIFA World Cup squads` page (FIFA's API doesn't expose club info).
- `data/leagues.json` — club-country → league-name defaults, plus per-club overrides for second-tier and unusual cases (Championship, 2. Bundesliga, J2, etc.).
- `scripts/build_ledger.py` — hourly aggregator. Reads FIFA match timelines (goals = event type 0, assists = 1, saves = 57), joins on the player index, buckets into leagues, writes `data/2026.json`. No secrets — FIFA + Wikipedia are public.
- `scripts/seed_players.py` — rebuilds `data/players-2026.json`. Run once and after each transfer window.
- `.github/workflows/update-2026.yml` — runs `build_ledger.py` every hour and commits `data/2026.json` back to `main` only when the payload changed.
- `robots.txt`, `sitemap.xml` — SEO basics
- `world-cup-league-tracker.html` — original prototype, kept for history

## Hosting

GitHub Pages — set `Settings → Pages → Source` to `main / (root)`. The site is served from `index.html`.

## Contributing

Corrections welcome — especially for the historical tournaments (2018/2014/2010), which list confirmed top contributors per league but aren't fully exhaustive. Open an issue or PR with a source link.

## License

MIT — see source. Not affiliated with FIFA.
