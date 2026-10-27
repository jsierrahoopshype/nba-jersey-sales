"""Finds every player or team name rendered in the jersey section's HTML that is not a link to its page.

A name counts as linked when it sits inside an <a> whose href resolves to:
  - a jersey player: https://hoopsmatic.com/jerseys/player/<slug>/
  - a team (current, historical or published name): https://hoopsmatic.com/jerseys/team/<franchise slug>/
  - a player with no jersey page: the confirmed page in data/external_player_links.json
Not checked (not shown as text, or not a place for a link): <title>, <script>, <style>, <svg> (chart
tooltips), <option>, <select>, visually hidden labels (.vh), the page's own <h1>, the current
breadcrumb, and a page's mentions of its own player or team.
"""
import json
import os
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = "https://hoopsmatic.com/jerseys/"
SKIP = {"title", "script", "style", "svg", "option", "select", "h1"}
VOID = {"img", "br", "meta", "link", "input", "hr", "source", "wbr"}


def name_index():
    """name -> expected absolute URL (None = confirmed no page anywhere: plain text allowed)."""
    D = os.path.join(ROOT, "docs", "data")
    idx = {}
    for p in json.load(open(os.path.join(D, "players.json"), encoding="utf-8")):
        idx[p["name"]] = SITE + "player/%s/" % p["slug"]
    for t in json.load(open(os.path.join(ROOT, "data", "crossover", "teams.json"), encoding="utf-8")):
        for n in [t["full_name"]] + t["historical_names"]:
            idx.setdefault(n, SITE + "team/%s/" % t["slug"])
    for r in json.load(open(os.path.join(D, "releases.json"), encoding="utf-8"))["releases"]:
        for e in r["entries"]:
            for nm, sl in zip(e["tn"], e["ts"]):
                idx.setdefault(nm, SITE + "team/%s/" % sl)
    ext = json.load(open(os.path.join(ROOT, "data", "external_player_links.json"), encoding="utf-8"))
    for n, url in ext["links"].items():
        idx.setdefault(n, url)
    for n in ext["no_page"]:
        idx.setdefault(n, None)
    # Award-only players not yet resolved still count as names to check.
    av = json.load(open(os.path.join(ROOT, "data", "crossover", "award_votes.json"), encoding="utf-8"))
    for season in av["seasons"].values():
        for n in season:
            idx.setdefault(n, "")
    return idx


class _Walker(HTMLParser):
    def __init__(self, page_url, pattern, idx, self_urls):
        super().__init__(convert_charrefs=True)
        self.page_url, self.pattern, self.idx, self.self_urls = page_url, pattern, idx, self_urls
        self.stack = []  # (tag, attrs)
        self.bad = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append((tag, dict(attrs)))

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if not data.strip():
            return
        href = None
        for tag, a in self.stack:
            if tag in SKIP or "vh" in (a.get("class") or "").split() or a.get("aria-current"):
                return
            if tag == "a":
                href = a.get("href")
        for m in self.pattern.finditer(data):
            name = m.group(0)
            want = self.idx.get(name)
            if want is None:
                continue  # confirmed: no page anywhere
            if want in self.self_urls:
                continue  # the page's own player or team
            got = urljoin(self.page_url, href.split("?")[0].split("#")[0]) if href else None
            if got != want:
                self.bad.append((name, got, data.strip()[:90]))


def unlinked(pages):
    """pages: {url: html}. Returns [(url, name, href or None, context)]."""
    idx = name_index()
    names = sorted(idx, key=len, reverse=True)
    pattern = re.compile(r"(?<![\w])(%s)(?![\w])" % "|".join(re.escape(n) for n in names))
    out = []
    for url, h in pages.items():
        own = set()
        mp = re.match(re.escape(SITE) + r"(player|team)/([^/]+)/$", url)
        if mp:
            own.add(url)
        w = _Walker(url, pattern, idx, own)
        w.feed(h)
        out += [(url,) + b for b in w.bad]
    return out
