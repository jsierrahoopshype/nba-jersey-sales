#!/usr/bin/env python3
"""Second crossover snapshot: award votes, NBA seasons played, share-card headshots, team logos.

Run by hand against local clones (no network, no token), like snapshot_crossover.py:
  python scripts/snapshot_extra.py --repos /path/to/dir/with/clones

The clones directory must contain nba-player-data and nba-headshots (a sparse,
blob-filtered clone of nba-headshots is enough: files are read with git show).

Writes:
  data/crossover/award_votes.json     per season (end year): every player with an
                                      All-NBA team or MVP votes, league-wide
  data/crossover/seasons_played.json  jersey players: regular seasons with 1+ game
  data/crossover/team_logos.json      team_id -> current logo svg exists in nba-headshots
  data/share/headshots/<slug>.webp    face-fill-320 headshots (the one HoopsMatic look, transparent)
                                      for the share cards; the pages link the 160px face-fill
"""
import argparse
import csv
import io
import json
import os
import re
import subprocess
import unicodedata
from collections import defaultdict

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "source", "nba-jersey-sales-rankings-2001-2026 - Player ranks.csv")
OUT = os.path.join(ROOT, "data", "crossover")
SHOTS = os.path.join(ROOT, "data", "share", "headshots")
ALL_NBA = {"All-NBA First Team": 1, "All-NBA Second Team": 2, "All-NBA Third Team": 3}
FIRST_YEAR, LAST_YEAR = 2001, 2026


def norm(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("'", "").replace("’", "").replace(".", "").lower()
    return re.sub(r"\s+", " ", s).strip()


def slugify(name):
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("'", "").replace("’", "").replace(".", "").lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write(name, obj):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


def git_head(path):
    out = subprocess.check_output(["git", "-C", path, "log", "-1", "--format=%H %cs"]).decode().split()
    return {"commit": out[0], "date": out[1]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", required=True)
    args = ap.parse_args()
    R = lambda *p: os.path.join(args.repos, *p)

    with open(SRC, encoding="utf-8-sig") as f:
        players = sorted({r["Player"] for r in csv.DictReader(f)})
    aliases = load_json(os.path.join(ROOT, "data", "player_aliases.json"))["aliases"]
    want = {}
    for n in players:
        a = aliases.get(n, {})
        want[norm(a.get("stats") or a.get("all") or n)] = n

    # ---- award votes: All-NBA team + MVP vote rank, every player, by season end year.
    pd = R("nba-player-data")
    seasons = defaultdict(dict)
    for a in load_json(os.path.join(pd, "awards.json")):
        if a["AWARD"] in ALL_NBA and a["YEAR"].isdigit() and FIRST_YEAR <= int(a["YEAR"]) <= LAST_YEAR:
            nm = want.get(norm(a["PLAYER / COACH"]), a["PLAYER / COACH"])
            row = seasons[a["YEAR"]].setdefault(nm, {"all_nba": None, "mvp_rank": None})
            row["all_nba"] = ALL_NBA[a["AWARD"]]
    for v in load_json(os.path.join(pd, "awardVotes.json")):
        if v["AWARD"] == "MVP" and v["YEAR"].isdigit() and FIRST_YEAR <= int(v["YEAR"]) <= LAST_YEAR and v["RNK"].isdigit():
            nm = want.get(norm(v["PLAYER"]), v["PLAYER"])
            row = seasons[v["YEAR"]].setdefault(nm, {"all_nba": None, "mvp_rank": None})
            row["mvp_rank"] = int(v["RNK"])
    coverage = {y: {"all_nba_players": sum(1 for r in seasons.get(str(y), {}).values() if r["all_nba"]),
                    "mvp_vote_players": sum(1 for r in seasons.get(str(y), {}).values() if r["mvp_rank"])}
                for y in range(FIRST_YEAR, LAST_YEAR + 1)}
    write("award_votes.json", {
        "source": {"nba-player-data": git_head(pd), "files": ["awards.json", "awardVotes.json"]},
        "note": "awardVotes.json carries the MVP vote rank (RNK), not the vote share.",
        "coverage": coverage, "seasons": seasons})

    # ---- regular seasons played (1+ game), all years, for the jersey players.
    played = defaultdict(set)
    for r in load_json(os.path.join(pd, "rsStats.json")):
        n = want.get(norm(r["PLAYER"]))
        if n and r["YEAR"].isdigit() and (r["GP"] or "0").isdigit() and int(r["GP"]) > 0:
            played[n].add(int(r["YEAR"]))
    write("seasons_played.json", {n: sorted(v) for n, v in played.items()})

    # ---- nba-headshots: current team logos + face-fill-320 headshots for the share cards.
    hs = R("nba-headshots")
    tree = set(subprocess.check_output(["git", "-C", hs, "ls-tree", "-r", "--name-only", "HEAD"]).decode().split())
    teams = load_json(os.path.join(OUT, "teams.json"))
    write("team_logos.json", {str(t["team_id"]): ("teams/logos/current/svg/%d.svg" % t["team_id"]) in tree for t in teams})
    os.makedirs(SHOTS, exist_ok=True)
    for fn in os.listdir(SHOTS):
        os.remove(os.path.join(SHOTS, fn))
    shots = load_json(os.path.join(OUT, "headshots.json"))
    for n, h in shots.items():
        # Same file stem as the face-fill image the pages use (<nba_id>-<slug>), in face-fill-320/.
        stem = h["image"].rsplit("/", 1)[1].rsplit(".", 1)[0]
        path = "players/headshots/face-fill-320/%s.webp" % stem
        if path not in tree:
            continue
        data = subprocess.check_output(["git", "-C", hs, "show", "HEAD:" + path])
        # Already square with the face filling it; keep the transparency (the card draws
        # the grey disc behind it).
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        im.save(os.path.join(SHOTS, slugify(n) + ".webp"), "WEBP", quality=90, method=6)
    print("award seasons", len(seasons), "played", len(played), "of", len(players), "headshots", len(os.listdir(SHOTS)))


if __name__ == "__main__":
    main()
