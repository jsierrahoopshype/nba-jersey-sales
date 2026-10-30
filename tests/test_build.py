"""Checks on the built jersey section. Run: python -m unittest discover tests"""
import csv
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import namecheck  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
SITE = "https://hoopsmatic.com/jerseys/"
FOOTER_TEXT = ["Anything wrong? Email us at hoopsmatic@hoopsmatic.com",
               "For any other inquiry, contact us at ads@hoopsmatic.com"]


def pages():
    out = {}
    for p in glob.glob(os.path.join(DOCS, "**", "index.html"), recursive=True):
        rel = os.path.relpath(os.path.dirname(p), DOCS)
        url = SITE if rel == "." else SITE + rel.replace(os.sep, "/") + "/"
        out[url] = open(p, encoding="utf-8").read()
    return out


def meta(html, attr, key):
    m = re.search(r'<meta %s="%s" content="([^"]*)"' % (attr, re.escape(key)), html)
    return m.group(1) if m else None


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = pages()
        cls.sitemap = re.findall(r"<loc>([^<]+)</loc>", open(os.path.join(DOCS, "sitemap.xml")).read())
        cls.players = json.load(open(os.path.join(DOCS, "data", "players.json")))
        cls.report = json.load(open(os.path.join(ROOT, "data", "build_report.json")))
        cls.seasons = json.load(open(os.path.join(DOCS, "data", "season-rankings.json")))

    def test_source_rows(self):
        with open(glob.glob(os.path.join(ROOT, "data", "source", "*Player ranks.csv"))[0], encoding="utf-8-sig") as f:
            self.assertEqual(len(list(csv.DictReader(f))), 768)

    def test_titles_descriptions_unique(self):
        titles, descs = {}, {}
        for url, h in self.pages.items():
            t = re.search(r"<title>([^<]+)</title>", h).group(1)
            self.assertTrue(t.endswith("| HoopsMatic"), url)
            self.assertNotIn(t, titles, "duplicate title %s" % url)
            titles[t] = url
            d = meta(h, "name", "description")
            self.assertTrue(d, url)
            self.assertNotIn(d, descs, "duplicate description %s" % url)
            descs[d] = url

    def test_canonical_and_og(self):
        for url, h in self.pages.items():
            can = re.search(r'<link rel="canonical" href="([^"]+)"', h).group(1)
            self.assertEqual(can, url)
            self.assertEqual(meta(h, "property", "og:url"), url)
            self.assertNotIn("github.io/nba-jersey-sales", can)

    def test_footer_exact(self):
        for url, h in self.pages.items():
            foot = re.search(r'<footer class="site-foot">(.*?)</footer>', h, re.S).group(1)
            lines = [re.sub(r"<[^>]+>", "", x) for x in re.findall(r"<p>(.*?)</p>", foot)]
            self.assertEqual(lines, FOOTER_TEXT, url)

    def test_no_dashes_or_banned_text(self):
        for path in glob.glob(os.path.join(DOCS, "**", "*.*"), recursive=True):
            if path.endswith((".html", ".json", ".csv", ".xml", ".js", ".css")):
                s = open(path, encoding="utf-8").read()
                self.assertNotIn("—", s, path)
                self.assertNotIn("–", s, path)
                self.assertNotIn("Data as of", s, path)
        for url, h in self.pages.items():
            head = h.split("<body", 1)[0] + "".join(re.findall(r"<h1>.*?</h1>", h, re.S))
            self.assertNotIn("HoopsHype", re.sub(r"<[^>]+>", "", head), url)

    def test_images_are_headshots_only(self):
        for url, h in self.pages.items():
            for src in re.findall(r'<img[^>]+src="([^"]+)"', h):
                self.assertTrue(src.startswith("https://jsierrahoopshype.github.io/nba-headshots/"), src)

    def test_index_rules_and_sitemap(self):
        sm = set(self.sitemap)
        self.assertEqual(len(sm), len(self.sitemap))
        for url, h in self.pages.items():
            noindex = '<meta name="robots" content="noindex, follow">' in h
            self.assertEqual(url in sm, not noindex, url)
        for p in self.players:
            url = SITE + "player/%s/" % p["slug"]
            self.assertIn(url, self.pages)
            self.assertEqual(url in sm, p["us_lists"] >= 3, url)
        for must in (SITE, SITE + "records/"):
            self.assertIn(must, sm)
        self.assertEqual(len([u for u in self.pages if "/team/" in u]), 30)

    def test_every_page_linked_from_hub(self):
        hub = self.pages[SITE]
        for url in self.pages:
            if url == SITE:
                continue
            self.assertIn('href="%s"' % url[len(SITE):], hub, url)

    def test_race_csvs(self):
        for fn, key, n_rows in (("jersey-points-race.csv", "points", None),
                                ("jersey-points-race-all-lists.csv", "list_points", self.report["counts"]["us_lists_with_entries"])):
            with open(os.path.join(DOCS, "data", fn), encoding="utf-8") as f:
                rows = list(csv.reader(f))
            self.assertEqual(rows[0][0], "date")
            if n_rows:
                self.assertEqual(len(rows) - 1, n_rows, fn)
            else:
                self.assertEqual(len(rows) - 1, len(self.seasons), fn)
            for j in range(1, len(rows[0])):
                col = [int(r[j]) for r in rows[1:]]
                self.assertEqual(col, sorted(col), rows[0][j])
            total = {rows[0][j]: int(rows[-1][j]) for j in range(1, len(rows[0]))}
            for p in self.players:
                if p[key]:
                    self.assertEqual(total[p["name"]], p[key], (fn, p["name"]))
        with open(os.path.join(DOCS, "data", "jersey-points-race.csv"), encoding="utf-8") as f:
            self.assertIn("Amar'e Stoudemire", f.readline())

    def test_season_counts_match_rankings(self):
        for p in self.players:
            pos = list(p["s"].values())
            self.assertEqual(p["no1_seasons"], sum(1 for x in pos if x == 1), p["name"])
            self.assertEqual(p["top5_seasons"], sum(1 for x in pos if x <= 5), p["name"])
            self.assertEqual(p["top10_seasons"], sum(1 for x in pos if x <= 10), p["name"])
            self.assertEqual(p["points"], sum(11 - x for x in pos if x <= 10), p["name"])
        for s, v in self.seasons.items():
            self.assertTrue(all(r["pos"] <= v["depth"] for r in v["rows"]), s)

    def test_2013_list_is_us(self):
        rel = {r["id"]: r for r in json.load(open(os.path.join(DOCS, "data", "releases.json")))["releases"]}
        self.assertTrue(rel["R23"]["us"])
        self.assertIn("R23", self.seasons["2012-13"]["lists"])
        self.assertEqual(self.seasons["2012-13"]["depth"], 10)
        self.assertEqual(sorted(r["type"] for r in rel.values() if not r["us"]), ["Europe only", "Philippines only"])
        for r in rel.values():
            if not r["us"]:
                self.assertTrue(all(e["m"] is None for e in r["entries"]))
                self.assertIsNone(r["compare"])

    def test_og_image_every_page(self):
        for url, h in self.pages.items():
            img = meta(h, "property", "og:image")
            self.assertTrue(img and img.startswith(SITE + "share/") and img.endswith(".png"), url)
            self.assertEqual(meta(h, "property", "og:image:width"), "1200", url)
            self.assertEqual(meta(h, "property", "og:image:height"), "630", url)
            self.assertTrue(meta(h, "property", "og:image:alt"), url)
            self.assertEqual(meta(h, "name", "twitter:card"), "summary_large_image", url)
            path = os.path.join(DOCS, img[len(SITE):])
            self.assertTrue(os.path.exists(path), path)
            self.assertLess(os.path.getsize(path), 300 * 1024, path)
            with Image.open(path) as im:
                self.assertEqual(im.size, (1200, 630), path)
        shares = glob.glob(os.path.join(DOCS, "share", "**", "*.png"), recursive=True)
        self.assertEqual(len(shares), len(self.pages))

    def test_hub_title_and_order(self):
        h = self.pages[SITE]
        self.assertTrue(re.search(r"<title>NBA Jersey Sales Rankings", h))
        order = ['class="stories"', 'id="latest"', 'id="ruled"', 'id="find-section"', 'id="time-machine"', 'id="pva"', 'id="methodology"']
        pos = [h.index(x) for x in order]
        self.assertEqual(pos, sorted(pos))
        self.assertNotIn("Popularity vs. production", h)

    def test_award_rank_wording(self):
        for url, h in self.pages.items():
            text = re.sub(r"<[^>]+>", " ", h).lower()
            self.assertNotIn("vote share", text, url)
            self.assertNotIn("share plus", text, url)
        hub = self.pages[SITE]
        meth = hub[hub.index('id="methodology"'):]
        meth = meth[:meth.index("</section>")]
        self.assertIn("All-NBA team, then MVP voting place", meth)
        self.assertIn("All-NBA team, then MVP voting place", hub[hub.index('id="pva"'):])
        season = self.pages[SITE + "season/2015-16/"]
        self.assertIn("All-NBA team, then MVP voting place", season)

    def test_moves_exact_when_both_ranks_known(self):
        rel = {r["id"]: r for r in json.load(open(os.path.join(DOCS, "data", "releases.json")))["releases"]}
        # Apr 2013 (15 deep) to Oct 2013 (10 deep): every player on both lists has an exact move.
        prev = {e["p"]: e["r"] for e in rel["R22"]["entries"]}
        for e in rel["R23"]["entries"]:
            if e["p"] in prev:
                self.assertIn(e["m"]["kind"], ("up", "down", "same"), e["p"])
                self.assertEqual(e["m"]["from"], prev[e["p"]], e["p"])
        cur = {e["p"] for e in rel["R23"]["entries"]}
        self.assertEqual(sorted(d["p"] for d in rel["R23"]["not_compared"]),
                         sorted(p for p, r in prev.items() if p not in cur and r > 10))
        self.assertTrue(all(d["r"] <= 10 for d in rel["R23"]["dropped"]))
        for r in rel.values():
            if r["us"] and r["compare"]:
                self.assertTrue(all(e["m"]["kind"] != "none" for e in r["entries"]), r["id"])

    def test_index_pages(self):
        sm = set(self.sitemap)
        for path, n in (("teams/", 30), ("seasons/", len(self.seasons))):
            url = SITE + path
            h = self.pages[url]
            self.assertIn(url, sm)
            self.assertIn('class="crumbs"', h)
            self.assertIn('"@type": "BreadcrumbList"', h)
            self.assertEqual(len(re.findall(r'href="\.\./%s/[^"/]+/"' % ("team" if path == "teams/" else "season"), h)) >= n, True, url)
        self.assertEqual(self.pages[SITE + "teams/"].count('class="tcard"'), 30)
        hub = self.pages[SITE]
        for anchor, link in (('id="teams"', 'href="teams/"'), ('id="seasons"', 'href="seasons/"')):
            sec = hub[hub.index(anchor):]
            sec = sec[:sec.index("</section>")]
            self.assertIn(link, sec)
            self.assertLessEqual(sec.count("<tr>") + sec.count("<tr "), 6)
        for url, h in self.pages.items():
            if "/team/" in url or "/season/" in url:
                self.assertIn('href="../../teams/"', h, url)
                self.assertIn('href="../../seasons/"', h, url)

    def test_pva_table_columns(self):
        for url in (SITE, SITE + "season/2015-16/"):
            h = self.pages[url]
            sec = h[h.index("Popularity vs. awards"):]
            heads = re.findall(r"<th[^>]*>([^<]*)</th>", sec[:sec.index("</thead>")])
            self.assertEqual(heads, ["Player", "Jersey", "Awards", "Gap"], url)
            body = sec[:sec.index("</table>")]
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body)[1:]:
                cells = re.findall(r"<td[^>]*>(.*?)</td>", row)
                self.assertRegex(cells[1], r"^No\. \d+$")
                self.assertRegex(re.sub(r"<[^>]+>", "", cells[2]), r"^(No\. \d+|-)$")
                self.assertIn('class="face"', cells[0])
                self.assertIn('class="tline sub3"', cells[0])
                self.assertNotIn("No award votes", cells[2])

    def test_names_are_name_links(self):
        h = self.pages[SITE + "season/2015-16/"]
        self.assertIn('<a class="nm" href="../../player/stephen-curry/">Stephen Curry</a>', h)
        self.assertIn('<a class="nm" href="../../team/golden-state-warriors/">', h)

    def test_every_name_is_a_link(self):
        """Every rendered player or team name links to its own page (see tests/namecheck.py for what is
        skipped). The only names allowed as plain text are the confirmed no-page players."""
        bad = namecheck.unlinked(self.pages)
        self.assertEqual(bad, [], "\n".join("%s: %s -> %s (%s)" % b for b in bad[:30]))
        ext = json.load(open(os.path.join(ROOT, "data", "external_player_links.json"), encoding="utf-8"))
        for n, url in ext["links"].items():
            self.assertTrue(url.startswith("https://hoopsmatic.com/") and "/jerseys/" not in url, n)
        # The checker itself must catch an unlinked name.
        probe = {SITE + "records/": "<p>LeBron James and the Seattle SuperSonics</p>"}
        self.assertEqual(sorted(x[1] for x in namecheck.unlinked(probe)), ["LeBron James", "Seattle SuperSonics"])

    def test_list_columns_as_of_date(self):
        """Trend, Streak, Lists and Best use only lists up to that list's date."""
        rel = json.load(open(os.path.join(DOCS, "data", "releases.json")))["releases"]
        byid = {r["id"]: r for r in rel}
        ranks = {p["name"]: p["l"] for p in self.players}
        nonempty_us = [r for r in rel if r["us"] and r["status"] != "empty"]
        # Apr 2013 (R22): Stephen Curry had no U.S. list yet; his first is Dec 2013 (R24, No. 6).
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import build_jerseys as B
        import jersey_model as JM
        rep = {}
        m = B.build_model(B.load_releases(rep), B.Cross().teams, B.Cross(), rep)
        r22 = m.byid["R22"]
        x = JM.player_as_of(JM.history_as_of(m.us, r22["date"]), "Stephen Curry")
        self.assertEqual((x["lists"], x["best"], x["streak"]), (0, None, 0))
        x = JM.player_as_of(JM.history_as_of(m.us, m.byid["R24"]["date"]), "Stephen Curry")
        self.assertEqual((x["lists"], x["best"], x["best_year"], x["streak"]), (1, 6, "2013", 1))
        # Every row of every list: Lists and Best equal a count over earlier lists only.
        for r in rel:
            for e in r["entries"]:
                mine = {k: v for k, v in ranks.get(e["p"], {}).items()}
                upto = [q["id"] for q in rel if q["us"] and q["status"] != "empty" and q["id"] in mine and
                        m.byid[q["id"]]["date"] <= m.byid[r["id"]]["date"]]
                self.assertEqual(e["x"]["ls"], len(upto), (r["id"], e["p"]))
                self.assertEqual(e["x"]["b"], min((mine[q] for q in upto), default=None), (r["id"], e["p"]))
        # The rendered Apr 2013 table: LeBron James's Lists cell counts lists up to Apr 9, 2013.
        h = self.pages[SITE + "season/2012-13/"]
        card = h[h.index("End of regular season</h3>"):]
        row = re.search(r'<tr class="xr[^"]*">(.*?)</tr>', card[card.index("player/lebron-james/") - 600:], re.S).group(1)
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        n_upto = sum(1 for q in nonempty_us if q["id"] in ranks["LeBron James"] and m.byid[q["id"]]["date"] <= r22["date"])
        self.assertEqual(int(cells[4]), n_upto)
        self.assertLess(n_upto, len(ranks["LeBron James"]))

    def test_sparkline_labels(self):
        rel = json.load(open(os.path.join(DOCS, "data", "releases.json")))["releases"]
        ranks = {p["name"]: p["l"] for p in self.players}
        nonempty_us = [r for r in rel if r["us"] and r["status"] != "empty"]
        latest = [r for r in nonempty_us if r["status"] == "full"][-1]
        last8 = nonempty_us[-8:]
        want = "Rank over last 8 lists: " + ", ".join(
            str(ranks["Stephen Curry"][q["id"]]) if q["id"] in ranks["Stephen Curry"] else "out" for q in last8)
        self.assertIn('aria-label="%s"' % want, self.pages[SITE])
        # Early lists: fewer than 8 slots, "unknown" on a No. 1 only list without the player.
        h = self.pages[SITE + "season/2002-03/"]
        self.assertIn('aria-label="Rank over last 3 lists: unknown, unknown, 2"', h)  # Allen Iverson, Apr 2003
        for url, page in self.pages.items():
            for lab in re.findall(r'<svg class="spark"[^>]*aria-label="([^"]+)"', page):
                self.assertRegex(lab, r"^Rank over last (list|\d lists): ((\d+|out|unknown)(, |$))+$")

    def test_no_eyebrow_or_sales_volume_language(self):
        for url, h in self.pages.items():
            self.assertNotIn('class="eyebrow"', h, url)
            self.assertNotIn('class="kicker"', h, url)
            self.assertNotIn("units sold</", h, url)

    def test_grid_matches(self):
        self.assertEqual(self.report["grid_vs_player_ranks"]["mismatches"], [])

    def test_lastmod_stable_on_rebuild(self):
        hashes = os.path.join(ROOT, "data", "page_hashes.json")
        before = json.load(open(hashes))
        backup = tempfile.mktemp()
        shutil.copy(hashes, backup)
        try:
            subprocess.check_call([sys.executable, os.path.join(ROOT, "scripts", "build_jerseys.py"), "--today", "2099-01-01"],
                                  stdout=subprocess.DEVNULL)
            after = json.load(open(hashes))
            self.assertEqual(before, after, "a rebuild with no content change moved a lastmod (or docs are stale: rebuild)")
            rep = json.load(open(os.path.join(ROOT, "data", "build_report.json")))
            self.assertEqual(rep["counts"]["share_images_rendered"], 0, "a rebuild with no content change redrew a share image")
        finally:
            shutil.copy(backup, hashes)
            subprocess.check_call([sys.executable, os.path.join(ROOT, "scripts", "build_jerseys.py")], stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
