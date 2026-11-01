"""G17: trips, categories, buckets and budgets are written by id (audit T4,
T5). Every read that lists them sends each row's id; every write takes it and
uses it; a name alone still works, for a tab opened before ids; a name sent
beside an id must agree with it, or nothing is written."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import json
from unittest import mock

import serve
from finance import api, categories, planning
from test_categories import DataFolder, record

TRIP = {"name": "Japan", "start": "2026-08-01", "end": "2026-08-07", "countries": ["JPN"],
        "notes": "", "extra_keys": []}


class Ids(DataFolder):
    def setUp(self):
        super().setUp()
        self.conn = record()
        self.addCleanup(self.conn.close)
        (self.root / "rules").mkdir()

    def write(self, path, body):
        with mock.patch("finance.api.config", lambda: None), mock.patch("finance.stats_rebuild.refresh_stats"):
            return serve.write(path, {}, json.dumps(body).encode())

    def cat(self, name):
        return self.conn.execute("SELECT id FROM category WHERE name = ?", (name,)).fetchone()[0]

    def bucket(self, name):
        return self.conn.execute("SELECT id FROM category_group WHERE name = ?", (name,)).fetchone()[0]

    def trip(self, name):
        row = self.conn.execute("SELECT id FROM trip WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None

    def bucket_of(self, cid):
        return self.conn.execute("""SELECT g.name FROM category c JOIN category_group g ON g.id = c.group_id
                                    WHERE c.id = ?""", (cid,)).fetchone()[0]

    def record_now(self):
        """Every user-owned row, to show a refused write changed nothing."""
        return [self.conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall()
                for t in ("category", "category_group", "budget", "trip", "category_alias")]


class ReadsTest(Ids):
    def test_every_list_of_rows_a_write_can_name_carries_their_ids(self):
        page = categories.overview(self.conn)
        food = next(g for g in page["groups"] if g["name"] == "Food")
        coffee = next(c for c in food["categories"] if c["name"] == "Coffee")
        self.assertEqual((food["id"], coffee["id"]), (self.bucket("Food"), self.cat("Coffee")))
        # T5: the old Fixed / Variable / Savings is cost_type; group stays, deprecated, the same value
        self.assertEqual(coffee["cost_type"], "Variable")
        self.assertEqual(coffee["group"], coffee["cost_type"])
        b = planning.budgets(self.conn, "2026-08")
        row = next(r for r in b["rows"] if r["category"] == "Coffee")
        self.assertEqual(row["category_id"], self.cat("Coffee"))
        self.assertEqual(next(g for g in b["groups"] if g["name"] == "Food")["id"], self.bucket("Food"))
        plan_cat = next(c for c in planning.categories(self.conn) if c["name"] == "Coffee")
        self.assertEqual((plan_cat["id"], plan_cat["cost_type"], plan_cat["group"]),
                         (self.cat("Coffee"), "Variable", "Variable"))
        planning.save_trip(self.conn, TRIP)
        self.assertEqual(api.trips(self.conn, long_stay=60)["trips"][0]["id"], self.trip("Japan"))


class CategoryWritesTest(Ids):
    def test_each_category_write_by_id(self):
        coffee, eating = self.cat("Coffee"), self.cat("Eating out")
        out = self.write("/api/categories", {"id": coffee, "need_class": "Need"})
        self.assertEqual((out["id"], out["name"]), (coffee, "Coffee"))
        self.assertEqual(self.conn.execute("SELECT need_class FROM category WHERE id = ?", (coffee,)).fetchone()[0],
                         "Need")
        # a rename by id needs no old name
        out = self.write("/api/categories/rename", {"id": coffee, "new_name": "Kopi"})
        self.assertEqual((out["id"], out["name"], self.cat("Kopi")), (coffee, "Kopi", coffee))
        out = self.write("/api/categories/move", {"id": coffee, "name": "Kopi", "group_id": self.bucket("Lifestyle"),
                                                  "group": "Lifestyle", "index": 0})
        self.assertEqual((out["group"], self.bucket_of(coffee)), ("Lifestyle", "Lifestyle"))
        out = self.write("/api/categories/add", {"name": "Pets", "group_id": self.bucket("Lifestyle")})
        self.assertEqual(self.bucket_of(out["id"]), "Lifestyle")
        out = self.write("/api/categories/delete", {"id": coffee, "name": "Kopi", "move_to_id": eating,
                                                    "move_to": "Eating out"})
        self.assertEqual((out["deleted"], out["moved_to_id"], out["transactions"]), ("Kopi", eating, 2))
        self.assertIsNone(self.conn.execute("SELECT 1 FROM category WHERE id = ?", (coffee,)).fetchone())
        # one nothing points at needs no place to move to
        out = self.write("/api/categories/delete", {"id": self.cat("Pets"), "name": "Pets"})
        self.assertEqual(out["deleted"], "Pets")

    def test_each_bucket_write_by_id(self):
        out = self.write("/api/category-groups/add", {"name": "Pets"})
        pets = out["id"]
        self.assertEqual(self.bucket("Pets"), pets)
        out = self.write("/api/category-groups/rename", {"id": pets, "new_name": "Animals"})
        self.assertEqual((out["id"], self.bucket("Animals")), (pets, pets))
        self.write("/api/categories/add", {"name": "Vet", "group_id": pets})
        ids = [g["id"] for g in categories.overview(self.conn)["groups"]]
        names = [g["name"] for g in categories.overview(self.conn)["groups"]]
        self.write("/api/category-groups/reorder", {"ids": [ids[1], ids[0], *ids[2:]],
                                                    "names": [names[1], names[0], *names[2:]]})
        self.assertEqual([g["id"] for g in categories.overview(self.conn)["groups"]][:2], [ids[1], ids[0]])
        out = self.write("/api/category-groups/delete", {"id": pets, "name": "Animals",
                                                         "move_to_id": self.bucket("Lifestyle"), "move_to": "Lifestyle"})
        self.assertEqual((out["deleted"], out["moved"], self.bucket_of(self.cat("Vet"))), ("Animals", 1, "Lifestyle"))

    def test_a_budget_by_category_id(self):
        coffee = self.cat("Coffee")
        out = self.write("/api/budgets", {"category_id": coffee, "month": "2026-10", "planned": 4200})
        self.assertEqual((out["category_id"], out["category"]), (coffee, "Coffee"))
        self.assertEqual(self.conn.execute("SELECT planned_minor FROM budget WHERE category_id = ? AND month = ?",
                                           (coffee, "2026-10")).fetchone()[0], 4200)

    def test_names_alone_still_work_for_a_page_opened_before_ids(self):
        coffee = self.cat("Coffee")
        self.write("/api/categories", {"name": "Coffee", "need_class": "Need"})
        self.write("/api/categories/rename", {"name": "Coffee", "new_name": "Kopi"})
        self.write("/api/categories/move", {"name": "Kopi", "group": "Lifestyle"})
        self.write("/api/budgets", {"category": "Kopi", "month": "2026-10", "planned": 100})
        self.write("/api/category-groups/add", {"name": "Pets"})
        self.write("/api/category-groups/rename", {"name": "Pets", "new_name": "Animals"})
        self.write("/api/categories/add", {"name": "Vet", "group": "Animals"})
        names = [g["name"] for g in categories.overview(self.conn)["groups"]]
        self.write("/api/category-groups/reorder", {"names": [names[1], names[0], *names[2:]]})
        self.write("/api/category-groups/delete", {"name": "Animals", "move_to": "Lifestyle"})
        self.write("/api/categories/delete", {"name": "Kopi", "move_to": "Eating out"})
        self.assertEqual((self.bucket_of(self.cat("Vet")), self.cat("Eating out") != coffee), ("Lifestyle", True))
        self.assertIsNone(self.conn.execute("SELECT 1 FROM category WHERE name = 'Kopi'").fetchone())

    def test_an_id_and_a_name_that_disagree_are_refused_and_nothing_is_written(self):
        coffee, eating = self.cat("Coffee"), self.cat("Eating out")
        food, life = self.bucket("Food"), self.bucket("Lifestyle")
        before = self.record_now()
        for path, body in (
                ("/api/categories", {"id": coffee, "name": "Eating out", "need_class": "Need"}),
                ("/api/categories/rename", {"id": coffee, "name": "Eating out", "new_name": "Kopi"}),
                ("/api/categories/move", {"id": coffee, "name": "Eating out", "group_id": life, "group": "Lifestyle"}),
                ("/api/categories/move", {"id": coffee, "name": "Coffee", "group_id": life, "group": "Food"}),
                ("/api/categories/add", {"name": "Pets", "group_id": life, "group": "Food"}),
                ("/api/categories/delete", {"id": coffee, "name": "Eating out", "move_to_id": eating,
                                            "move_to": "Eating out"}),
                ("/api/categories/delete", {"id": coffee, "name": "Coffee", "move_to_id": eating,
                                            "move_to": "Groceries"}),
                ("/api/category-groups/rename", {"id": food, "name": "Lifestyle", "new_name": "Meals"}),
                ("/api/category-groups/delete", {"id": life, "name": "Food", "move_to_id": food, "move_to": "Food"}),
                ("/api/category-groups/delete", {"id": life, "name": "Lifestyle", "move_to_id": food,
                                                 "move_to": "Lifestyle"}),
                ("/api/budgets", {"category_id": coffee, "category": "Eating out", "month": "2026-10",
                                  "planned": 1})):
            with self.assertRaisesRegex(serve.Refused, "is called .* now, not .*: reload the page", msg=path):
                self.write(path, body)
        self.assertEqual(self.record_now(), before)

    def test_a_move_or_delete_by_id_needs_the_names_beside_its_ids(self):
        # Greptile P1 on #94: SQLite hands the highest id out again, so an id alone may mean a newer row
        coffee, eating = self.cat("Coffee"), self.cat("Eating out")
        food, life = self.bucket("Food"), self.bucket("Lifestyle")
        self.write("/api/category-groups/add", {"name": "Pets"})
        pets = self.bucket("Pets")
        before = self.record_now()
        for path, body in (
                ("/api/categories/delete", {"id": coffee, "move_to_id": eating, "move_to": "Eating out"}),
                ("/api/categories/delete", {"id": coffee, "name": "Coffee", "move_to_id": eating}),
                ("/api/categories/delete", {"id": coffee}),
                ("/api/categories/move", {"id": coffee, "group_id": life, "group": "Lifestyle"}),
                ("/api/categories/move", {"id": coffee, "name": "Coffee", "group_id": life}),
                ("/api/category-groups/delete", {"id": pets}),
                ("/api/category-groups/delete", {"id": life, "name": "Lifestyle", "move_to_id": food}),
                ("/api/category-groups/delete", {"id": life, "move_to_id": food, "move_to": "Food"})):
            with self.assertRaisesRegex(serve.Refused, "name beside its id: reload the page", msg=body):
                self.write(path, body)
        self.assertEqual(self.record_now(), before)

    def test_a_stale_tab_never_moves_rows_into_a_newer_category_that_took_an_id(self):
        out = self.write("/api/categories/add", {"name": "Pets", "group_id": self.bucket("Food"), "group": "Food"})
        pets = out["id"]
        self.write("/api/categories/delete", {"id": pets, "name": "Pets"})
        # SQLite gives the next category the id Pets had
        self.assertEqual(self.write("/api/categories/add", {"name": "Bills", "group": "Food"})["id"], pets)
        before = self.record_now()
        with self.assertRaisesRegex(serve.Refused, "called Bills now, not Pets"):
            self.write("/api/categories/delete", {"id": self.cat("Coffee"), "name": "Coffee",
                                                  "move_to_id": pets, "move_to": "Pets"})
        self.assertEqual(self.record_now(), before)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM txn WHERE category_id = ?",
                                           (self.cat("Coffee"),)).fetchone()[0], 2)

    def test_a_reorder_whose_names_disagree_with_its_ids_is_refused(self):
        groups = categories.overview(self.conn)["groups"]
        ids, names = [g["id"] for g in groups], [g["name"] for g in groups]
        before = self.record_now()
        for bad in ([names[1], names[0], *names[2:]], names[:-1]):
            with self.assertRaisesRegex(serve.Refused, "reload the page"):
                self.write("/api/category-groups/reorder", {"ids": ids, "names": bad})
        self.assertEqual(self.record_now(), before)

    def test_a_rename_by_id_into_a_name_another_row_has_is_refused(self):
        before = self.record_now()
        with self.assertRaisesRegex(serve.Refused, "already a category called 'eating out'"):
            self.write("/api/categories/rename", {"id": self.cat("Coffee"), "new_name": "eating out"})
        with self.assertRaisesRegex(serve.Refused, "already a bucket called 'Lifestyle'"):
            self.write("/api/category-groups/rename", {"id": self.bucket("Food"), "new_name": "Lifestyle"})
        self.assertEqual(self.record_now(), before)

    def test_an_id_no_row_has_and_an_id_that_is_not_a_number(self):
        with self.assertRaisesRegex(serve.Refused, "not in your record any more"):
            self.write("/api/categories/rename", {"id": 987654, "new_name": "Kopi"})
        with self.assertRaisesRegex(serve.Refused, "not in your record any more"):
            self.write("/api/budgets", {"category_id": 987654, "month": "2026-10", "planned": 1})
        for bad in ("3", True, 0, -1, 2.0):
            with self.assertRaises(ValueError, msg=bad):
                self.write("/api/categories/rename", {"id": bad, "new_name": "Kopi"})
        with self.assertRaises(ValueError):
            self.write("/api/category-groups/reorder", {"ids": ["1"]})
        with self.assertRaisesRegex(serve.Refused, "every bucket once"):
            self.write("/api/category-groups/reorder", {"ids": [self.bucket("Food")]})

    def test_a_rename_and_its_undo_keep_the_category_s_id(self):
        coffee = self.cat("Coffee")
        self.write("/api/categories/rename", {"id": coffee, "name": "Coffee", "new_name": "Kopi"})
        self.write("/api/categories/rename", {"id": coffee, "name": "Kopi", "new_name": "Coffee"})
        self.assertEqual(self.cat("Coffee"), coffee)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM txn WHERE category_id = ?", (coffee,)).fetchone()[0], 2)


class TripWritesTest(Ids):
    def test_a_new_trip_answers_with_its_id_and_a_save_by_id_renames_it(self):
        out = self.write("/api/trips", TRIP)
        tid = out["id"]
        self.assertEqual(self.trip("Japan"), tid)
        out = self.write("/api/trips", {**TRIP, "id": tid, "old_name": "Japan", "name": "Japan 2026"})
        self.assertEqual((out["id"], self.trip("Japan 2026"), self.trip("Japan")), (tid, tid, None))
        # Undo puts the old name back on the same trip
        out = self.write("/api/trips/undo", {"undo": out["undo"]})
        self.assertEqual((out["id"], out["name"], self.trip("Japan")), (tid, "Japan", tid))

    def test_a_save_by_id_sends_no_old_name_when_it_has_none(self):
        tid = self.write("/api/trips", TRIP)["id"]
        self.write("/api/trips", {**TRIP, "id": tid, "notes": "ramen"})
        self.assertEqual(self.conn.execute("SELECT notes FROM trip WHERE id = ?", (tid,)).fetchone()[0], "ramen")

    def test_remove_by_id_and_undo_brings_it_back_under_the_same_id(self):
        tid = self.write("/api/trips", TRIP)["id"]
        self.write("/api/trips", {**TRIP, "name": "Later", "start": "2026-09-01", "end": "2026-09-02"})
        out = self.write("/api/trips/remove", {"id": tid, "name": "Japan"})
        self.assertEqual((out["removed"], out["id"], out["restore"]["trip"]["id"]), ("Japan", tid, tid))
        self.assertIsNone(self.trip("Japan"))
        back = self.write("/api/trips/restore", out["restore"])
        self.assertEqual((back["id"], self.trip("Japan")), (tid, tid))

    def test_a_restore_whose_id_a_newer_trip_took_gets_a_new_one(self):
        tid = self.write("/api/trips", TRIP)["id"]
        out = self.write("/api/trips/remove", {"id": tid, "name": "Japan"})
        # SQLite hands the highest id out again: the next trip takes Japan's
        newer = self.write("/api/trips", {**TRIP, "name": "Bali", "countries": ["IDN"]})["id"]
        self.assertEqual(newer, tid)
        back = self.write("/api/trips/restore", out["restore"])
        self.assertNotEqual(back["id"], tid)
        self.assertEqual((self.trip("Japan"), self.trip("Bali")), (back["id"], tid))
        # a remove from a tab that still thinks that id is Japan is refused
        with self.assertRaisesRegex(serve.Refused, "is called Bali now, not Japan"):
            self.write("/api/trips/remove", {"id": tid, "name": "Japan"})
        self.assertEqual(self.trip("Bali"), tid)

    def test_names_alone_still_work_for_trips(self):
        self.write("/api/trips", TRIP)
        self.write("/api/trips", {**TRIP, "name": "Japan 2026", "old_name": "Japan"})
        out = self.write("/api/trips/remove", {"name": "Japan 2026"})
        self.assertIsNone(self.trip("Japan 2026"))
        self.write("/api/trips/restore", out["restore"])
        self.assertIsNotNone(self.trip("Japan 2026"))

    def test_an_undo_from_a_page_opened_before_ids_still_works(self):
        out = self.write("/api/trips", TRIP)
        undo = {**out["undo"], "after": {k: v for k, v in out["undo"]["after"].items() if k != "id"}}
        self.write("/api/trips/undo", {"undo": undo})
        self.assertIsNone(self.trip("Japan"))

    def test_an_id_and_a_name_that_disagree_are_refused_and_nothing_is_written(self):
        tid = self.write("/api/trips", TRIP)["id"]
        self.write("/api/trips", {**TRIP, "name": "Bali", "start": "2026-09-01", "end": "2026-09-02"})
        before = self.record_now()
        for path, body in (("/api/trips", {**TRIP, "id": tid, "old_name": "Bali", "name": "Japan 2026"}),
                           ("/api/trips/remove", {"id": tid, "name": "Bali"})):
            with self.assertRaisesRegex(serve.Refused, "is called Japan now, not Bali: reload the page", msg=path):
                self.write(path, body)
        # a rename by id into another trip's name
        with self.assertRaisesRegex(serve.Refused, "already called 'Bali'"):
            self.write("/api/trips", {**TRIP, "id": tid, "name": "Bali"})
        with self.assertRaisesRegex(serve.Refused, "not in your record any more"):
            self.write("/api/trips/remove", {"id": 987654, "name": "Japan"})
        # a remove by id needs the name beside it
        with self.assertRaisesRegex(serve.Refused, "name beside its id"):
            self.write("/api/trips/remove", {"id": tid})
        self.assertEqual(self.record_now(), before)
