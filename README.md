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
| `data/crossover/` | Snapshots of other HoopsMatic datasets (headshots, awards, award votes, seasons played, salaries, Career Map, attendance, team logos). |
| `data/share/headshots/` | Face-tight headshots for the share cards (from nba-headshots). |
| `data/player_aliases.json` | Same person under another name in a sibling repo (confirmed by NBA id). |
| `data/page_hashes.json` | Content hash and lastmod per URL. A sitemap date moves only when that page changes. |
| `data/share_hashes.json` | Content hash per share image. A card is redrawn only when its hash changes. |

Code: `scripts/build_jerseys.py` (pages and exports), `scripts/jersey_model.py` (ranking rules, unit-tested in `tests/test_rules.py`), `scripts/share_images.py` (1200x630 PNG cards, Pillow, Inter font in `scripts/fonts/`, OFL).

Outputs: `docs/` pages (hub, records, /teams/ and /seasons/ index pages, player, team and season pages), `docs/data/*.json`, `docs/data/jersey-points-race.csv` (season ranking points, bar chart race input), `docs/data/jersey-points-race-all-lists.csv` (every-list points), `docs/share/**.png` (og:image per page), `docs/sitemap.xml`, and `data/build_report.json` (grid cross-check, counts, season rankings, award coverage, crossover misses).

## New lists

1. Replace the CSVs in `data/source/` with the new export.
2. Add the release's official URL to `data/release_sources.csv` once confirmed.
3. Optional: refresh the crossover snapshots with local clones of the sibling repos:
   `python scripts/snapshot_crossover.py --repos /path/to/clones` and
   `python scripts/snapshot_extra.py --repos /path/to/clones` (nba-player-data, nba-headshots).
4. Rebuild, run the tests, commit. Bump `TEMPLATE` in `share_images.py` whenever the card layout changes.

## Rules the build follows

- U.S. lists are every list except Europe only and Philippines only. The Oct 3, 2013 list is the NBA release's U.S. top 10 for the full 2012-13 season (checked against pr.nba.com) and counts as a U.S. list.
- Season ranking (main measure): average rank across the season's complete U.S. lists; missing from a list = one spot below its last place; depth = the shortest complete list; ties by best single rank, then the later list, else shared. No. 1 only lists (2001, 2002) give only the No. 1. The empty Jan 2005 list is skipped.
- No. 1 / Top 5 / Top 10 seasons are the headline counts; list counts sit next to them, labeled.
- Jersey ranking points = 11 minus season rank, top 10 only. Every-list points use the same scoring on each U.S. list.
- Change is computed only between adjacent complete U.S. lists: the exact move for a player on both; a player missing from the next list dropped out only if the previous rank fits inside it, otherwise not compared.
- Popularity vs. awards: league-wide award rank per season, All-NBA team, then MVP voting place.
- Player pages are indexable only with 3+ U.S. lists; the rest carry `noindex, follow` and stay out of the sitemap. Season pages with only a No. 1 and the team page with no ranked player are noindex too.
- All page text is a template filled from data.
