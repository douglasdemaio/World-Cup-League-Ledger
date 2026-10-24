#!/usr/bin/env python3
"""
Build the live Euro ledger payload from the competition timeline feed and the
seeded player index.

For every played match, walks the event timeline and aggregates per player:
  - Type  0 = Goal  -> goals
  - Type  1 = Assist -> assists
  - Type 57 = Goal Prevention (save) -> saves

Joins each player to a club + league via data/players-2028.json and
data/leagues.json, then groups into the {league, total, players[]} shape
the front-end already renders.

The script is idempotent: it only writes the ledger payload if the new data
differs from the existing file. Designed to run from a GitHub Action with no
secrets — the public data feeds are enough.
"""

import json
import re
import sys
import time
import unicodedata
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PLAYERS_FILE = DATA_DIR / "players-2028.json"
LEAGUES_FILE = DATA_DIR / "leagues.json"
OUT_FILE = DATA_DIR / "2028.json"

UA = "Mozilla/5.0 (compatible; EuroLedgerBot/1.0; +https://wcclubtracker.com)"
FIFA = "https://api.fifa.com/api/v3"
COMP = "17"
SEASON = "285023"

# FIFA's v3 timeline never emits save events for the World Cup (verified empty
# on 2028 *and* 2022 — Type 57/60/14 are simply not in the feed), and FIFA's
# /topsavers endpoint exists but stays empty until they decide to publish
# keeper stats post-tournament. Until then we pull saves from ESPN's public
# match-summary endpoint, which carries per-match keeper save totals as the
# "saves" leader per team.
ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world"

EVENT_GOAL = 0
EVENT_ASSIST = 1
EVENT_SAVE = 57

# Save events have historically shown up under more than one code in FIFA's
# v3 timeline depending on competition. We count the primary one but also
# *watch* these so the diagnostics below tell us if saves live elsewhere.
SAVE_CANDIDATE_TYPES = {57, 60, 14}

# Fields a timeline event may use to carry the acting player, in priority order.
# Goals/assists use IdPlayer; keeper events sometimes use IdSubPlayer or nest
# the player inside a "Player" object.
PLAYER_ID_FIELDS = ("IdPlayer", "IdSubPlayer")

# FIFA 3-letter nationality -> display name shown next to the player.
NAT_DISPLAY = {
    "ALG": "Algeria", "ARG": "Argentina", "AUS": "Australia", "AUT": "Austria",
    "BEL": "Belgium", "BIH": "Bosnia & Herzegovina", "BRA": "Brazil",
    "CAN": "Canada", "CIV": "Ivory Coast", "COD": "DR Congo", "COL": "Colombia",
    "CPV": "Cape Verde", "CRO": "Croatia", "CUW": "Curaçao", "CZE": "Czechia",
    "ECU": "Ecuador", "EGY": "Egypt", "ENG": "England", "ESP": "Spain",
    "FRA": "France", "GER": "Germany", "GHA": "Ghana", "HAI": "Haiti",
    "IRN": "Iran", "IRQ": "Iraq", "JOR": "Jordan", "JPN": "Japan",
    "KOR": "South Korea", "KSA": "Saudi Arabia", "MAR": "Morocco", "MEX": "Mexico",
    "NED": "Netherlands", "NOR": "Norway", "NZL": "New Zealand", "PAN": "Panama",
    "PAR": "Paraguay", "POR": "Portugal", "QAT": "Qatar", "RSA": "South Africa",
    "SCO": "Scotland", "SEN": "Senegal", "SUI": "Switzerland", "SWE": "Sweden",
    "TUN": "Tunisia", "TUR": "Turkey", "URU": "Uruguay", "USA": "USA",
    "UZB": "Uzbekistan",
}


def event_player_id(ev):
    """Best-effort actor id for an event, tolerant of the keeper-event shapes."""
    for f in PLAYER_ID_FIELDS:
        v = ev.get(f)
        if v:
            return v
    # Some feeds nest the player: {"Player": {"IdPlayer": "..."}}
    p = ev.get("Player")
    if isinstance(p, dict) and p.get("IdPlayer"):
        return p["IdPlayer"]
    return None


