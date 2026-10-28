#!/usr/bin/env python3
"""One-off snapshot of cross-section data for the jersey pages.

Jersey lists change twice a year, so this is run by hand against local clones
of the sibling repos and the trimmed result is committed to data/crossover/.
Nothing here fetches from the network and no token is needed.

Usage:
  python scripts/snapshot_crossover.py --repos /path/to/dir/with/clones

The clones directory must contain: nba-headshots, nba-player-data,
salary-season-finder, nba-career-map, nba-polymarket, nba-attendance,
bar-chart-race. Players are matched by normalized name (accents, apostrophes
and periods stripped). Name variants for the same person are pinned in
data/player_aliases.json. Anything that does not match cleanly is left out and
listed in data/crossover/misses.json.
"""
import argparse
import csv
import glob
import json
import os
import re
import subprocess
import unicodedata
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "source", "nba-jersey-sales-rankings-2001-2026 - Player ranks.csv")
OUT = os.path.join(ROOT, "data", "crossover")
# The one HoopsMatic headshot look: face-fill (the face fills the circle), 160px.
HEADSHOT_BASE = "https://jsierrahoopshype.github.io/nba-headshots/players/headshots/face-fill/"

# Awards worth showing next to a jersey rank (season = YEAR, the season's end year).
AWARDS_KEEP = {
    "Most Valuable Player", "Finals MVP", "NBA Champion", "All-NBA First Team",
    "All-NBA Second Team", "All-NBA Third Team", "All-Star", "Defensive Player of the Year",
    "Rookie of the Year", "Sixth Man of the Year", "Most Improved Player", "Scoring Leader",
    "Clutch Player of the Year", "All-Defensive First Team", "All-Defensive Second Team",
    "All-Rookie First Team", "All-Rookie Second Team", "NBA Cup MVP", "NBA Cup Champion",
}
PROD_MIN_GP = 20


