"""Ranking rules for the jersey section, kept free of HTML so the tests can call them directly.

Terms used everywhere:
  complete list   a U.S. list with more than a No. 1 in the source (status "full")
  partial list    a U.S. list with only the No. 1 (2001, 2002, Jun 2006)
  empty list      a list with no entries (Jan 2005, from the grid only)
  eligible list   a complete U.S. list; moves are computed only between adjacent eligible lists
"""
from collections import OrderedDict


# --------------------------------------------------------------------------
# Movement between adjacent eligible U.S. lists
# --------------------------------------------------------------------------

def compute_moves(us_lists):
    """Set e["move"] on every entry of the U.S. lists (in date order) and r["dropped"], r["not_compared"],
    r["compare_id"], r["compare_len"].

    A move is computed only between adjacent eligible lists. A player on both lists gets the exact
    move, whatever the two lengths (No. 12 on a 15-player list to No. 8 on a 10-player list = up 4).
    A player on this list and absent from the previous one is a re-entry, or a first appearance if
    never on any earlier U.S. list. A player on the previous list and absent from this one dropped
    out when the previous rank is within this list's length; when it was deeper (No. 12, then absent
    from a 10-player list) the player is not compared.
    """
    seen = set()
    prev = None
    for r in us_lists:
        r["compare_id"], r["compare_len"], r["dropped"], r["not_compared"] = None, None, [], []
        if r["status"] == "full" and prev is not None:
            r["compare_id"], r["compare_len"] = prev["id"], min(prev["n"], r["n"])
            pm = {x["player"]: x["rank"] for x in prev["entries"]}
            cur = {x["player"] for x in r["entries"]}
            for e in r["entries"]:
                p = e["player"]
                if p in pm:
                    d = pm[p] - e["rank"]
                    e["move"] = {"kind": "up" if d > 0 else ("down" if d < 0 else "same"), "delta": abs(d), "vs": prev["id"], "from": pm[p]}
                else:
                    e["move"] = {"kind": "back" if p in seen else "new", "delta": 0, "vs": prev["id"]}
            gone = [x for x in prev["entries"] if x["player"] not in cur]
            r["dropped"] = [x for x in gone if x["rank"] <= r["n"]]
            r["not_compared"] = [x for x in gone if x["rank"] > r["n"]]
        else:
            for e in r["entries"]:
                if e["player"] not in seen:
                    e["move"] = {"kind": "new", "delta": 0, "vs": None}
                else:
                    e["move"] = {"kind": "none", "delta": 0, "vs": None,
                                 "why": "no earlier complete U.S. list to compare" if r["status"] == "full" else "only the No. 1 is known"}
        for e in r["entries"]:
            seen.add(e["player"])
        if r["status"] == "full":
            prev = r


# --------------------------------------------------------------------------
# Season ranking: one aggregated U.S. ranking per season
# --------------------------------------------------------------------------