def http_json(url, retries=3):
    last_err = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed after {retries} attempts: {last_err}")


def load_player_index():
    return json.loads(PLAYERS_FILE.read_text(encoding="utf-8"))


def load_leagues():
    return json.loads(LEAGUES_FILE.read_text(encoding="utf-8"))


def league_for(club, clubnat, leagues):
    if not club:
        return None
    over = leagues["overrides"].get(club)
    if over:
        return over
    return leagues["defaults"].get(clubnat)


# Title-case a FIFA-style name like "Raul JIMENEZ" -> "Raúl Jiménez".
# We don't have accents; best-effort title-case is good enough for display
# since the back-end source of truth is the player ID.
def display_name(fifa_name):
    parts = fifa_name.split()
    out = []
    for p in parts:
        if p.isupper() and len(p) > 1:
            out.append(p.capitalize())
        else:
            out.append(p)
    return " ".join(out)


def fetch_matches():
    d = http_json(f"{FIFA}/calendar/matches?idCompetition={COMP}&idSeason={SEASON}&language=en&count=500")
    return d["Results"]


def fetch_timeline(stage, match):
    return http_json(f"{FIFA}/timelines/{COMP}/{SEASON}/{stage}/{match}?language=en")


def _norm_name(s):
    """Strip diacritics + everything but letters, lowercase. Collapses
    'Al-Owais' ↔ 'ALOWAIS', 'Kim Seung-Gyu' ↔ 'KIM Seunggyu', and absorbs
    the case difference between ESPN (mixed) and FIFA (surnames in caps)."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


def build_player_lookup(players):
    """(nat, normalized name) -> FIFA pid. Indexed by full name, surname, AND
    first name so single-name keepers ('Alisson') still match ESPN's
    'Alisson Becker', and hyphenated names collapse to the same key."""
    out = {}
    for pid, meta in players.items():
        nat = meta.get("nat", "")
        name = meta.get("name", "")
        if not nat or not name:
            continue
        parts = name.split()
        out.setdefault((nat, _norm_name(name)), pid)
        if parts:
            out.setdefault((nat, _norm_name(parts[-1])), pid)
            out.setdefault((nat, _norm_name(parts[0])), pid)
    return out


def fifa_dates_for_espn(matches):
    """ESPN scoreboard query needs YYYYMMDD; derive from FIFA's match dates so
    we only ask ESPN for days that actually have WC matches."""
    out = set()
    for m in matches:
        if m.get("MatchStatus") not in (0, 3):  # played or live
            continue
        d = m.get("Date") or ""
        if len(d) >= 10:
            out.add(d[0:4] + d[5:7] + d[8:10])
    return sorted(out)


def fetch_espn_completed_events(dates):
    out = []
    for ds in dates:
        try:
            d = http_json(f"{ESPN}/scoreboard?dates={ds}")
        except Exception as e:
            print(f"  ESPN scoreboard {ds}: {e}", file=sys.stderr)
            continue
        for ev in d.get("events", []):
            status = (ev.get("status") or {}).get("type") or {}
            if status.get("completed") or status.get("state") == "in":
                out.append(ev["id"])
        time.sleep(0.05)
    return out


def fetch_saves_from_espn(players, matches):
    """Aggregate {pid: total_saves} from ESPN's per-match saves leaders."""
    lookup = build_player_lookup(players)
    counts = defaultdict(int)
    unmatched = []

    dates = fifa_dates_for_espn(matches)
    event_ids = fetch_espn_completed_events(dates)
    print(f"ESPN: {len(event_ids)} events across {len(dates)} dates for save aggregation", file=sys.stderr)

    for i, eid in enumerate(event_ids, 1):
        try:
            d = http_json(f"{ESPN}/summary?event={eid}")
        except Exception as e:
            print(f"  ESPN summary {eid}: {e}", file=sys.stderr)
            continue
        for team in d.get("leaders", []) or []:
            nat = ((team.get("team") or {}).get("abbreviation") or "").upper()
            for cat in team.get("leaders", []) or []:
                if cat.get("name") != "saves":
                    continue
                for ldr in cat.get("leaders", []) or []:
                    try:
                        n = int(float(ldr.get("displayValue") or 0))
                    except (TypeError, ValueError):
                        n = 0
                    if n <= 0:
                        continue
                    ath = ldr.get("athlete") or {}
                    full = ath.get("displayName") or ""
                    last = ath.get("lastName") or ""
                    first = ath.get("firstName") or (full.split(" ", 1)[0] if full else "")
                    pid = (lookup.get((nat, _norm_name(full)))
                           or lookup.get((nat, _norm_name(last)))
                           or lookup.get((nat, _norm_name(first))))
                    if pid:
                        counts[pid] += n
                    else:
                        unmatched.append((nat, full, n))
        time.sleep(0.05)

    if unmatched:
        print(f"  ESPN: {len(unmatched)} saves leaders not matched to FIFA index (sample): {unmatched[:5]}", file=sys.stderr)
    print(f"  ESPN: matched saves for {len(counts)} keepers", file=sys.stderr)
    return counts


