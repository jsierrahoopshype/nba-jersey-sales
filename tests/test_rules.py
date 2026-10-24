"""Unit checks on the ranking rules in scripts/jersey_model.py. Run: python -m unittest discover tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import jersey_model as M  # noqa: E402


def lst(rid, names, status=None, season="2020-21", us=True):
    entries = [{"rank": i + 1, "player": n} for i, n in enumerate(names)]
    st = status or ("empty" if not names else ("partial" if len(names) == 1 else "full"))
    return {"id": rid, "entries": entries, "n": len(entries), "status": st, "season": season, "us": us}


class SeasonRanking(unittest.TestCase):
    def test_absent_counts_one_below_last_and_depth_is_shortest(self):
        a = lst("A", ["P%d" % i for i in range(1, 16)])            # 15-list
        b = lst("B", ["P2", "P1", "X"] + ["P%d" % i for i in range(3, 10)])  # 10-list, no P10..P15
        sr = M.season_ranking("2020-21", [a, b])
        self.assertEqual(sr["depth"], 10)
        x = next(r for r in sr["all_rows"] if r["player"] == "X")
        self.assertEqual(x["ranks"], [16, 3])                      # missing from a 15-list = 16
        p12 = next(r for r in sr["all_rows"] if r["player"] == "P12")
        self.assertEqual(p12["ranks"], [12, 11])                   # missing from a 10-list = 11
        self.assertTrue(all(r["pos"] <= 10 for r in sr["rows"]))

    def test_ties_best_rank_then_later_list_then_shared(self):
        # P and Q average 2.5. P's best is 1 -> P ahead.
        sr = M.season_ranking("s", [lst("A", ["P", "Z", "Q", "Y"]), lst("B", ["Z", "Q", "Y", "P"])])
        pos = {r["player"]: r["pos"] for r in sr["all_rows"]}
        self.assertLess(pos["P"], pos["Q"])
        # Same average and same best: the later list decides.
        sr = M.season_ranking("s", [lst("A", ["P", "Q", "Z"]), lst("B", ["Q", "P", "Z"])])
        pos = {r["player"]: r["pos"] for r in sr["all_rows"]}
        self.assertEqual((pos["Q"], pos["P"]), (1, 2))
        # Identical on every list cannot happen on real lists; equal keys share the position.
        sr = M.season_ranking("s", [lst("A", ["P", "Q", "Z"])])
        rows = sr["all_rows"]
        self.assertEqual([r["pos"] for r in rows], [1, 2, 3])
        a = lst("A", ["P", "Q", "R", "S"])
        b = lst("B", ["P", "R", "Q", "S"])
        c = lst("C", ["P", "Q", "R", "S"])
        d = lst("D", ["P", "R", "Q", "S"])
        sr = M.season_ranking("s", [a, b, c, d])
        pos = {r["player"]: r["pos"] for r in sr["all_rows"]}
        # Q and R: same average (2.5), same best (2); latest list D has R ahead.
        self.assertEqual((pos["R"], pos["Q"]), (2, 3))

    def test_shared_position(self):
        a = lst("A", ["P", "Q", "Z"])
        a["entries"].append({"rank": 2, "player": "Q2"})  # a tie printed in a list
        a["n"] = 4
        sr = M.season_ranking("s", [a])
        pos = {r["player"]: r["pos"] for r in sr["all_rows"]}
        self.assertEqual(pos["Q"], pos["Q2"])
        self.assertTrue(all(r["shared"] for r in sr["all_rows"] if r["player"] in ("Q", "Q2")))
        self.assertEqual(pos["Z"], 4)

    def test_partial_and_empty_lists(self):
        sr = M.season_ranking("2001-02", [lst("R02", ["Kobe"])])
        self.assertEqual((sr["kind"], sr["depth"], [r["player"] for r in sr["rows"]]), ("no1", 1, ["Kobe"]))
        sr = M.season_ranking("2005-06", [lst("A", ["W", "L", "I"]), lst("B", ["W", "I", "L"]), lst("C", ["Z"])])
        self.assertEqual([r["id"] for r in sr["fed"]], ["A", "B"])
        self.assertEqual(sr["skipped"][0][0]["id"], "C")
        self.assertNotIn("Z", [r["player"] for r in sr["all_rows"]])
        sr = M.season_ranking("2004-05", [lst("E", []), lst("F", ["S", "T"])])
        self.assertEqual([r["id"] for r in sr["fed"]], ["F"])
        self.assertEqual(sr["skipped"][0][1], "empty list")


class Moves(unittest.TestCase):
    def test_list_length_rule(self):
        a = lst("A", ["P%d" % i for i in range(1, 16)])                    # 15 deep
        b = lst("B", ["P2", "P1", "P3", "P4", "N", "P6", "P7", "P12", "P9", "P10"])  # 10 deep, no P5, P8, P11..15
        c = lst("C", ["P1", "P2", "P3", "P4", "P6", "P7", "P9", "P10", "P13", "N", "P5", "P8", "Q", "P20", "P14"])  # 15 deep
        M.compute_moves([a, b, c])
        mb = {e["player"]: e["move"] for e in b["entries"]}
        self.assertEqual(b["compare_len"], 10)
        self.assertEqual((mb["P2"]["kind"], mb["P2"]["delta"]), ("up", 1))
        self.assertEqual(mb["N"]["kind"], "new")
        self.assertEqual(mb["P12"]["kind"], "none")                      # No. 12 -> 8: outside the shared top 10
        dropped = {e["player"] for e in b["dropped"]}
        self.assertEqual(dropped, {"P5", "P8"})                          # No. 12-15 absent from a 10-list did not drop out
        mc = {e["player"]: e["move"] for e in c["entries"]}
        self.assertEqual(c["compare_len"], 10)
        self.assertEqual(mc["P5"]["kind"], "none")                       # No. 11 now, beyond the 10 both share
        self.assertEqual(mc["Q"]["kind"], "new")                         # never seen: a first appearance
        self.assertEqual(mc["P13"]["kind"], "back")                      # within 10 now, absent from B, seen on A
        self.assertEqual(mc["P20"]["kind"], "new")

    def test_partial_and_empty_lists_are_not_compared(self):
        a = lst("A", ["X", "Y", "Z"])
        p = lst("P", ["Y"])
        e = lst("E", [])
        b = lst("B", ["Y", "X", "Z"])
        M.compute_moves([a, p, e, b])
        self.assertEqual(p["entries"][0]["move"]["kind"], "none")
        self.assertIsNone(p["compare_id"])
        self.assertEqual(b["compare_id"], "A")                           # skips the partial and the empty list
        self.assertEqual(b["entries"][0]["move"]["kind"], "up")

    def test_first_list_entries(self):
        p = lst("R01", ["Kobe"])
        a = lst("R03", ["Kobe", "AI"])
        M.compute_moves([p, a])
        self.assertEqual(p["entries"][0]["move"]["kind"], "new")
        self.assertEqual(a["entries"][0]["move"]["kind"], "none")
        self.assertEqual(a["entries"][1]["move"]["kind"], "new")


class Droughts(unittest.TestCase):
    def test_only_complete_lists_create_droughts(self):
        ls = [lst("A", ["T1", "x"]), lst("B", ["y", "z"]), lst("E", []), lst("P", ["q"]), lst("C", ["y", "z"]), lst("D", ["T1", "z"])]
        n, a, b = M.longest_drought(ls, lambda r: any(e["player"] == "T1" for e in r["entries"]))
        self.assertEqual((n, a["id"], b["id"]), (2, "B", "C"))
        ls[3] = lst("P", ["T1"])                                        # a partial list with the team ends the run
        n, a, b = M.longest_drought(ls, lambda r: any(e["player"] == "T1" for e in r["entries"]))
        self.assertEqual(n, 1)


class Awards(unittest.TestCase):
    def test_order_matches_share_plus_all_nba(self):
        votes = {"A": {"all_nba": 1, "mvp_rank": 3}, "B": {"all_nba": 1, "mvp_rank": 1}, "C": {"all_nba": 2, "mvp_rank": 2},
                 "D": {"all_nba": None, "mvp_rank": 4}, "E": {"all_nba": 3, "mvp_rank": None}, "F": {"all_nba": None, "mvp_rank": None}}
        by, rows = M.award_ranking(votes)
        self.assertEqual([r["player"] for r in rows], ["B", "A", "C", "E", "D"])
        self.assertNotIn("F", by)
        # Any shares below 1 that fall with the vote rank give the same order.
        share = {1: .95, 2: .6, 3: .3, 4: .01}
        score = {p: {1: 3, 2: 2, 3: 1}.get(v["all_nba"], 0) + share.get(v["mvp_rank"], 0) for p, v in votes.items() if p != "F"}
        self.assertEqual(sorted(score, key=lambda p: -score[p]), [r["player"] for r in rows])


if __name__ == "__main__":
    unittest.main()
