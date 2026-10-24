#!/usr/bin/env python3
"""Build the HoopsMatic NBA jersey sales section (served at hoopsmatic.com/jerseys/).

Inputs (all committed, nothing fetched):
  data/source/*.csv            the two source CSVs, unchanged
  data/release_sources.csv     Release ID -> official announcement URL (blank = none found)
  data/crossover/*.json        snapshot from scripts/snapshot_crossover.py
  data/page_hashes.json        content hash + lastmod per URL (updated here)

Outputs:
  docs/                        prerendered pages, JSON, CSV, sitemap
  data/build_report.json       grid cross-check, counts, crossover misses

Every number and name on the pages comes from those inputs. All page text is a
template filled from data.

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
import unicodedata
from collections import defaultdict, OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DOCS = os.path.join(ROOT, "docs")
RANKS_CSV = os.path.join(DATA, "source", "nba-jersey-sales-rankings-2001-2026 - Player ranks.csv")
GRID_CSV = os.path.join(DATA, "source", "nba-jersey-sales-rankings-2001-2026 - Grid U.S. lists.csv")
SITE = "https://hoopsmatic.com/jerseys/"
SILHOUETTE = "https://jsierrahoopshype.github.io/nba-headshots/fallbacks/player_silhouette.svg"
RACE_TOOL = "https://jsierrahoopshype.github.io/bar-chart-race/"
ATTENDANCE_TOOL = "https://hoopsmatic.com/nba-attendance/"
INTL_TYPES = {"Europe only", "Philippines only", "Worldwide release (U.S. top 10)"}
INDEX_MIN_LISTS = 3
SHOW_N = 25
# Jersey names published under a team name that is not in teams.json.
TEAM_NAME_EXTRA = {"Los Angeles Clippers": "la-clippers"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
FOOTER = ('<footer class="site-foot">'
          '<p>Anything wrong? Email us at <a href="mailto:hoopsmatic@hoopsmatic.com">hoopsmatic@hoopsmatic.com</a></p>'
          '<p>For business inquiries, contact us at <a href="mailto:ads@hoopsmatic.com">ads@hoopsmatic.com</a></p>'
          '</footer>')


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
    us_types_in_grid = sorted({rel[k]["type"] for k in grid_cols if rel[k]["type"] in INTL_TYPES})
    report["grid_vs_player_ranks"] = {
        "grid_columns": len(hdr) - 1, "matched_columns": len(grid_cols), "mismatches": mismatches,
        "grid_columns_without_player_ranks_release": [x["date"] + " " + x["type"] for x in extra],
        "grid_includes_international_types": us_types_in_grid,
        "us_releases_missing_from_grid": sorted(k for k, v in rel.items() if v["type"] not in INTL_TYPES and k not in grid_cols and not v.get("grid_only")),
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
        r["short"] = short_label(r["precision_date"])
        r["source"] = srcs.get(r["id"])
        r["title"] = "%s, %s" % (r["type"], r["label"])
        for e in r["entries"]:
            e["pts"] = (11 - e["rank"]) if (r["us"] and e["rank"] <= 10) else 0
    return out


def build_model(releases, teams, report):
    team_by_name = {}
    for t in teams:
        team_by_name[t["full_name"]] = t["slug"]
        for h in t["historical_names"]:
            team_by_name[h] = t["slug"]
    team_by_name.update(TEAM_NAME_EXTRA)
    unmapped = set()
    for r in releases:
        for e in r["entries"]:
            parts = [p.strip() for p in e["team"].split("/")] if e["team"] else []
            # "New Orleans/Oklahoma City Hornets" is one team name, not two teams.
            if e["team"] in team_by_name:
                parts = [e["team"]]
            slugs = []
            for p in parts:
                s = team_by_name.get(p)
                if s is None:
                    unmapped.add(p)
                elif s not in slugs:
                    slugs.append(s)
            e["team_slugs"] = slugs
            e["multi"] = len(slugs) > 1
            e["slug"] = slugify(e["player"])
    if unmapped:
        raise SystemExit("Unmapped team names: %s" % sorted(unmapped))

    us = [r for r in releases if r["us"]]
    nonempty_us = [r for r in us if r["status"] != "empty"]

    # Movement vs the previous U.S. list that has the player, stopping at the first full list.
    seen = set()
    prev_full = None
    for i, r in enumerate(nonempty_us):
        prior = nonempty_us[:i][::-1]
        for e in r["entries"]:
            mv = None
            for q in prior:
                hit = next((x for x in q["entries"] if x["player"] == e["player"]), None)
                if hit:
                    d = hit["rank"] - e["rank"]
                    mv = {"kind": "up" if d > 0 else ("down" if d < 0 else "same"), "delta": abs(d), "vs": q["id"]}
                    break
                if q["status"] == "full":
                    break
            if mv is None:
                mv = {"kind": "back" if e["player"] in seen else "new", "delta": 0, "vs": None}
            e["move"] = mv
        r["compare_id"] = prev_full["id"] if prev_full else None
        if r["status"] == "full" and prev_full:
            cur = {e["player"] for e in r["entries"]}
            r["dropped"] = [x for x in prev_full["entries"] if x["player"] not in cur]
        else:
            r["dropped"] = []
        for e in r["entries"]:
            seen.add(e["player"])
        if r["status"] == "full":
            prev_full = r
    for r in releases:
        if not r["us"]:
            for e in r["entries"]:
                e["move"] = None
        r.setdefault("dropped", [])
        r.setdefault("compare_id", None)

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
        p["points"] = sum(e["pts"] for _, e in u)
        p["no1"] = sum(1 for _, e in u if e["rank"] == 1)
        p["peak"] = min((e["rank"] for _, e in u), default=None)
        p["peak_times"] = sum(1 for _, e in u if e["rank"] == p["peak"])
        p["first"] = u[0][0] if u else None
        p["last"] = u[-1][0] if u else None
        p["indexable"] = p["lists"] >= INDEX_MIN_LISTS
        # Longest unbroken run: an empty or No. 1-only list neither breaks nor extends a run.
        best, cur, start = (0, None, None), 0, None
        on = {r["id"] for r, _ in u}
        for r in nonempty_us:
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
    return players, us, nonempty_us


# --------------------------------------------------------------------------
# Crossover helpers
# --------------------------------------------------------------------------

class Cross:
    def __init__(self):
        C = os.path.join(DATA, "crossover")
        self.headshots = load_json(os.path.join(C, "headshots.json"), {})
        self.teams = load_json(os.path.join(C, "teams.json"), [])
        prod = load_json(os.path.join(C, "production.json"), {})
        self.prod = prod.get("players", {})
        self.prod_min_gp = prod.get("min_gp", 20)
        self.top10 = prod.get("season_top10", {})
        aw = load_json(os.path.join(C, "awards.json"), {})
        self.awards = aw.get("awards", {})
        self.mvp = aw.get("mvp_vote_rank", {})
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

    def img(self, name):
        h = self.headshots.get(name)
        return h["image"] if h else SILHOUETTE

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


# --------------------------------------------------------------------------
# HTML helpers
# --------------------------------------------------------------------------

def page(root, url, title, desc, body, indexable=True, nav="hub"):
    robots = "" if indexable else '<meta name="robots" content="noindex, follow">\n'
    tabs = [("hub", root, "All lists"), ("records", root + "records/", "Records"),
            ("seasons", root + "#seasons", "Seasons"), ("teams", root + "#teams", "Teams")]
    navhtml = "".join('<a href="%s"%s>%s</a>' % (esc(h), ' class="active"' if k == nav else "", esc(t)) for k, h, t in tabs)
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<title>%s</title>\n<meta name=\"description\" content=\"%s\">\n%s"
            "<link rel=\"canonical\" href=\"%s\">\n"
            "<meta property=\"og:type\" content=\"website\">\n<meta property=\"og:site_name\" content=\"HoopsMatic\">\n"
            "<meta property=\"og:title\" content=\"%s\">\n<meta property=\"og:description\" content=\"%s\">\n"
            "<meta property=\"og:url\" content=\"%s\">\n"
            "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">\n"
            "<link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin>\n"
            "<link href=\"https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap\" rel=\"stylesheet\">\n"
            "<link rel=\"stylesheet\" href=\"%sassets/jerseys.css\">\n</head>\n<body data-root=\"%s\">\n"
            "<div class=\"container narrow\">\n<nav class=\"tabs\">%s</nav>\n%s\n%s\n</div>\n"
            "<script src=\"%sassets/jerseys.js\" defer></script>\n</body>\n</html>\n") % (
        esc(title), esc(desc), robots, esc(url), esc(title), esc(desc), esc(url), root, root, navhtml, body, FOOTER, root)


class Ctx:
    """Link builders relative to the page being written."""

    def __init__(self, root, players, teams_by_slug, cross):
        self.root, self.players, self.teams, self.cross = root, players, teams_by_slug, cross

    def plink(self, name):
        p = self.players.get(name)
        if not p:
            return esc(name)
        return '<a href="%splayer/%s/">%s</a>' % (self.root, p["slug"], esc(name))

    def face(self, name, size=32):
        return '<img class="face" src="%s" alt="%s" width="%d" height="%d" loading="lazy">' % (
            esc(self.cross.img(name)), esc(name), size, size)

    def swatch(self, slug):
        t = self.teams.get(slug)
        return '<span class="sw" style="background:%s"></span>' % esc(t["color"] if t and t.get("color") else "#999")

    def team_cell(self, e):
        """Team as published, each franchise linked, with a swatch per team."""
        names = [x.strip() for x in e["team"].split("/")] if len(e["team_slugs"]) > 1 else [e["team"]]
        bits = []
        for nm, sl in zip(names, e["team_slugs"]):
            bits.append('%s<a href="%steam/%s/">%s</a>' % (self.swatch(sl), self.root, sl, esc(nm)))
        out = " / ".join(bits)
        return out

    def tlink(self, slug):
        t = self.teams[slug]
        return '%s<a href="%steam/%s/">%s</a>' % (self.swatch(slug), self.root, slug, esc(t["full_name"]))

    def slink(self, season):
        return '<a href="%sseason/%s/">%s</a>' % (self.root, season, esc(season))


def move_html(mv):
    if not mv:
        return '<span class="mv dash">&middot;</span>'
    k = mv["kind"]
    if k == "up":
        return '<span class="mv up" title="Up %d">&#9650;%d</span>' % (mv["delta"], mv["delta"])
    if k == "down":
        return '<span class="mv down" title="Down %d">&#9660;%d</span>' % (mv["delta"], mv["delta"])
    if k == "same":
        return '<span class="mv same" title="No change">=</span>'
    if k == "new":
        return '<span class="mv new">NEW</span>'
    return '<span class="mv back">BACK</span>'


def flag_html(e):
    notes = []
    if e["flag"]:
        notes.append(esc(e["flag"]))
    if e["multi"]:
        notes.append("Listed with more than one team; counts for each team listed")
    if not notes:
        return ""
    return '<span class="note">%s</span>' % "; ".join(notes)


def show_all(rows_html, n_total, colspan=None, label="rows"):
    """Rows past SHOW_N get class 'more' and a show-all button (JS)."""
    if n_total <= SHOW_N:
        return rows_html, ""
    btn = '<button class="showall" type="button" data-label="%s">Show all %d</button>' % (label, n_total)
    return rows_html, btn


def table(head, rows, cls="lb"):
    """head: list of (label, css). rows: list of lists of cell html. Rows past SHOW_N fold."""
    th = "".join('<th class="%s">%s</th>' % (c, esc(h)) for h, c in head)
    trs = []
    for i, r in enumerate(rows):
        tds = "".join('<td class="%s">%s</td>' % (head[j][1], v) for j, v in enumerate(r))
        trs.append('<tr%s>%s</tr>' % (' class="more"' if i >= SHOW_N else "", tds))
    btn = ""
    if len(rows) > SHOW_N:
        btn = '<button class="showall" type="button">Show all %d</button>' % len(rows)
    return '<div class="table-wrap"><table class="%s"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>%s' % (
        cls, th, "".join(trs), btn)


def release_note(r):
    if r["status"] == "empty":
        return "This list is empty in the source data."
    if r["status"] == "partial":
        return "Only the No. 1 is in the source data; the full list was not found."
    return ""


def source_link(r):
    if r.get("source"):
        return '<a class="src" href="%s" rel="noopener" target="_blank">Source</a>' % esc(r["source"])
    return ""


def list_table(ctx, r, with_moves=True, compact=False):
    head = [("No.", "rank"), ("Player", "name left")]
    if with_moves:
        head.append(("Move", "mvc"))
    if not compact:
        head.append(("Team", "team left"))
    rows = []
    for e in r["entries"]:
        if compact:
            cells = [str(e["rank"]), ctx.plink(e["player"]) + '<span class="tline">%s</span>' % ctx.team_cell(e) + flag_html(e)]
        else:
            cells = [str(e["rank"]), ctx.face(e["player"], 28) + ctx.plink(e["player"]) + flag_html(e)]
        if with_moves:
            cells.append(move_html(e.get("move")))
        if not compact:
            cells.append(ctx.team_cell(e))
        rows.append(cells)
    return table(head, rows)


# --------------------------------------------------------------------------
# SVG charts (plain SVG, swipe sideways inside .chart-scroll)
# --------------------------------------------------------------------------

def rank_chart(ctx, p, nonempty_us, all_us):
    on = {r["id"]: e for r, e in p["us"]}
    if not on:
        return ""
    ids = [r["id"] for r in all_us]
    i0 = max(0, ids.index(p["first"]["id"]) - 1)
    i1 = min(len(ids) - 1, ids.index(p["last"]["id"]) + 1)
    window = all_us[i0:i1 + 1]
    maxr = max(25 if any(r["n"] >= 20 for r in window) else 15, max(e["rank"] for e in on.values()))
    colw, top, h, left = 30, 16, 230, 34
    width = left + colw * len(window) + 10
    height = top + h + 64
    y = lambda rk: top + (rk - 1) * h / (maxr - 1)
    x = lambda i: left + colw * i + colw / 2
    parts = ['<svg class="rchart" width="%d" height="%d" viewBox="0 0 %d %d" role="img" aria-label="%s">' % (
        width, height, width, height, esc("%s rank on each U.S. jersey sales list" % p["name"]))]
    for rk in [1, 5, 10, 15, 20, 25]:
        if rk <= maxr:
            parts.append('<line class="grid" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (left, width - 10, y(rk), y(rk)))
            parts.append('<text class="ytk" x="%d" y="%.1f">%d</text>' % (left - 6, y(rk) + 4, rk))
    for i, r in enumerate(window):
        cx = x(i)
        if r["status"] != "full":
            parts.append('<rect class="gap" x="%.1f" y="%d" width="%d" height="%d"><title>%s</title></rect>' % (
                cx - colw / 2 + 2, top, colw - 4, h, esc(r["title"] + ": " + release_note(r))))
        elif r["n"] < maxr:
            parts.append('<rect class="short" x="%.1f" y="%.1f" width="%d" height="%.1f"><title>%s</title></rect>' % (
                cx - colw / 2 + 2, y(r["n"]) + 6, colw - 4, max(0, top + h - y(r["n"]) - 6),
                esc("%s had %d spots" % (r["title"], r["n"]))))
        parts.append('<text class="xtk" transform="translate(%.1f,%d) rotate(-60)">%s</text>' % (cx + 4, top + h + 12, esc(r["short"])))
    # Segments connect only when no full list in between was missed.
    seq = [r for r in window if r["status"] != "empty"]
    prev = None
    for r in seq:
        e = on.get(r["id"])
        if e:
            if prev is not None:
                pi = window.index(prev[0])
                parts.append('<line class="seg" x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f"/>' % (
                    x(pi), y(prev[1]["rank"]), x(window.index(r)), y(e["rank"])))
            prev = (r, e)
        elif r["status"] == "full":
            prev = None
    for i, r in enumerate(window):
        e = on.get(r["id"])
        if e:
            parts.append('<circle class="pt%s" cx="%.1f" cy="%.1f" r="5"><title>%s</title></circle>' % (
                " one" if e["rank"] == 1 else "", x(i), y(e["rank"]), esc("%s: No. %d" % (r["title"], e["rank"]))))
    parts.append("</svg>")
    return '<div class="chart-scroll">%s</div>' % "".join(parts)


def bar_chart(values, label_fn, aria, unit=""):
    """values: list of (label, value or None). None = no data (drawn as a gap)."""
    nums = [v for _, v in values if v is not None]
    vmax = max([1] + [abs(v) for v in nums])
    vmin = min([0] + nums)
    colw, top, h, left = 30, 14, 150, 40
    width = left + colw * len(values) + 10
    height = top + h + 58
    neg = vmin < 0
    zero = top + (h * (vmax / (vmax - vmin)) if neg else h)
    scale = (h / (vmax - vmin)) if neg else h / vmax
    parts = ['<svg class="bchart" width="%d" height="%d" viewBox="0 0 %d %d" role="img" aria-label="%s">' % (
        width, height, width, height, esc(aria))]
    parts.append('<line class="grid" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (left, width - 10, zero, zero))
    parts.append('<text class="ytk" x="%d" y="%.1f">0</text>' % (left - 6, zero + 4))
    parts.append('<text class="ytk" x="%d" y="%.1f">%s</text>' % (left - 6, top + 8, esc(label_fn(vmax))))
    for i, (lab, v) in enumerate(values):
        cx = left + colw * i + 3
        if v is None:
            parts.append('<rect class="gap" x="%d" y="%d" width="%d" height="%d"><title>%s</title></rect>' % (
                cx, top, colw - 6, h, esc(lab + ": no data")))
        else:
            bh = abs(v) * scale
            yy = zero - bh if v >= 0 else zero
            parts.append('<rect class="bar%s" x="%d" y="%.1f" width="%d" height="%.1f"><title>%s</title></rect>' % (
                " neg" if v < 0 else "", cx, yy, colw - 6, max(bh, 0.5), esc("%s: %s%s" % (lab, label_fn(v), unit))))
        parts.append('<text class="xtk" transform="translate(%d,%d) rotate(-60)">%s</text>' % (cx + 14, top + h + 12, esc(lab)))
    parts.append("</svg>")
    return '<div class="chart-scroll">%s</div>' % "".join(parts)


# --------------------------------------------------------------------------
# Analytics shared by pages
# --------------------------------------------------------------------------

def popularity_vs_production(releases, cross):
    """Per season final U.S. list: jersey rank vs league production rank."""
    seasons = OrderedDict()
    for r in releases:
        if not (r["us"] and r["final"] and r["entries"]):
            continue
        end = season_end(r["season"])
        rows, unranked = [], []
        for e in r["entries"]:
            pr = cross.prod.get(e["player"], {}).get(end)
            if pr and pr.get("prod_rank"):
                rows.append({"player": e["player"], "jersey": e["rank"], "prod": pr["prod_rank"],
                             "gap": pr["prod_rank"] - e["rank"], "pra": pr["pra"], "gp": pr["gp"],
                             "ppg": pr["ppg"], "rpg": pr["rpg"], "apg": pr["apg"]})
            else:
                unranked.append({"player": e["player"], "jersey": e["rank"], "gp": (pr or {}).get("gp")})
        rows.sort(key=lambda x: (-x["gap"], x["jersey"]))
        listed = {e["player"] for e in r["entries"]}
        missing = []
        if r["status"] == "full":
            missing = [n for n in cross.top10.get(end, []) if n not in listed]
        seasons[r["season"]] = {"release": r, "rows": rows, "unranked": unranked, "top10_missing": missing}
    career = defaultdict(list)
    for s in seasons.values():
        for x in s["rows"]:
            career[x["player"]].append(x["gap"])
    career_rows = [{"player": n, "median_gap": statistics.median(g), "n": len(g)} for n, g in career.items() if len(g) >= 3]
    career_rows.sort(key=lambda x: (-x["median_gap"], x["player"]))
    return seasons, career_rows


def compute_records(players, releases, us, nonempty_us, cross):
    rec = {}
    rec["one_list"] = sorted(
        [(p["name"], p["us"][0][0], p["us"][0][1]) for p in players.values() if p["lists"] == 1],
        key=lambda x: (x[1]["date"], x[2]["rank"]))
    # No. 1 reigns over consecutive non-empty U.S. lists.
    reigns, cur = [], None
    for r in nonempty_us:
        top = r["entries"][0]["player"]
        if cur and cur["player"] == top:
            cur["end"], cur["n"] = r, cur["n"] + 1
        else:
            cur = {"player": top, "start": r, "end": r, "n": 1}
            reigns.append(cur)
    rec["reigns"] = sorted(reigns, key=lambda x: (-x["n"], x["start"]["date"]))
    rec["no1_totals"] = sorted([p for p in players.values() if p["no1"]], key=lambda p: (-p["no1"], p["name"]))
    rec["never_no1"] = sorted([p for p in players.values() if p["lists"] and p["peak"] > 1],
                              key=lambda p: (-p["lists"], p["peak"], p["name"]))
    moves = []
    byid = {r["id"]: r for r in releases}
    for r in nonempty_us:
        for e in r["entries"]:
            mv = e.get("move")
            if mv and mv["kind"] in ("up", "down") and mv["vs"]:
                prev = byid[mv["vs"]]
                pe = next(x for x in prev["entries"] if x["player"] == e["player"])
                moves.append({"player": e["player"], "from": pe["rank"], "to": e["rank"], "prev": prev, "cur": r,
                              "delta": pe["rank"] - e["rank"]})
    rec["jumps"] = sorted([m for m in moves if m["delta"] > 0], key=lambda m: (-m["delta"], m["cur"]["date"]))
    rec["falls"] = sorted([m for m in moves if m["delta"] < 0], key=lambda m: (m["delta"], m["cur"]["date"]))
    fast = []
    for p in players.values():
        d = cross.draft.get(p["name"])
        if not (d and p["first"]):
            continue
        n = int(p["first"]["season"][:4]) - int(d["draft_year"]) + 1
        if 1 <= n <= 2:
            fast.append({"player": p["name"], "season_no": n, "release": p["first"],
                         "rank": p["us"][0][1]["rank"], "draft_year": d["draft_year"], "pick": d.get("draft_pick")})
    rec["fast"] = sorted(fast, key=lambda x: (x["season_no"], x["release"]["date"], x["rank"]))
    duos = defaultdict(list)
    for r in nonempty_us:
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


def stat_cards(items):
    return '<div class="stat-cards">%s</div>' % "".join(
        '<div class="stat-card"><div class="lbl">%s</div><div class="num%s">%s</div>%s</div>' % (
            esc(lbl), " sm" if len(re.sub("<[^>]+>", "", num)) > 7 else "", num,
            ('<div class="sub">%s</div>' % sub) if sub else "") for lbl, num, sub in items)


def section(title, inner, hint="", sid=None):
    return '<section class="section"%s><h2>%s</h2>%s%s</section>' % (
        (' id="%s"' % sid) if sid else "", esc(title), ('<p class="hint">%s</p>' % hint) if hint else "", inner)


def build_player_page(site, p, ctx, model):
    releases, us, nonempty_us, teams_by_slug, cross = model
    name, slug = p["name"], p["slug"]
    url = SITE + "player/%s/" % slug
    y0, y1 = p["years"]
    span = y0 if y0 == y1 else "%s-%s" % (y0, y1)
    title = "%s jersey sales rankings history, %s | HoopsMatic" % (name, span)
    if p["lists"]:
        desc = "%s made %s of NBA jersey sales, from the %s list to the %s list. Peak: No. %d%s." % (
            name, plural(p["lists"], "U.S. list"), p["first"]["label"], p["last"]["label"], p["peak"],
            (", No. 1 on %s" % plural(p["no1"], "list")) if p["no1"] else "")
    else:
        r0 = p["intl"][0][0]
        desc = "%s appears on %s of NBA jersey sales outside the U.S. lists, first on the %s (%s)." % (
            name, plural(len(p["intl"]), "international list"), r0["type"], r0["label"])
    b = []
    b.append('<div class="ehead">%s<h1>%s jersey sales rankings</h1></div>' % (ctx.face(name, 72), esc(name)))
    if p["lists"]:
        b.append('<p class="subtitle">%s %s on %s, from %s to %s.</p>' % (
            ctx.face(name, 0).replace('<img', '<img hidden') if False else "", esc(name),
            plural(p["lists"], "U.S. list"), esc(p["first"]["label"]), esc(p["last"]["label"])))
        run_n, run_a, run_b = p["run"]
        run_sub = esc(run_a["label"] + " to " + run_b["label"]) if run_n > 1 else esc(run_a["label"])
        b.append(stat_cards([
            ("Lists made", str(p["lists"]), "U.S. lists"),
            ("Peak rank", "No. %d" % p["peak"], esc(plural(p["peak_times"], "time"))),
            ("No. 1 finishes", str(p["no1"]), ""),
            ("Longest run", str(run_n), run_sub),
            ("Jersey points", str(p["points"]), "top 10 only"),
            ("First list", esc(p["first"]["label"]), esc(p["first"]["type"])),
            ("Last list", esc(p["last"]["label"]), esc(p["last"]["type"])),
        ]))
        b.append(section("Rank on every U.S. list", rank_chart(ctx, p, nonempty_us, us),
                         "No. 1 at the top. Shaded columns are lists with only the No. 1 or no data; a line breaks when a full list was missed. Swipe sideways on phones."))
        # Team at each list, with moves.
        rows, prev_team = [], None
        for r, e in p["us"]:
            moved = prev_team is not None and e["team_slugs"] != prev_team
            prev_team = e["team_slugs"]
            rows.append(['<a href="%sseason/%s/">%s</a><span class="sub2">%s</span>%s' % (
                ctx.root, r["season"], esc(r["label"]), esc(r["type"]), ""),
                str(e["rank"]), move_html(e["move"]),
                ctx.team_cell(e) + (' <span class="tag">moved</span>' if moved else "") + flag_html(e)])
        b.append(section("Team at each list", table([("List", "left"), ("No.", "rank"), ("Move", "mvc"), ("Team", "team left")], rows),
                         "Move compares with the previous U.S. list that has the player. NEW is a first U.S. list, BACK a return after missing one."))
        # Season by season context.
        by_season = OrderedDict()
        for r, e in p["us"]:
            by_season.setdefault(r["season"], []).append((r, e))
        blocks = []
        for season, items in by_season.items():
            end = season_end(season)
            lines = []
            ranks = ", ".join("No. %d (%s)" % (e["rank"], r["type"]) for r, e in items)
            lines.append('<div><span class="k">Lists</span>%s</div>' % esc(ranks))
            sal = cross.salary(name, end)
            if sal:
                srk = cross.salary_rank(name, end)
                lines.append('<div><span class="k">Salary</span>%s%s</div>' % (
                    esc(money(sal)), esc(" (No. %d in the NBA)" % srk) if srk else ""))
            pr = cross.prod.get(name, {}).get(end)
            if pr and pr.get("prod_rank"):
                lines.append('<div><span class="k">Production</span>No. %d of %d (%s PTS, %s REB, %s AST in %d games)</div>' % (
                    pr["prod_rank"], pr["pool"], pr["ppg"], pr["rpg"], pr["apg"], pr["gp"]))
            elif pr:
                lines.append('<div><span class="k">Production</span>%s, under the %d-game minimum</div>' % (
                    esc(plural(pr["gp"], "game")), cross.prod_min_gp))
            mv = cross.mvp.get(name, {}).get(end)
            if mv:
                lines.append('<div><span class="k">MVP vote</span>%s</div>' % ordinal(mv))
            aw = cross.awards.get(name, {}).get(end)
            if aw:
                lines.append('<div><span class="k">Awards</span>%s</div>' % esc(", ".join(sorted(aw))))
            blocks.append('<div class="season-row"><div class="sh">%s</div>%s</div>' % (ctx.slink(season), "".join(lines)))
        b.append(section("Season by season", '<div class="season-rows">%s</div>' % "".join(blocks),
                         "Salary from nba-player-data (shown only where it agrees with Salary Season Finder), league salary rank from Salary Season Finder. Production is the league rank in points + rebounds + assists per game that regular season (min. %d games). Awards and MVP voting by season." % cross.prod_min_gp))
        # Teammates on the same list, same team.
        mates = defaultdict(list)
        for r, e in p["us"]:
            for x in r["entries"]:
                if x["player"] != name and set(x["team_slugs"]) & set(e["team_slugs"]):
                    shared = [s for s in e["team_slugs"] if s in x["team_slugs"]]
                    mates[(x["player"], shared[0])].append(r)
        if mates:
            rows = []
            for (m, s), rs in sorted(mates.items(), key=lambda kv: (-len(kv[1]), kv[1][0]["date"])):
                rows.append([ctx.face(m, 28) + ctx.plink(m) + '<span class="sub2">%s</span>' % esc(
                    rs[0]["label"] if len(rs) == 1 else rs[0]["label"] + " to " + rs[-1]["label"]), ctx.tlink(s), str(len(rs))])
            b.append(section("Teammates on the same list", table([("Teammate", "name left"), ("Team", "team left"), ("Lists", "num")], rows),
                             "Players from the same team on the same U.S. list."))
    else:
        b.append('<p class="subtitle">%s has no U.S. list in the source data.</p>' % esc(name))
    # Birthplace (Career Map).
    bio = cross.bio.get(name)
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
        rows = [[esc(r["label"]), esc(r["type"]), str(e["rank"]), ctx.team_cell(e) + flag_html(e)] for r, e in p["intl"]]
        b.append(section("International lists", table([("Date", "left"), ("List", "left"), ("No.", "rank"), ("Team", "team left")], rows),
                         "Europe, Philippines and the 2013 worldwide release. Not counted in any U.S. number on this page."))
    site.add("player/" + slug, url, page("../../", url, title, desc, "\n".join(b), indexable=p["indexable"], nav=None), p["indexable"])


def build_team_page(site, t, ctx, model, team_entries):
    releases, us, nonempty_us, teams_by_slug, cross = model
    slug = t["slug"]
    url = SITE + "team/%s/" % slug
    ent = team_entries.get(slug, [])
    us_ent = [(r, e) for r, e in ent if r["us"]]
    intl_ent = [(r, e) for r, e in ent if not r["us"]]
    years = sorted({r["date"][:4] for r, _ in us_ent})
    span = (years[0] if years[0] == years[-1] else "%s-%s" % (years[0], years[-1])) if years else ""
    players_set = sorted({e["player"] for _, e in us_ent})
    pts_total = sum(e["pts"] for _, e in us_ent)
    indexable = bool(us_ent)
    title = ("%s jersey sales rankings history, %s | HoopsMatic" % (t["full_name"], span)) if span else \
        "%s jersey sales rankings history | HoopsMatic" % t["full_name"]
    full_lists = [r for r in us if r["status"] == "full"]
    # Longest drought over full U.S. lists.
    on = defaultdict(list)
    for r, e in us_ent:
        on[r["id"]].append(e)
    best, cur, start = (0, None, None), 0, None
    for r in full_lists:
        if on.get(r["id"]):
            cur = 0
        else:
            if cur == 0:
                start = r
            cur += 1
            if cur > best[0]:
                best = (cur, start, r)
    drought_n, d_a, d_b = best
    ongoing = drought_n and d_b is full_lists[-1]
    # Best-ever list: most jersey points, then most players, then best rank.
    best_list = None
    for r in us:
        es = on.get(r["id"])
        if es:
            key = (sum(e["pts"] for e in es), len(es), -min(e["rank"] for e in es))
            if best_list is None or key > best_list[0]:
                best_list = (key, r, es)
    if us_ent:
        desc = "%s on NBA jersey sales lists: %s on %s, %d jersey points. Best list: %s. Longest drought: %s." % (
            t["full_name"], plural(len(players_set), "player"), plural(len(on), "U.S. list"), pts_total,
            best_list[1]["label"], plural(drought_n, "list"))
    else:
        desc = "%s: no player on any of the %d full U.S. NBA jersey sales lists in the source data." % (t["full_name"], len(full_lists))
    b = ['<div class="ehead">%s<h1>%s jersey sales rankings</h1></div>' % (
        '<span class="sw big" style="background:%s"></span>' % esc(t.get("color") or "#999"), esc(t["full_name"]))]
    if us_ent:
        b.append('<p class="subtitle">%s on %s, %s to %s.</p>' % (
            esc(plural(len(players_set), "player")), esc(plural(len(on), "U.S. list")),
            esc(us_ent[0][0]["label"]), esc(us_ent[-1][0]["label"])))
    else:
        b.append('<p class="subtitle">No %s player appears on any U.S. list in the source data.</p>' % esc(t["full_name"]))
    cards = [("Players", str(len(players_set)), "on U.S. lists"), ("Lists with a player", str(len(on)), "of %d U.S. lists" % len(nonempty_us)),
             ("Jersey points", str(pts_total), "top 10 only")]
    if drought_n:
        cards.append(("Longest drought", str(drought_n), esc(("%s to %s" % (d_a["label"], d_b["label"])) + (", ongoing" if ongoing else ""))))
    b.append(stat_cards(cards))
    # Points by season (+ road draw if available).
    seasons = sorted({r["season"] for r in us})
    pts_by = {s: 0 for s in seasons}
    players_by = defaultdict(set)
    for r, e in us_ent:
        pts_by[r["season"]] += e["pts"]
        players_by[r["season"]].add(e["player"])
    b.append(section("Jersey points by season", bar_chart([(s, pts_by[s]) for s in seasons], lambda v: str(int(v)),
                                                          "%s jersey points by season" % t["full_name"]),
                     "Sum of jersey points (No. 1 = 10 down to No. 10 = 1) across every U.S. list in the season. Swipe sideways on phones."))
    if best_list:
        _, r, es = best_list
        rows = [[str(e["rank"]), ctx.face(e["player"], 28) + ctx.plink(e["player"]) + flag_html(e), str(e["pts"])] for e in es]
        b.append(section("Best-ever list: %s" % r["title"], table([("No.", "rank"), ("Player", "name left"), ("Pts", "num")], rows) + source_link(r),
                         "The U.S. list with the most jersey points for this team (ties: more players, then better rank)."))
    # Ranked players per season.
    if us_ent:
        blocks = []
        for s in seasons:
            lists = [r for r in us if r["season"] == s and on.get(r["id"])]
            if not lists:
                continue
            lines = []
            for r in lists:
                names = ", ".join("%s No. %d%s" % (ctx.plink(e["player"]), e["rank"],
                                                   ' <span class="note">%s</span>' % esc("shared listing: " + e["team"]) if e["multi"] else "")
                                  for e in sorted(on[r["id"]], key=lambda e: e["rank"]))
                lines.append('<div class="ln"><span class="k2">%s</span>%s</div>' % (esc(r["type"] + ", " + r["label"]), names))
            blocks.append('<div class="season-row"><div class="sh">%s<span class="sub2">%d pts</span></div>%s</div>' % (
                ctx.slink(s), pts_by[s], "".join(lines)))
        b.append(section("Ranked players by season", '<div class="season-rows">%s</div>' % "".join(blocks),
                         "Players listed with more than one team (traded during the sales period) count for each team listed."))
        # Player totals for this team.
        tot = defaultdict(lambda: [0, 0, 99])
        for r, e in us_ent:
            tot[e["player"]][0] += 1
            tot[e["player"]][1] += e["pts"]
            tot[e["player"]][2] = min(tot[e["player"]][2], e["rank"])
        rows = [[ctx.face(n, 28) + ctx.plink(n), str(v[0]), "No. %d" % v[2], str(v[1])]
                for n, v in sorted(tot.items(), key=lambda kv: (-kv[1][1], -kv[1][0], kv[0]))]
        b.append(section("Players with this team", table([("Player", "name left"), ("Lists", "num"), ("Peak", "num"), ("Pts", "num")], rows)))
    # Attendance road draw (HoopsMatic attendance tracker), by season end year.
    att = cross.att.get(str(t["team_id"]), {})
    if att:
        rows, vals = [], []
        for s in seasons:
            a = att.get(season_end(s))
            vals.append((s, round(a["mean_delta"]) if a else None))
            rows.append([ctx.slink(s), str(pts_by[s]),
                         ('%+d<span class="sub2">%s</span>' % (round(a["mean_delta"]), esc(plural(a["games"], "game")))) if a else '<span class="dash">no data</span>'])
        b.append(section("Road attendance draw", bar_chart(vals, lambda v: "%+d" % v, "%s road attendance draw by season" % t["full_name"], " fans") +
                         table([("Season", "left nw"), ("Jersey pts", "num"), ("Road draw", "num")], rows),
                         'Average crowd above or below the home arena\'s usual attendance when this team visited, from the <a href="%s">HoopsMatic NBA attendance tracker</a>. Seasons marked no data are missing there.' % ATTENDANCE_TOOL))
    if intl_ent:
        rows = [[esc(r["label"]), esc(r["type"]), str(e["rank"]), ctx.face(e["player"], 28) + ctx.plink(e["player"])] for r, e in intl_ent]
        b.append(section("International lists", table([("Date", "left"), ("List", "left"), ("No.", "rank"), ("Player", "name left")], rows),
                         "Not counted in any U.S. number on this page."))
    site.add("team/" + slug, url, page("../../", url, title, desc, "\n".join(b), indexable=indexable, nav="teams"), indexable)
    return {"slug": slug, "name": t["full_name"], "points": pts_total, "players": len(players_set), "lists": len(on),
            "drought": drought_n, "indexable": indexable, "color": t.get("color")}


def movers_between(prev, cur):
    pm = {e["player"]: e["rank"] for e in prev["entries"]}
    out = []
    for e in cur["entries"]:
        if e["player"] in pm and pm[e["player"]] != e["rank"]:
            out.append((e["player"], pm[e["player"]], e["rank"]))
    return sorted(out, key=lambda x: -(x[1] - x[2]))


def build_season_page(site, season, ctx, model, pvp, first_season_of):
    releases, us, nonempty_us, teams_by_slug, cross = model
    url = SITE + "season/%s/" % season
    lists = [r for r in releases if r["season"] == season]
    us_lists = [r for r in lists if r["us"]]
    final = next((r for r in us_lists if r["final"]), None)
    debuts = sorted({e["player"] for r in us_lists for e in r["entries"] if first_season_of.get(e["player"]) == season},
                    key=lambda n: min((r["date"], e["rank"], n) for r in us_lists for e in r["entries"] if e["player"] == n))
    # Previous season's last full U.S. list -> who is on no U.S. list this season.
    prev_full = [r for r in us if r["status"] == "full" and r["date"] < (us_lists[0]["date"] if us_lists else "")]
    prev_last = prev_full[-1] if prev_full else None
    this_names = {e["player"] for r in us_lists for e in r["entries"]}
    has_full = any(r["status"] == "full" for r in us_lists)
    dropped = [e for e in prev_last["entries"] if e["player"] not in this_names] if (prev_last and has_full) else []
    indexable = has_full
    title = "%s NBA jersey sales rankings, %s | HoopsMatic" % (season, plural(len(lists), "list"))
    no1 = final["entries"][0]["player"] if final and final["entries"] else None
    desc = "Every NBA jersey sales list from the %s season (%s)." % (season, ", ".join(r["type"] for r in lists))
    if no1:
        desc += " No. 1 on the final U.S. list: %s." % no1
    if debuts:
        desc += " %s." % plural(len(debuts), "debut")
    b = ['<h1>%s NBA jersey sales rankings</h1>' % esc(season)]
    sub = "%s from %s." % (plural(len(lists), "list"), join_names(r["label"] for r in lists))
    if no1:
        sub += " No. 1 on the season's final U.S. list: %s." % esc(no1)
    b.append('<p class="subtitle">%s</p>' % sub.replace(esc(no1), ctx.plink(no1)) if no1 else '<p class="subtitle">%s</p>' % sub)
    cards = []
    for r in lists:
        note = release_note(r)
        tag = "" if r["us"] else '<span class="tag">International</span>'
        inner = list_table(ctx, r, with_moves=r["us"], compact=True) if r["entries"] else ""
        cards.append('<div class="lcard"><div class="lhead"><h3>%s</h3><span class="sub2">%s</span>%s%s</div>%s%s</div>' % (
            esc(r["type"]), esc(r["label"] + (" (season final)" if r["final"] else "")), tag, source_link(r),
            ('<p class="hint">%s</p>' % esc(note)) if note else "", inner))
    b.append(section("Every list this season", '<div class="lcards">%s</div>' % "".join(cards),
                     "Side by side; swipe sideways on phones. Moves compare with the previous U.S. list that has the player."))
    # Movers between consecutive U.S. lists (first comparison reaches back to the previous season).
    mv_blocks = []
    for r in us_lists:
        if r["status"] != "full" or not r["compare_id"]:
            continue
        prev = next(x for x in releases if x["id"] == r["compare_id"])
        m = movers_between(prev, r)
        if not m:
            continue
        rows = [[ctx.plink(n), "No. %d" % a, "No. %d" % c, move_html({"kind": "up" if a > c else "down", "delta": abs(a - c)})] for n, a, c in m]
        mv_blocks.append('<h3 class="h3">%s to %s</h3>%s' % (esc(prev["title"]), esc(r["title"]),
                                                             table([("Player", "name left"), ("From", "num"), ("To", "num"), ("Move", "mvc")], rows)))
    if mv_blocks:
        b.append(section("Movers", "".join(mv_blocks), "Players on both lists, biggest climb first."))
    if debuts:
        rows = []
        for n in debuts:
            r, e = next((r, e) for r in us_lists for e in r["entries"] if e["player"] == n)
            rows.append([ctx.face(n, 28) + ctx.plink(n) + '<span class="sub2">%s</span>' % esc(r["title"]), "No. %d" % e["rank"], ctx.team_cell(e)])
        b.append(section("Debuts", table([("Player / first list", "name left"), ("Rank", "num"), ("Team", "team left")], rows),
                         "First appearance on any U.S. list in the source data."))
    if dropped:
        rows = [[ctx.plink(e["player"]), "No. %d" % e["rank"], ctx.team_cell(e)] for e in dropped]
        b.append(section("Dropped off", table([("Player", "name left"), ("Last rank", "num"), ("Team", "team left")], rows),
                         "On the %s list but on no U.S. list in %s." % (esc(prev_last["title"]), esc(season))))
    s = pvp.get(season)
    if s and s["rows"]:
        b.append(section("Popularity vs. production", pvp_table(ctx, s),
                         "Final U.S. list of the season. Gap = production rank minus jersey rank; a big positive gap means the jersey sold far above the player's stat line."))
    nav = []
    seasons = sorted({r["season"] for r in releases})
    i = seasons.index(season)
    if i > 0:
        nav.append('<a href="../%s/">%s</a>' % (seasons[i - 1], esc(seasons[i - 1])))
    if i < len(seasons) - 1:
        nav.append('<a href="../%s/">%s</a>' % (seasons[i + 1], esc(seasons[i + 1])))
    b.append('<p class="pager">%s</p>' % " &middot; ".join(nav))
    site.add("season/" + season, url, page("../../", url, title, desc, "\n".join(b), indexable=indexable, nav="seasons"), indexable)
    return {"season": season, "lists": len(lists), "no1": no1, "indexable": indexable}


def pvp_table(ctx, s):
    rows = [[ctx.plink(x["player"]), "No. %d" % x["jersey"], "No. %d" % x["prod"],
             '<span class="%s">%+d</span>' % ("pos" if x["gap"] > 0 else ("neg" if x["gap"] < 0 else "flat"), x["gap"])] for x in s["rows"]]
    out = table([("Player", "name left"), ("Jersey", "num"), ("Prod.", "num"), ("Gap", "num")], rows)
    extra = []
    if s["unranked"]:
        extra.append("No production rank (under the games minimum or no stats row): %s." % ", ".join(
            "%s (No. %d)" % (ctx.plink(x["player"]), x["jersey"]) for x in s["unranked"]))
    if s["top10_missing"]:
        extra.append("Top 10 in production but not on this list: %s." % esc(", ".join(s["top10_missing"])))
    return out + "".join('<p class="hint">%s</p>' % x for x in extra)


def build_records(site, ctx, rec, model):
    url = SITE + "records/"
    title = "NBA jersey sales records, 2001-2026 | HoopsMatic"
    top_reign = rec["reigns"][0]
    top_jump = rec["jumps"][0] if rec["jumps"] else None
    desc = "NBA jersey sales records from every U.S. list: longest No. 1 reign (%s, %s), biggest jumps and falls, one-list players, fastest entries after the draft and same-team duos." % (
        top_reign["player"], plural(top_reign["n"], "list"))
    b = ['<h1>NBA jersey sales records</h1>',
         '<p class="subtitle">Computed from every U.S. list in the source data. %s held No. 1 on %s in a row.</p>' % (
             ctx.plink(top_reign["player"]), plural(top_reign["n"], "list"))]
    rows = [[ctx.face(x["player"], 28) + ctx.plink(x["player"]), str(x["n"]), esc(x["start"]["label"] + (" to " + x["end"]["label"] if x["n"] > 1 else ""))] for x in rec["reigns"]]
    b.append(section("Longest No. 1 reigns", table([("Player", "name left"), ("Lists", "num"), ("From / to", "left")], rows),
                     "Consecutive U.S. lists at No. 1. The empty 2005-01 list is skipped."))
    rows = [[ctx.face(p["name"], 28) + ctx.plink(p["name"]), str(p["no1"]), str(p["lists"])] for p in rec["no1_totals"]]
    b.append(section("Most No. 1 finishes", table([("Player", "name left"), ("No. 1s", "num"), ("Lists", "num")], rows)))
    rows = [[ctx.face(p["name"], 28) + ctx.plink(p["name"]), str(p["lists"]), "No. %d" % p["peak"]] for p in rec["never_no1"]]
    b.append(section("Most lists without ever hitting No. 1", table([("Player", "name left"), ("Lists", "num"), ("Peak", "num")], rows)))
    for key, ttl in (("jumps", "Biggest jumps between consecutive U.S. lists"), ("falls", "Biggest falls between consecutive U.S. lists")):
        rows = [[ctx.plink(m["player"]) + '<span class="sub2">%s</span>' % esc(m["prev"]["label"] + " to " + m["cur"]["label"]),
                 "No. %d" % m["from"], "No. %d" % m["to"], move_html({"kind": "up" if m["delta"] > 0 else "down", "delta": abs(m["delta"])})] for m in rec[key]]
        b.append(section(ttl, table([("Player", "name left"), ("From", "num"), ("To", "num"), ("Move", "mvc")], rows),
                         "Player on both lists; compared with the previous U.S. list that has the player. List lengths changed over time (25, 20, 15)."))
    rows = [[ctx.face(x["player"], 28) + ctx.plink(x["player"]) + '<span class="sub2">%s</span>' % esc(x["release"]["title"]),
             "No. %d" % x["rank"],
             esc("%s, No. %s" % (x["draft_year"], x["pick"]) if x["pick"] else "%s, undrafted" % x["draft_year"]) +
             '<span class="sub2">%s</span>' % ("Rookie season" if x["season_no"] == 1 else "Second season")] for x in rec["fast"]]
    b.append(section("Fastest entries after the draft", table([("Player / first list", "name left"), ("Rank", "num"), ("Draft", "left")], rows),
                     "First U.S. list in the player's rookie or second season. Draft year and pick from Salary Season Finder; the draft year is the first season's start year."))
    rows = [[ctx.plink(n) + '<span class="sub2">%s</span>' % esc(r["title"]), "No. %d" % e["rank"], ctx.team_cell(e) + flag_html(e)]
            for n, r, e in rec["one_list"]]
    b.append(section("One-list players", table([("Player / only list", "name left"), ("Rank", "num"), ("Team", "team left")], rows),
                     "Exactly one U.S. list in the source data."))
    rows = [[ctx.plink(d["a"]) + " &amp; " + ctx.plink(d["b"]) + '<span class="sub2">%s</span>' % esc(
                 d["lists"][0]["label"] + (" to " + d["lists"][-1]["label"] if len(d["lists"]) > 1 else "")),
             ctx.tlink(d["team"]), str(len(d["lists"]))] for d in rec["duos"]]
    b.append(section("Same-team duos on the same list", table([("Duo", "name left"), ("Team", "team left"), ("Lists", "num")], rows),
                     "Two players from the same team on the same U.S. list."))
    site.add("records", url, page("../", url, title, desc, "\n".join(b), indexable=True, nav="records"), True)


def build_hub(site, ctx, model, players, team_summ, season_summ, pvp, career, releases_json_count):
    releases, us, nonempty_us, teams_by_slug, cross = model
    url = SITE
    n_lists = sum(1 for r in releases if r["entries"])
    n_us = sum(1 for r in us if r["entries"])
    n_intl = n_lists - n_us
    first, last = releases[0], releases[-1]
    leader = max(players.values(), key=lambda p: (p["no1"], p["points"]))
    pts_leader = max(players.values(), key=lambda p: p["points"])
    title = "NBA jersey sales rankings, %s-%s | HoopsMatic" % (first["date"][:4], last["date"][:4])
    desc = "Every official NBA jersey sales list from %s to %s: %d U.S. lists and %d international. Time machine, all-time jersey points (leader: %s), records, teams and seasons." % (
        first["label"], last["label"], n_us, n_intl, pts_leader["name"])
    b = ['<h1>NBA jersey sales rankings, %s-%s</h1>' % (first["date"][:4], last["date"][:4])]
    b.append('<p class="subtitle">%d official lists from %s to %s: %d U.S. lists and %d international. Most No. 1 finishes: %s (%d).</p>' % (
        n_lists, esc(first["label"]), esc(last["label"]), n_us, n_intl, ctx.plink(leader["name"]), leader["no1"]))
    # Time machine: prerender the latest list; JS swaps lists from data/releases.json.
    latest = [r for r in releases if r["entries"]][-1]
    opts = "".join('<option value="%d"%s>%s%s</option>' % (
        i, " selected" if r is latest else "", esc(r["title"]), "" if r["entries"] else " (empty)")
        for i, r in enumerate(releases))
    tm = ('<div class="tm" id="tm" data-count="%d">'
          '<div class="tm-controls"><button type="button" class="tm-prev" aria-label="Previous list">&#9664;</button>'
          '<input type="range" class="tm-range" min="0" max="%d" value="%d" aria-label="Pick a list">'
          '<button type="button" class="tm-next" aria-label="Next list">&#9654;</button></div>'
          '<select class="tm-select" aria-label="Pick a list">%s</select>'
          '<div class="tm-body">%s</div></div>') % (
        len(releases), len(releases) - 1, releases.index(latest), opts, tm_body(ctx, latest, releases))
    b.append(section("Time machine", tm, "Every published list in order, international lists included. Arrows compare with the previous U.S. list that has the player; NEW marks a first U.S. list.", sid="time-machine"))
    # Popularity vs production.
    seasons = list(pvp.keys())
    sel = "".join('<option value="%s"%s>%s</option>' % (s, " selected" if s == seasons[-1] else "", esc(s)) for s in reversed(seasons))
    panes = "".join('<div class="pvp-pane" data-season="%s"%s>%s</div>' % (s, "" if s == seasons[-1] else " hidden", pvp_table(ctx, pvp[s]) if pvp[s]["rows"] else '<p class="hint">No production data for this list.</p>') for s in seasons)
    rows_hi = [[ctx.face(x["player"], 28) + ctx.plink(x["player"]), '<span class="pos">%+g</span>' % x["median_gap"], str(x["n"])] for x in career if x["median_gap"] > 0]
    rows_lo = [[ctx.face(x["player"], 28) + ctx.plink(x["player"]), '<span class="neg">%+g</span>' % x["median_gap"], str(x["n"])] for x in reversed(career) if x["median_gap"] < 0]
    pvp_html = ('<div class="pvp"><label class="lbl2">Season <select class="pvp-select">%s</select></label>%s</div>'
                '<h3 class="h3">Career: sells most above production</h3>%s'
                '<h3 class="h3">Career: sells most below production</h3>%s') % (
        sel, panes, table([("Player", "name left"), ("Median gap", "num"), ("Lists", "num")], rows_hi),
        table([("Player", "name left"), ("Median gap", "num"), ("Lists", "num")], rows_lo))
    b.append(section("Popularity vs. production", pvp_html,
                     "Jersey rank on each season's final U.S. list against the player's league rank in points + rebounds + assists per game that season (min. %d games). Gap = production rank minus jersey rank. Career uses the median gap over 3+ season-final lists." % cross.prod_min_gp, sid="pvp"))
    # All-time jersey points.
    ranked = sorted([p for p in players.values() if p["points"]], key=lambda p: (-p["points"], -p["lists"], p["name"]))
    rows = [[str(i), ctx.face(p["name"], 28) + ctx.plink(p["name"]), str(p["points"]), str(p["lists"]), str(p["no1"])] for i, p in enumerate(ranked, 1)]
    b.append(section("All-time jersey points", table([("#", "rank"), ("Player", "name left"), ("Pts", "num"), ("Lists", "num"), ("No. 1s", "num")], rows) +
                     '<p class="dl"><a href="data/jersey-points-race.csv" download>Download cumulative jersey points (CSV)</a> for the <a href="%s">bar chart race tool</a>.</p>' % RACE_TOOL,
                     "Jersey points: No. 1 = 10 points down to No. 10 = 1 point, U.S. lists only.", sid="points"))
    # Seasons, teams, records, players.
    srows = []
    for s in season_summ:
        srows.append('<li><a href="season/%s/">%s</a><span class="sub2">%s%s</span></li>' % (
            s["season"], esc(s["season"]), esc(plural(s["lists"], "list")), esc(", No. 1: " + s["no1"]) if s["no1"] else ""))
    b.append(section("Seasons", '<ul class="linkgrid">%s</ul>' % "".join(srows), sid="seasons"))
    trows = [[ctx.tlink(t["slug"]), str(t["points"]), str(t["players"]), str(t["lists"])] for t in sorted(team_summ, key=lambda t: (-t["points"], t["name"]))]
    b.append(section("Teams", table([("Team", "team left"), ("Pts", "num"), ("Players", "num"), ("Lists", "num")], trows),
                     "Franchise totals: Seattle SuperSonics count for Oklahoma City, Charlotte Bobcats for Charlotte, New Orleans Hornets for New Orleans, New Jersey Nets for Brooklyn.", sid="teams"))
    b.append(section("Records", '<p><a href="records/">Longest No. 1 reigns, biggest jumps and falls, one-list players, fastest entries after the draft and same-team duos</a></p>', sid="records"))
    prow = sorted(players.values(), key=lambda p: p["name"].split(" ", 1)[-1] + p["name"])
    b.append(section("All players", '<ul class="linkgrid small">%s</ul>' % "".join(
        '<li>%s<span class="sub2">%s</span></li>' % (ctx.plink(p["name"]), esc(plural(p["lists"], "U.S. list"))) for p in prow), sid="players"))
    # International block.
    intl = [r for r in releases if not r["us"]]
    cards = "".join('<div class="lcard"><div class="lhead"><h3>%s</h3><span class="sub2">%s</span>%s</div>%s</div>' % (
        esc(r["type"]), esc(r["label"] + ", " + r["season"]), source_link(r), list_table(ctx, r, with_moves=False, compact=True)) for r in intl)
    b.append(section("International lists", '<div class="lcards">%s</div>' % cards,
                     "Kept apart from every U.S. count, peak, streak, No. 1 and point total.", sid="international"))
    # Methodology.
    gaps = []
    for r in us:
        if r["status"] != "full":
            gaps.append("%s (%s)" % (r["title"], "empty" if r["status"] == "empty" else "No. 1 only"))
    lengths = sorted({r["n"] for r in us if r["status"] == "full"})
    meth = ("<ul class=\"meth\">"
            "<li>U.S. lists are every list except Europe only, Philippines only and the 2013 worldwide release (U.S. top 10). All U.S. counts, peaks, streaks, No. 1s and points use U.S. lists only.</li>"
            "<li>Jersey points: 11 minus rank for the top 10 of each U.S. list (No. 1 = 10, No. 10 = 1). Ranks 11 and lower score 0, so lists of different lengths compare fairly.</li>"
            "<li>Full U.S. list lengths in the source: %s spots.</li>"
            "<li>Known gaps, shown as they are: %s.</li>"
            "<li>A list with only the No. 1, or no data, does not break or extend a player's run and is skipped when comparing moves.</li>"
            "<li>Players listed with more than one team (traded during the sales period) count for each team listed.</li>"
            "<li>Rows marked with a small note carry the source flag (for example, from a 2025 HoopsHype article only, or a team filled in).</li>"
            "<li>Production rank: the player's league rank in points + rebounds + assists per game that regular season, minimum %d games (stats from nba-player-data). Salary, awards, MVP votes, birthplace and road attendance come from other HoopsMatic datasets; a player or season that did not match cleanly is left blank.</li>"
            "</ul>") % (esc(join_names(str(x) for x in lengths)), esc("; ".join(gaps)), cross.prod_min_gp)
    b.append(section("Methodology", meth, sid="methodology"))
    site.add("", url, page("", url, title, desc, "\n".join(b), indexable=True, nav="hub"), True)


def tm_body(ctx, r, releases):
    """Prerendered time machine body for one release (same markup the JS builds)."""
    head = '<div class="tm-head"><h3>%s</h3><span class="sub2">%s</span>%s%s</div>' % (
        esc(r["title"]), esc(r["season"] + (" season final" if r["final"] else "") + ("" if r["us"] else ", international")),
        '<a class="sub2" href="season/%s/">Season page</a>' % r["season"], source_link(r))
    note = release_note(r)
    body = ('<p class="hint">%s</p>' % esc(note)) if note else ""
    if r["entries"]:
        body += list_table(ctx, r, with_moves=r["us"])
    if r.get("dropped"):
        body += '<p class="hint">Dropped off since the previous full U.S. list: %s.</p>' % ", ".join(
            "%s (No. %d)" % (ctx.plink(e["player"]), e["rank"]) for e in r["dropped"])
    return head + body


# --------------------------------------------------------------------------
# JSON + CSV exports
# --------------------------------------------------------------------------

def write_json(rel, obj):
    p = os.path.join(DOCS, "data", rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def export_data(releases, players, cross, teams_by_slug, rec, pvp, career):
    write_json("releases.json", {
        "teams": {s: {"name": t["full_name"], "color": t.get("color")} for s, t in teams_by_slug.items()},
        "releases": [{
            "id": r["id"], "title": r["title"], "label": r["label"], "season": r["season"], "type": r["type"],
            "us": r["us"], "final": r["final"], "status": r["status"], "source": r["source"], "note": release_note(r),
            "entries": [{"r": e["rank"], "p": e["player"], "s": e["slug"], "t": e["team"], "ts": e["team_slugs"],
                         "f": flag_html(e) and (e["flag"] + ("; " if e["flag"] and e["multi"] else "") + ("Listed with more than one team; counts for each team listed" if e["multi"] else "")),
                         "m": e["move"], "img": cross.img(e["player"])} for e in r["entries"]],
            "dropped": [{"p": e["player"], "s": e["slug"], "r": e["rank"]} for e in r["dropped"]],
        } for r in releases]})
    write_json("players.json", [{
        "name": p["name"], "slug": p["slug"], "us_lists": p["lists"], "points": p["points"], "no1": p["no1"],
        "peak": p["peak"], "first": p["first"]["id"] if p["first"] else None, "last": p["last"]["id"] if p["last"] else None,
        "longest_run": p["run"][0], "indexable": p["indexable"],
        "ranks": [{"release": r["id"], "rank": e["rank"], "team": e["team"], "teams": e["team_slugs"]} for r, e in p["us"]],
        "international": [{"release": r["id"], "rank": e["rank"], "team": e["team"]} for r, e in p["intl"]],
    } for p in players.values()])
    write_json("records.json", {
        "longest_no1_reigns": [{"player": x["player"], "lists": x["n"], "from": x["start"]["id"], "to": x["end"]["id"]} for x in rec["reigns"]],
        "most_lists_without_no1": [{"player": p["name"], "lists": p["lists"], "peak": p["peak"]} for p in rec["never_no1"]],
        "biggest_jumps": [{"player": m["player"], "from": m["from"], "to": m["to"], "prev": m["prev"]["id"], "list": m["cur"]["id"]} for m in rec["jumps"]],
        "biggest_falls": [{"player": m["player"], "from": m["from"], "to": m["to"], "prev": m["prev"]["id"], "list": m["cur"]["id"]} for m in rec["falls"]],
        "fastest_after_draft": [{"player": x["player"], "season_no": x["season_no"], "list": x["release"]["id"], "rank": x["rank"],
                                 "draft_year": x["draft_year"], "pick": x["pick"]} for x in rec["fast"]],
        "one_list_players": [{"player": n, "list": r["id"], "rank": e["rank"]} for n, r, e in rec["one_list"]],
        "same_team_duos": [{"players": [d["a"], d["b"]], "team": d["team"], "lists": [r["id"] for r in d["lists"]]} for d in rec["duos"]],
    })
    write_json("popularity-vs-production.json", {
        "seasons": {s: {"list": v["release"]["id"], "rows": v["rows"], "no_production_rank": v["unranked"], "top10_not_listed": v["top10_missing"]} for s, v in pvp.items()},
        "career": career})
    # Bar chart race: wide format (first column = date, one column per player, cumulative points).
    us_rel = [r for r in releases if r["us"] and r["entries"]]
    names = sorted([p for p in players.values() if p["points"]], key=lambda p: (-p["points"], p["name"]))
    cum = {p["name"]: 0 for p in names}
    with open(os.path.join(DOCS, "data", "jersey-points-race.csv"), "w", encoding="utf-8", newline="") as f:
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
    teams = cross.teams
    teams_by_slug = {t["slug"]: t for t in teams}
    players, us, nonempty_us = build_model(releases, teams, report)
    model = (releases, us, nonempty_us, teams_by_slug, cross)

    # Clean generated output (keep docs/assets, which is hand-written).
    for d in ("player", "team", "season", "records", "data"):
        shutil.rmtree(os.path.join(DOCS, d), ignore_errors=True)
    site = Site(args.today)
    pvp, career = popularity_vs_production(releases, cross)
    rec = compute_records(players, releases, us, nonempty_us, cross)

    for p in players.values():
        build_player_page(site, p, Ctx("../../", players, teams_by_slug, cross), model)
    team_entries = defaultdict(list)
    for r in releases:
        for e in r["entries"]:
            for s in e["team_slugs"]:
                team_entries[s].append((r, e))
    team_summ = [build_team_page(site, t, Ctx("../../", players, teams_by_slug, cross), model, team_entries) for t in teams]
    first_season_of = {}
    for r in us:
        for e in r["entries"]:
            first_season_of.setdefault(e["player"], r["season"])
    season_summ = [build_season_page(site, s, Ctx("../../", players, teams_by_slug, cross), model, pvp, first_season_of)
                   for s in sorted({r["season"] for r in releases})]
    build_records(site, Ctx("../", players, teams_by_slug, cross), rec, model)
    build_hub(site, Ctx("", players, teams_by_slug, cross), model, players, team_summ, season_summ, pvp, career, len(releases))
    export_data(releases, players, cross, teams_by_slug, rec, pvp, career)
    site.write()
    open(os.path.join(DOCS, ".nojekyll"), "w").close()

    report["counts"] = {
        "releases_total": len(releases), "releases_with_entries": sum(1 for r in releases if r["entries"]),
        "us_lists_with_entries": sum(1 for r in us if r["entries"]), "players": len(players),
        "player_pages_indexable": sum(1 for p in players.values() if p["indexable"]),
        "player_pages_noindex": sum(1 for p in players.values() if not p["indexable"]),
        "team_pages": len(team_summ), "team_pages_noindex": [t["slug"] for t in team_summ if not t["indexable"]],
        "season_pages": len(season_summ), "season_pages_noindex": [s["season"] for s in season_summ if not s["indexable"]],
        "sitemap_urls": sum(1 for x in site.pages if x[2]),
        "release_sources_found": sum(1 for r in releases if r["source"]),
        "release_sources_blank": [r["id"] for r in releases if not r["source"] and r["entries"]],
    }
    report["crossover_misses"] = cross.misses
    report["salary_mismatches_left_out"] = sorted(["%s %s" % k for k in cross.sal_bad])
    report["polymarket_mvp_seasons"] = cross.poly.get("mvp_seasons_covered", {})
    with open(os.path.join(DATA, "build_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(json.dumps(report["counts"], indent=1))
    print("grid mismatches:", len(report["grid_vs_player_ranks"]["mismatches"]))


if __name__ == "__main__":
    main()