def aggregate():
    players = load_player_index()
    leagues = load_leagues()
    matches = fetch_matches()

    # MatchStatus: 0 = played, 1 = scheduled, 3 = live (observed empirically)
    interesting = [m for m in matches if m.get("MatchStatus") in (0, 3)]
    total_in_tournament = sum(1 for m in matches if m.get("MatchStatus") != 4)  # exclude postponed
    played = sum(1 for m in matches if m.get("MatchStatus") == 0)
    live = sum(1 for m in matches if m.get("MatchStatus") == 3)

    print(f"Matches: {played} played, {live} live, {total_in_tournament - played - live} scheduled", file=sys.stderr)

    counts = {"goals": defaultdict(int), "assists": defaultdict(int), "saves": defaultdict(int)}
    EVENT_KEY = {EVENT_GOAL: "goals", EVENT_ASSIST: "assists", EVENT_SAVE: "saves"}

    # --- diagnostics: prove what the timeline actually contains ---
    type_hist = defaultdict(int)          # every event Type we ever see
    save_probe = {"seen": 0, "with_idplayer": 0, "with_any_player": 0, "fields": defaultdict(int)}

    for i, m in enumerate(interesting, 1):
        print(f"  [{i}/{len(interesting)}] timeline match={m['IdMatch']} stage={m['IdStage']}", file=sys.stderr)
        try:
            tl = fetch_timeline(m["IdStage"], m["IdMatch"])
        except Exception as e:
            print(f"    skip: {e}", file=sys.stderr)
            continue
        for ev in tl.get("Event", []):
            tp = ev.get("Type")
            type_hist[tp] += 1

            # Probe anything that *might* be a save so the logs reveal the truth.
            if tp in SAVE_CANDIDATE_TYPES:
                save_probe["seen"] += 1
                if ev.get("IdPlayer"):
                    save_probe["with_idplayer"] += 1
                if event_player_id(ev):
                    save_probe["with_any_player"] += 1
                for k, v in ev.items():
                    if v not in (None, "", [], {}):
                        save_probe["fields"][k] += 1

            stat = EVENT_KEY.get(tp)
            if not stat:
                continue
            pid = event_player_id(ev)        # was ev.get("IdPlayer") — now tolerant
            if not pid:
                continue
            counts[stat][pid] += 1
        time.sleep(0.05)

    # Dump the diagnostics. These three lines tell you exactly why saves are empty.
    top_types = sorted(type_hist.items(), key=lambda kv: -kv[1])[:25]
    print(f"DIAG event-type histogram (top 25): {top_types}", file=sys.stderr)
    print(f"DIAG save-candidate types {sorted(SAVE_CANDIDATE_TYPES)}: "
          f"seen={save_probe['seen']} with_IdPlayer={save_probe['with_idplayer']} "
          f"with_any_player_field={save_probe['with_any_player']}", file=sys.stderr)
    print(f"DIAG fields present on save-candidate events: {dict(save_probe['fields'])}", file=sys.stderr)
    print(f"DIAG raw counts (timeline): goals={len(counts['goals'])} assists={len(counts['assists'])} saves={len(counts['saves'])} players", file=sys.stderr)

    # FIFA's timeline doesn't carry save events for this competition; pull them
    # from ESPN's per-match summary leaders instead and aggregate the same way.
    saves_counts = fetch_saves_from_espn(players, matches)

    out = {
        "updated": datetime.now(timezone.utc).strftime("%B %-d, %Y %H:%M UTC — ")
                   + f"{played} of {total_in_tournament} matches played"
                   + (f", {live} live" if live else ""),
        "goals": group_by_league(counts["goals"], players, leagues),
        "assists": group_by_league(counts["assists"], players, leagues),
        "saves": group_by_league(saves_counts, players, leagues),
        "notes": {
            "goals": f"Live from FIFA. Updated automatically; data through the most recent match. ({played}/{total_in_tournament} matches)",
            "assists": "Live from FIFA. Assists are credited only when FIFA's timeline records an Assist event (Type 1) for the pass before the goal — own goals and direct-free-kick goals don't generate one.",
            "saves": "Live from ESPN match summaries (FIFA's v3 timeline doesn't carry keeper events). Counts each team's top saver per match; goals conceded are not deducted.",
        },
    }
    return out


