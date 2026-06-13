#!/usr/bin/env python3
"""
Seed data/players-2026.json from FIFA squads + Wikipedia clubs.

FIFA's API gives us every WC2026 player's IdPlayer, name, and nationality, but
no club. Wikipedia's "2026 FIFA World Cup squads" article has each player's
current club with its country code. This script joins the two by team +
fuzzy-matched name and writes the index the ledger builder reads.

Run on the seed pass and after each transfer window:

    python3 scripts/seed_players.py

Output: data/players-2026.json keyed by FIFA IdPlayer.
"""

import json
import re
import sys
import time
import unicodedata
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT = DATA_DIR / "players-2026.json"
UA = "Mozilla/5.0 (compatible; WCClubTrackerBot/1.0; +https://wcclubtracker.com)"

FIFA = "https://api.fifa.com/api/v3"
COMP = "17"
SEASON = "285023"
WIKI_URL = "https://en.wikipedia.org/w/index.php?title=2026_FIFA_World_Cup_squads&action=raw"


def http_json(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def http_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def norm(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z]", "", s).lower()
    return s


def fifa_team_ids():
    """Return all 48 WC2026 team IDs from the matches list."""
    matches = http_json(f"{FIFA}/calendar/matches?idCompetition={COMP}&idSeason={SEASON}&language=en&count=500")
    ids = {}
    for m in matches["Results"]:
        for side in ("Home", "Away"):
            t = m.get(side)
            if t and t.get("IdTeam"):
                ids[t["IdTeam"]] = t.get("IdCountry") or ""
    return ids


def fifa_squads(team_ids):
    """Return {idCountry: [{IdPlayer, name, nat}, ...]}."""
    out = {}
    for i, (tid, country) in enumerate(sorted(team_ids.items()), 1):
        print(f"  [{i}/{len(team_ids)}] FIFA squad {tid} ({country})", file=sys.stderr)
        try:
            d = http_json(f"{FIFA}/teams/{tid}/squad?idCompetition={COMP}&idSeason={SEASON}")
        except Exception as e:
            print(f"    skip: {e}", file=sys.stderr)
            continue
        players = []
        for p in d.get("Players", []):
            name = (p.get("PlayerName") or [{}])[0].get("Description") or ""
            players.append({"IdPlayer": p["IdPlayer"], "name": name, "nat": p.get("IdCountry") or country})
        out[country or tid] = {"IdTeam": tid, "players": players}
        time.sleep(0.05)
    return out


SECTION_RE = re.compile(r"^===\s*(.+?)\s*===\s*$", re.MULTILINE)
PLAYER_START_RE = re.compile(r"\{\{\s*nat fs[^|}]*?player\s*\|", re.DOTALL)


def extract_player_bodies(text):
    """Yield the inside of each `{{nat fs ... player | ... }}` template, with
    balanced nested templates. Python's `re` can't do recursion, so scan."""
    for m in PLAYER_START_RE.finditer(text):
        i = m.end()
        depth = 1
        n = len(text)
        while i < n - 1 and depth > 0:
            if text[i] == "{" and text[i + 1] == "{":
                depth += 1
                i += 2
            elif text[i] == "}" and text[i + 1] == "}":
                depth -= 1
                i += 2
            else:
                i += 1
        if depth == 0:
            yield text[m.end():i - 2]


def collapse_links(text):
    """Replace [[A|B]] -> B and [[A]] -> A before splitting on '|' delimiters."""
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    return text


def parse_field(body, key):
    m = re.search(rf"\|\s*{key}\s*=\s*([^|\n}}]*)", body)
    return m.group(1).strip() if m else ""


def strip_wiki(text):
    text = re.sub(r"\{\{[^}]*\}\}", "", text)
    return text.strip()


def parse_wiki_squads(wikitext):
    """Return {section_name: [{name, club, clubnat}, ...]}."""
    out = {}
    sections = list(SECTION_RE.finditer(wikitext))
    for i, m in enumerate(sections):
        section = m.group(1).strip()
        start = m.end()
        end = sections[i + 1].start() if i + 1 < len(sections) else len(wikitext)
        body = wikitext[start:end]
        players = []
        for raw in extract_player_bodies(body):
            fields = "|" + collapse_links(raw)
            name = strip_wiki(parse_field(fields, "name"))
            club = strip_wiki(parse_field(fields, "club"))
            clubnat = parse_field(fields, "clubnat").upper()
            if name:
                players.append({"name": name, "club": club, "clubnat": clubnat})
        if players:
            out[section] = players
    return out


# Complete FIFA 3-letter IdCountry -> Wikipedia section name for the 48 WC2026 teams.
COUNTRY_TO_SECTION = {
    "ALG": "Algeria", "ARG": "Argentina", "AUS": "Australia", "AUT": "Austria",
    "BEL": "Belgium", "BIH": "Bosnia and Herzegovina", "BRA": "Brazil",
    "CAN": "Canada", "CIV": "Ivory Coast", "COD": "DR Congo", "COL": "Colombia",
    "CPV": "Cape Verde", "CRO": "Croatia", "CUW": "Curaçao", "CZE": "Czech Republic",
    "ECU": "Ecuador", "EGY": "Egypt", "ENG": "England", "ESP": "Spain",
    "FRA": "France", "GER": "Germany", "GHA": "Ghana", "HAI": "Haiti",
    "IRN": "Iran", "IRQ": "Iraq", "JOR": "Jordan", "JPN": "Japan",
    "KOR": "South Korea", "KSA": "Saudi Arabia", "MAR": "Morocco", "MEX": "Mexico",
    "NED": "Netherlands", "NOR": "Norway", "NZL": "New Zealand", "PAN": "Panama",
    "PAR": "Paraguay", "POR": "Portugal", "QAT": "Qatar", "RSA": "South Africa",
    "SCO": "Scotland", "SEN": "Senegal", "SUI": "Switzerland", "SWE": "Sweden",
    "TUN": "Tunisia", "TUR": "Turkey", "URU": "Uruguay", "USA": "United States",
    "UZB": "Uzbekistan",
}


def section_for(country, sections_by_norm):
    if country in COUNTRY_TO_SECTION:
        return COUNTRY_TO_SECTION[country]
    # FIFA 3-letter codes often share initials with the country name
    return sections_by_norm.get(norm(country), None)


def best_match(name, candidates):
    """Pick the closest candidate by normalized similarity. Returns (idx, score)."""
    target = norm(name)
    if not target:
        return None, 0.0
    best_i, best_s = None, 0.0
    for i, c in enumerate(candidates):
        score = SequenceMatcher(None, target, norm(c["name"])).ratio()
        # Boost on last-name match (FIFA uppercases the surname)
        target_tokens = set(re.findall(r"[A-Za-z]+", name))
        cand_tokens = set(re.findall(r"[A-Za-z]+", c["name"]))
        if target_tokens & cand_tokens:
            score += 0.05
        if score > best_s:
            best_i, best_s = i, score
    return best_i, best_s


def main():
    print("Fetching Wikipedia squads…", file=sys.stderr)
    wikitext = http_text(WIKI_URL)
    wiki = parse_wiki_squads(wikitext)
    print(f"  parsed {len(wiki)} team sections, {sum(len(v) for v in wiki.values())} players", file=sys.stderr)

    print("Fetching FIFA team list…", file=sys.stderr)
    team_ids = fifa_team_ids()
    print(f"  {len(team_ids)} teams", file=sys.stderr)

    print("Fetching FIFA squads…", file=sys.stderr)
    fifa = fifa_squads(team_ids)

    # Map by normalized section name to handle slight title differences
    sections_by_norm = {norm(k): k for k in wiki.keys()}

    out = {}
    miss_team = []
    low_score = []
    for country, info in fifa.items():
        section = section_for(country, sections_by_norm)
        wiki_players = wiki.get(section) if section else None
        if not wiki_players:
            miss_team.append((country, section))
            continue
        used = set()
        for fp in info["players"]:
            # Restrict candidate pool by excluding already-used wiki entries
            pool = [(i, wp) for i, wp in enumerate(wiki_players) if i not in used]
            cands = [wp for _, wp in pool]
            idx, score = best_match(fp["name"], cands)
            if idx is None:
                continue
            real_idx = pool[idx][0]
            wp = wiki_players[real_idx]
            if score < 0.55:
                low_score.append((country, fp["name"], wp["name"], round(score, 2)))
                # Still record it — better than dropping the player
            used.add(real_idx)
            out[fp["IdPlayer"]] = {
                "name": fp["name"],
                "nat": fp["nat"],
                "club": wp["club"],
                "clubnat": wp["clubnat"],
            }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"\nWrote {OUT} with {len(out)} players.", file=sys.stderr)
    if miss_team:
        print(f"Teams without a Wikipedia section match ({len(miss_team)}): {miss_team}", file=sys.stderr)
    if low_score:
        print(f"Low-confidence name matches ({len(low_score)}):", file=sys.stderr)
        for row in low_score[:30]:
            print(f"  {row}", file=sys.stderr)


if __name__ == "__main__":
    main()
