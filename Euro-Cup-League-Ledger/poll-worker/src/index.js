// League Ledger predictions worker.
// Routes:
//   GET  /tally  → aggregate counts per stat + total participants
//   POST /vote   → record (or replace) a client's picks; rejected after the R16 deadline.
//
// Storage: a single KV namespace bound as `POLL`. Keys:
//   tally:goals|assists|saves   → JSON {league: count}
//   client:<clientId>           → JSON {goals, assists, saves, at}  (used to apply replacement votes)
//   rate:<ip>                   → presence-only; short TTL for a soft per-IP rate limit

const POLL_DEADLINE = Date.UTC(2028, 5, 8, 0, 0, 0); // 8 June 2028 00:00 UTC — end of R16
const STATS = ["goals", "assists", "saves"];
const ALLOWED_LEAGUES = new Set([
  "Premier League (England)",
  "La Liga (Spain)",
  "Bundesliga (Germany)",
  "Serie A (Italy)",
  "Ligue 1 (France)",
  "Primeira Liga (Portugal)",
  "Eredivisie (Netherlands)",
  "Saudi Pro League",
  "Süper Lig (Turkey)",
  "MLS (USA/Canada)",
  "Liga MX (Mexico)",
  "K League 1 (South Korea)",
  "HNL (Croatia)",
  "Série A (Brazil)",
  "Pro League (Belgium)",
  "Other / a different league"
]);
const RATE_WINDOW_SECONDS = 60; // Cloudflare KV's minimum expirationTtl
const CORS_HEADERS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, POST, OPTIONS",
  "access-control-allow-headers": "content-type",
  "access-control-max-age": "86400",
};

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (req.method === "OPTIONS") return new Response(null, { headers: CORS_HEADERS });
    if (url.pathname === "/tally" && req.method === "GET") return handleTally(env);
    if (url.pathname === "/vote"  && req.method === "POST") return handleVote(req, env);
    if (url.pathname === "/" || url.pathname === "/healthz") {
      return json({ ok: true, deadline: POLL_DEADLINE, closed: Date.now() >= POLL_DEADLINE });
    }
    return json({ error: "not found" }, 404);
  }
};

async function handleTally(env) {
  const out = {
    deadline: POLL_DEADLINE,
    closed: Date.now() >= POLL_DEADLINE,
    total: 0,
    goals: {},
    assists: {},
    saves: {}
  };
  for (const stat of STATS) {
    const raw = await env.POLL.get(`tally:${stat}`);
    const t = raw ? safeParse(raw, {}) : {};
    out[stat] = t;
    out.total += Object.values(t).reduce((a, b) => a + (Number(b) || 0), 0);
  }
  return json(out, 200, { "cache-control": "no-store" });
}

async function handleVote(req, env) {
  if (Date.now() >= POLL_DEADLINE) {
    return json({ error: "voting closed", deadline: POLL_DEADLINE }, 403);
  }

  const ip = req.headers.get("cf-connecting-ip") || "unknown";
  const rateKey = `rate:${ip}`;
  if (await env.POLL.get(rateKey)) {
    return json({ error: "slow down" }, 429);
  }

  let body;
  try { body = await req.json(); }
  catch { return json({ error: "bad json" }, 400); }

  const clientId = String(body.clientId || "");
  if (!/^[A-Za-z0-9_-]{8,64}$/.test(clientId)) {
    return json({ error: "bad clientId" }, 400);
  }

  const picks = {};
  for (const stat of STATS) {
    const v = body[stat];
    if (typeof v === "string" && ALLOWED_LEAGUES.has(v)) picks[stat] = v;
  }
  if (Object.keys(picks).length === 0) {
    return json({ error: "no valid picks" }, 400);
  }

  // Fetch this client's prior picks so a re-vote moves their vote rather than double-counting.
  const prevRaw = await env.POLL.get(`client:${clientId}`);
  const prev = prevRaw ? safeParse(prevRaw, {}) : {};

  for (const stat of STATS) {
    if (!picks[stat]) continue;
    const tallyRaw = await env.POLL.get(`tally:${stat}`);
    const t = tallyRaw ? safeParse(tallyRaw, {}) : {};
    if (prev[stat] && (t[prev[stat]] || 0) > 0) t[prev[stat]] -= 1;
    if (t[prev[stat]] === 0) delete t[prev[stat]];
    t[picks[stat]] = (t[picks[stat]] || 0) + 1;
    await env.POLL.put(`tally:${stat}`, JSON.stringify(t));
  }

  const merged = { ...prev, ...picks, at: Date.now() };
  await env.POLL.put(`client:${clientId}`, JSON.stringify(merged));
  await env.POLL.put(rateKey, "1", { expirationTtl: RATE_WINDOW_SECONDS });

  return json({ ok: true, picks });
}

function json(obj, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...CORS_HEADERS, "content-type": "application/json", ...extraHeaders }
  });
}

function safeParse(s, fallback) {
  try { return JSON.parse(s); } catch { return fallback; }
}