def group_by_league(counts_by_pid, players, leagues):
    """Turn {idPlayer: n} into a list of {league, total, players[]} buckets."""
    by_league = defaultdict(list)
    unknown = 0
    for pid, n in counts_by_pid.items():
        meta = players.get(pid)
        if not meta:
            unknown += 1
            continue
        league = league_for(meta["club"], meta["clubnat"], leagues)
        if not league:
            unknown += 1
            continue
        by_league[league].append({
            "name": display_name(meta["name"]),
            "nat": NAT_DISPLAY.get(meta["nat"], meta["nat"]),
            "n": n,
            "club": meta["club"],
        })
    if unknown:
        print(f"  {unknown} stat events from unknown players/leagues — dropped", file=sys.stderr)

    out = []
    for league, players_list in by_league.items():
        players_list.sort(key=lambda p: (-p["n"], p["name"]))
        out.append({"league": league, "total": sum(p["n"] for p in players_list), "players": players_list})
    out.sort(key=lambda b: (-b["total"], b["league"]))
    return out


def validate_payload(payload):
    """Warn loudly when a 'Live from FIFA' category is empty after matches start."""
    updated = payload.get("updated", "")
    played = 0
    m = re.search(r"(\d+)\s+of\s+\d+\s+matches played", updated)
    if m:
        played = int(m.group(1))
    for stat in ("goals", "assists", "saves"):
        rows = payload.get(stat) or []
        total = sum(r.get("total", 0) for r in rows)
        if played > 0 and total == 0:
            print(f"WARN {stat!r} is empty after {played} matches played — "
                  f"caption still claims live FIFA data. Check the DIAG lines above.",
                  file=sys.stderr)


def write_if_changed(payload):
    new_text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if OUT_FILE.exists():
        old = OUT_FILE.read_text(encoding="utf-8")
        # Compare ignoring the timestamp line so a no-op poll doesn't churn the file.
        if scrub_updated(old) == scrub_updated(new_text):
            print("No data change — leaving 2028.json untouched.", file=sys.stderr)
            return False
    OUT_FILE.write_text(new_text, encoding="utf-8")
    print(f"Wrote {OUT_FILE}", file=sys.stderr)
    return True


def scrub_updated(text):
    """Strip the `updated` line for change-detection."""
    return "\n".join(line for line in text.splitlines() if '"updated":' not in line)


def main():
    payload = aggregate()
    validate_payload(payload)
    changed = write_if_changed(payload)
    sys.exit(0 if changed or "--allow-no-change" in sys.argv else 0)


if __name__ == "__main__":
    main()
