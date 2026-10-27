#!/usr/bin/env python3
"""Build the HoopsMatic NBA jersey sales section (served at hoopsmatic.com/jerseys/).

Inputs (all committed, nothing fetched):
  data/source/*.csv            the two source CSVs, unchanged
  data/release_sources.csv     Release ID -> official announcement URL (blank = none found)
  data/crossover/*.json        snapshots from scripts/snapshot_crossover.py and scripts/snapshot_extra.py
  data/share/headshots/        face-tight headshots for the share cards (snapshot_extra.py)
  data/page_hashes.json        content hash + lastmod per URL (updated here)
  data/share_hashes.json       content hash per share image (updated by share_images.py)

Outputs:
  docs/                        prerendered pages, JSON, CSV, share images, sitemap
  data/build_report.json       grid cross-check, counts, crossover misses

Every number and name on the pages comes from those inputs. All page text is a
template filled from data. The ranking rules live in jersey_model.py.

Usage: python scripts/build_jerseys.py [--today YYYY-MM-DD]
"""
import argparse
import csv
import datetime
import hashlib
import html
import json
import os
import re
import shutil
import statistics
import sys
import unicodedata
from collections import defaultdict, OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jersey_model as M  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DOCS = os.path.join(ROOT, "docs")
RANKS_CSV = os.path.join(DATA, "source", "nba-jersey-sales-rankings-2001-2026 - Player ranks.csv")
GRID_CSV = os.path.join(DATA, "source", "nba-jersey-sales-rankings-2001-2026 - Grid U.S. lists.csv")
SITE = "https://hoopsmatic.com/jerseys/"
HEADSHOTS = "https://jsierrahoopshype.github.io/nba-headshots/"
SILHOUETTE = HEADSHOTS + "fallbacks/player_silhouette.svg"
LOGO = HEADSHOTS + "teams/logos/current/svg/%s.svg"
RACE_TOOL = "https://jsierrahoopshype.github.io/bar-chart-race/"
ATTENDANCE_TOOL = "https://hoopsmatic.com/nba-attendance/"
INTL_TYPES = {"Europe only", "Philippines only"}
# Lists whose source type is relabelled after checking the official release.
# R23: the Oct 3, 2013 NBA release ("Heat's LeBron James has most popular NBA jersey worldwide",
# pr.nba.com/lebron-james-most-popular-nba-jersey/) prints several lists; the ten in the source are
# its "Top 10 Selling Jerseys in the United States for the 2012-13 season" (adidas sales, full season),
# in the same order. It is a U.S. list.
RECLASS = {"R23": "Full season (U.S. top 10)"}
INDEX_MIN_LISTS = 3
SHOW_N = 25
TOP_N = 5
# Jersey names published under a team name that is not in teams.json.
TEAM_NAME_EXTRA = {"Los Angeles Clippers": "la-clippers"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
STACK_COLORS = ["#3b82f6", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6"]
OTHER_COLOR = "#a1a1aa"
FOOTER = ('<footer class="site-foot">'
          '<p>Anything wrong? Email us at <a href="mailto:hoopsmatic@hoopsmatic.com">hoopsmatic@hoopsmatic.com</a></p>'
          '<p>For business inquiries, contact us at <a href="mailto:ads@hoopsmatic.com">ads@hoopsmatic.com</a></p>'
          '</footer>')
PRESETS = [("stephen-curry", "lebron-james", "Curry vs. LeBron"), ("luka-doncic", "nikola-jokic", "Luka vs. Jokic"),
           ("victor-wembanyama", "cooper-flagg", "Wembanyama vs. Flagg")]


def esc(s):
    return html.escape(str(s), quote=True)


def slugify(name):
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("'", "").replace("’", "").replace(".", "").lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def ordinal(n):
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return "%d%s" % (n, suf)


def money(n):
    return "${:,}".format(n)


def plural(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


def join_names(names):
    names = list(names)
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def season_end(season):
    return str(int(season[:4]) + 1)


def season_from_date(d):
    y, m = int(d[:4]), int(d[5:7])
    start = y if m >= 8 else y - 1
    return "%d-%02d" % (start, (start + 1) % 100)


def poss(name):
    return name + ("'" if name.endswith("s") else "'s")


def pct(a, b):
    return int(round(100.0 * a / b)) if b else 0


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

def date_label(d):
    """Date shown at the precision the source gives (year, month or day)."""
    parts = d.split("-")
    if len(parts) == 1:
        return parts[0]
    mon = MONTHS[int(parts[1]) - 1]
    if len(parts) == 2:
        return "%s %s" % (mon, parts[0])
    return "%s %d, %s" % (mon, int(parts[2]), parts[0])


def month_year(d):
    parts = d.split("-")
    return parts[0] if len(parts) == 1 else "%s %s" % (MONTHS[int(parts[1]) - 1], parts[0])


def short_label(d):
    parts = d.split("-")
    return parts[0] if len(parts) == 1 else "%s-%s" % (parts[0], parts[1])


def load_releases(report):
    with open(RANKS_CSV, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    report["player_ranks_rows"] = len(rows)
    rel = OrderedDict()
    for r in rows:
        k = r["Release ID"]
        if k not in rel:
            rel[k] = {"id": k, "date": r["Release date"], "season": r["Season"], "type": r["List type"],
                      "final": r["Season final list"] == "Yes", "entries": [], "precision_date": r["Release date"]}
        rel[k]["entries"].append({"rank": int(r["Rank"]), "player": r["Player"],
                                  "team": r["Team (at the time)"], "flag": r["Flag"]})
        if (r["Season final list"] == "Yes") != rel[k]["final"]:
            report.setdefault("warnings", []).append("Season final list differs inside %s" % k)

    # Grid: cross-check every cell and take the date precision it prints.
    with open(GRID_CSV, encoding="utf-8-sig") as f:
        grid = list(csv.reader(f))
    hdr = grid[0]
    mismatches, grid_cols, extra = [], {}, []
    for i, h in enumerate(hdr[1:], 1):
        parts = h.split("\n")
        gdate, gtype, gfinal = parts[0].strip(), parts[1].strip(), "(season final)" in h
        match = [k for k, v in rel.items() if v["date"].startswith(gdate) and v["type"] == gtype]
        cells = {int(row[0]): row[i].strip() for row in grid[1:] if row[i].strip()}
        if len(match) != 1:
            extra.append({"column": i, "date": gdate, "type": gtype, "final": gfinal, "cells": cells})
            continue
        k = match[0]
        grid_cols[k] = i
        rel[k]["precision_date"] = gdate
        if rel[k]["final"] != gfinal:
            mismatches.append({"release": k, "issue": "season final flag", "grid": gfinal, "player_ranks": rel[k]["final"]})
        pr = {e["rank"]: e["player"] for e in rel[k]["entries"]}
        for rk in sorted(set(pr) | set(cells)):
            if pr.get(rk, "") != cells.get(rk, ""):
                mismatches.append({"release": k, "rank": rk, "grid": cells.get(rk, ""), "player_ranks": pr.get(rk, "")})
    # A grid column with no Player-ranks release (the empty 2005-01 list) stays visible as an empty list.
    ids = list(rel.keys())
    for x in extra:
        if x["cells"]:
            mismatches.append({"grid_column": x["date"] + " " + x["type"], "issue": "grid column has players but no Player-ranks release"})
            continue
        sort_date = x["date"] + "-01" * (3 - len(x["date"].split("-")))
        prev = [k for k in ids if rel[k]["date"] < sort_date]
        nxt = [k for k in ids if rel[k]["date"] > sort_date]
        new_id = "GRID-" + x["date"]
        if prev and nxt:
            a, b = int(prev[-1][1:]), int(nxt[0][1:])
            if b - a == 2:
                new_id = "R%02d" % (a + 1)
        rel[new_id] = {"id": new_id, "date": sort_date, "season": season_from_date(sort_date), "type": x["type"],
                       "final": x["final"], "entries": [], "precision_date": x["date"], "grid_only": True}
    # Relabel after the grid check (the grid prints the source type).
    for k, new_type in RECLASS.items():
        if k in rel:
            rel[k]["source_type"] = rel[k]["type"]
            rel[k]["type"] = new_type
    report["grid_vs_player_ranks"] = {
        "grid_columns": len(hdr) - 1, "matched_columns": len(grid_cols), "mismatches": mismatches,
        "grid_columns_without_player_ranks_release": [x["date"] + " " + x["type"] for x in extra],
        "grid_includes_international_types": sorted({rel[k]["type"] for k in grid_cols if rel[k]["type"] in INTL_TYPES}),
        "us_releases_missing_from_grid": sorted(k for k, v in rel.items() if v["type"] not in INTL_TYPES and k not in grid_cols and not v.get("grid_only")),
        "reclassified": {k: {"source_type": rel[k].get("source_type"), "type": v} for k, v in RECLASS.items() if k in rel},
    }

    srcs = {}
    p = os.path.join(DATA, "release_sources.csv")
    if os.path.exists(p):
        with open(p, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if (r.get("url") or "").strip():
                    srcs[r["Release ID"].strip()] = r["url"].strip()

    out = sorted(rel.values(), key=lambda r: r["date"])
    for r in out:
        r["entries"].sort(key=lambda e: e["rank"])
        r["us"] = r["type"] not in INTL_TYPES
        r["n"] = len(r["entries"])
        r["status"] = "empty" if r["n"] == 0 else ("partial" if r["n"] == 1 else "full")
        r["label"] = date_label(r["precision_date"])
        r["my"] = month_year(r["precision_date"])
        r["short"] = short_label(r["precision_date"])
        r["source"] = srcs.get(r["id"])
        r["title"] = "%s, %s" % (r["type"], r["label"])
        for e in r["entries"]:
            e["pts"] = (11 - e["rank"]) if (r["us"] and e["rank"] <= 10) else 0
    return out


class Model:
    pass


def build_model(releases, teams, cross, report):
    team_by_name = {}
    for t in teams:
        team_by_name[t["full_name"]] = t["slug"]
        for h in t["historical_names"]:
            team_by_name[h] = t["slug"]
    team_by_name.update(TEAM_NAME_EXTRA)
    current = {t["full_name"] for t in teams} | set(TEAM_NAME_EXTRA)
    teams_by_slug = {t["slug"]: t for t in teams}
    unmapped = set()
    for r in releases:
        for e in r["entries"]:
            parts = [p.strip() for p in e["team"].split("/")] if e["team"] else []
            # "New Orleans/Oklahoma City Hornets" is one team name, not two teams.
            if e["team"] in team_by_name:
                parts = [e["team"]]
            slugs, names = [], []
            for p in parts:
                s = team_by_name.get(p)
                if s is None:
                    unmapped.add(p)
                elif s not in slugs:
                    slugs.append(s)
                    names.append(p)
            e["team_slugs"] = slugs
            e["team_names"] = names
            # Today's logo only when the published name is the current team; history gets the color swatch.
            hist_flag = "team was the" in (e["flag"] or "")
            e["logos"] = [(LOGO % teams_by_slug[s]["team_id"]) if (nm in current and not hist_flag and cross.has_logo(teams_by_slug[s]["team_id"])) else None
                          for s, nm in zip(slugs, names)]
            e["multi"] = len(slugs) > 1
            e["slug"] = slugify(e["player"])
    if unmapped:
        raise SystemExit("Unmapped team names: %s" % sorted(unmapped))

    m = Model()
    m.releases, m.teams_by_slug, m.cross = releases, teams_by_slug, cross
    m.byid = {r["id"]: r for r in releases}
    m.us = [r for r in releases if r["us"]]
    m.nonempty_us = [r for r in m.us if r["status"] != "empty"]
    m.eligible = [r for r in m.us if r["status"] == "full"]
    M.compute_moves(m.us)
    for r in releases:
        if not r["us"]:
            r["compare_id"], r["compare_len"], r["dropped"] = None, None, []
            for e in r["entries"]:
                e["move"] = None

    # Season rankings.
    m.seasons = sorted({r["season"] for r in releases})
    m.sr = OrderedDict()
    for s in m.seasons:
        m.sr[s] = M.season_ranking(s, [r for r in m.us if r["season"] == s])
        for row in m.sr[s]["rows"]:
            row["pts"] = M.season_points(row["pos"])
            e = row["entry"]
            row["team"], row["team_slugs"] = e["team"], e["team_slugs"]

    # Award ranks per season (season end year).
    m.awards = {}
    for s in m.seasons:
        by, rows = M.award_ranking(cross.award_seasons.get(season_end(s), {}))
        m.awards[s] = {"by": by, "rows": rows}

    players = OrderedDict()
    for r in releases:
        for e in r["entries"]:
            p = players.setdefault(e["player"], {"name": e["player"], "slug": e["slug"], "us": [], "intl": []})
            (p["us"] if r["us"] else p["intl"]).append((r, e))
    slugs = defaultdict(list)
    for p in players.values():
        slugs[p["slug"]].append(p["name"])
    dup = {k: v for k, v in slugs.items() if len(v) > 1}
    if dup:
        raise SystemExit("Duplicate player slugs: %s" % dup)

    for p in players.values():
        u = p["us"]
        p["lists"] = len(u)
        p["list_points"] = sum(e["pts"] for _, e in u)
        p["no1_lists"] = sum(1 for _, e in u if e["rank"] == 1)
        p["top5_lists"] = sum(1 for _, e in u if e["rank"] <= 5)
        p["top10_lists"] = sum(1 for _, e in u if e["rank"] <= 10)
        p["peak"] = min((e["rank"] for _, e in u), default=None)
        p["peak_times"] = sum(1 for _, e in u if e["rank"] == p["peak"])
        p["first"] = u[0][0] if u else None
        p["first_rank"] = u[0][1]["rank"] if u else None
        p["last"] = u[-1][0] if u else None
        p["first_no1"] = next((r for r, e in u if e["rank"] == 1), None)
        p["indexable"] = p["lists"] >= INDEX_MIN_LISTS
        p["season"] = OrderedDict()
        p["season_all"] = OrderedDict()
        for s, sr in m.sr.items():
            for row in sr.get("all_rows", []):
                if row["player"] == p["name"]:
                    p["season_all"][s] = row
                    if row in sr["rows"]:
                        p["season"][s] = row
        pos = [row["pos"] for row in p["season"].values()]
        p["no1_seasons"] = sum(1 for x in pos if x == 1)
        p["top5_seasons"] = sum(1 for x in pos if x <= 5)
        p["top10_seasons"] = sum(1 for x in pos if x <= 10)
        p["best_season"] = min(pos) if pos else None
        p["best_season_list"] = [s for s, row in p["season"].items() if row["pos"] == p["best_season"]]
        p["season_points"] = sum(row["pts"] for row in p["season"].values())
        p["first_no1_season"] = next((s for s, row in p["season"].items() if row["pos"] == 1), None)
        # Longest unbroken run: a partial or empty list neither breaks nor extends a run.
        best, cur, start = (0, None, None), 0, None
        on = {r["id"] for r, _ in u}
        for r in m.nonempty_us:
            if r["id"] in on:
                if cur == 0:
                    start = r
                cur += 1
                if cur > best[0]:
                    best = (cur, start, r)
            elif r["status"] == "full":
                cur = 0
        p["run"] = best
        years = sorted({r["date"][:4] for r, _ in (u or p["intl"])})
        p["years"] = (years[0], years[-1]) if years else (None, None)
    m.players = players
    # Every name the pages can show -> its page: a jersey player, a team (current, historical or
    # published name, all to the franchise page), or a confirmed HoopsMatic page for a player
    # with no jersey page (data/external_player_links.json).
    links = {}
    ext = load_json(os.path.join(DATA, "external_player_links.json"), {"links": {}})
    for n, url in ext.get("links", {}).items():
        links[n] = ("ext", url)
    for t in teams:
        for n in [t["full_name"]] + t["historical_names"]:
            links[n] = ("team", t["slug"])
    for r in releases:
        for e in r["entries"]:
            for nm, sl in zip(e["team_names"], e["team_slugs"]):
                links[nm] = ("team", sl)
            if e["team"] in team_by_name:
                links[e["team"]] = ("team", team_by_name[e["team"]])
    for p in players.values():
        links[p["name"]] = ("player", p["slug"])
    m.name_links = links
    m.name_re = re.compile(r"(?<![\w])(%s)(?![\w])" % "|".join(
        re.escape(esc(n)) for n in sorted(links, key=len, reverse=True)))
    return m


# --------------------------------------------------------------------------
# Crossover helpers
# --------------------------------------------------------------------------

class Cross:
    def __init__(self):
        C = os.path.join(DATA, "crossover")
        self.headshots = load_json(os.path.join(C, "headshots.json"), {})
        self.teams = load_json(os.path.join(C, "teams.json"), [])
        aw = load_json(os.path.join(C, "awards.json"), {})
        self.awards = aw.get("awards", {})
        sal = load_json(os.path.join(C, "salaries.json"), {})
        self.sal = sal.get("nba_player_data", {})
        self.ssf = sal.get("salary_season_finder", {})
        self.sal_bad = {(m["player"], m["season_end"]) for m in sal.get("mismatches", [])}
        self.draft = load_json(os.path.join(C, "draft.json"), {})
        self.bio = load_json(os.path.join(C, "bio.json"), {})
        self.att = load_json(os.path.join(C, "attendance_road_draw.json"), {})
        self.poly = load_json(os.path.join(C, "polymarket_mvp.json"), {})
        self.sources = load_json(os.path.join(C, "sources.json"), {})
        self.misses = load_json(os.path.join(C, "misses.json"), {})
        av = load_json(os.path.join(C, "award_votes.json"), {})
        self.award_seasons = av.get("seasons", {})
        self.award_coverage = av.get("coverage", {})
        self.award_source = av.get("source", {})
        self.played = load_json(os.path.join(C, "seasons_played.json"), {})
        self.logos = load_json(os.path.join(C, "team_logos.json"), {})

    def img(self, name):
        h = self.headshots.get(name)
        return h["image"] if h else SILHOUETTE

    def has_logo(self, team_id):
        return bool(self.logos.get(str(team_id)))

    def salary(self, name, end):
        """Salary for a season only when both salary sources agree (or only one has it)."""
        if (name, end) in self.sal_bad:
            return None
        rows = self.sal.get(name, {}).get(end)
        if not rows:
            return None
        return sum(x["salary"] for x in rows)

    def salary_rank(self, name, end):
        if (name, end) in self.sal_bad:
            return None
        return (self.ssf.get(name, {}).get(end) or {}).get("rank_league")

    def honors(self, name, end):
        """Awards for a season, All-Star left out."""
        return sorted(a for a in self.awards.get(name, {}).get(end, []) if a != "All-Star")


# --------------------------------------------------------------------------
# HTML helpers
# --------------------------------------------------------------------------

def page(root, url, title, desc, body, indexable=True, nav="hub", share=None, share_alt=""):
    robots = "" if indexable else '<meta name="robots" content="noindex, follow">\n'
    tabs = [("hub", root, "Rankings"), ("records", root + "records/", "Records"),
            ("seasons", root + "seasons/", "Seasons"), ("teams", root + "teams/", "Teams")]
    navhtml = "".join('<a href="%s"%s>%s</a>' % (esc(h), ' class="active"' if k == nav else "", esc(t)) for k, h, t in tabs)
    og = ""
    if share:
        img = SITE + "share/" + share + ".png"
        og = ('<meta property="og:image" content="%s">\n<meta property="og:image:width" content="1200">\n'
              '<meta property="og:image:height" content="630">\n<meta property="og:image:alt" content="%s">\n'
              '<meta name="twitter:card" content="summary_large_image">\n<meta name="twitter:title" content="%s">\n'
              '<meta name="twitter:description" content="%s">\n<meta name="twitter:image" content="%s">\n'
              '<meta name="twitter:image:alt" content="%s">\n') % (
            esc(img), esc(share_alt), esc(title), esc(desc), esc(img), esc(share_alt))
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<title>%s</title>\n<meta name=\"description\" content=\"%s\">\n%s"
            "<link rel=\"canonical\" href=\"%s\">\n"
            "<meta property=\"og:type\" content=\"website\">\n<meta property=\"og:site_name\" content=\"HoopsMatic\">\n"
            "<meta property=\"og:title\" content=\"%s\">\n<meta property=\"og:description\" content=\"%s\">\n"
            "<meta property=\"og:url\" content=\"%s\">\n%s"
            "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">\n"
            "<link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin>\n"
            "<link href=\"https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap\" rel=\"stylesheet\">\n"
            "<link rel=\"stylesheet\" href=\"%sassets/jerseys.css\">\n</head>\n<body data-root=\"%s\">\n"
            "<div class=\"container narrow\">\n<nav class=\"tabs\">%s</nav>\n%s\n%s\n</div>\n"
            "<div class=\"tip\" id=\"tip\" role=\"tooltip\" hidden></div>\n"
            "<script src=\"%sassets/jerseys.js\" defer></script>\n</body>\n</html>\n") % (
        esc(title), esc(desc), robots, esc(url), esc(title), esc(desc), esc(url), og, root, root, navhtml, body, FOOTER, root)


class Ctx:
    """Link builders relative to the page being written."""

    def __init__(self, root, m):
        self.root, self.m = root, m
        self.players, self.teams, self.cross = m.players, m.teams_by_slug, m.cross

    def name_href(self, name):
        kind, val = self.m.name_links.get(name, (None, None))
        if kind == "player":
            return "%splayer/%s/" % (self.root, val)
        if kind == "team":
            return "%steam/%s/" % (self.root, val)
        return val

    def names(self, text, skip=()):
        """Escape a template sentence and link every player and team name in it. skip: names left as
        text (the page's own player or team)."""
        out = esc(text)

        def sub(mt):
            nm = html.unescape(mt.group(0))
            if nm in skip:
                return mt.group(0)
            return '<a class="nm" href="%s">%s</a>' % (esc(self.name_href(nm)), mt.group(0))
        return self.m.name_re.sub(sub, out)

    def plink(self, name):
        p = self.players.get(name)
        if not p:
            href = self.name_href(name)
            return ('<a class="nm" href="%s">%s</a>' % (esc(href), esc(name))) if href else esc(name)
        return '<a class="nm" href="%splayer/%s/">%s</a>' % (self.root, p["slug"], esc(name))

    def face(self, name, size=32, cls="face"):
        return '<img class="%s" src="%s" alt="%s" width="%d" height="%d" loading="lazy">' % (
            cls, esc(self.cross.img(name)), esc(name), size, size)

    def swatch(self, slug):
        t = self.teams.get(slug)
        return '<span class="sw" style="background:%s"></span>' % esc(t["color"] if t and t.get("color") else "#999")

    def mark(self, slug, logo):
        """Small current logo when the published name is today's team, the color swatch otherwise."""
        if logo:
            return '<img class="logo" src="%s" alt="" width="16" height="16" loading="lazy">' % esc(logo)
        return self.swatch(slug)

    def team_cell(self, e):
        """Team as published, each franchise linked, with a logo or swatch per team."""
        bits = []
        for nm, sl, lg in zip(e["team_names"], e["team_slugs"], e["logos"]):
            bits.append('%s<a class="nm" href="%steam/%s/">%s</a>' % (self.mark(sl, lg), self.root, sl, esc(nm)))
        return " / ".join(bits)

    def team_logo(self, slug):
        t = self.teams[slug]
        return LOGO % t["team_id"] if self.cross.has_logo(t["team_id"]) else None

    def tlink(self, slug):
        t = self.teams[slug]
        return '%s<a class="nm" href="%steam/%s/">%s</a>' % (self.mark(slug, self.team_logo(slug)), self.root, slug, esc(t["full_name"]))

    def slink(self, season):
        return '<a href="%sseason/%s/">%s</a>' % (self.root, season, esc(season))

    def rel_name(self, r):
        return r["type"] + ", " + r["label"]


def move_html(mv, r=None):
    """Change cell. Unchanged and not-compared rows stay quiet."""
    if not mv:
        return '<span class="mv dash">&middot;</span>'
    k = mv["kind"]
    if k == "up":
        return '<span class="mv up" title="Up %d from No. %d">&#9650;%d</span>' % (mv["delta"], mv["from"], mv["delta"])
    if k == "down":
        return '<span class="mv down" title="Down %d from No. %d">&#9660;%d</span>' % (mv["delta"], mv["from"], mv["delta"])
    if k == "same":
        return '<span class="mv same" title="Same rank">=</span>'
    if k == "new":
        return '<span class="mv new">First appearance</span>'
    if k == "back":
        return '<span class="mv back">Re-entry</span>'
    return '<span class="mv dash" title="%s">&middot;</span>' % esc("Not compared: " + mv.get("why", ""))


def flag_html(e, ctx=None):
    notes = []
    if e["flag"]:
        notes.append(ctx.names(e["flag"]) if ctx else esc(e["flag"]))
    if e["multi"]:
        notes.append("Listed with more than one team; counts for each team listed")
    if not notes:
        return ""
    return '<span class="note">%s</span>' % "; ".join(notes)


def table(head, rows, cls="lb", row_cls=None, limit=SHOW_N):
    """head: list of (label html, css). rows: list of lists of cell html. Rows past limit fold."""
    th = "".join('<th class="%s">%s</th>' % (c, h) for h, c in head)
    trs = []
    for i, r in enumerate(rows):
        tds = "".join('<td class="%s">%s</td>' % (head[j][1], v) for j, v in enumerate(r))
        cl = [] if not row_cls else [x for x in [row_cls[i]] if x]
        if limit and i >= limit:
            cl.append("more")
        trs.append('<tr%s>%s</tr>' % ((' class="%s"' % " ".join(cl)) if cl else "", tds))
    btn = ""
    if limit and len(rows) > limit:
        btn = '<button class="showall" type="button">Show all %d</button>' % len(rows)
    return '<div class="table-wrap"><table class="%s"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>%s' % (
        cls, th, "".join(trs), btn)


def release_note(r):
    if r["status"] == "empty":
        return "This list is empty in the source data."
    if r["status"] == "partial":
        return "Incomplete: only the No. 1 is in the source data."
    if r["id"] in RECLASS:
        return "Published in the NBA's worldwide release as the U.S. top 10 for the full 2012-13 season; counted as a U.S. list."
    return ""


def source_link(r):
    if r.get("source"):
        return '<a class="src" href="%s" rel="noopener" target="_blank">Source</a>' % esc(r["source"])
    return ""


def since_head(m, r):
    """'Change since {Month Year}' header with the full dates behind a tap."""
    if not r.get("compare_id"):
        return "Change"
    prev = m.byid[r["compare_id"]]
    full = "Compared with the %s list (%s). This list: %s. Exact move for every player on both lists." % (
        prev["type"], prev["label"], r["label"])
    return '<button type="button" class="since" data-tip="%s" title="%s">Change since %s</button>' % (esc(full), esc(full), esc(prev["my"]))


def list_table(ctx, r, compact=False):
    with_moves = r["us"]
    head = [("No.", "rank"), ("Player", "name left")]
    if with_moves:
        head.append((since_head(ctx.m, r), "mvc"))
    rows, cls = [], []
    for e in r["entries"]:
        who = ctx.face(e["player"], 28) + '<span class="pwrap">' + ctx.plink(e["player"]) + \
            '<span class="tline">%s</span>' % ctx.team_cell(e) + flag_html(e, ctx) + '</span>'
        cells = [str(e["rank"]), who]
        if with_moves:
            cells.append(move_html(e.get("move")))
        rows.append(cells)
        k = (e.get("move") or {}).get("kind")
        cls.append("lead" if e["rank"] == 1 else ("quiet" if k in ("same", "none") else ""))
    return table(head, rows, row_cls=cls)


def details(summary, inner, cls="more-block", open_=False):
    return '<details class="%s"%s><summary>%s</summary>%s</details>' % (cls, " open" if open_ else "", summary, inner)


def stat_cards(items):
    return '<div class="stat-cards">%s</div>' % "".join(
        '<div class="stat-card"><div class="lbl">%s</div><div class="num%s">%s</div>%s</div>' % (
            esc(lbl), " sm" if len(re.sub("<[^>]+>", "", num)) > 7 else "", num,
            ('<div class="sub">%s</div>' % sub) if sub else "") for lbl, num, sub in items)


def crumbs(root, items):
    """Breadcrumb links plus BreadcrumbList JSON-LD. items: (label, path relative to the section root or None)."""
    trail = [("NBA jersey sales", "")] + list(items)
    links = []
    for k, (lab, path) in enumerate(trail):
        last = k == len(trail) - 1
        links.append(('<span aria-current="page">%s</span>' % esc(lab)) if last else '<a href="%s%s">%s</a>' % (root, path, esc(lab)))
    ld = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        dict({"@type": "ListItem", "position": k + 1, "name": lab}, **({"item": SITE + path} if path is not None else {}))
        for k, (lab, path) in enumerate(trail)]}
    return '<nav class="crumbs" aria-label="Breadcrumb">%s</nav><script type="application/ld+json">%s</script>' % (
        ' <span class="sep" aria-hidden="true">&#8250;</span> '.join(links), json.dumps(ld, ensure_ascii=False).replace("</", "<\\/"))


def index_links(root):
    return '<p class="pager">More: <a href="%steams/">All teams</a> &middot; <a href="%sseasons/">All seasons</a></p>' % (root, root)


def section(title, inner, hint="", sid=None, cls="section"):
    return '<section class="%s"%s><h2>%s</h2>%s%s</section>' % (
        cls, (' id="%s"' % sid) if sid else "", esc(title), ('<p class="hint">%s</p>' % hint) if hint else "", inner)


def hbars(items, fmt=str, link=None):
    """Plain horizontal bars for counts of lists or seasons. items: (label html, value, sub)."""
    vmax = max([1] + [v for _, v, _ in items])
    out = []
    for lab, v, sub in items:
        out.append('<div class="hb"><div class="hb-l">%s</div><div class="hb-t"><span class="hb-b" style="width:%.1f%%"></span>'
                   '<span class="hb-v">%s</span></div>%s</div>' % (lab, 100.0 * v / vmax, esc(fmt(v)), ('<div class="hb-s">%s</div>' % sub) if sub else ""))
    return '<div class="hbars">%s</div>' % "".join(out)


# --------------------------------------------------------------------------
# Player chart (season rankings by default, every list on toggle). The same columns and states
# are written to the page as JSON so jerseys.js can redraw it with a second player.
# --------------------------------------------------------------------------

def chart_columns(m):
    seasons = []
    for s, sr in m.sr.items():
        seasons.append({"k": s, "lab": s, "kind": sr["kind"] or "none", "depth": sr["depth"],
                        "fed": [r["my"] for r in sr["fed"]]})
    lists = []
    for r in m.us:
        lists.append({"k": r["id"], "lab": r["short"], "kind": r["status"], "depth": r["n"], "title": r["type"] + ", " + r["label"]})
    return {"season": seasons, "list": lists}


def player_series(p):
    """Compact per-player values: season positions and list ranks."""
    return {"name": p["name"], "slug": p["slug"],
            "s": {s: row["pos"] for s, row in p["season"].items()},
            "l": {r["id"]: e["rank"] for r, e in p["us"]}}


def player_markers(m, p):
    """Chart markers in priority order (phones show the first four)."""
    out = {"season": [], "list": []}
    if not p["us"]:
        return out
    r0, e0 = p["us"][0]
    first_season = r0["season"]
    out["list"].append({"k": r0["id"], "t": "First list: %s (No. %d)" % (r0["my"], e0["rank"])})
    out["season"].append({"k": first_season, "t": "First appearance: %s (%s)" % (first_season, r0["my"])})
    if p["first_no1"]:
        out["list"].append({"k": p["first_no1"]["id"], "t": "First No. 1: %s" % p["first_no1"]["my"]})
    if p["first_no1_season"]:
        out["season"].append({"k": p["first_no1_season"], "t": "First No. 1 season: %s" % p["first_no1_season"]})
    if p["peak"] and p["peak"] > 1:
        rp = next(r for r, e in p["us"] if e["rank"] == p["peak"])
        out["list"].append({"k": rp["id"], "t": "Career peak: No. %d, %s" % (p["peak"], rp["my"])})
    if p["best_season"] and p["best_season"] > 1:
        out["season"].append({"k": p["best_season_list"][0], "t": "Career peak: No. %d, %s" % (p["best_season"], p["best_season_list"][0])})
    prev = None
    for r, e in p["us"]:
        if prev is not None and e["team_slugs"] != prev:
            out["list"].append({"k": r["id"], "t": "Team change: %s, %s" % (" / ".join(e["team_names"]), r["my"])})
        prev = e["team_slugs"]
    prev = None
    for s in p["season_all"]:
        row = p["season_all"][s]
        if prev is not None and row["entry"]["team_slugs"] != prev:
            out["season"].append({"k": s, "t": "Team change: %s, %s" % (" / ".join(row["entry"]["team_names"]), s)})
        prev = row["entry"]["team_slugs"]
    for kind in out:
        seen, uniq = set(), []
        for x in out[kind]:
            key = (x["k"], x["t"])
            if key not in seen:
                seen.add(key)
                uniq.append(x)
        out[kind] = uniq
    return out


def col_state(col, val):
    if val:
        return "ranked"
    if col["kind"] == "full":
        return "outside"
    if col["kind"] in ("partial", "no1"):
        return "unknown"
    return "nodata"


def tip_text(mode, col, val, who):
    if mode == "season":
        base = "%s, %s" % (who, col["lab"])
        if val:
            return "%s: No. %d in the season ranking (from %s)" % (base, val, join_names(col["fed"]))
        if col["kind"] == "full":
            return "%s: outside the season top %d" % (base, col["depth"])
        if col["kind"] == "no1":
            return "%s: unknown, only the No. 1 is known" % base
        return "%s: no data" % base
    base = "%s, %s" % (who, col["title"])
    if val:
        return "%s: No. %d" % (base, val)
    if col["kind"] == "full":
        return "%s: not on this %d-player list" % (base, col["depth"])
    if col["kind"] == "partial":
        return "%s: unknown, incomplete list (only the No. 1)" % base
    return "%s: no data, empty list" % base


MIN_COL = 28  # px per column: below this a chart keeps its minimum width and scrolls sideways


def xp(v):
    """An x position or width as a percentage of the chart's width."""
    return "%.3f%%" % v


def fluid_svg(cls, n, height, aria):
    """An SVG that fills its container; x positions are percentages, so text never stretches.
    Its min-width keeps every column at least MIN_COL wide, and the scroller takes over below that."""
    return '<svg class="%s fluid" width="100%%" height="%d" style="min-width:%dpx" role="img" aria-label="%s">' % (
        cls, height, n * MIN_COL + 8, esc(aria))


def x_label(at, y, lab):
    """A rotated column label at x = `at` percent (a nested SVG carries the percentage)."""
    return '<svg x="%s" y="0" width="1" height="1" overflow="visible"><text class="xtk" transform="translate(4,%d) rotate(-60)">%s</text></svg>' % (
        xp(at), y, esc(lab))


def render_chart(cols, mode, series, markers):
    """SVG for one or two series. Mirrors drawChart() in jerseys.js."""
    cs = cols[mode]
    idx = [i for i, c in enumerate(cs) if any(s[mode[0]].get(c["k"]) for s in series)]
    if not idx:
        return ""
    i0, i1 = max(0, min(idx) - 1), min(len(cs) - 1, max(idx) + 1)
    win = cs[i0:i1 + 1]
    vals = [v for s in series for c in win for v in [s[mode[0]].get(c["k"])] if v]
    maxr = max([15] + [c["depth"] for c in win if c["kind"] == "full"] + vals)
    maxr = 25 if maxr > 20 else (20 if maxr > 15 else 15)
    top, h = 26, 200
    band = top + h + 22
    height = band + 70
    n = len(win)
    cp = 100.0 / n
    y = lambda rk: top + (rk - 1) * h / (maxr - 1)
    x = lambda i: (i + .5) * cp
    A = ['<svg class="yax" width="30" height="%d" viewBox="0 0 30 %d" aria-hidden="true">' % (height, height)]
    P = [fluid_svg("rchart", n, height, "%s rank by %s, No. 1 at the top" % (" and ".join(s["name"] for s in series), "season" if mode == "season" else "list"))]
    for rk in [1, 5, 10, 15, 20, 25]:
        if rk <= maxr:
            P.append('<line class="grid" x1="0" x2="100%%" y1="%.1f" y2="%.1f"/>' % (y(rk), y(rk)))
            A.append('<text class="ytk" x="26" y="%.1f">%d</text>' % (y(rk) + 4, rk))
    A.append('<text class="ytk" x="26" y="%.1f">Out</text></svg>' % (band + 4))
    for i, c in enumerate(win):
        bx, bw = xp((i + .07) * cp), xp(.86 * cp)
        if c["kind"] in ("empty", "none"):
            P.append('<rect class="nodata" x="%s" y="%d" width="%s" height="%d"/>' % (bx, top - 6, bw, band - top + 14))
        elif c["kind"] in ("partial", "no1"):
            P.append('<rect class="gap" x="%s" y="%d" width="%s" height="%d"/>' % (bx, top - 6, bw, band - top + 14))
        elif c["depth"] < maxr:
            P.append('<rect class="short" x="%s" y="%.1f" width="%s" height="%.1f"/>' % (
                bx, y(c["depth"]) + 6, bw, max(0, top + h - y(c["depth"]) - 6)))
        P.append(x_label(x(i), band + 16, c["lab"]))
    for si, s in enumerate(series):
        key = mode[0]
        cls = "s%d" % si
        prev = None
        for i, c in enumerate(win):
            v = s[key].get(c["k"])
            st = col_state(c, v)
            if st == "ranked":
                if prev is not None:
                    P.append('<line class="seg %s%s" x1="%s" y1="%.1f" x2="%s" y2="%.1f"/>' % (
                        cls, " bridge" if prev[2] else "", xp(x(prev[0])), y(prev[1]), xp(x(i)), y(v)))
                prev = (i, v, False)
            elif st == "outside":
                prev = None
            elif prev is not None:
                prev = (prev[0], prev[1], True)
        for i, c in enumerate(win):
            v = s[key].get(c["k"])
            st = col_state(c, v)
            tip = esc(tip_text(mode, c, v, s["name"]))
            if st == "ranked":
                P.append('<g class="pt %s%s" tabindex="0" data-tip="%s"><circle cx="%s" cy="%.1f" r="5"/><title>%s</title></g>' % (
                    cls, " one" if v == 1 else "", tip, xp(x(i)), y(v), tip))
            elif si == 0 and st == "outside":
                P.append('<g class="pt out" tabindex="0" data-tip="%s"><circle cx="%s" cy="%.1f" r="3.5"/><title>%s</title></g>' % (tip, xp(x(i)), band, tip))
            elif si == 0 and st == "unknown":
                P.append('<g class="pt unk" tabindex="0" data-tip="%s"><text x="%s" y="%.1f">?</text><title>%s</title></g>' % (tip, xp(x(i)), band + 4, tip))
    if markers:
        k2i = {c["k"]: i for i, c in enumerate(win)}
        for k, mk in enumerate(markers, 1):
            if mk["k"] not in k2i:
                continue
            cx = xp(x(k2i[mk["k"]]))
            P.append('<g class="mk%s" data-tip="%s"><line x1="%s" x2="%s" y1="%d" y2="%d"/><circle cx="%s" cy="%d" r="7"/><text x="%s" y="%d">%d</text></g>' % (
                " mx" if k > 4 else "", esc(mk["t"]), cx, cx, top - 12, band, cx, top - 16, cx, top - 12, k))
    P.append("</svg>")
    return '<div class="yaxis">%s</div><div class="chart-scroll">%s</div>' % ("".join(A), "".join(P))


def marker_key(markers):
    if not markers:
        return ""
    return '<ol class="mkey">%s</ol>' % "".join('<li%s>%s</li>' % (' class="mx"' if n > 4 else "", mk.get("h") or esc(mk["t"])) for n, mk in enumerate(markers, 1))


CHART_LEGEND = ('<div class="legend"><span><i class="lg-ranked"></i>Ranked</span><span><i class="lg-out"></i>Outside a complete list</span>'
                '<span><i class="lg-unk">?</i>Unknown (incomplete list)</span><span><i class="lg-nodata"></i>No data</span></div>')


# --------------------------------------------------------------------------
# Analytics shared by pages
# --------------------------------------------------------------------------

def award_label(a):
    if not a:
        return "No award votes"
    bits = []
    if a["all_nba"]:
        bits.append("All-NBA %s team" % {1: "1st", 2: "2nd", 3: "3rd"}[a["all_nba"]])
    if a["mvp_rank"]:
        bits.append("MVP %s" % ordinal(a["mvp_rank"]))
    return " \u00b7 ".join(bits)


def popularity_vs_awards(m):
    """Per season: season jersey ranking against the league-wide award rank."""
    out = OrderedDict()
    for s, sr in m.sr.items():
        if not sr["rows"]:
            continue
        aw = m.awards[s]
        rows = []
        for row in sr["rows"]:
            a = aw["by"].get(row["player"])
            rows.append({"player": row["player"], "jersey": row["pos"], "award": a["rank"] if a else None,
                         "gap": (a["rank"] - row["pos"]) if a else None, "detail": award_label(a)})
        listed = {row["player"] for row in sr["rows"]}
        missing = [x for x in aw["rows"] if x["rank"] <= 10 and x["player"] not in listed] if sr["kind"] == "full" else []
        out[s] = {"rows": rows, "award_top10_missing": missing, "n_award": len(aw["rows"]), "depth": sr["depth"]}
    career = defaultdict(list)
    nov = defaultdict(int)
    for s, v in out.items():
        for x in v["rows"]:
            if x["gap"] is not None:
                career[x["player"]].append(x["gap"])
            elif v["depth"] >= 10 and x["jersey"] <= 10:
                nov[x["player"]] += 1
    career_rows = [{"player": n, "median_gap": statistics.median(g), "n": len(g)} for n, g in career.items() if len(g) >= 3]
    career_rows.sort(key=lambda x: (-x["median_gap"], x["player"]))
    no_votes = sorted([{"player": n, "seasons": k} for n, k in nov.items() if k >= 2], key=lambda x: (-x["seasons"], x["player"]))
    return out, career_rows, no_votes


def team_points(m):
    """Season-ranking points (default) and every-list points per team."""
    season = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))  # slug -> season -> player -> pts
    for s, sr in m.sr.items():
        for row in sr["rows"]:
            if row["pts"]:
                for sl in row["team_slugs"]:
                    season[sl][s][row["player"]] += row["pts"]
    lists = defaultdict(lambda: {"pts": 0, "lists": set()})
    for r in m.us:
        for e in r["entries"]:
            if e["pts"]:
                for sl in e["team_slugs"]:
                    lists[sl]["pts"] += e["pts"]
                    lists[sl]["lists"].add(r["id"])
    return season, lists