def season_ranking(season, lists):
    """lists: every U.S. list of the season, in date order.

    Complete lists feed the ranking. Each player's rank is averaged across them; a player missing
    from a list counts as one spot below that list's last place. The ranking goes only as deep as
    the shortest complete list. Ties: better single best rank, then better rank on the later list
    (latest first); still tied = shared position. With no complete list, a partial list gives only
    the No. 1. Empty lists are skipped.
    """
    complete = [r for r in lists if r["status"] == "full"]
    partial = [r for r in lists if r["status"] == "partial"]
    empty = [r for r in lists if r["status"] == "empty"]
    out = {"season": season, "fed": [], "skipped": [], "depth": 0, "rows": [], "kind": None}
    for r in empty:
        out["skipped"].append((r, "empty list"))
    if complete:
        out["kind"] = "full"
        out["fed"] = complete
        for r in partial:
            out["skipped"].append((r, "only the No. 1 is known; the complete lists decide this season"))
        depth = min(r["n"] for r in complete)
        out["depth"] = depth
        names = OrderedDict()
        for r in complete:
            for e in r["entries"]:
                names.setdefault(e["player"], None)
        rows = []
        for p in names:
            ranks, on, last_e = [], [], None
            for r in complete:
                e = next((x for x in r["entries"] if x["player"] == p), None)
                ranks.append(e["rank"] if e else r["n"] + 1)
                on.append(e is not None)
                if e:
                    last_e = e
            rows.append({"player": p, "ranks": ranks, "on": on, "avg": sum(ranks) / len(ranks),
                         "best": min(k for k, o in zip(ranks, on) if o), "entry": last_e})
        for row in rows:
            row["key"] = (round(row["avg"], 9), row["best"]) + tuple(reversed(row["ranks"]))
        rows.sort(key=lambda x: (x["key"], x["player"]))
        pos = 0
        for i, row in enumerate(rows):
            if i == 0 or row["key"] != rows[i - 1]["key"]:
                pos = i + 1
            row["pos"] = pos
            row["shared"] = False
        for i, row in enumerate(rows):
            if (i > 0 and rows[i - 1]["pos"] == row["pos"]) or (i + 1 < len(rows) and rows[i + 1]["pos"] == row["pos"]):
                row["shared"] = True
        out["all_rows"] = rows
        out["rows"] = [x for x in rows if x["pos"] <= depth]
    elif partial:
        out["kind"] = "no1"
        r = partial[-1]
        out["fed"] = [r]
        for x in partial[:-1]:
            out["skipped"].append((x, "an earlier No. 1 only list"))
        out["depth"] = 1
        e = r["entries"][0]
        out["rows"] = [{"player": e["player"], "ranks": [1], "on": [True], "avg": 1.0, "best": 1, "entry": e, "pos": 1, "shared": False}]
        out["all_rows"] = out["rows"]
    return out


def season_points(pos):
    return 11 - pos if pos and pos <= 10 else 0


# --------------------------------------------------------------------------
# Awards: MVP vote share + All-NBA (1st = 3, 2nd = 2, 3rd = 1), ranked league-wide
# --------------------------------------------------------------------------

def award_ranking(season_votes):
    """season_votes: {player: {"all_nba": 1|2|3|None, "mvp_rank": int|None}} for one season.

    Award rank: All-NBA team (1st, then 2nd, then 3rd, then none), then MVP voting place; equal
    keys share a position. Players with neither are not ranked ("No award votes").
    """
    rows = []
    for p, v in season_votes.items():
        pts = {1: 3, 2: 2, 3: 1}.get(v.get("all_nba"), 0)
        mvp = v.get("mvp_rank")
        if not pts and not mvp:
            continue
        rows.append({"player": p, "all_nba": v.get("all_nba"), "all_nba_pts": pts, "mvp_rank": mvp,
                     "key": (-pts, mvp if mvp else 10 ** 6)})
    rows.sort(key=lambda x: (x["key"], x["player"]))
    pos = 0
    for i, x in enumerate(rows):
        if i == 0 or x["key"] != rows[i - 1]["key"]:
            pos = i + 1
        x["rank"] = pos
    return {x["player"]: x for x in rows}, rows


# --------------------------------------------------------------------------
# Droughts: only complete lists can create one
# --------------------------------------------------------------------------

def longest_drought(us_lists, has_player):
    """Longest run of consecutive complete U.S. lists with no ranked player.

    has_player(r) -> bool. A partial list with a player ends a run; a partial list without one and an
    empty list are skipped (they neither create nor extend a drought).
    Returns (length, first list, last list).
    """
    best, cur, start = (0, None, None), 0, None
    for r in us_lists:
        if r["status"] == "empty":
            continue
        if has_player(r):
            cur = 0
            continue
        if r["status"] != "full":
            continue
        if cur == 0:
            start = r
        cur += 1
        if cur > best[0]:
            best = (cur, start, r)
    return best
