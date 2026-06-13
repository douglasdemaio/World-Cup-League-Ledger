#!/usr/bin/env python3
"""
Build data/2026.json from FIFA timelines + the seeded player index.

For every played WC2026 match, walks the event timeline and aggregates per
player:
  - Type  0 = Goal  -> goals
  - Type  1 = Assist -> assists
  - Type 57 = Goal Prevention (save) -> saves

Joins each player to a club + league via data/players-2026.json and
data/leagues.json, then groups into the {league, total, players[]} shape
the front-end already renders.

The script is idempotent: it only writes 2026.json if the new payload
differs from the existing file. Designed to run from a GitHub Action with
no secrets — both FIFA and Wikipedia are public.
"""

import json
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PLAYERS_FILE = DATA_DIR / "players-2026.json"
LEAGUES_FILE = DATA_DIR / "leagues.json"
OUT_FILE = DATA_DIR / "2026.json"

UA = "Mozilla/5.0 (compatible; WCClubTrackerBot/1.0; +https://wcclubtracker.com)"
FIFA = "https://api.fifa.com/api/v3"
COMP = "17"
SEASON = "285023"

EVENT_GOAL = 0
EVENT_ASSIST = 1
EVENT_SAVE = 57

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

    for i, m in enumerate(interesting, 1):
        print(f"  [{i}/{len(interesting)}] timeline match={m['IdMatch']} stage={m['IdStage']}", file=sys.stderr)
        try:
            tl = fetch_timeline(m["IdStage"], m["IdMatch"])
        except Exception as e:
            print(f"    skip: {e}", file=sys.stderr)
            continue
        for ev in tl.get("Event", []):
            tp = ev.get("Type")
            stat = EVENT_KEY.get(tp)
            if not stat:
                continue
            pid = ev.get("IdPlayer")
            if not pid:
                continue
            counts[stat][pid] += 1
        time.sleep(0.05)

    out = {
        "updated": datetime.now(timezone.utc).strftime("%B %-d, %Y %H:%M UTC — ")
                   + f"{played} of {total_in_tournament} matches played"
                   + (f", {live} live" if live else ""),
        "goals": group_by_league(counts["goals"], players, leagues),
        "assists": group_by_league(counts["assists"], players, leagues),
        "saves": group_by_league(counts["saves"], players, leagues),
        "notes": {
            "goals": f"Live from FIFA. Updated automatically; data through the most recent match. ({played}/{total_in_tournament} matches)",
            "assists": "Live from FIFA. Assists are credited only when FIFA's timeline records an Assist event (Type 1) for the pass before the goal — own goals and direct-free-kick goals don't generate one.",
            "saves": "Live from FIFA. Counts FIFA Goal-Prevention events (Type 57); goals conceded are not deducted.",
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


def write_if_changed(payload):
    new_text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if OUT_FILE.exists():
        old = OUT_FILE.read_text(encoding="utf-8")
        # Compare ignoring the timestamp line so a no-op poll doesn't churn the file.
        if scrub_updated(old) == scrub_updated(new_text):
            print("No data change — leaving 2026.json untouched.", file=sys.stderr)
            return False
    OUT_FILE.write_text(new_text, encoding="utf-8")
    print(f"Wrote {OUT_FILE}", file=sys.stderr)
    return True


def scrub_updated(text):
    """Strip the `updated` line for change-detection."""
    return "\n".join(line for line in text.splitlines() if '"updated":' not in line)


def main():
    payload = aggregate()
    changed = write_if_changed(payload)
    sys.exit(0 if changed or "--allow-no-change" in sys.argv else 0)


if __name__ == "__main__":
    main()