def compute_records(m):
    players = m.players
    rec = {}
    rec["one_list"] = sorted(
        [(p["name"], p["us"][0][0], p["us"][0][1]) for p in players.values() if p["lists"] == 1],
        key=lambda x: (x[2]["rank"], x[1]["date"]))
    # No. 1 reigns over consecutive non-empty U.S. lists (a partial list counts: its No. 1 is known).
    reigns, cur = [], None
    for r in m.nonempty_us:
        top = r["entries"][0]["player"]
        if cur and cur["player"] == top:
            cur["end"], cur["n"] = r, cur["n"] + 1
        else:
            cur = {"player": top, "start": r, "end": r, "n": 1}
            reigns.append(cur)
    rec["reigns_chrono"] = reigns
    rec["reigns"] = sorted(reigns, key=lambda x: (-x["n"], x["start"]["date"]))
    rec["no1_seasons"] = sorted([p for p in players.values() if p["no1_seasons"]], key=lambda p: (-p["no1_seasons"], -p["no1_lists"], p["name"]))
    rec["top10_seasons"] = sorted([p for p in players.values() if p["top10_seasons"]],
                                  key=lambda p: (-p["top10_seasons"], -p["top5_seasons"], -p["no1_seasons"], p["name"]))
    rec["never_no1"] = sorted([p for p in players.values() if p["lists"] and p["peak"] > 1],
                              key=lambda p: (-p["lists"], p["peak"], p["name"]))
    rec["points"] = sorted([p for p in players.values() if p["season_points"]], key=lambda p: (-p["season_points"], -p["list_points"], p["name"]))
    moves = []
    for r in m.eligible:
        for e in r["entries"]:
            mv = e.get("move")
            if mv and mv["kind"] in ("up", "down"):
                moves.append({"player": e["player"], "from": mv["from"], "to": e["rank"], "prev": m.byid[mv["vs"]], "cur": r,
                              "delta": mv["from"] - e["rank"]})
    rec["jumps"] = sorted([x for x in moves if x["delta"] > 0], key=lambda x: (-x["delta"], x["cur"]["date"]))
    rec["falls"] = sorted([x for x in moves if x["delta"] < 0], key=lambda x: (x["delta"], x["cur"]["date"]))
    # Early-career appearances, in seasons played (a season with 1+ regular-season game).
    early = []
    for p in players.values():
        played = m.cross.played.get(p["name"])
        if not (played and p["first"]):
            continue
        end = int(season_end(p["first"]["season"]))
        n = 1 + sum(1 for y in played if y < end)
        if 1 <= n <= 2 and end in played:
            d = m.cross.draft.get(p["name"]) or {}
            early.append({"player": p["name"], "season_no": n, "release": p["first"], "rank": p["first_rank"],
                          "nba_first": "%d-%02d" % (played[0] - 1, played[0] % 100), "draft_year": d.get("draft_year"), "pick": d.get("draft_pick")})
    rec["early"] = sorted(early, key=lambda x: (x["season_no"], x["rank"], x["release"]["date"]))
    duos = defaultdict(list)
    for r in m.nonempty_us:
        by_team = defaultdict(list)
        for e in r["entries"]:
            for s in e["team_slugs"]:
                by_team[s].append(e["player"])
        for s, names in by_team.items():
            names = sorted(set(names))
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    duos[(names[i], names[j], s)].append(r)
    rec["duos"] = sorted([{"a": k[0], "b": k[1], "team": k[2], "lists": v} for k, v in duos.items()],
                         key=lambda d: (-len(d["lists"]), d["lists"][0]["date"]))
    return rec


