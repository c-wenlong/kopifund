"""G18: the colour every bucket is drawn in (categories.shown_colours) and
what picking one another bucket has does (a swap)."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import unittest

from finance import categories, db, spending
from test_schema_v5 import record


class ShownColoursTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)

    def shown(self):
        return {g["name"]: g["shown"] for g in categories.overview(self.conn)["groups"]}

    def id_of(self, name):
        return self.conn.execute("SELECT id FROM category_group WHERE name = ?", (name,)).fetchone()[0]

    def pick(self, name, colour):
        return categories.set_group_style(self.conn, name, self.id_of(name), colour=colour)

    def assert_unique(self):
        shown = self.shown()
        self.assertEqual(len(set(shown.values())), len(shown), shown)

    def test_with_nothing_chosen_each_bucket_keeps_its_places_colour(self):
        groups = categories.overview(self.conn)["groups"]
        spend = [g for g in groups if g["kind"] == "spend"]
        others = [g for g in groups if g["kind"] != "spend"]
        self.assertEqual([g["shown"] for g in spend + others], [f"c{i}" for i in range(1, len(groups) + 1)])
        self.assertTrue(all(g["colour"] is None for g in groups))

    def test_a_colour_another_bucket_is_drawn_in_swaps_the_two(self):
        before = self.shown()
        out = self.pick("Food", before["Getting around"])
        self.assertEqual(out["swapped"], "Getting around")
        after = self.shown()
        self.assertEqual(after["Food"], before["Getting around"])
        self.assertEqual(after["Getting around"], before["Food"])
        # no other bucket moved
        self.assertEqual({k: v for k, v in after.items() if k not in ("Food", "Getting around")},
                         {k: v for k, v in before.items() if k not in ("Food", "Getting around")})
        self.assert_unique()

    def test_a_colour_another_bucket_chose_swaps_the_two_too(self):
        self.pick("Giving", "c12")
        food = self.shown()["Food"]
        out = self.pick("Food", "c12")
        self.assertEqual(out["swapped"], "Giving")
        self.assertEqual((self.shown()["Food"], self.shown()["Giving"]), ("c12", food))
        self.assert_unique()

    def test_a_colour_no_bucket_has_moves_nobody(self):
        before = self.shown()
        out = self.pick("Food", "c12")
        self.assertIsNone(out["swapped"])
        after = self.shown()
        self.assertEqual(after["Food"], "c12")
        self.assertEqual({k: v for k, v in after.items() if k != "Food"}, {k: v for k, v in before.items() if k != "Food"})

    def test_its_own_colour_is_no_swap(self):
        food = self.shown()["Food"]
        self.assertIsNone(self.pick("Food", food)["swapped"])
        self.assertEqual(self.shown()["Food"], food)

    def test_an_icon_alone_moves_no_colour(self):
        before = self.shown()
        out = categories.set_group_style(self.conn, "Food", self.id_of("Food"), icon="coffee")
        self.assertEqual((out["icon"], out["swapped"]), ("coffee", None))
        self.assertEqual(self.shown(), before)

    def test_no_two_share_after_a_reorder_or_a_new_bucket(self):
        self.pick("Food", "c5")
        self.pick("Travel", "c1")
        names = [g["name"] for g in categories.overview(self.conn)["groups"]]
        categories.reorder_groups(self.conn, names=list(reversed(names)))
        self.assert_unique()
        categories.add_group(self.conn, "Pets")
        self.assert_unique()
        self.assertEqual(self.shown()["Food"], "c5")

    def test_a_free_colour_moves_nobody_after_a_reorder_either(self):
        # Food takes c2, then trades places with Getting around: Getting around's place colour (c1) is
        # taken, so it waits and gets c3. Food then takes the free c12: Getting around keeps c3
        self.pick("Food", "c2")
        names = [g["name"] for g in categories.overview(self.conn)["groups"]]
        i, j = names.index("Food"), names.index("Getting around")
        names[i], names[j] = names[j], names[i]
        categories.reorder_groups(self.conn, names=names)
        before = self.shown()
        self.assertEqual(before["Getting around"], "c3")
        out = self.pick("Food", "c12")
        self.assertIsNone(out["swapped"])
        after = self.shown()
        self.assertEqual(after["Food"], "c12")
        self.assertEqual({k: v for k, v in after.items() if k != "Food"}, {k: v for k, v in before.items() if k != "Food"})

    def test_a_pick_recolours_only_its_bucket_and_the_one_it_swaps_with(self):
        import random
        rnd = random.Random(18)
        for step in range(60):
            names = [g["name"] for g in categories.overview(self.conn)["groups"]]
            if step % 7 == 3:
                rnd.shuffle(names)
                categories.reorder_groups(self.conn, names=names)
                continue
            before = self.shown()
            name = rnd.choice(names)
            colour = rnd.choice(categories.SERIES_COLOURS + (None,))
            out = self.pick(name, colour)
            after = self.shown()
            changed = {k for k in after if after[k] != before[k]}
            with self.subTest(step=step, name=name, colour=colour):
                self.assertLessEqual(changed, {name, out["swapped"]})
                if colour is not None:
                    self.assertEqual(after[name], colour)
                self.assertEqual(len(set(after.values())), len(after))

    def test_past_twelve_buckets_a_pick_still_sticks(self):
        for n in range(3):
            categories.add_group(self.conn, f"Extra {n}")
        self.assertEqual(len(self.shown()), 14)
        self.pick("Transfers", "c1")
        self.assertEqual(self.shown()["Transfers"], "c1")
        import random
        rnd = random.Random(14)
        for step in range(40):
            names = list(self.shown())
            if step % 5 == 2:
                rnd.shuffle(names)
                categories.reorder_groups(self.conn, names=names)
                continue
            name, colour = rnd.choice(names), rnd.choice(categories.SERIES_COLOURS)
            self.pick(name, colour)
            with self.subTest(step=step, name=name, colour=colour):
                self.assertEqual(self.shown()[name], colour)

    def test_two_saved_alike_are_still_drawn_apart(self):
        # a record written some other way: the first in order keeps the colour
        self.conn.execute("UPDATE category_group SET colour = 'c4' WHERE name IN ('Food', 'Travel')")
        shown = self.shown()
        self.assertEqual(shown["Food"], "c4")
        self.assertNotEqual(shown["Travel"], "c4")
        self.assert_unique()

    def test_past_twelve_buckets_a_colour_comes_round_again(self):
        for n in range(4):
            categories.add_group(self.conn, f"Extra {n}")
        shown = self.shown()
        self.assertEqual(len(shown), 15)
        self.assertEqual(set(shown.values()), set(categories.SERIES_COLOURS))

    def test_spending_draws_each_bucket_in_the_colour_categories_shows(self):
        conn = record()
        self.addCleanup(conn.close)
        gid = conn.execute("SELECT id FROM category_group WHERE name = 'Food'").fetchone()[0]
        categories.set_group_style(conn, group_id=gid, colour="c10")      # Income's, by its place
        out = spending.explore(conn, {"from": "2026-08", "to": "2026-08"})
        shown = {g["name"]: g["shown"] for g in categories.overview(conn)["groups"]}
        self.assertEqual({k: v["shown"] for k, v in out["bucket_style"].items()},
                         {k: shown[k] for k in out["bucket_order"]})
        self.assertEqual(out["bucket_style"]["Food"]["shown"], "c10")
        self.assertNotIn(shown["Income"], [v["shown"] for v in out["bucket_style"].values()])


if __name__ == "__main__":
    unittest.main()
