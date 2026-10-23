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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
SITE = "https://hoopsmatic.com/jerseys/"
FOOTER_TEXT = ["Anything wrong? Email us at hoopsmatic@hoopsmatic.com",
               "For business inquiries, contact us at ads@hoopsmatic.com"]


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

    def test_race_csv(self):
        with open(os.path.join(DOCS, "data", "jersey-points-race.csv"), encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0][0], "date")
        self.assertEqual(len(rows) - 1, self.report["counts"]["us_lists_with_entries"])
        for j in range(1, len(rows[0])):
            col = [int(r[j]) for r in rows[1:]]
            self.assertEqual(col, sorted(col), rows[0][j])
        total = {rows[0][j]: int(rows[-1][j]) for j in range(1, len(rows[0]))}
        for p in self.players:
            if p["points"]:
                self.assertEqual(total[p["name"]], p["points"], p["name"])

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
        finally:
            shutil.copy(backup, hashes)
            subprocess.check_call([sys.executable, os.path.join(ROOT, "scripts", "build_jerseys.py")], stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