# --------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------

class Site:
    def __init__(self, today):
        self.pages = []  # (url, path, indexable, html)
        self.cards = []  # share image specs
        self.today = today

    def add(self, rel_dir, url, html_text, indexable):
        path = os.path.join(DOCS, rel_dir, "index.html") if rel_dir else os.path.join(DOCS, "index.html")
        self.pages.append((url, path, indexable, html_text))

    def write(self):
        hashes_path = os.path.join(DATA, "page_hashes.json")
        old = load_json(hashes_path, {})
        new = {}
        for url, path, indexable, text in self.pages:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            h = hashlib.sha256(text.encode("utf-8")).hexdigest()
            prev = old.get(url)
            lastmod = prev["lastmod"] if prev and prev.get("hash") == h else self.today
            new[url] = {"hash": h, "lastmod": lastmod}
        with open(hashes_path, "w", encoding="utf-8") as f:
            json.dump(new, f, indent=1, sort_keys=True)
            f.write("\n")
        sm = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for url, path, indexable, text in sorted(self.pages, key=lambda x: x[0]):
            if indexable:
                sm.append("  <url><loc>%s</loc><lastmod>%s</lastmod></url>" % (esc(url), new[url]["lastmod"]))
        sm.append("</urlset>")
        with open(os.path.join(DOCS, "sitemap.xml"), "w", encoding="utf-8") as f:
            f.write("\n".join(sm) + "\n")


def team_color(m, slug):
    t = m.teams_by_slug.get(slug)
    return (t or {}).get("color") or "#3b82f6"


def player_team_slug(p):
    if p["us"]:
        return p["us"][-1][1]["team_slugs"][0] if p["us"][-1][1]["team_slugs"] else None
    if p["intl"]:
        return p["intl"][-1][1]["team_slugs"][0] if p["intl"][-1][1]["team_slugs"] else None
    return None


def player_takeaway(p):
    n = p["name"]
    if not p["lists"]:
        r0 = p["intl"][0][0]
        return "%s appears only on international lists, first on the %s list (%s)." % (n, r0["type"], r0["my"])
    first = "%s first made a U.S. list in %s" % (n, p["first"]["my"])
    if p["no1_seasons"]:
        return "%s, reached No. 1 in %s and finished No. 1 in %s." % (first, p["first_no1"]["my"], plural(p["no1_seasons"], "season"))
    if p["first_no1"]:
        return "%s and reached No. 1 in %s, but never finished a season at No. 1 (best season rank: No. %d)." % (
            first, p["first_no1"]["my"], p["best_season"])
    if p["best_season"]:
        return "%s, peaked at No. %d on a list and finished in a season top 10 %s (best season rank: No. %d)." % (
            first, p["peak"], plural(p["top10_seasons"], "time"), p["best_season"])
    return "%s and peaked at No. %d, outside every season ranking." % (first, p["peak"])


