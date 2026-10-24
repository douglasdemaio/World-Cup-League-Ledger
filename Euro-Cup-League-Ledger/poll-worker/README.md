# wcll-poll — predictions worker

Tiny Cloudflare Worker that backs the "Your call" panel on
[wcclubtracker.com](https://wcclubtracker.com). It stores per-stat top-league
predictions in KV and rejects writes after the R16 deadline
(**8 June 2028 00:00 UTC**).

## Routes

| Method | Path      | What it does                                                       |
| ------ | --------- | ------------------------------------------------------------------ |
| `GET`  | `/tally`  | Returns `{deadline, closed, total, goals, assists, saves}`.        |
| `POST` | `/vote`   | Records (or replaces) one client's picks. JSON body:               |
|        |           | `{clientId, goals?, assists?, saves?}` — each value an allow-listed league string. |
| `GET`  | `/`       | Health check.                                                      |

Voting `POST` after the deadline returns HTTP 403 with the deadline echoed back.
Per-IP soft rate limit: one write per 5 s.

## One-time deploy

You need a Cloudflare account (free tier is plenty) and `node >= 18`.

```bash
cd poll-worker
npm install
npx wrangler login                  # browser-based; one-off
npx wrangler kv namespace create POLL
# → copy the printed id into wrangler.toml, replacing REPLACE_WITH_KV_NAMESPACE_ID
npx wrangler deploy
```

`wrangler deploy` prints the live URL — something like
`https://wcll-poll.<your-subdomain>.workers.dev`. Paste that into
`index.html` at the `POLL_API_ENDPOINT` constant in the inline script
(search for it; it's right above the polls section), commit, push.

## Local dev

```bash
npm run dev      # serves on http://localhost:8787 with a local KV
npm run tail     # stream live logs from the deployed worker
```

## Resetting tallies (e.g. after a test run)

```bash
npx wrangler kv key list   --binding POLL
npx wrangler kv key delete --binding POLL tally:goals
npx wrangler kv key delete --binding POLL tally:assists
npx wrangler kv key delete --binding POLL tally:saves
# (optionally also delete client:* and rate:* keys)
```

## Locking down CORS

The worker ships with `access-control-allow-origin: *` for convenience.
Tighten it by replacing the `*` in `CORS_HEADERS` with
`https://wcclubtracker.com` once you've confirmed everything works.