def norm(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("'", "").replace("’", "").replace(".", "").lower()
    return re.sub(r"\s+", " ", s).strip()


def career_slug(name):
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def git_head(path):
    try:
        out = subprocess.check_output(["git", "-C", path, "log", "-1", "--format=%H %cs"]).decode().split()
        return {"commit": out[0], "date": out[1]}
    except Exception:
        return {"commit": None, "date": None}


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write(name, obj):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


def money(s):
    s = (s or "").replace("$", "").replace(",", "").strip()
    return int(s) if s.isdigit() else None


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", required=True)
    args = ap.parse_args()
    R = lambda *p: os.path.join(args.repos, *p)

    with open(SRC, encoding="utf-8-sig") as f:
        players = sorted({r["Player"] for r in csv.DictReader(f)})
    aliases = load_json(os.path.join(ROOT, "data", "player_aliases.json"))["aliases"]

    def lookup_name(name, source):
        a = aliases.get(name, {})
        return a.get(source) or a.get("all") or name

    misses = defaultdict(list)
    sources = {}

    # ---- headshots (nba-headshots players_all.json; NBA id is the key, not the slug)
    sources["nba-headshots"] = git_head(R("nba-headshots"))
    hs_all = load_json(R("nba-headshots", "players", "metadata", "players_all.json"))["players"]
    fill = set(subprocess.check_output(
        ["git", "-C", R("nba-headshots"), "ls-tree", "-r", "--name-only", "HEAD",
         "players/headshots/face-fill"]).decode().split())
    by_name = defaultdict(list)
    for p in hs_all:
        by_name[norm(p["full_name"])].append(p)
    headshots = {}
    for n in players:
        cands = by_name.get(norm(lookup_name(n, "headshots")), [])
        if len(cands) != 1:
            misses["headshots"].append({"player": n, "reason": "no match" if not cands else "ambiguous"})
            continue
        p = cands[0]
        fn = (p.get("headshot") or {}).get("filename") or ""
        stem = fn.rsplit(".", 1)[0]
        if not stem or ("players/headshots/face-fill/%s.webp" % stem) not in fill:
            misses["headshots"].append({"player": n, "reason": "no face-fill image", "nba_id": p["nba_id"]})
            continue
        headshots[n] = {"nba_id": p["nba_id"], "full_name": p["full_name"], "image": HEADSHOT_BASE + stem + ".webp"}
    write("headshots.json", headshots)

    # ---- teams (30 slugs) + plain colors from bar-chart-race's team color map
    teams = load_json(R("nba-headshots", "teams", "metadata", "teams.json"))
    sources["bar-chart-race"] = git_head(R("bar-chart-race"))
    cfg = open(R("bar-chart-race", "src", "bar_race", "config.py"), encoding="utf-8").read()
    block = cfg.split("NBA_TEAM_COLORS", 1)[1].split("}", 1)[0]
    colors = dict(re.findall(r'"([A-Z]{3})":\s*"(#[0-9A-Fa-f]{6})"', block))
    write("teams.json", [{
        "team_id": t["team_id"], "slug": t["slug"], "abbrev": t["abbrev"], "full_name": t["full_name"],
        "city": t["city"], "nickname": t["nickname"], "historical_names": t.get("historical_names", []),
        "color": colors.get(t["abbrev"]),
    } for t in teams])

    # ---- nba-player-data: awards, MVP votes, stats, salaries
    sources["nba-player-data"] = git_head(R("nba-player-data"))
    want = {norm(lookup_name(n, "stats")): n for n in players}

    awards = defaultdict(lambda: defaultdict(list))
    for a in load_json(R("nba-player-data", "awards.json")):
        n = want.get(norm(a["PLAYER / COACH"]))
        if n and a["AWARD"] in AWARDS_KEEP and a["YEAR"].isdigit():
            if a["AWARD"] not in awards[n][a["YEAR"]]:
                awards[n][a["YEAR"]].append(a["AWARD"])
    mvp = defaultdict(dict)
    for v in load_json(R("nba-player-data", "awardVotes.json")):
        n = want.get(norm(v["PLAYER"]))
        if n and v["AWARD"] == "MVP" and v["RNK"].isdigit():
            mvp[n][v["YEAR"]] = int(v["RNK"])

    # Regular-season production. Traded players have one row per team: sum them.
    tot = defaultdict(lambda: {"gp": 0, "pts": 0.0, "reb": 0.0, "ast": 0.0, "teams": []})
    for r in load_json(R("nba-player-data", "rsStats.json")):
        if not r["YEAR"].isdigit() or int(r["YEAR"]) < 1995:
            continue
        gp = num(r["GP"]) or 0
        k = (norm(r["PLAYER"]), r["YEAR"])
        t = tot[k]
        t["name"] = r["PLAYER"]
        t["gp"] += int(gp)
        t["pts"] += num(r["PTS"]) or 0
        t["reb"] += num(r["REB"]) or 0
        t["ast"] += num(r["AST"]) or 0
        t["teams"].append(r["TEAM"])
    by_year = defaultdict(list)
    for (nn, yr), t in tot.items():
        if t["gp"] >= PROD_MIN_GP:
            t["pra"] = (t["pts"] + t["reb"] + t["ast"]) / t["gp"]
            by_year[yr].append((nn, t))
    prod = defaultdict(dict)
    top10 = {}
    for yr, lst in by_year.items():
        lst.sort(key=lambda x: -x[1]["pra"])
        top10[yr] = [t["name"] for _, t in lst[:10]]
        for i, (nn, t) in enumerate(lst, 1):
            if nn in want:
                prod[want[nn]][yr] = {
                    "gp": t["gp"], "ppg": round(t["pts"] / t["gp"], 1), "rpg": round(t["reb"] / t["gp"], 1),
                    "apg": round(t["ast"] / t["gp"], 1), "pra": round(t["pra"], 1),
                    "prod_rank": i, "pool": len(lst),
                }
    for (nn, yr), t in tot.items():
        if nn in want and t["gp"] < PROD_MIN_GP and yr not in prod[want[nn]]:
            prod[want[nn]][yr] = {"gp": t["gp"], "prod_rank": None, "pool": len(by_year.get(yr, []))}
    write("production.json", {"min_gp": PROD_MIN_GP, "players": prod, "season_top10": top10})

    sal = defaultdict(lambda: defaultdict(list))
    for s in load_json(R("nba-player-data", "salaries.json")):
        n = want.get(norm(s["PLAYER"]))
        m = money(s["SALARY"])
        if n and m and s["YEAR"].isdigit():
            sal[n][s["YEAR"]].append({"team": s["TEAM"], "salary": m})

    # ---- salary-season-finder: salary by season from 1990-91 (cross-check) + draft info
    sources["salary-season-finder"] = git_head(R("salary-season-finder"))
    ssf = defaultdict(dict)
    draft = {}
    for s in load_json(R("salary-season-finder", "data", "data.json"))["seasons"]:
        n = want.get(norm(s["player"]))
        if not n:
            continue
        end = str(int(s["season"][:4]) + 1)
        ssf[n][end] = {"salary": s.get("salary"), "rank_league": s.get("salary_rank_league"), "team": s.get("team")}
        if s.get("draft_year"):
            draft[n] = {"draft_year": s["draft_year"], "draft_pick": s.get("draft_pick")}
    checks = []
    for n in players:
        for yr in sorted(set(sal.get(n, {})) & set(ssf.get(n, {}))):
            a = sum(x["salary"] for x in sal[n][yr])
            b = ssf[n][yr]["salary"]
            if b is not None and a != b:
                checks.append({"player": n, "season_end": yr, "nba_player_data": a, "salary_season_finder": b})
    for n in players:
        if n not in awards and n not in prod:
            misses["nba-player-data"].append({"player": n, "reason": "no awards or stats rows"})
        if n not in sal:
            misses["salaries"].append({"player": n, "reason": "no salary rows"})
        if n not in draft:
            misses["draft"].append({"player": n, "reason": "no draft year in salary-season-finder"})
    write("awards.json", {"awards": awards, "mvp_vote_rank": mvp})
    write("salaries.json", {"nba_player_data": sal, "salary_season_finder": ssf, "mismatches": checks})
    write("draft.json", draft)

    # ---- career map: birthplace + nationality, plus the player's Career Map page
    sources["nba-career-map"] = git_head(R("nba-career-map"))
    bio = load_json(R("nba-career-map", "data", "players", "player_bio.json"))
    bio_idx = defaultdict(list)
    for k in bio:
        bio_idx[norm(k)].append(k)
    car_idx = defaultdict(list)
    for c in load_json(R("nba-career-map", "data", "players", "nba_players_careers.json")):
        car_idx[norm(c["player"])].append(c)
    pages = set(os.path.basename(p) for p in glob.glob(R("nba-career-map", "player", "*.html")))
    birth = {}
    for n in players:
        key = norm(lookup_name(n, "career-map"))
        bk, ck = bio_idx.get(key, []), car_idx.get(key, [])
        if len(bk) != 1 or len(ck) != 1:
            misses["career-map"].append({"player": n, "reason": "no match" if not bk else "ambiguous"})
            continue
        b, c = bio[bk[0]], ck[0]
        slug = career_slug(c["player"])
        birth[n] = {
            "birth_place": b.get("birth_place") or None,
            "nationality": c.get("nationality") or None,
            "career_map_url": ("https://hoopsmatic.com/nba-career-map/player/%s.html" % slug)
            if slug + ".html" in pages else None,
        }
        if not birth[n]["birth_place"]:
            misses["career-map"].append({"player": n, "reason": "birth place empty"})
    write("bio.json", birth)

    # ---- polymarket: which MVP seasons it covers at all
    sources["nba-polymarket"] = git_head(R("nba-polymarket"))
    seasons = defaultdict(int)
    for f in glob.glob(R("nba-polymarket", "data", "markets", "*.json")) + glob.glob(R("nba-polymarket", "data", "archive", "*", "*.json")):
        try:
            d = load_json(f)
        except Exception:
            continue
        t = d.get("eventTitle") or ""
        m = re.search(r"(\d{4}-\d{2}) MVP$", t)
        if m and "Finals" not in t:
            seasons[m.group(1)] += 1
    write("polymarket_mvp.json", {"mvp_seasons_covered": dict(seasons),
                                  "note": "MVP odds are shown only for seasons that also have a jersey list."})

    # ---- attendance: road draw per team per season (season = end year)
    sources["nba-attendance"] = git_head(R("nba-attendance"))
    att = defaultdict(dict)
    with open(R("nba-attendance", "output", "team_draw_by_season.csv"), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if int(r["season"]) >= 2001:
                att[r["teamId"]][r["season"]] = {"games": int(r["games"]), "mean_delta": float(r["mean_delta"])}
    write("attendance_road_draw.json", att)

    write("sources.json", sources)
    write("misses.json", misses)
    print("headshots", len(headshots), "of", len(players))
    for k, v in misses.items():
        print("misses", k, len(v), [x["player"] for x in v])
    print("salary mismatches", len(checks))
    print("polymarket MVP seasons", dict(seasons))


if __name__ == "__main__":
    main()