def build_player_page(site, p, ctx, m, pva):
    name, slug = p["name"], p["slug"]
    url = SITE + "player/%s/" % slug
    y0, y1 = p["years"]
    span = y0 if y0 == y1 else "%s-%s" % (y0, y1)
    title = "%s Jersey Sales Rankings, %s | HoopsMatic" % (name, span)
    if p["lists"]:
        if p["no1_seasons"]:
            lead = "No. 1 in %s" % plural(p["no1_seasons"], "season")
        elif p["best_season"]:
            lead = "best season rank No. %d" % p["best_season"]
        else:
            lead = "best rank No. %d" % p["peak"]
        span_my = ("in " + p["first"]["my"]) if p["lists"] == 1 else "from %s to %s" % (p["first"]["my"], p["last"]["my"])
        desc = "%s jersey sales rankings: %s, %s %s on the NBA's best-selling jersey lists." % (
            name, lead, plural(p["lists"], "U.S. list"), span_my)
    else:
        r0 = p["intl"][0][0]
        desc = "%s jersey sales rankings: %s of best-selling NBA jerseys outside the U.S., first the %s list (%s)." % (
            name, plural(len(p["intl"]), "international list"), r0["type"], r0["label"])
    b = ['<div class="ehead">%s<h1>%s jersey sales rankings</h1></div>' % (ctx.face(name, 72), esc(name))]
    b.append('<p class="takeaway">%s</p>' % ctx.names(player_takeaway(p), skip=(name,)))
    cols = chart_columns(m)
    if p["lists"]:
        bs = p["best_season"]
        b.append(stat_cards([
            ("First appearance", esc(p["first"]["my"]), esc("No. %d, %s" % (p["first_rank"], p["first"]["type"]))),
            ("Best season rank", ("No. %d" % bs) if bs else "None", esc(join_names(p["best_season_list"]) if bs and len(p["best_season_list"]) <= 2 else (plural(len(p["best_season_list"]), "season") if bs else "outside every season ranking"))),
            ("No. 1 seasons", str(p["no1_seasons"]), ""),
            ("Top 5 seasons", str(p["top5_seasons"]), ""),
            ("Top 10 seasons", str(p["top10_seasons"]), ""),
        ]))
        run_n, run_a, run_b = p["run"]
        kv = [("U.S. lists made", str(p["lists"])), ("No. 1 lists", str(p["no1_lists"])), ("Top 5 lists", str(p["top5_lists"])),
              ("Top 10 lists", str(p["top10_lists"])), ("Best list rank", "No. %d (%s)" % (p["peak"], plural(p["peak_times"], "time"))),
              ("Longest run", "%s (%s)" % (plural(run_n, "list"), run_a["my"] + (" to " + run_b["my"] if run_n > 1 else ""))),
              ("Last list", "%s, %s" % (p["last"]["type"], p["last"]["label"])),
              ("Season ranking points", str(p["season_points"])), ("Every-list points", str(p["list_points"]))]
        b.append(details("More numbers: every-list counts and points", '<div class="kv">%s</div><p class="hint">%s</p>' % (
            "".join('<div><span class="k">%s</span>%s</div>' % (esc(k), esc(v)) for k, v in kv),
            "Season counts use one HoopsMatic ranking per season. List counts use every U.S. list. Points: No. 1 = 10 down to No. 10 = 1."), open_=False))
        me = player_series(p)
        mk = player_markers(m, p)
        for kind in mk:
            for x in mk[kind]:
                x["h"] = ctx.names(x["t"], skip=(name,))
        opts = "".join('<option value="%s">%s</option>' % (q["slug"], esc(q["name"]))
                       for q in sorted(m.players.values(), key=lambda q: q["name"].split(" ", 1)[-1] + q["name"]) if q["lists"] and q["slug"] != slug)
        by_slug = {q["slug"]: q["name"] for q in m.players.values()}
        presets = "".join('<span class="preset">%s vs. %s <a class="chip" href="%splayer/%s/?vs=%s" aria-label="%s">Compare</a></span>' % (
            ctx.plink(by_slug[a]), ctx.plink(by_slug[bb]), ctx.root, a, bb, esc("Compare %s with %s" % (by_slug[a], by_slug[bb])))
            for a, bb, lab in PRESETS if a in by_slug and bb in by_slug)
        controls = ('<div class="chart-ctl"><div class="seg-btns" role="group" aria-label="Chart basis">'
                    '<button type="button" class="segb" data-mode="season" aria-pressed="true">Season rankings</button>'
                    '<button type="button" class="segb" data-mode="list" aria-pressed="false">Every list</button></div>'
                    '<label class="lbl2" for="vs-select">Compare with</label><select id="vs-select" class="vs-select"><option value="">Nobody</option>%s</select>'
                    '<div class="chips">%s</div></div>') % (opts, presets)
        chart = ('<div class="pchart" data-player="%s">%s<div class="chart-frame">%s</div>%s<div class="vs-key" hidden></div>%s'
                 '<script type="application/json" class="chart-data">%s</script></div>') % (
            esc(slug), controls, render_chart(cols, "season", [me], mk["season"]), CHART_LEGEND, marker_key(mk["season"]),
            json.dumps({"cols": cols, "me": me, "markers": mk}, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
        b.append(section("Rank by season", chart,
                         "No. 1 at the top. One HoopsMatic ranking per season by default; switch to every list. Tap a point for details. Swipe sideways on phones."))
        # Season rows: the jersey ranking leads; salary, awards and award rank on expand.
        by_season = OrderedDict()
        for r, e in p["us"]:
            by_season.setdefault(r["season"], []).append((r, e))
        blocks = []
        for season, items in by_season.items():
            end = season_end(season)
            sr = m.sr[season]
            row = p["season"].get(season)
            if row:
                lead = "No. %d%s" % (row["pos"], " (shared)" if row["shared"] else "")
            elif sr["kind"] == "full":
                lead = "Outside the top %d" % sr["depth"]
            else:
                lead = "Unknown"
            lists_line = " &middot; ".join("%s No. %d" % (esc(r["my"]), e["rank"]) for r, e in items)
            inner = []
            for r, e in items:
                inner.append('<div><span class="k">%s</span>No. %d %s<span class="tline">%s</span></div>' % (
                    esc(r["my"]), e["rank"], move_html(e["move"]), ctx.team_cell(e)))
            sal = ctx.cross.salary(name, end)
            if sal:
                srk = ctx.cross.salary_rank(name, end)
                inner.append('<div><span class="k">Salary</span>%s%s</div>' % (esc(money(sal)), esc(" (No. %d in the NBA)" % srk) if srk else ""))
            a = m.awards[season]["by"].get(name)
            inner.append('<div><span class="k">Awards</span>%s</div>' % esc(
                ("No. %d of %d with votes (%s)" % (a["rank"], len(m.awards[season]["rows"]), award_label(a))) if a else "No award votes"))
            hon = [x for x in ctx.cross.honors(name, end) if not x.startswith("All-NBA") and x != "Most Valuable Player"]
            if hon:
                inner.append('<div><span class="k">Other honors</span>%s</div>' % esc(", ".join(hon)))
            summary = '<span class="sr-s">%s</span><span class="sr-r">%s</span><span class="sr-l">%s</span>' % (esc(season), esc(lead), lists_line)
            blocks.append(details(summary, '<div class="sr-body">%s<p class="hint"><a href="%sseason/%s/">%s season page</a></p></div>' % (
                "".join(inner), ctx.root, season, esc(season)), cls="srow"))
        b.append(section("Season by season", '<div class="season-rows">%s</div>' % "".join(blocks),
                         "Season rank first, then each U.S. list. Expand a season for salary, awards and award rank. Salary from nba-player-data (shown only where it agrees with Salary Season Finder); awards are All-NBA teams and MVP voting from nba-player-data."))
        mates = defaultdict(list)
        for r, e in p["us"]:
            for x in r["entries"]:
                if x["player"] != name and set(x["team_slugs"]) & set(e["team_slugs"]):
                    shared = [s for s in e["team_slugs"] if s in x["team_slugs"]]
                    mates[(x["player"], shared[0])].append(r)
        if mates:
            rows = []
            for (mt, s), rs in sorted(mates.items(), key=lambda kv: (-len(kv[1]), kv[1][0]["date"])):
                rows.append([ctx.face(mt, 28) + '<span class="pwrap">' + ctx.plink(mt) + '<span class="sub2">%s</span></span>' % esc(
                    rs[0]["my"] if len(rs) == 1 else rs[0]["my"] + " to " + rs[-1]["my"]), ctx.tlink(s), str(len(rs))])
            b.append(section("Teammates on the same list", table([("Teammate", "name left"), ("Team", "team left"), ("Lists", "num")], rows),
                             "Players from the same team on the same U.S. list."))
    else:
        b.append('<p class="subtitle">%s has no U.S. list in the source data.</p>' % esc(name))
    bio = ctx.cross.bio.get(name)
    if bio and (bio.get("birth_place") or bio.get("nationality")):
        bits = []
        if bio.get("birth_place"):
            bits.append('<div><span class="k">Birthplace</span>%s</div>' % esc(bio["birth_place"]))
        if bio.get("nationality"):
            bits.append('<div><span class="k">Nationality</span>%s</div>' % esc(bio["nationality"]))
        if bio.get("career_map_url"):
            bits.append('<div><a href="%s">%s on the NBA Career Map</a></div>' % (esc(bio["career_map_url"]), esc(name)))
        b.append(section("Birthplace", '<div class="kv">%s</div>' % "".join(bits)))
    if p["intl"]:
        rows = [[esc(r["label"]), esc(r["type"]), str(e["rank"]), ctx.team_cell(e) + flag_html(e, ctx)] for r, e in p["intl"]]
        b.append(section("International lists", table([("Date", "left"), ("List", "left"), ("No.", "rank"), ("Team", "team left")], rows),
                         "Europe and Philippines lists. Not counted in any U.S. number on this page."))
    alt = "%s NBA jersey sales card: %s" % (name, (
        "best season rank No. %d, %s, %s" % (p["best_season"], plural(p["no1_seasons"], "No. 1 season"), plural(p["top10_seasons"], "top 10 season"))
        if p["best_season"] else ("best list rank No. %d" % p["peak"] if p["lists"] else plural(len(p["intl"]), "international list"))))
    site.add("player/" + slug, url, page("../../", url, title, desc, "\n".join(b), indexable=p["indexable"], nav=None,
                                         share="player/" + slug, share_alt=alt), p["indexable"])
    tslug = player_team_slug(p)
    site.cards.append({"key": "player/" + slug, "kind": "player", "name": name, "slug": slug,
                       "color": team_color(m, tslug) if tslug else "#3b82f6",
                       "stats": [("Best season rank", ("No. %d" % p["best_season"]) if p["best_season"] else "None"),
                                 ("No. 1 seasons", str(p["no1_seasons"])), ("Top 10 seasons", str(p["top10_seasons"]))]
                       if p["lists"] else [("International lists", str(len(p["intl"])))],
                       "line": "NBA jersey sales rankings, %s" % span})


def stacked_chart(ctx, m, seasons, by_season, top):
    """Stacked bars of ranking points per season: top contributors plus Other."""
    vals = [sum(by_season.get(s, {}).values()) for s in seasons]
    vmax = max([10] + vals)
    # The y-axis is its own small SVG outside the scroller, so it stays pinned on phones.
    top_pad, h = 14, 150
    height = top_pad + h + 58
    n = len(seasons)
    cp = 100.0 / n
    A = ['<svg class="yax" width="30" height="%d" viewBox="0 0 30 %d" aria-hidden="true">' % (height, height)]
    P = [fluid_svg("bchart", n, height, "HoopsMatic ranking points by season, stacked by player")]
    base = top_pad + h
    P.append('<line class="grid" x1="0" x2="100%%" y1="%d" y2="%d"/>' % (base, base))
    for t in sorted({0, vmax // 2, vmax}):
        yy = base - h * t / vmax
        A.append('<text class="ytk" x="26" y="%.1f">%d</text>' % (yy + 4, t))
        if t:
            P.append('<line class="grid" x1="0" x2="100%%" y1="%.1f" y2="%.1f"/>' % (yy, yy))
    A.append("</svg>")
    for i, s in enumerate(seasons):
        yy = base
        parts = by_season.get(s, {})
        segs = [(k, parts[k]) for k in top if parts.get(k)]
        other = sum(v for k, v in parts.items() if k not in top)
        if other:
            segs.append(("Other", other))
        for k, v in segs:
            hh = h * v / vmax
            yy -= hh
            col = STACK_COLORS[top.index(k)] if k in top else OTHER_COLOR
            tip = "%s, %s: %s" % (s, k, plural(v, "point"))
            P.append('<rect x="%s" y="%.1f" width="%s" height="%.1f" fill="%s" tabindex="0" data-tip="%s"><title>%s</title></rect>' % (
                xp((i + .1) * cp), yy, xp(.8 * cp), max(hh, .5), col, esc(tip), esc(tip)))
        P.append(x_label((i + .5) * cp, base + 12, s))
    P.append("</svg>")
    leg = "".join('<span><i style="background:%s"></i>%s</span>' % (STACK_COLORS[i], ctx.plink(k)) for i, k in enumerate(top))
    if any(k not in top for s in seasons for k in by_season.get(s, {})):
        leg += '<span><i style="background:%s"></i>Other</span>' % OTHER_COLOR
    return '<div class="chart-frame"><div class="yaxis">%s</div><div class="chart-scroll">%s</div></div><div class="legend">%s</div>' % (
        "".join(A), "".join(P), leg)


def bar_chart(values, label_fn, aria, unit=""):
    """values: list of (label, value or None). None = no data (drawn as a gap)."""
    nums = [v for _, v in values if v is not None]
    vmax = max([1] + [abs(v) for v in nums])
    vmin = min([0] + nums)
    # The y-axis is its own small SVG outside the scroller, so it stays pinned on phones.
    top, h, axw = 14, 150, 40
    height = top + h + 58
    n = len(values)
    cp = 100.0 / n
    neg = vmin < 0
    zero = top + (h * (vmax / (vmax - vmin)) if neg else h)
    scale = (h / (vmax - vmin)) if neg else h / vmax
    axis = ('<svg class="yax" width="%d" height="%d" viewBox="0 0 %d %d" aria-hidden="true">'
            '<text class="ytk" x="%d" y="%.1f">0</text><text class="ytk" x="%d" y="%.1f">%s</text></svg>') % (
        axw, height, axw, height, axw - 4, zero + 4, axw - 4, top + 8, esc(label_fn(vmax)))
    parts = [fluid_svg("bchart", n, height, aria)]
    parts.append('<line class="grid" x1="0" x2="100%%" y1="%.1f" y2="%.1f"/>' % (zero, zero))
    for i, (lab, v) in enumerate(values):
        bx, bw = xp((i + .1) * cp), xp(.8 * cp)
        if v is None:
            parts.append('<rect class="gap" x="%s" y="%d" width="%s" height="%d"><title>%s</title></rect>' % (
                bx, top, bw, h, esc(lab + ": no data")))
        else:
            bh = abs(v) * scale
            yy = zero - bh if v >= 0 else zero
            parts.append('<rect class="bar%s" x="%s" y="%.1f" width="%s" height="%.1f"><title>%s</title></rect>' % (
                " neg" if v < 0 else "", bx, yy, bw, max(bh, 0.5), esc("%s: %s%s" % (lab, label_fn(v), unit))))
        parts.append(x_label((i + .5) * cp, top + h + 12, lab))
    parts.append("</svg>")
    return '<div class="chart-frame"><div class="yaxis wide">%s</div><div class="chart-scroll">%s</div></div>' % (axis, "".join(parts))


def build_team_page(site, t, ctx, m, team_entries, tp_season, tp_lists):
    slug = t["slug"]
    url = SITE + "team/%s/" % slug
    ent = team_entries.get(slug, [])
    us_ent = [(r, e) for r, e in ent if r["us"]]
    intl_ent = [(r, e) for r, e in ent if not r["us"]]
    years = sorted({r["date"][:4] for r, _ in us_ent})
    span = (years[0] if years[0] == years[-1] else "%s-%s" % (years[0], years[-1])) if years else ""
    players_set = sorted({e["player"] for _, e in us_ent})
    by_season = {s: dict(v) for s, v in tp_season.get(slug, {}).items()}
    contrib = defaultdict(int)
    for s, v in by_season.items():
        for n, x in v.items():
            contrib[n] += x
    total = sum(contrib.values())
    ranked_contrib = sorted(contrib.items(), key=lambda kv: (-kv[1], kv[0]))
    indexable = bool(us_ent)
    on = defaultdict(list)
    for r, e in us_ent:
        on[r["id"]].append(e)
    drought_n, d_a, d_b = M.longest_drought(m.us, lambda r: bool(on.get(r["id"])))
    ongoing = drought_n and d_b is m.eligible[-1]
    best_season = max(by_season.items(), key=lambda kv: (sum(kv[1].values()), kv[0])) if by_season else None
    full = t["full_name"]
    the = "the " + full
    title = ("%s Jersey Sales Rankings, %s | HoopsMatic" % (full, span)) if span else \
        "%s Jersey Sales Rankings | HoopsMatic" % full
    if us_ent:
        lead = ("led by %s (%d%% of points)" % (ranked_contrib[0][0], pct(ranked_contrib[0][1], total))) if total else "none in a season top 10"
        desc = "%s jersey sales: %s on the NBA's best-selling jersey lists since %s, %s%s." % (
            full, plural(len(players_set), "player"), years[0], lead,
            (". Best season: %s" % best_season[0]) if best_season else "")
    else:
        desc = "%s jersey sales: no player on any of the %d complete U.S. lists of best-selling NBA jerseys in the source data." % (full, len(m.eligible))
    logo = ctx.team_logo(slug)
    mark = ('<img class="logo big" src="%s" alt="" width="48" height="48">' % esc(logo)) if logo else \
        '<span class="sw big" style="background:%s"></span>' % esc(t.get("color") or "#999")
    b = [crumbs(ctx.root, [("Teams", "teams/"), (full, "team/%s/" % slug)]),
         '<div class="ehead">%s<h1>%s jersey sales rankings</h1></div>' % (mark, esc(full))]
    if total:
        topn, topv = ranked_contrib[0]
        if topv == total:
            tk = "All of %s HoopsMatic jersey ranking points since 2001 come from %s (%s)." % (poss(the), topn, plural(total, "point"))
        else:
            tk = "%s accounts for %d%% of %s HoopsMatic jersey ranking points since 2001 (%d of %d)." % (topn, pct(topv, total), poss(the), topv, total)
    elif us_ent:
        tk = "%s on U.S. lists, but none finished a season in the top 10 of the HoopsMatic season ranking." % plural(len(players_set), "%s player" % full)
    else:
        tk = "No %s player appears on any U.S. list in the source data." % full
    b.append('<p class="takeaway">%s</p>' % ctx.names(tk, skip=[n for n, v in m.name_links.items() if v == ("team", slug)]))
    cards = []
    if total:
        cards.append(("Largest contributor", ctx.plink(ranked_contrib[0][0]), "%d%% of points" % pct(ranked_contrib[0][1], total)))
    cards.append(("Ranked players", str(len(players_set)), "on U.S. lists"))
    if best_season:
        cards.append(("Best single season", esc(best_season[0]), esc(plural(sum(best_season[1].values()), "point"))))
    if drought_n:
        cards.append(("Longest stretch without a ranked player", plural(drought_n, "list"), esc("%s to %s" % (d_a["my"], d_b["my"])) + (", ongoing" if ongoing else "")))
    b.append(stat_cards(cards))
    seasons = m.seasons
    if total:
        top = [n for n, _ in ranked_contrib[:4]]
        b.append(section("Points by season, by player", '<p class="caveat">HoopsMatic ranking points, not units sold or official team merchandise rankings.</p>' +
                         stacked_chart(ctx, m, seasons, by_season, top),
                         "Each season's HoopsMatic ranking: No. 1 = 10 points down to No. 10 = 1. Top contributors plus Other. Swipe sideways on phones."))
    if us_ent:
        tot = defaultdict(lambda: {"lists": 0, "peak": 99, "seasons": 0, "best": None, "pts": 0})
        for r, e in us_ent:
            x = tot[e["player"]]
            x["lists"] += 1
            x["peak"] = min(x["peak"], e["rank"])
        for s, sr in m.sr.items():
            for row in sr["rows"]:
                if slug in row["team_slugs"]:
                    x = tot[row["player"]]
                    x["seasons"] += 1
                    x["best"] = row["pos"] if x["best"] is None else min(x["best"], row["pos"])
                    x["pts"] += row["pts"]
        rows = [[ctx.face(n, 28) + ctx.plink(n), str(v["pts"]), ("No. %d" % v["best"]) if v["best"] else '<span class="dash">none</span>', str(v["lists"])]
                for n, v in sorted(tot.items(), key=lambda kv: (-kv[1]["pts"], -kv[1]["lists"], kv[0]))]
        b.append(section("Players with this team", table([("Player", "name left"), ("Pts", "num"), ("Best season", "num"), ("Lists", "num")], rows),
                         "Points and best season come from the season rankings, with the team on the player's last list of that season. Lists counts every U.S. list with this team."))
        blocks = []
        for s in seasons:
            lists = [r for r in m.us if r["season"] == s and on.get(r["id"])]
            rows_s = [row for row in m.sr[s]["rows"] if slug in row["team_slugs"]]
            if not lists:
                continue
            lead = ", ".join("%s No. %d" % (ctx.plink(row["player"]), row["pos"]) for row in rows_s) or "No player in the season ranking"
            inner = []
            for r in lists:
                names = ", ".join("%s No. %d%s" % (ctx.plink(e["player"]), e["rank"],
                                                   ' <span class="note">%s</span>' % ctx.names("shared listing: " + e["team"]) if e["multi"] else "")
                                  for e in sorted(on[r["id"]], key=lambda e: e["rank"]))
                inner.append('<div class="ln"><span class="k2">%s</span>%s</div>' % (esc(r["type"] + ", " + r["label"]), names))
            summary = '<span class="sr-s">%s</span><span class="sr-r">%s</span><span class="sr-l">%s</span>' % (
                esc(s), esc(plural(sum(by_season.get(s, {}).values()), "pt")), lead)
            blocks.append(details(summary, '<div class="sr-body">%s<p class="hint">%s</p></div>' % ("".join(inner), ctx.slink(s) + " season page"), cls="srow"))
        b.append(section("Ranked players by season", '<div class="season-rows">%s</div>' % "".join(blocks),
                         "Season ranking first; expand for every U.S. list. Players listed with more than one team (traded during the sales period) count for each team listed."))
        lp = tp_lists.get(slug)
        if lp and lp["pts"]:
            b.append(details("Every-list points: %s from %s" % (esc(plural(lp["pts"], "point")), esc(plural(len(lp["lists"]), "U.S. list"))),
                             '<p class="hint">The same scoring applied to every U.S. list instead of one ranking per season: No. 1 = 10 down to No. 10 = 1 on each list.</p>'))
    att = ctx.cross.att.get(str(t["team_id"]), {})
    if att:
        rows, vals = [], []
        for s in seasons:
            a = att.get(season_end(s))
            vals.append((s, round(a["mean_delta"]) if a else None))
            rows.append([ctx.slink(s), str(sum(by_season.get(s, {}).values())),
                         ('%+d<span class="sub2">%s</span>' % (round(a["mean_delta"]), esc(plural(a["games"], "game")))) if a else '<span class="dash">no data</span>'])
        b.append(section("Road attendance draw", bar_chart(vals, lambda v: "%+d" % v, "%s road attendance draw by season" % full, " fans") +
                         table([("Season", "left nw"), ("Ranking pts", "num"), ("Road draw", "num")], rows),
                         'Average crowd above or below the home arena\'s usual attendance when this team visited, from the <a href="%s">HoopsMatic NBA attendance tracker</a>. Seasons marked no data are missing there.' % ATTENDANCE_TOOL))
    if intl_ent:
        rows = [[esc(r["label"]), esc(r["type"]), str(e["rank"]), ctx.face(e["player"], 28) + ctx.plink(e["player"])] for r, e in intl_ent]
        b.append(section("International lists", table([("Date", "left"), ("List", "left"), ("No.", "rank"), ("Player", "name left")], rows),
                         "Not counted in any U.S. number on this page."))
    alt = "%s NBA jersey sales card: %s" % (full, ("%s leads with %d%% of HoopsMatic ranking points" % (ranked_contrib[0][0], pct(ranked_contrib[0][1], total))) if total
                                             else plural(len(players_set), "player") + " on U.S. lists")
    b.append(index_links(ctx.root))
    site.add("team/" + slug, url, page("../../", url, title, desc, "\n".join(b), indexable=indexable, nav="teams",
                                       share="team/" + slug, share_alt=alt), indexable)
    site.cards.append({"key": "team/" + slug, "kind": "team", "name": full, "color": t.get("color") or "#3b82f6",
                       "stats": [("Ranked players", str(len(players_set))),
                                 ("Largest contributor", ranked_contrib[0][0] if total else "None"),
                                 ("Best season", best_season[0] if best_season else "None")],
                       "line": "NBA jersey sales rankings" + (", %s" % span if span else "")})
    # Best player: most ranking points with this team; then best season rank, then most lists.
    best_player = None
    if us_ent:
        bp = {}
        for n in players_set:
            rows_n = [row for sr in m.sr.values() for row in sr["rows"] if row["player"] == n and slug in row["team_slugs"]]
            bp[n] = (-contrib.get(n, 0), min([row["pos"] for row in rows_n] or [999]), -sum(1 for _, e in us_ent if e["player"] == n), n)
        best_player = min(bp, key=lambda n: bp[n])
    return {"slug": slug, "name": full, "points": total, "players": len(players_set), "lists": len(on), "best": best_player,
            "best_pts": contrib.get(best_player, 0) if best_player else 0,
            "list_points": (tp_lists.get(slug) or {}).get("pts", 0), "list_fed": len((tp_lists.get(slug) or {}).get("lists", ())),
            "drought": drought_n, "indexable": indexable, "color": t.get("color")}


def pva_table(ctx, s):
    # Jersey and Awards are both plain ranks; the honors behind the award rank sit under the name.
    rows = []
    for x in s["rows"]:
        if x["award"] is None:
            aw, gap = '<span class="dash">-</span>', '<span class="dash">-</span>'
        else:
            aw = "No. %d" % x["award"]
            gap = '<span class="%s">%+d</span>' % ("pos" if x["gap"] > 0 else ("neg" if x["gap"] < 0 else "flat"), x["gap"])
        who = ctx.face(x["player"], 28) + '<span class="pwrap">%s<span class="tline sub3">%s</span></span>' % (ctx.plink(x["player"]), esc(x["detail"]))
        rows.append([who, "No. %d" % x["jersey"], aw, gap])
    out = table([("Player", "name left"), ("Jersey", "num"), ("Awards", "num"), ("Gap", "num")], rows, cls="lb pvat")
    if s["award_top10_missing"]:
        out += '<p class="hint">Award top 10 outside this jersey ranking: %s.</p>' % ", ".join(
            "%s (No. %d)" % (ctx.plink(x["player"]), x["rank"]) for x in s["award_top10_missing"])
    return out


def build_season_page(site, season, ctx, m, pva, first_season_of):
    url = SITE + "season/%s/" % season
    lists = [r for r in m.releases if r["season"] == season]
    us_lists = [r for r in lists if r["us"]]
    sr = m.sr[season]
    rows = sr["rows"]
    no1 = rows[0]["player"] if rows else None
    debuts = sorted({e["player"] for r in us_lists for e in r["entries"] if first_season_of.get(e["player"]) == season},
                    key=lambda n: min((r["date"], e["rank"], n) for r in us_lists for e in r["entries"] if e["player"] == n))
    indexable = sr["kind"] == "full"
    top3 = [x["player"] for x in rows[:3]]
    title = "%s NBA Jersey Sales Rankings: %s No. 1 | HoopsMatic" % (season, no1) if no1 else "%s NBA Jersey Sales Rankings | HoopsMatic" % season
    fed_labels = [r["my"] for r in sr["fed"]]
    if sr["kind"] == "full":
        desc = "Best-selling NBA jerseys of %s: %s lead the season ranking built from %s." % (
            season, join_names(top3), plural(len(sr["fed"]), "official U.S. list"))
        tk = "%s finished No. 1 in the %s HoopsMatic season ranking%s, built from %s (%s)." % (
            no1, season, " (shared)" if rows[0]["shared"] else "", plural(len(sr["fed"]), "U.S. list"), join_names(fed_labels))
    else:
        desc = "Best-selling NBA jersey of %s: %s was No. 1. Only the No. 1 of the season's list is in the source data." % (season, no1)
        tk = "%s was No. 1 on the only %s list in the source data, which has just the No. 1." % (no1, season)
    b = [crumbs(ctx.root, [("Seasons", "seasons/"), (season, "season/%s/" % season)]),
         '<h1>%s NBA jersey sales rankings</h1>' % esc(season), '<p class="takeaway">%s</p>' % ctx.names(tk)]
    # Season ranking.
    head = [("No.", "rank"), ("Player", "name left")]
    for r in sr["fed"]:
        head.append(('<span title="%s">%s</span>' % (esc(ctx.rel_name(r)), esc(r["my"])), "num lc"))
    if len(sr["fed"]) > 1:
        head.append(("Avg.", "num"))
    trs = []
    for row in rows:
        e = row["entry"]
        cells = ["%d%s" % (row["pos"], "=" if row["shared"] else ""),
                 ctx.face(row["player"], 28) + '<span class="pwrap">' + ctx.plink(row["player"]) + '<span class="tline">%s</span></span>' % ctx.team_cell(e)]
        for k, r in enumerate(sr["fed"]):
            cells.append(str(row["ranks"][k]) if row["on"][k] else '<span class="dash" title="%s">%d*</span>' % (
                esc("Not on this list; counted as No. %d" % (r["n"] + 1)), r["n"] + 1))
        if len(sr["fed"]) > 1:
            cells.append("%.2f" % row["avg"])
        trs.append(cells)
    hint = "Lists that fed this ranking: %s." % "; ".join("%s (%d players)" % (ctx.rel_name(r), r["n"]) for r in sr["fed"])
    if sr["kind"] == "full":
        hint += " Average rank across them; a player missing from a list counts one spot below its last place (marked *). The ranking goes %d deep, the length of the shortest list. Ties: better best rank, then better rank on the later list; = marks a shared position." % sr["depth"]
    if sr["skipped"]:
        hint += " Not counted: %s." % "; ".join("%s (%s)" % (ctx.rel_name(r), why) for r, why in sr["skipped"])
    b.append(section("Season ranking", table(head, trs, cls="lb sx", row_cls=["lead" if row["pos"] == 1 else "" for row in rows]), esc(hint), sid="ranking"))
    cards = []
    for r in lists:
        note = release_note(r)
        tag = "" if r["us"] else '<span class="tag">International</span>'
        if r["status"] == "partial":
            tag += '<span class="tag warn">Incomplete</span>'
        inner = list_table(ctx, r, compact=True) if r["entries"] else ""
        cards.append('<div class="lcard"><div class="lhead"><h3>%s</h3><span class="sub2">%s</span>%s%s</div>%s%s</div>' % (
            esc(r["type"]), esc(r["label"] + (" (season final)" if r["final"] else "")), tag, source_link(r),
            ('<p class="hint">%s</p>' % esc(note)) if note else "", inner))
    b.append(section("Every list this season", '<div class="lcards">%s</div>' % "".join(cards),
                     "Side by side; swipe sideways on phones. Change compares with the previous complete U.S. list: the exact move for every player on both lists."))
    mv_blocks = []
    for r in us_lists:
        if not r["compare_id"]:
            continue
        prev = m.byid[r["compare_id"]]
        mv = [(e["player"], e["move"]["from"], e["rank"]) for e in r["entries"] if e["move"]["kind"] in ("up", "down")]
        mv.sort(key=lambda x: -(x[1] - x[2]))
        if not mv:
            continue
        rws = [[ctx.face(n, 28) + ctx.plink(n), '<span class="pair">No. %d <span class="arr">&#8594;</span> No. %d</span>' % (a, c), move_html({"kind": "up" if a > c else "down", "delta": abs(a - c), "from": a})] for n, a, c in mv]
        mv_blocks.append('<h3 class="h3">%s to %s</h3>%s' % (esc(ctx.rel_name(prev)), esc(ctx.rel_name(r)),
                                                             table([("Player", "name left"), ("Before / after", "num"), ("Move", "mvc")], rws)))
    if mv_blocks:
        b.append(section("Movers", "".join(mv_blocks), "Players on both lists, biggest climb first."))
    if debuts:
        rws = []
        for n in debuts:
            r, e = next((r, e) for r in us_lists for e in r["entries"] if e["player"] == n)
            rws.append([ctx.face(n, 28) + '<span class="pwrap">' + ctx.plink(n) + '<span class="sub2">%s</span></span>' % esc(ctx.rel_name(r)), "No. %d" % e["rank"], ctx.team_cell(e)])
        b.append(section("First appearances", table([("Player / first list", "name left"), ("Rank", "num"), ("Team", "team left")], rws),
                         "First appearance on any U.S. list in the source data."))
    # Dropped off: within the top L of the last complete list before the season, on no U.S. list this season.
    first_el = next((r for r in us_lists if r["status"] == "full"), None)
    if first_el and first_el["compare_id"]:
        prev = m.byid[first_el["compare_id"]]
        # Dropped only if the previous rank fits inside one of this season's complete lists.
        L = max(r["n"] for r in us_lists if r["status"] == "full")
        names = {e["player"] for r in us_lists for e in r["entries"]}
        dropped = [e for e in prev["entries"] if e["rank"] <= L and e["player"] not in names]
        if dropped:
            rws = [[ctx.face(e["player"], 28) + ctx.plink(e["player"]), "No. %d" % e["rank"], ctx.team_cell(e)] for e in dropped]
            b.append(section("Dropped off", table([("Player", "name left"), ("Last rank", "num"), ("Team", "team left")], rws),
                             "No. %d or better on the %s list and on no U.S. list in %s." % (L, esc(ctx.rel_name(prev)), esc(season))))
    s = pva.get(season)
    if s and s["rows"]:
        b.append(section("Popularity vs. awards", pva_table(ctx, s),
                         "Season jersey rank against the league-wide award rank: All-NBA team, then MVP voting place. Gap = award rank minus jersey rank; positive means the jersey ranked above the player's awards."))
    nav = []
    i = m.seasons.index(season)
    if i > 0:
        nav.append('<a href="../%s/">%s</a>' % (m.seasons[i - 1], esc(m.seasons[i - 1])))
    if i < len(m.seasons) - 1:
        nav.append('<a href="../%s/">%s</a>' % (m.seasons[i + 1], esc(m.seasons[i + 1])))
    b.append('<p class="pager">%s</p>' % " &middot; ".join(nav))
    b.append(index_links(ctx.root))
    alt = "%s NBA jersey sales card: %s No. 1 in the HoopsMatic season ranking" % (season, no1)
    site.add("season/" + season, url, page("../../", url, title, desc, "\n".join(b), indexable=indexable, nav="seasons",
                                           share="season/" + season, share_alt=alt), indexable)
    tslug = rows[0]["team_slugs"][0] if rows and rows[0]["team_slugs"] else None
    site.cards.append({"key": "season/" + season, "kind": "season", "name": "%s NBA jersey sales" % season,
                       "color": team_color(m, tslug) if tslug else "#3b82f6",
                       "stats": [("No. %d" % x["pos"], x["player"]) for x in rows[:3]],
                       "line": "HoopsMatic season ranking from %s" % plural(len(sr["fed"]), "U.S. list")})
    return {"season": season, "lists": len(lists), "lists_with_entries": sum(1 for r in lists if r["entries"]), "fed": len(sr["fed"]),
            "kind": sr["kind"], "no1": no1, "top3": rows[:3], "indexable": indexable}


def rec_block(sid, title, headline, visual, full_head, full_rows, hint=""):
    return ('<section class="section rec" id="%s"><h2>%s</h2><p class="headline">%s</p>%s%s%s</section>') % (
        sid, esc(title), headline, visual,
        details("View full ranking (%d)" % len(full_rows), table(full_head, full_rows, limit=0)) if full_rows else "",
        ('<p class="hint">%s</p>' % hint) if hint else "")


def build_records(site, ctx, rec, m):
    url = SITE + "records/"
    P = m.players
    n1 = rec["no1_seasons"][0]
    reign = rec["reigns"][0]
    title = "NBA Jersey Sales Records: No. 1 Seasons and Reigns | HoopsMatic"
    desc = "NBA jersey sales records since 2001: %s has the most No. 1 seasons (%d); %s held No. 1 on %d straight lists. Climbs, falls and duos." % (
        n1["name"], n1["no1_seasons"], reign["player"], reign["n"])
    cats = [("no1-seasons", "Most No. 1 seasons"), ("top10-seasons", "Most top 10 seasons"), ("reigns", "No. 1 reigns"),
            ("never-no1", "Most lists without a No. 1"), ("climbs", "Biggest climbs"), ("falls", "Biggest falls"),
            ("points", "Jersey ranking points since 2001"), ("early", "Early-career appearances"),
            ("one-list", "One-list players"), ("duos", "Same-team duos")]
    b = ['<h1>NBA jersey sales records</h1>',
         '<p class="takeaway">%s</p>' % ctx.names("%s finished No. 1 in %s, more than anyone since 2000-01." % (n1["name"], plural(n1["no1_seasons"], "season"))),
         '<nav class="jump" aria-label="Record categories">%s</nav>' % "".join('<a class="chip" href="#%s">%s</a>' % (k, esc(t)) for k, t in cats)]
    face = lambda n: ctx.face(n, 24) + ctx.plink(n)
    # 1. No. 1 seasons.
    L = rec["no1_seasons"]
    b.append(rec_block("no1-seasons", "Most No. 1 seasons",
                       "%s leads with %s (and %s)." % (ctx.plink(n1["name"]), plural(n1["no1_seasons"], "No. 1 season"), plural(n1["no1_lists"], "No. 1 list")),
                       hbars([(face(p["name"]), p["no1_seasons"], "%s &middot; %s" % (plural(p["no1_lists"], "No. 1 list"), plural(p["lists"], "U.S. list"))) for p in L[:TOP_N]]),
                       [("Player", "name left"), ("No. 1 seasons", "num"), ("No. 1 lists", "num")],
                       [[face(p["name"]), str(p["no1_seasons"]), str(p["no1_lists"])] for p in L],
                       "Seasons: one HoopsMatic ranking per season. Lists: every U.S. list."))
    L = rec["top10_seasons"]
    b.append(rec_block("top10-seasons", "Most top 10 seasons",
                       "%s finished in the season top 10 %s." % (ctx.plink(L[0]["name"]), plural(L[0]["top10_seasons"], "time")),
                       hbars([(face(p["name"]), p["top10_seasons"], "%s &middot; %s" % (plural(p["top5_seasons"], "top 5 season"), plural(p["top10_lists"], "top 10 list"))) for p in L[:TOP_N]]),
                       [("Player", "name left"), ("Top 10 seasons", "num"), ("Top 5 seasons", "num"), ("Top 10 lists", "num"), ("Top 5 lists", "num")],
                       [[face(p["name"]), str(p["top10_seasons"]), str(p["top5_seasons"]), str(p["top10_lists"]), str(p["top5_lists"])] for p in L]))
    # 3. Reigns as a timeline.
    chrono = rec["reigns_chrono"]
    tot = sum(x["n"] for x in chrono)
    tl = "".join('<a class="tl-seg" href="%splayer/%s/" style="flex:%d;background:%s" data-tip="%s" title="%s"></a>' % (
        ctx.root, P[x["player"]]["slug"], x["n"], esc(team_color(m, x["start"]["entries"][0]["team_slugs"][0])),
        esc("%s: %s, %s" % (x["player"], plural(x["n"], "list"), x["start"]["my"] + (" to " + x["end"]["my"] if x["n"] > 1 else ""))),
        esc("%s: %s" % (x["player"], plural(x["n"], "list")))) for x in chrono)
    vis = '<div class="timeline" aria-hidden="true">%s</div><div class="tl-axis"><span>%s</span><span>%s</span></div>' % (tl, esc(chrono[0]["start"]["my"]), esc(chrono[-1]["end"]["my"]))
    top5 = "".join('<li>%s<span class="pwrap">%s<span class="sub2">%s, %s</span></span></li>' % (
        ctx.face(x["player"], 28), ctx.plink(x["player"]), esc(plural(x["n"], "list")), esc(x["start"]["my"] + (" to " + x["end"]["my"] if x["n"] > 1 else "")))
        for x in rec["reigns"][:TOP_N])
    b.append(rec_block("reigns", "No. 1 reigns",
                       "%s held No. 1 on %s in a row, %s to %s." % (ctx.plink(reign["player"]), plural(reign["n"], "U.S. list"), esc(reign["start"]["my"]), esc(reign["end"]["my"])),
                       vis + '<ol class="top5">%s</ol>' % top5,
                       [("Player", "name left"), ("Lists", "num"), ("From / to", "left")],
                       [[face(x["player"]), str(x["n"]), esc(x["start"]["my"] + (" to " + x["end"]["my"] if x["n"] > 1 else ""))] for x in rec["reigns"]],
                       "Consecutive U.S. lists at No. 1, %s in all. The empty Jan 2005 list is skipped; lists with only the No. 1 count." % plural(tot, "list")))
    L = rec["never_no1"]
    b.append(rec_block("never-no1", "Most lists without a No. 1",
                       "%s made %s without ever reaching No. 1 (best: No. %d)." % (ctx.plink(L[0]["name"]), plural(L[0]["lists"], "U.S. list"), L[0]["peak"]),
                       hbars([(face(p["name"]), p["lists"], "best No. %d" % p["peak"]) for p in L[:TOP_N]]),
                       [("Player", "name left"), ("Lists", "num"), ("Best", "num")],
                       [[face(p["name"]), str(p["lists"]), "No. %d" % p["peak"]] for p in L]))
    for key, sid, ttl in (("jumps", "climbs", "Biggest climbs"), ("falls", "falls", "Biggest falls")):
        L = rec[key]
        if not L:
            continue
        x0 = L[0]
        pairs = "".join('<div class="pairrow"><span class="pp">%s</span><span class="pair"><b>No. %d</b><span class="arr">&#8594;</span><b>No. %d</b></span><span class="sub2">%s to %s</span></div>' % (
            face(x["player"]), x["from"], x["to"], esc(x["prev"]["my"]), esc(x["cur"]["my"])) for x in L[:TOP_N])
        b.append(rec_block(sid, ttl, "%s went from No. %d to No. %d between the %s and %s lists." % (
            ctx.plink(x0["player"]), x0["from"], x0["to"], esc(x0["prev"]["my"]), esc(x0["cur"]["my"])),
            '<div class="pairs">%s</div>' % pairs,
            [("Player", "name left"), ("Before", "num"), ("After", "num"), ("Lists", "left")],
            [[face(x["player"]), "No. %d" % x["from"], "No. %d" % x["to"], esc(x["prev"]["my"] + " to " + x["cur"]["my"])] for x in L],
            "Adjacent complete U.S. lists only, with the exact move for a player on both (lists ran 25, 20, 15 and 10 deep)."))
    L = rec["points"]
    b.append(rec_block("points", "Jersey ranking points since 2001",
                       "%s leads with %s. Scoring: in each season's HoopsMatic ranking, No. 1 earns 10 points, No. 2 earns 9, down to 1 point for No. 10." % (
                           ctx.plink(L[0]["name"]), plural(L[0]["season_points"], "point")),
                       hbars([(face(p["name"]), p["season_points"], "%s on every list" % plural(p["list_points"], "point")) for p in L[:TOP_N]]),
                       [("Player", "name left"), ("Season pts", "num"), ("Every-list pts", "num"), ("Lists", "num")],
                       [[face(p["name"]), str(p["season_points"]), str(p["list_points"]), str(p["lists"])] for p in L],
                       'Every-list points apply the same scoring to each U.S. list. Downloads for the <a href="%s">bar chart race tool</a>: <a href="../data/jersey-points-race.csv" download>season ranking points (CSV)</a>, <a href="../data/jersey-points-race-all-lists.csv" download>every-list points (CSV)</a>.' % RACE_TOOL))
    L = rec["early"]
    if L:
        rk = L[0]
        b.append(rec_block("early", "Early-career appearances",
                           "%s reached No. %d on a U.S. list in %s, in the player's %s NBA season." % (
                               ctx.plink(rk["player"]), rk["rank"], esc(rk["release"]["my"]), "first" if rk["season_no"] == 1 else "second"),
                           '<ol class="top5">%s</ol>' % "".join('<li>%s<span class="pwrap">%s<span class="sub2">No. %d, %s, %s</span></span></li>' % (
                               ctx.face(x["player"], 28), ctx.plink(x["player"]), x["rank"], esc(x["release"]["my"]), "rookie season" if x["season_no"] == 1 else "second season") for x in L[:TOP_N]),
                           [("Player / first list", "name left"), ("Rank", "num"), ("Season played", "left"), ("First NBA season", "left")],
                           [[face(x["player"]) + '<span class="sub2">%s</span>' % esc(ctx.rel_name(x["release"])), "No. %d" % x["rank"],
                             "Rookie season" if x["season_no"] == 1 else "Second season", esc(x["nba_first"])] for x in L],
                           "First U.S. list in the player's first or second season played (a season with at least one regular-season game, from nba-player-data), so a missed first season does not count."))
    L = rec["one_list"]
    cards = "".join('<a class="pcard" href="%splayer/%s/">%s<span class="pc-n">%s</span><span class="pc-r">No. %d</span><span class="sub2">%s</span></a>' % (
        ctx.root, P[n]["slug"], ctx.face(n, 56, "face big"), esc(n), e["rank"], esc(r["my"])) for n, r, e in L[:TOP_N])
    b.append(rec_block("one-list", "One-list players",
                       "%s reached No. %d on %s only U.S. list (%s)." % (ctx.plink(L[0][0]), L[0][2]["rank"], "the player's", esc(L[0][1]["my"])),
                       '<div class="pcards">%s</div>' % cards,
                       [("Player / only list", "name left"), ("Rank", "num"), ("Team", "team left")],
                       [[face(n) + '<span class="sub2">%s</span>' % esc(ctx.rel_name(r)), "No. %d" % e["rank"], ctx.team_cell(e) + flag_html(e, ctx)] for n, r, e in L],
                       "Exactly one U.S. list in the source data, best rank first."))
    L = rec["duos"]
    d0 = L[0]
    pairs = "".join('<div class="duo"><span class="duo-f">%s%s</span><span class="duo-n">%s &amp; %s</span><span class="sub2">%s, %s</span></div>' % (
        ctx.face(d["a"], 44, "face big"), ctx.face(d["b"], 44, "face big"), ctx.plink(d["a"]), ctx.plink(d["b"]),
        ctx.tlink(d["team"]), esc(plural(len(d["lists"]), "list"))) for d in L[:TOP_N])
    b.append(rec_block("duos", "Same-team duos",
                       "%s and %s shared %s with the %s." % (ctx.plink(d0["a"]), ctx.plink(d0["b"]), esc(plural(len(d0["lists"]), "U.S. list")), ctx.tlink(d0["team"])),
                       '<div class="duos">%s</div>' % pairs,
                       [("Duo", "name left"), ("Team", "team left"), ("Lists", "num")],
                       [[ctx.face(d["a"], 24) + ctx.face(d["b"], 24) + ctx.plink(d["a"]) + " &amp; " + ctx.plink(d["b"]) + '<span class="sub2">%s</span>' % esc(
                           d["lists"][0]["my"] + (" to " + d["lists"][-1]["my"] if len(d["lists"]) > 1 else "")), ctx.tlink(d["team"]), str(len(d["lists"]))] for d in L],
                       "Two players from the same team on the same U.S. list."))
    site.add("records", url, page("../", url, title, desc, "\n".join(b), indexable=True, nav="records", share="records",
                                  share_alt="NBA jersey sales records card: %s, %s" % (n1["name"], plural(n1["no1_seasons"], "No. 1 season"))), True)
    site.cards.append({"key": "records", "kind": "records", "name": "NBA jersey sales records", "color": team_color(m, player_team_slug(n1)),
                       "stats": [("Most No. 1 seasons", "%s (%d)" % (n1["name"], n1["no1_seasons"])),
                                 ("Longest No. 1 reign", "%s (%s)" % (reign["player"], plural(reign["n"], "list"))),
                                 ("Most top 10 seasons", "%s (%d)" % (rec["top10_seasons"][0]["name"], rec["top10_seasons"][0]["top10_seasons"]))],
                       "line": "Every U.S. list since 2001"})


def what_changed(m, r):
    """3-4 findings from templates for one U.S. list against the previous complete list."""
    out = []
    if not r.get("compare_id"):
        return out
    prev = m.byid[r["compare_id"]]
    L = r["compare_len"]
    lead, plead = r["entries"][0]["player"], prev["entries"][0]["player"]
    if lead != plead:
        out.append("%s took over No. 1 from %s." % (lead, plead))
    else:
        k = 0
        for q in reversed([x for x in m.nonempty_us if x["date"] <= r["date"]]):
            if q["entries"][0]["player"] != lead:
                break
            k += 1
        out.append("%s stayed No. 1 (%s in a row)." % (lead, plural(k, "list")))
    ups = sorted([e for e in r["entries"] if e["move"]["kind"] == "up"], key=lambda e: (-e["move"]["delta"], e["rank"]))
    if ups:
        e = ups[0]
        out.append("Biggest climb: %s, No. %d to No. %d." % (e["player"], e["move"]["from"], e["rank"]))
    pm = {x["player"]: x["rank"] for x in prev["entries"]}
    t10 = [e["player"] for e in r["entries"] if e["rank"] <= min(10, L) and not (e["player"] in pm and pm[e["player"]] <= min(10, L))]
    if t10:
        out.append("New in the top %d: %s." % (min(10, L), join_names(t10)))
    news = [e["player"] for e in r["entries"] if e["move"]["kind"] == "new"]
    backs = [e["player"] for e in r["entries"] if e["move"]["kind"] == "back"]
    if news or backs:
        bits = []
        if news:
            bits.append("first appearance%s: %s" % ("" if len(news) == 1 else "s", join_names(news)))
        if backs:
            bits.append("re-entr%s: %s" % ("y" if len(backs) == 1 else "ies", join_names(backs)))
        s = "; ".join(bits)
        out.append(s[0].upper() + s[1:] + ".")
    if r["dropped"]:
        out.append("Dropped out: %s." % join_names(x["player"] for x in r["dropped"]))
    # Notable unchanged stretch: same rank on 3+ eligible lists in a row (within each pair's shared length).
    best = None
    idx = m.eligible.index(r)
    for e in r["entries"]:
        k = 1
        j = idx
        while j > 0:
            q = m.eligible[j - 1]
            hit = next((x for x in q["entries"] if x["player"] == e["player"]), None)
            if not hit or hit["rank"] != e["rank"]:
                break
            k += 1
            j -= 1
        if k >= 3 and (best is None or k > best[1]):
            best = (e, k)
    if best:
        out.append("%s has held No. %d for %s in a row." % (best[0]["player"], best[0]["rank"], plural(best[1], "list")))
    return out[:4]


def build_teams_index(site, ctx, m, team_summ):
    url = SITE + "teams/"
    ts = sorted(team_summ, key=lambda t: (-t["points"], -t["list_points"], t["name"]))
    lead = [t for t in ts if t["points"]]
    title = "NBA Jersey Sales by Team: All %d Teams Ranked | HoopsMatic" % len(ts)
    desc = "NBA jersey sales by team since 2001: %s lead with %d HoopsMatic ranking points, then %s and %s. Every team's best-selling players." % (
        lead[0]["name"], lead[0]["points"], lead[1]["name"], lead[2]["name"])
    b = [crumbs(ctx.root, [("Teams", "teams/")]), '<h1>NBA jersey sales by team</h1>',
         '<p class="takeaway">%s</p>' % ctx.names("%s lead all %d teams with %d HoopsMatic jersey ranking points since 2001, most of them from %s." % (
             lead[0]["name"], len(ts), lead[0]["points"], lead[0]["best"]))]
    cards = []
    for rank, t in enumerate(ts, 1):
        logo = ctx.team_logo(t["slug"])
        mark = ('<img class="logo big" src="%s" alt="" width="40" height="40" loading="lazy">' % esc(logo)) if logo else \
            '<span class="sw big" style="background:%s"></span>' % esc(t["color"] or "#999")
        best = ('<span class="tc-best">%s%s</span>' % (ctx.face(t["best"], 24), ctx.plink(t["best"]))) if t["best"] else '<span class="tc-best dash">No ranked player</span>'
        cards.append('<li class="tcard" data-pts="%d" data-rank="%d" data-name="%s">%s<div class="tc-body"><a class="nm tc-name" href="%steam/%s/">%s</a>'
                     '<span class="tc-pts">%s</span>%s<span class="sub2">%s &middot; every list: %s from %s</span></div></li>' % (
                         t["points"], rank, esc(t["name"]), mark, ctx.root, t["slug"], esc(t["name"]), esc(plural(t["points"], "point")), best,
                         esc(plural(t["players"], "ranked player")), esc(plural(t["list_points"], "pt")), esc(plural(t["list_fed"], "list"))))
    sort = ('<div class="seg-btns" role="group" aria-label="Sort teams">'
            '<button type="button" class="segb" data-sort="pts" aria-pressed="true">By points</button>'
            '<button type="button" class="segb" data-sort="name" aria-pressed="false">By name</button></div>')
    b.append(section("All %d teams" % len(ts), sort + '<ul class="tgrid">%s</ul>' % "".join(cards),
                     "Points: HoopsMatic jersey ranking points from the season rankings (No. 1 = 10 down to No. 10 = 1), credited to the team on the player's last list of the season. Top player: most of those points with the team. Every list: the same scoring on each U.S. list. HoopsMatic ranking points, not units sold or official team merchandise rankings.", sid="teams"))
    b.append('<p class="pager"><a href="../seasons/">All seasons</a> &middot; <a href="../records/">Records</a></p>')
    site.add("teams", url, page(ctx.root, url, title, desc, "\n".join(b), indexable=True, nav="teams", share="teams",
                               share_alt="NBA jersey sales by team card: %s lead with %d HoopsMatic ranking points" % (lead[0]["name"], lead[0]["points"])), True)
    site.cards.append({"key": "teams", "kind": "teams", "name": "NBA jersey sales by team", "color": lead[0]["color"] or "#3b82f6",
                       "stats": [(lead[0]["name"], "%d pts" % lead[0]["points"]), (lead[1]["name"], "%d pts" % lead[1]["points"]),
                                 ("Teams with points", "%d of %d" % (len(lead), len(ts)))],
                       "line": "HoopsMatic jersey ranking points since 2001"})


def build_seasons_index(site, ctx, m, season_summ):
    url = SITE + "seasons/"
    ss = list(reversed(season_summ))
    first, last = season_summ[0], season_summ[-1]
    title = "NBA Jersey Sales by Season, %s to %s | HoopsMatic" % (first["season"], last["season"])
    desc = "Best-selling NBA jerseys of every season since %s: the No. 1 and top 3 of each HoopsMatic season ranking, from %s in %s back to %s." % (
        first["season"], last["no1"], last["season"], first["no1"])
    b = [crumbs(ctx.root, [("Seasons", "seasons/")]), '<h1>NBA jersey sales by season</h1>',
         '<p class="takeaway">%s</p>' % ctx.names("%s different No. 1s across %d seasons, newest first. %s was No. 1 in %s." % (
             len({x["no1"] for x in ss if x["no1"]}), len(ss), last["no1"], last["season"]))]
    rows = []
    for x in ss:
        top = x["top3"]
        no1 = (ctx.face(top[0]["player"], 28) + '<span class="pwrap">%s<span class="tline sub3">%s</span></span>' % (
            ctx.plink(top[0]["player"]), "".join('<span class="t3">No. %d %s</span>' % (r["pos"], ctx.plink(r["player"])) for r in top[1:]) or "Only the No. 1 is known")) if top else ""
        rows.append([ctx.slink(x["season"]) + '<span class="sub2">%s</span>' % esc("%s, %d in ranking" % (plural(x["lists_with_entries"], "list"), x["fed"])),
                     no1])
    b.append(section("Every season", table([("Season", "left sc"), ("No. 1 and top 3", "name left")], rows, cls="lb seasons-t", limit=0),
                     "Top 3 of each season's HoopsMatic ranking, built from that season's complete U.S. lists. Lists: published lists with entries that season (U.S. and international); in ranking: the U.S. lists that fed it.", sid="seasons"))
    b.append('<p class="pager"><a href="../teams/">All teams</a> &middot; <a href="../records/">Records</a></p>')
    site.add("seasons", url, page(ctx.root, url, title, desc, "\n".join(b), indexable=True, nav="seasons", share="seasons",
                                 share_alt="NBA jersey sales by season card: %s No. 1 in %s" % (last["no1"], last["season"])), True)
    site.cards.append({"key": "seasons", "kind": "seasons", "name": "NBA jersey sales by season", "color": team_color(m, last["top3"][0]["team_slugs"][0]),
                       "stats": [("%s No. 1" % last["season"], last["no1"]), ("Seasons", str(len(ss))),
                                 ("Different No. 1s", str(len({x["no1"] for x in ss if x["no1"]})))],
                       "line": "Every season since %s" % first["season"]})


def build_hub(site, ctx, m, team_summ, season_summ, pva, career, no_votes, rec):
    url = SITE
    releases, us = m.releases, m.us
    n_lists = sum(1 for r in releases if r["entries"])
    n_us = sum(1 for r in us if r["entries"])
    n_intl = n_lists - n_us
    first, last = releases[0], releases[-1]
    y0, y1 = first["date"][:4], last["date"][:4]
    latest_s = m.seasons[-1]
    latest_no1 = m.sr[latest_s]["rows"][0]["player"]
    title = "NBA Jersey Sales Rankings %s-%s: Best-Selling Jerseys | HoopsMatic" % (y0, y1)
    n1 = rec["no1_seasons"][0]
    desc = "NBA jersey sales rankings from every official list, %s-%s: best-selling NBA jerseys by season, %s No. 1 in %s, plus player and team history." % (
        y0, y1, latest_no1, latest_s)
    b = ['<h1>NBA jersey sales rankings, %s-%s</h1>' % (y0, y1)]
    b.append('<p class="subtitle">%d official lists from %s to %s: %d U.S. lists and %d international, plus one HoopsMatic ranking per season.</p>' % (
        n_lists, esc(first["label"]), esc(last["label"]), n_us, n_intl))
    # (1) Story cards.
    nn = rec["never_no1"][0]
    ol = rec["one_list"][0]
    stories = [
        (n1["name"], "%s No. 1 seasons" % n1["no1_seasons"], "%s finished No. 1 in %s, more than anyone since 2000-01." % (n1["name"], plural(n1["no1_seasons"], "season")),
         "player/%s/" % n1["slug"], "See the player"),
        (nn["name"], "%d lists, no No. 1" % nn["lists"], "%s made %s without reaching No. 1 (best: No. %d)." % (nn["name"], plural(nn["lists"], "U.S. list"), nn["peak"]),
         "records/#never-no1", "See the record"),
        (ol[0], "No. %d on one list" % ol[2]["rank"], "%s reached No. %d in %s and never made another U.S. list." % (
            ol[0], ol[2]["rank"], ol[1]["my"]), "records/#one-list", "See the record"),
    ]
    b.append('<div class="stories">%s</div>' % "".join(
        '<div class="story">%s<a class="st-h" href="%s">%s</a><span class="st-l">%s</span><a class="st-c" href="%s">%s</a></div>' % (
            ctx.face(n, 48, "face big"), esc(h), esc(hd), ctx.names(ln), esc(h), esc(cta)) for n, hd, ln, h, cta in stories))
    # (2) Latest list + what changed.
    latest = [r for r in us if r["status"] == "full"][-1]
    wc = what_changed(m, latest)
    inner = list_table(ctx, latest) + source_link(latest)
    if wc:
        inner += '<h3 class="h3">What changed?</h3><ul class="findings">%s</ul>' % "".join('<li>%s</li>' % ctx.names(x) for x in wc)
    b.append(section("Latest list: %s" % ctx.rel_name(latest), inner, sid="latest"))
    # (3) Who ruled: season No. 1s.
    segs, cur = [], None
    for s, sr in m.sr.items():
        if not sr["rows"]:
            continue
        p = sr["rows"][0]["player"]
        if cur and cur["player"] == p:
            cur["seasons"].append(s)
        else:
            cur = {"player": p, "seasons": [s], "team": sr["rows"][0]["team_slugs"][0]}
            segs.append(cur)
    cells = "".join('<a class="rl-cell" href="season/%s/" style="background:%s" data-tip="%s" title="%s"><span class="vh">%s</span></a>' % (
        s, esc(team_color(m, m.sr[s]["rows"][0]["team_slugs"][0])), esc("%s: %s" % (s, m.sr[s]["rows"][0]["player"])),
        esc("%s: %s" % (s, m.sr[s]["rows"][0]["player"])), esc("%s: %s" % (s, m.sr[s]["rows"][0]["player"]))) for s in m.seasons if m.sr[s]["rows"])
    lst = "".join('<li>%s<span class="pwrap">%s<span class="sub2">%s%s</span></span></li>' % (
        ctx.face(x["player"], 28), ctx.plink(x["player"]), esc(x["seasons"][0] + (" to " + x["seasons"][-1] if len(x["seasons"]) > 1 else "")),
        esc(", " + plural(len(x["seasons"]), "season"))) for x in reversed(segs))
    b.append(section("Who ruled jersey sales?", '<div class="ruled">%s</div><div class="tl-axis"><span>%s</span><span>%s</span></div><ol class="reigns">%s</ol>' % (
        cells, esc(m.seasons[0]), esc(m.seasons[-1]), lst), "No. 1 of each season's HoopsMatic ranking, newest first. Tap a season for its page.", sid="ruled"))
    # (4) Search + directory.
    # i: the face-tight headshot (silhouette fallback) or the team's logo (swatch color when none).
    idx = [{"n": p["name"], "u": "player/%s/" % p["slug"], "t": "Player", "i": m.cross.img(p["name"])} for p in m.players.values()] + \
          [{"n": t["name"], "u": "team/%s/" % t["slug"], "t": "Team", "i": ctx.team_logo(t["slug"]) or "", "c": t["color"] or "#999"} for t in team_summ]
    prow = sorted(m.players.values(), key=lambda p: p["name"].split(" ", 1)[-1] + p["name"])
    directory = details("All %d players" % len(prow), '<ul class="linkgrid small">%s</ul>' % "".join(
        '<li>%s<span class="sub2">%s</span></li>' % (ctx.plink(p["name"]), esc(plural(p["lists"], "U.S. list"))) for p in prow))
    directory += details("All %d teams" % len(team_summ), '<ul class="linkgrid small">%s</ul>' % "".join(
        '<li>%s</li>' % ctx.tlink(t["slug"]) for t in sorted(team_summ, key=lambda t: t["name"])))
    search = ('<div class="search"><label class="lbl2" for="find">Player or team</label>'
              '<input id="find" class="find" type="search" autocomplete="off" placeholder="Type a name" aria-describedby="find-hint">'
              '<p id="find-hint" class="hint">Matches update as you type.</p><ul class="find-res" aria-live="polite"></ul>'
              '<script type="application/json" class="find-data">%s</script></div>') % json.dumps(idx, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    b.append(section("Find a player or team", search + directory, sid="find-section"))
    # (5) Time machine.
    years = sorted({r["date"][:4] for r in releases})
    ms = milestones(m)
    yopts = '<option value="">All years</option>' + "".join('<option value="%s">%s</option>' % (y, y) for y in years)
    lopts = "".join('<option value="%s"%s>%s</option>' % (r["id"], " selected" if r is latest else "", esc(tm_option(r))) for r in releases if r["status"] != "empty")
    shortcuts = "".join('<span class="ms-item">%s<button type="button" class="chip ms" data-list="%s" title="%s" aria-label="%s">%s</button></span>' % (
        ctx.plink(x["who"]), x["id"], esc(x["line"]), esc(x["label"]), esc(x["what"])) for x in ms)
    tm = ('<div class="tm" id="tm" data-latest="%s">'
          '<div class="tm-filters"><label class="lbl2">Year <select class="tm-year">%s</select></label>'
          '<label class="lbl2">Lists <select class="tm-type"><option value="">All</option><option value="us">U.S.</option><option value="intl">International</option></select></label>'
          '<label class="lbl2 cb"><input type="checkbox" class="tm-empty"> Show the empty list</label></div>'
          '<div class="tm-controls"><button type="button" class="tm-prev" aria-label="Previous list">&#9664;</button>'
          '<select class="tm-select" aria-label="Pick a list">%s</select>'
          '<button type="button" class="tm-next" aria-label="Next list">&#9654;</button></div>'
          '<div class="chips ms-row" aria-label="Milestones">%s</div>'
          '<div class="tm-body" aria-live="polite">%s</div></div>') % (latest["id"], yopts, lopts, shortcuts, tm_body(ctx, latest))
    b.append(section("Time machine", tm, "Every published list in order. Incomplete lists are labeled; the empty Jan 2005 list is hidden unless you ask for it. Your pick stays in the address bar.", sid="time-machine"))
    # (6) Popularity vs. awards.
    seasons = [s for s in pva if m.sr[s]["kind"] == "full"]
    sel = "".join('<option value="%s"%s>%s</option>' % (s, " selected" if s == seasons[-1] else "", esc(s)) for s in reversed(seasons))
    panes = "".join('<div class="pva-pane" data-season="%s"%s>%s</div>' % (s, "" if s == seasons[-1] else " hidden", pva_table(ctx, pva[s])) for s in seasons)
    rows_hi = [[ctx.face(x["player"], 28) + ctx.plink(x["player"]), '<span class="pos">%+g</span>' % x["median_gap"], str(x["n"])] for x in career if x["median_gap"] > 0]
    rows_lo = [[ctx.face(x["player"], 28) + ctx.plink(x["player"]), '<span class="neg">%+g</span>' % x["median_gap"], str(x["n"])] for x in reversed(career) if x["median_gap"] < 0]
    rows_nv = [[ctx.face(x["player"], 28) + ctx.plink(x["player"]), str(x["seasons"])] for x in no_votes]
    pva_html = ('<div class="pva"><label class="lbl2">Season <select class="pva-select">%s</select></label>%s</div>' % (sel, panes) +
                details("Career view: who sells above or below the awards",
                        '<h3 class="h3">Career: jersey ranks above awards</h3>%s'
                        '<h3 class="h3">Career: awards above jersey ranks</h3>%s'
                        '<h3 class="h3">Most top 10 jersey seasons with no award votes</h3>%s' % (
                            table([("Player", "name left"), ("Median gap", "num"), ("Seasons", "num")], rows_hi),
                            table([("Player", "name left"), ("Median gap", "num"), ("Seasons", "num")], rows_lo),
                            table([("Player", "name left"), ("Seasons", "num")], rows_nv))))
    b.append(section("Popularity vs. awards", pva_html,
                     "Each season's HoopsMatic jersey ranking against a league-wide award rank: All-NBA team, then MVP voting place. Players with no MVP votes and no All-NBA team show No award votes. Gap = award rank minus jersey rank. Career uses the median gap over 3+ seasons with both ranks.", sid="pva"))
    # (7) Methodology, archive, downloads.
    gaps = []
    for r in us:
        if r["status"] != "full":
            gaps.append("%s (%s)" % (r["title"], "empty" if r["status"] == "empty" else "incomplete, No. 1 only"))
    lengths = sorted({r["n"] for r in us if r["status"] == "full"})
    cov = m.cross.award_coverage
    cov_years = sorted(int(y) for y, v in cov.items() if v.get("all_nba_players") and v.get("mvp_vote_players"))
    meth = ("<ul class=\"meth\">"
            "<li>U.S. lists are every list except Europe only and Philippines only. All U.S. counts, rankings, moves and points use U.S. lists only.</li>"
            "<li>The Oct 3, 2013 list was checked against the official NBA release: its ten players are the release's U.S. top 10 for the full 2012-13 season (the worldwide, China, Latin America, Europe and Philippines lists in the same release are not in the source). It now counts as a U.S. list of the 2012-13 season.</li>"
            "<li>Season ranking (the main measure): for each season, every complete U.S. list of that season is averaged. A player missing from a list counts one spot below its last place (missing from a 15-player list = 16). The ranking goes as deep as the season's shortest complete list. Ties: better single best rank, then better rank on the later list; still tied = shared position. Lists with only a No. 1 (2001, 2002) give that season's No. 1 only; the Jun 2006 No. 1 only list is not counted because 2005-06 has two complete lists. The empty Jan 2005 list is skipped. International lists never count.</li>"
            "<li>No. 1, Top 5 and Top 10 seasons count season rankings. No. 1, Top 5 and Top 10 lists count every U.S. list.</li>"
            "<li>Jersey ranking points: 11 minus rank for the top 10 of each season ranking (No. 1 = 10, No. 10 = 1). Every-list points apply the same scoring to each U.S. list. Team points go to the team on the player's last list of the season.</li>"
            "<li>Change compares two adjacent complete U.S. lists. A player on both gets the exact move, whatever the two lengths (No. 12 on a 15-player list to No. 8 on a 10-player list = up 4). A player absent from the next list dropped out only if the previous rank fits inside that list; No. 12, then absent from a 10-player list, is not compared. First appearance is a first U.S. list; Re-entry is a return after missing the previous complete list. International lists get no change.</li>"
            "<li>Complete U.S. list lengths in the source: %s spots. Known gaps, shown as they are: %s. A team's stretch without a ranked player counts complete lists only.</li>"
            "<li>Players listed with more than one team (traded during the sales period) count for each team listed. Rows with a small note carry the source flag.</li>"
            "<li>Popularity vs. awards: each season, every player with an All-NBA team or MVP votes gets a league-wide award rank: All-NBA team, then MVP voting place (1st team ahead of 2nd, 2nd ahead of 3rd, any team ahead of none). Players with neither show No award votes. Data from nba-player-data (awards.json and awardVotes.json); it covers %s to %s, every season with a jersey list. No All-Star data.</li>"
            "<li>Salary, birthplace and road attendance come from other HoopsMatic datasets; a player or season that did not match cleanly is left blank.</li>"
            "</ul>") % (esc(join_names(str(x) for x in lengths)), esc("; ".join(gaps)),
                        ("%d-%02d" % (cov_years[0] - 1, cov_years[0] % 100)) if cov_years else "none",
                        ("%d-%02d" % (cov_years[-1] - 1, cov_years[-1] % 100)) if cov_years else "none")
    b.append(section("Methodology", meth, sid="methodology"))
    # Short previews; the full lists live on /seasons/ and /teams/. The #seasons and #teams anchors stay here.
    latest5 = list(reversed(season_summ))[:5]
    srows = [[ctx.slink(x["season"]), (ctx.face(x["no1"], 28) + ctx.plink(x["no1"])) if x["no1"] else "", str(x["lists_with_entries"])] for x in latest5]
    b.append(section("Seasons", table([("Season", "left nw"), ("No. 1", "name left"), ("Lists", "num")], srows, limit=0) +
                     '<p class="more-link"><a href="seasons/">See all %d seasons</a></p>' % len(season_summ),
                     "The five latest seasons. Every season since 2000-01 is on the seasons page.", sid="seasons"))
    ts = sorted(team_summ, key=lambda t: (-t["points"], -t["list_points"], t["name"]))[:5]
    trows = [[ctx.tlink(t["slug"]), str(t["points"]), (ctx.face(t["best"], 28) + ctx.plink(t["best"])) if t["best"] else ""] for t in ts]
    b.append(section("Teams", table([("Team", "team left"), ("Pts", "num"), ("Top player", "name left")], trows, limit=0) +
                     '<p class="more-link"><a href="teams/">See all %d teams</a></p>' % len(team_summ),
                     "Top 5 by HoopsMatic jersey ranking points (season rankings, No. 1 = 10 down to No. 10 = 1).", sid="teams"))
    intl = [r for r in releases if not r["us"]]
    cards = "".join('<div class="lcard"><div class="lhead"><h3>%s</h3><span class="sub2">%s</span>%s</div>%s</div>' % (
        esc(r["type"]), esc(r["label"] + ", " + r["season"]), source_link(r), list_table(ctx, r, compact=True)) for r in intl)
    b.append(section("International lists", '<div class="lcards">%s</div>' % cards,
                     "Kept apart from every U.S. count, ranking, move and point total.", sid="international"))
    b.append(section("Records and downloads", '<p><a href="records/">Records: most No. 1 seasons, reigns, climbs, one-list players and same-team duos</a></p>'
                     '<ul class="dl">'
                     '<li><a href="data/jersey-points-race.csv" download>Season ranking points, cumulative (CSV)</a> for the <a href="%s">bar chart race tool</a></li>'
                     '<li><a href="data/jersey-points-race-all-lists.csv" download>Every-list points, cumulative (CSV)</a></li>'
                     '<li><a href="data/season-rankings.json">Season rankings (JSON)</a> &middot; <a href="data/releases.json">Every list (JSON)</a> &middot; <a href="data/popularity-vs-awards.json">Popularity vs. awards (JSON)</a></li>'
                     '</ul>' % RACE_TOOL, sid="records"))
    site.add("", url, page("", url, title, desc, "\n".join(b), indexable=True, nav="hub", share="hub",
                           share_alt="NBA jersey sales rankings card: %s No. 1 in %s, %d U.S. lists since %s" % (latest_no1, latest_s, n_us, y0)), True)
    site.cards.append({"key": "hub", "kind": "hub", "name": "NBA Jersey Sales Rankings", "color": team_color(m, m.sr[latest_s]["rows"][0]["team_slugs"][0]),
                       "stats": [("%s No. 1" % latest_s, latest_no1), ("Most No. 1 seasons", "%s (%d)" % (n1["name"], n1["no1_seasons"])), ("U.S. lists", str(n_us))],
                       "line": "Every official list, %s-%s" % (y0, y1)})


def tm_option(r):
    t = r["title"] + ("" if r["us"] else " (international)")
    if r["status"] == "partial":
        t += " (incomplete)"
    if r["status"] == "empty":
        t += " (empty)"
    return t


def milestones(m):
    """Time machine shortcuts generated from data: first lists and first No. 1s of the season No. 1s."""
    names = []
    for s, sr in m.sr.items():
        if sr["rows"] and sr["rows"][0]["player"] not in names:
            names.append(sr["rows"][0]["player"])
    out = []
    for n in names:
        p = m.players[n]
        r0 = p["first"]
        out.append({"id": r0["id"], "date": r0["date"], "label": "%s first list" % poss(n), "who": n, "what": "first list",
                    "line": "%s first U.S. list: No. %d, %s." % (poss(n), p["first_rank"], r0["title"])})
        if p["first_no1"] and p["first_no1"] is not r0:
            r1 = p["first_no1"]
            out.append({"id": r1["id"], "date": r1["date"], "label": "%s first No. 1" % poss(n), "who": n, "what": "first No. 1",
                        "line": "%s first No. 1: %s." % (poss(n), r1["title"])})
    return sorted(out, key=lambda x: x["date"])


def tm_body(ctx, r):
    """Prerendered time machine body for one release (same markup the JS builds)."""
    tags = ('<span class="tag">International</span>' if not r["us"] else "") + ('<span class="tag warn">Incomplete</span>' if r["status"] == "partial" else "")
    head = '<div class="tm-head"><h3>%s</h3><span class="sub2">%s</span>%s%s%s</div>' % (
        esc(r["title"]), esc(r["season"] + (" season final" if r["final"] else "")), tags,
        '<a class="sub2" href="season/%s/">Season page</a>' % r["season"], source_link(r))
    note = release_note(r)
    body = ('<p class="hint">%s</p>' % esc(note)) if note else ""
    if r["entries"]:
        body += list_table(ctx, r)
    if r.get("dropped"):
        body += '<p class="hint">Dropped out since the previous complete U.S. list: %s.</p>' % ", ".join(
            "%s (No. %d)" % (ctx.plink(e["player"]), e["rank"]) for e in r["dropped"])
    if r.get("not_compared"):
        body += '<p class="hint">Not compared (ranked deeper than this list\'s %d spots before): %s.</p>' % (r["n"], ", ".join(
            "%s (No. %d)" % (ctx.plink(e["player"]), e["rank"]) for e in r["not_compared"]))
    return head + body


# --------------------------------------------------------------------------
# JSON + CSV exports
# --------------------------------------------------------------------------

def write_json(rel, obj):
    p = os.path.join(DOCS, "data", rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def export_data(m, rec, pva, career):
    releases, players, cross = m.releases, m.players, m.cross
    hub_ctx = Ctx("", m)
    ms = milestones(m)
    write_json("releases.json", {
        "teams": {s: {"name": t["full_name"], "color": t.get("color")} for s, t in m.teams_by_slug.items()},
        "milestones": [{"id": x["id"], "label": x["label"], "line": x["line"]} for x in ms],
        "releases": [{
            "id": r["id"], "title": r["title"], "label": r["label"], "my": r["my"], "year": r["date"][:4], "season": r["season"], "type": r["type"],
            "us": r["us"], "final": r["final"], "status": r["status"], "source": r["source"], "note": release_note(r),
            "compare": ({"id": r["compare_id"], "my": m.byid[r["compare_id"]]["my"], "label": m.byid[r["compare_id"]]["label"],
                         "type": m.byid[r["compare_id"]]["type"], "len": r["compare_len"]} if r.get("compare_id") else None),
            "entries": [{"r": e["rank"], "p": e["player"], "s": e["slug"], "tn": e["team_names"], "ts": e["team_slugs"], "lg": e["logos"],
                         "fh": flag_html(e, hub_ctx)[len('<span class="note">'):-len('</span>')] if flag_html(e, hub_ctx) else "",
                         "f": flag_html(e, hub_ctx) and (e["flag"] + ("; " if e["flag"] and e["multi"] else "") + ("Listed with more than one team; counts for each team listed" if e["multi"] else "")),
                         "m": e["move"], "img": cross.img(e["player"])} for e in r["entries"]],
            "dropped": [{"p": e["player"], "s": e["slug"], "r": e["rank"]} for e in r["dropped"]],
            "not_compared": [{"p": e["player"], "s": e["slug"], "r": e["rank"]} for e in r.get("not_compared", [])],
        } for r in releases]})
    write_json("players.json", [{
        "name": p["name"], "slug": p["slug"], "us_lists": p["lists"], "no1_seasons": p["no1_seasons"], "top5_seasons": p["top5_seasons"],
        "top10_seasons": p["top10_seasons"], "best_season": p["best_season"], "no1_lists": p["no1_lists"], "top5_lists": p["top5_lists"],
        "top10_lists": p["top10_lists"], "points": p["season_points"], "list_points": p["list_points"],
        "peak": p["peak"], "first": p["first"]["id"] if p["first"] else None, "last": p["last"]["id"] if p["last"] else None,
        "longest_run": p["run"][0], "indexable": p["indexable"],
        "s": {s: row["pos"] for s, row in p["season"].items()},
        "l": {r["id"]: e["rank"] for r, e in p["us"]},
        "ranks": [{"release": r["id"], "rank": e["rank"], "team": e["team"], "teams": e["team_slugs"]} for r, e in p["us"]],
        "international": [{"release": r["id"], "rank": e["rank"], "team": e["team"]} for r, e in p["intl"]],
    } for p in players.values()])
    write_json("season-rankings.json", {s: {
        "kind": sr["kind"], "depth": sr["depth"],
        "lists": [r["id"] for r in sr["fed"]], "skipped": [{"list": r["id"], "why": w} for r, w in sr["skipped"]],
        "rows": [{"pos": row["pos"], "shared": row["shared"], "player": row["player"], "team": row["team"], "teams": row["team_slugs"],
                  "ranks": row["ranks"], "avg": round(row["avg"], 4), "pts": row["pts"]} for row in sr["rows"]],
    } for s, sr in m.sr.items()})
    write_json("records.json", {
        "most_no1_seasons": [{"player": p["name"], "no1_seasons": p["no1_seasons"], "no1_lists": p["no1_lists"]} for p in rec["no1_seasons"]],
        "most_top10_seasons": [{"player": p["name"], "top10_seasons": p["top10_seasons"], "top5_seasons": p["top5_seasons"]} for p in rec["top10_seasons"]],
        "longest_no1_reigns": [{"player": x["player"], "lists": x["n"], "from": x["start"]["id"], "to": x["end"]["id"]} for x in rec["reigns"]],
        "most_lists_without_no1": [{"player": p["name"], "lists": p["lists"], "peak": p["peak"]} for p in rec["never_no1"]],
        "biggest_climbs": [{"player": x["player"], "from": x["from"], "to": x["to"], "prev": x["prev"]["id"], "list": x["cur"]["id"]} for x in rec["jumps"]],
        "biggest_falls": [{"player": x["player"], "from": x["from"], "to": x["to"], "prev": x["prev"]["id"], "list": x["cur"]["id"]} for x in rec["falls"]],
        "jersey_ranking_points": [{"player": p["name"], "season_points": p["season_points"], "list_points": p["list_points"]} for p in rec["points"]],
        "early_career_appearances": [{"player": x["player"], "season_no": x["season_no"], "list": x["release"]["id"], "rank": x["rank"],
                                      "first_nba_season": x["nba_first"]} for x in rec["early"]],
        "one_list_players": [{"player": n, "list": r["id"], "rank": e["rank"]} for n, r, e in rec["one_list"]],
        "same_team_duos": [{"players": [d["a"], d["b"]], "team": d["team"], "lists": [r["id"] for r in d["lists"]]} for d in rec["duos"]],
    })
    write_json("popularity-vs-awards.json", {
        "seasons": {s: {"lists": [r["id"] for r in m.sr[s]["fed"]], "rows": v["rows"],
                        "award_top10_not_in_jersey_ranking": [{"player": x["player"], "rank": x["rank"]} for x in v["award_top10_missing"]]} for s, v in pva.items()},
        "career": career})
    # Bar chart race: wide format (first column = date, one column per player, cumulative points).
    names = sorted([p for p in players.values() if p["season_points"]], key=lambda p: (-p["season_points"], p["name"]))
    cum = {p["name"]: 0 for p in names}
    with open(os.path.join(DOCS, "data", "jersey-points-race.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date"] + [p["name"] for p in names])
        for s, sr in m.sr.items():
            if not sr["rows"]:
                continue
            for row in sr["rows"]:
                if row["player"] in cum:
                    cum[row["player"]] += row["pts"]
            w.writerow([sr["fed"][-1]["date"]] + [cum[p["name"]] for p in names])
    us_rel = [r for r in releases if r["us"] and r["entries"]]
    names = sorted([p for p in players.values() if p["list_points"]], key=lambda p: (-p["list_points"], p["name"]))
    cum = {p["name"]: 0 for p in names}
    with open(os.path.join(DOCS, "data", "jersey-points-race-all-lists.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date"] + [p["name"] for p in names])
        for r in us_rel:
            for e in r["entries"]:
                if e["player"] in cum:
                    cum[e["player"]] += e["pts"]
            w.writerow([r["date"]] + [cum[p["name"]] for p in names])


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", default=datetime.date.today().isoformat())
    args = ap.parse_args()
    report = {}
    releases = load_releases(report)
    cross = Cross()
    m = build_model(releases, cross.teams, cross, report)

    # Clean generated output (keep docs/assets, which is hand-written, and docs/share, which is cached by hash).
    for d in ("player", "team", "season", "records", "teams", "seasons", "data"):
        shutil.rmtree(os.path.join(DOCS, d), ignore_errors=True)
    site = Site(args.today)
    pva, career, no_votes = popularity_vs_awards(m)
    rec = compute_records(m)
    tp_season, tp_lists = team_points(m)

    for p in m.players.values():
        build_player_page(site, p, Ctx("../../", m), m, pva)
    team_entries = defaultdict(list)
    for r in releases:
        for e in r["entries"]:
            for s in e["team_slugs"]:
                team_entries[s].append((r, e))
    team_summ = [build_team_page(site, t, Ctx("../../", m), m, team_entries, tp_season, tp_lists) for t in cross.teams]
    first_season_of = {}
    for r in m.us:
        for e in r["entries"]:
            first_season_of.setdefault(e["player"], r["season"])
    season_summ = [build_season_page(site, s, Ctx("../../", m), m, pva, first_season_of) for s in m.seasons]
    build_records(site, Ctx("../", m), rec, m)
    build_teams_index(site, Ctx("../", m), m, team_summ)
    build_seasons_index(site, Ctx("../", m), m, season_summ)
    build_hub(site, Ctx("", m), m, team_summ, season_summ, pva, career, no_votes, rec)
    export_data(m, rec, pva, career)
    site.write()
    open(os.path.join(DOCS, ".nojekyll"), "w").close()
    import share_images
    share = share_images.render_all(site.cards, os.path.join(DOCS, "share"), os.path.join(DATA, "share_hashes.json"),
                                    os.path.join(DATA, "share", "headshots"))

    report["counts"] = {
        "releases_total": len(releases), "releases_with_entries": sum(1 for r in releases if r["entries"]),
        "us_lists_with_entries": sum(1 for r in m.us if r["entries"]), "players": len(m.players),
        "player_pages_indexable": sum(1 for p in m.players.values() if p["indexable"]),
        "player_pages_noindex": sum(1 for p in m.players.values() if not p["indexable"]),
        "team_pages": len(team_summ), "team_pages_noindex": [t["slug"] for t in team_summ if not t["indexable"]],
        "season_pages": len(season_summ), "season_pages_noindex": [s["season"] for s in season_summ if not s["indexable"]],
        "sitemap_urls": sum(1 for x in site.pages if x[2]),
        "share_images": share["total"], "share_images_rendered": share["rendered"],
        "release_sources_found": sum(1 for r in releases if r["source"]),
        "release_sources_blank": [r["id"] for r in releases if not r["source"] and r["entries"]],
    }
    report["season_rankings"] = {s: {"kind": sr["kind"], "depth": sr["depth"], "lists": [r["id"] for r in sr["fed"]],
                                      "top5": [x["player"] for x in sr["rows"][:5]]} for s, sr in m.sr.items()}
    report["award_coverage"] = cross.award_coverage
    report["crossover_misses"] = cross.misses
    report["salary_mismatches_left_out"] = sorted(["%s %s" % k for k in cross.sal_bad])
    with open(os.path.join(DATA, "build_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(json.dumps(report["counts"], indent=1))
    print("grid mismatches:", len(report["grid_vs_player_ranks"]["mismatches"]))


if __name__ == "__main__":
    main()
