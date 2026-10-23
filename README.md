# nba-jersey-sales

NBA jersey sales rankings, 2001-2026. Static site in `docs/` (GitHub Pages), served at https://hoopsmatic.com/jerseys/.

## Rebuild

```
python scripts/build_jerseys.py
python -m unittest discover tests
```

No API keys, no network. The build reads only committed files:

| Path | What |
| --- | --- |
| `data/source/` | The two source CSVs, unchanged. Player ranks is the data; the Grid is only a cross-check. |
| `data/release_sources.csv` | Release ID -> official NBA/NBPA announcement. Blank when no page was opened and confirmed. |
| `data/crossover/` | Snapshot of other HoopsMatic datasets (headshots, awards, stats, salaries, Career Map, attendance). |
| `data/player_aliases.json` | Same person under another name in a sibling repo (confirmed by NBA id). |
| `data/page_hashes.json` | Content hash and lastmod per URL. A sitemap date moves only when that page changes. |

Outputs: `docs/` pages, `docs/data/*.json`, `docs/data/jersey-points-race.csv` (bar chart race input), `docs/sitemap.xml`, and `data/build_report.json` (grid cross-check, counts, crossover misses).

## New lists

1. Replace the CSVs in `data/source/` with the new export.
2. Add the release's official URL to `data/release_sources.csv` once confirmed.
3. Optional: refresh the crossover snapshot with local clones of the sibling repos:
   `python scripts/snapshot_crossover.py --repos /path/to/clones`
4. Rebuild, run the tests, commit.

## Rules the build follows

- U.S. lists are every list except Europe only, Philippines only and the 2013 worldwide release. All U.S. numbers use U.S. lists only.
- Jersey points = 11 minus rank, top 10 only, U.S. lists only.
- Player pages are indexable only with 3+ U.S. lists; the rest carry `noindex, follow` and stay out of the sitemap. Season pages with only a No. 1 and the team page with no ranked player are noindex too.
- All page text is a template filled from data.
