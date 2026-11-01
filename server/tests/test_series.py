"""G16: a recurring series' own view on Recurring and the owner's edits to it
(server/finance/series.py, read by server/finance/recurring.py). Edits live in the record, are
read over what was detected everywhere the series shows, and stay with their series
when detection runs again on a record that moved under them. The property checks run
on a few hundred seeded, made-up bills: whatever the shape, an edit survives an older
statement that changes the series' id, an end stops lateness, a cadence moves the next
payment, and Not recurring hides the series everywhere and takes back cleanly."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import json
import os
import random
import tempfile
import unittest
from calendar import monthrange
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import serve
from analytics.stats import recurring_detector as rd
from finance import db, home, recurring, series
from test_db import SNAP, block, parsed, row
from test_recurring import CATEGORIES, SUBS

SEEDS = 120
PAYEE = "Streamco"
ODD = "Oddco"           # a payee the rules mark recurring whose charges keep to no cadence
LIVE = ("active", "paused")


def load(conn, rows, flagged=()):
    """(key, payee, ISO day, "-amount") rows into the record, every one in one statement,
    in a category no one buys in; a key already there is the same row again."""
    keyed = [row(k, amount, date=day, payee=payee, merchant=payee.upper(), category="Subscriptions")
             for k, payee, day, amount in rows]
    names = sorted({r[1] for r in rows})
    db.load_parsed(conn, parsed([block()], keyed), payees=[{"name": p, "kind": "Merchant", "country": "SGP"} for p in names])
    for p in flagged:
        conn.execute("UPDATE payee SET recurring = 1 WHERE name = ?", (p,))


def build(rows, flagged=()):
    conn = db.connect(":memory:")
    db.import_user_tables(conn, {**{k: [dict(x) for x in v] for k, v in SNAP.items()},
                                 "categories": [dict(c) for c in CATEGORIES + SUBS]})
    load(conn, rows, flagged)
    return conn


def stat(conn):
    """The detector's own result over the record as it is."""
    return {recurring.STAT: rd.run(db.snapshot(conn))}


def back(day, cadence):
    """The day one cadence before `day`."""
    if cadence in rd.DAY_STEP:
        return day - timedelta(days=rd.DAY_STEP[cadence])
    i = day.year * 12 + day.month - 1 - rd.MONTH_STEP[cadence]
    y, m = divmod(i, 12)
    return date(y, m + 1, min(day.day, monthrange(y, m + 1)[1]))


def bill(seed):
    """(cadence, price, rows, last): one made-up bill at PAYEE, its charges a day late now
    and then, and the day of its last charge."""
    rnd = random.Random(seed)
    cadence = rnd.choice(["monthly", "monthly", "monthly", "weekly", "fortnightly", "quarterly"])
    price = rnd.choice([15.0, 22.5, 40.0, 99.0])
    n = {"monthly": rnd.randint(8, 14), "weekly": rnd.randint(10, 20), "fortnightly": rnd.randint(8, 14),
         "quarterly": rnd.randint(5, 7)}[cadence]
    days = [date(2025, rnd.randint(1, 6), rnd.randint(1, 28))]
    for _ in range(n - 1):
        days.append(recurring.step(days[-1], cadence))
    paid = [d + timedelta(days=rnd.choice([0, 0, 0, 1])) for d in days]
    rows = [(f"s{i:03d}", PAYEE, d.isoformat(), f"-{price:.2f}") for i, d in enumerate(paid)]
    return cadence, price, rows, paid[-1]


def missed(cadence):
    """Days after the last charge by which the next is past its tolerance: late, and not yet paused."""
    return round(rd.NOMINAL[cadence] * 1.3) + 3


def ends(last, days):
    """The record's last day `days` after `last`: a charge elsewhere on the same account, so its
    statements cover to then."""
    return [("club", "Club", (last + timedelta(days=days)).isoformat(), "-12.00")]


def every(page):
    return [r for part in ("active", "paused", "stopped") for r in page[part]]


def mine(page):
    return [r for r in every(page) if r["payee"] == PAYEE]


def only(page):
    found = mine(page)
    assert len(found) == 1, [(r["id"], r["cadence"]) for r in every(page)]
    return found[0]


def seeds(check, step=1):
    failed = []
    for seed in range(0, SEEDS, step):
        try:
            check(seed)
        except AssertionError as e:
            failed.append((seed, str(e)[:400]))
    return failed


class Properties(unittest.TestCase):
    def check_survives(self, seed, changed):
        """An edit stays with its series when an older statement brings charges a fifth
        cheaper before its first, which joins them as one series whose id is now theirs."""
        rnd = random.Random(seed * 7 + 1)
        cadence, price, rows, last = bill(seed)
        end = ends(last, 2)
        conn = build(rows + end)
        try:
            res = stat(conn)
            today = last + timedelta(days=2)
            sid = only(recurring.page(conn, res, today=today))["id"]
            amount = round(price * 110)
            day = rnd.randint(1, 7) if cadence in series.DAY_CADENCES else rnd.randint(1, 28)
            recurring.save_series(conn, res, {"id": sid, "amount": amount, "day": day}, today=today)
            saved = series.read(conn)[0]["keys"]
            first, older = date.fromisoformat(rows[0][2]), []
            for i in range(3):
                first = back(first, cadence)
                older.append((f"o{i}", PAYEE, first.isoformat(), f"-{price * 0.8:.2f}"))
            load(conn, rows + older + end)
            # the stat as it was before the older statement, or run again on it
            res = res if seed % 2 else stat(conn)
            page = recurring.page(conn, res, today=today)
            rec = recurring.Record(conn, res[recurring.STAT]["data"])
            held = max((x for state in (*recurring.STATES, recurring.NOT_RECURRING) for x in rec.data.get(state, [])
                        if x["payee"] == PAYEE), key=lambda x: len(saved & rec.own_keys(x)))
            self.assertTrue(saved <= rec.own_keys(held), "the series holds every payment it had")
            self.assertEqual((held.get("edit") or {}).get("amount"), amount, (sid, held["id"]))
            row_ = next(r for r in every(page) if r["id"] == recurring.series_id(held))
            self.assertEqual(row_["amount"], amount)
            view = recurring.series_view(conn, res, row_["id"], today=today)
            self.assertEqual((view["edit"]["amount"], view["edit"]["day"], view["amount"]), (amount, day, amount))
            if view["state"] in LIVE and cadence not in series.NO_DAY:
                self.assertEqual(view["day"], day)
            # no other series of the payee took the edit too
            self.assertEqual(sum(1 for x in mine(page) if x["amount"] == amount), 1)
            if row_["id"] != sid:
                changed.append(seed)
                # saving from the view writes the edit under the id the series has now
                recurring.save_series(conn, res, {"id": row_["id"], "amount": amount, "day": day}, today=today)
                self.assertEqual([e["series_id"] for e in series.read(conn)], [row_["id"]])
        finally:
            conn.close()

    def test_an_edit_survives_detection_run_again_on_an_older_statement(self):
        changed = []
        self.assertEqual(seeds(lambda s: self.check_survives(s, changed)), [])
        # the older statement changed the id in most histories, so the payments are what kept the edit
        self.assertGreater(len(changed), SEEDS // 2)

    def check_ended(self, seed):
        """Nothing is late after the day the owner says a series ended: the series is
        stopped, out of Coming up and out of Needs your eye; an end still ahead clips its
        schedule there."""
        cadence, price, rows, last = bill(seed)
        silence = missed(cadence)
        conn = build(rows + ends(last, silence))
        try:
            res = stat(conn)
            today = last + timedelta(days=silence)
            before = only(recurring.page(conn, res, today=today))
            self.assertTrue(before["state"] == "paused" or before["late"], before)
            sid = before["id"]
            if seed % 3 == 0:
                # an end still ahead of the statements: what was expected before it stays expected
                end = today + timedelta(days=3)
                recurring.save_series(conn, res, {"id": sid, "ended_on": end.isoformat()}, today=today)
                up = recurring.coming_up(conn, res, today=today)["rows"]
                self.assertTrue(all(r["expected"] <= end.isoformat() for r in up if r["id"] == sid and r["status"] != "extra"))
                view = recurring.series_view(conn, res, sid, today=today)
                self.assertTrue(all(r["expected"] <= end.isoformat() for r in view["schedule"] if r["status"] != "extra"))
                self.assertTrue(view["next_expected"] is None or view["next_expected"] <= end.isoformat(), view["next_expected"])
                return
            recurring.save_series(conn, res, {"id": sid, "ended_on": last.isoformat()}, today=today)
            page = recurring.page(conn, res, today=today)
            self.assertEqual([r["id"] for r in page["stopped"] if r["payee"] == PAYEE], [sid])
            self.assertEqual([r for r in page["active"] + page["paused"] if r["payee"] == PAYEE], [])
            self.assertEqual(page["stopped"][[r["id"] for r in page["stopped"]].index(sid)]["ended_on"], last.isoformat())
            up = recurring.coming_up(conn, res, today=today)
            self.assertEqual([r for r in up["rows"] if r["id"] == sid and r["status"] in ("due", "late")], [])
            self.assertEqual([r for r in home.needs_your_eye(conn, res, up)["recurring_off"] if r["id"] == sid], [])
            view = recurring.series_view(conn, res, sid, today=today)
            self.assertEqual((view["state"], view["late"], view["next_expected"]), ("stopped", False, None))
            self.assertFalse([r for r in view["schedule"] if r["status"] in ("due", "late")])
        finally:
            conn.close()

    def test_an_end_stops_lateness(self):
        self.assertEqual(seeds(self.check_ended), [])

    def check_cadence(self, seed):
        """How often the owner says a series charges moves its next payment and its cost a
        year: the next is after the last paid and within two of the new cadence of it, and
        the payments Coming up expects are that cadence apart."""
        rnd = random.Random(seed * 13 + 5)
        cadence, price, rows, last = bill(seed)
        conn = build(rows + ends(last, 2))
        try:
            res = stat(conn)
            today = last + timedelta(days=2)
            sid = only(recurring.page(conn, res, today=today))["id"]
            new = rnd.choice([c for c in ("weekly", "fortnightly", "monthly", "two-monthly", "quarterly", "half-yearly",
                                          "yearly") if c != cadence])
            view = recurring.save_series(conn, res, {"id": sid, "cadence": new}, today=today)
            self.assertEqual((view["cadence"], view["detected"]["cadence"]), (new, cadence))
            self.assertEqual(view["yearly"], view["amount"] * recurring.PER_YEAR[new])
            page = recurring.page(conn, res, today=today)
            r = only(page)
            self.assertEqual(r["cadence"], new)
            if r["state"] == "active":
                lp, nxt = date.fromisoformat(r["last_paid"]), date.fromisoformat(r["next_expected"])
                reach = recurring.step(recurring.step(lp, new), new) + timedelta(days=rd.tolerance(new))
                self.assertTrue(lp < nxt <= reach, (lp, nxt, new))
            due = sorted(date.fromisoformat(x["expected"]) for x in page["coming_up"]["rows"]
                         if x["id"] == sid and x["status"] != "extra")
            for a, b in zip(due, due[1:]):
                self.assertGreaterEqual((b - a).days, rd.BANDS[new][0] - 3, (due, new))
            # a longer cadence than the detected one is not late two days after a payment
            if rd.NOMINAL[new] > rd.NOMINAL[cadence]:
                self.assertFalse(r["late"], r)
        finally:
            conn.close()

    def test_a_cadence_moves_the_next_payment(self):
        self.assertEqual(seeds(self.check_cadence), [])

    def check_hidden(self, seed):
        """Not recurring takes a series out of Recurring's lists, Coming up on both pages
        and Needs your eye; its view still opens, it is listed to take back, and taking it
        back gives the page it had."""
        cadence, price, rows, last = bill(seed)
        silence = missed(cadence) if seed % 2 else 2
        conn = build(rows + ends(last, silence))
        try:
            res = stat(conn)
            today = last + timedelta(days=silence)
            before = recurring.page(conn, res, today=today)
            sid = only(before)["id"]
            up = recurring.coming_up(conn, res, today=today)
            eye = home.needs_your_eye(conn, res, up)["recurring_off"]
            if seed % 2 and only(before)["state"] == "active":
                self.assertIn(sid, [r["id"] for r in eye], "a late bill is in Needs your eye before")
            recurring.save_series(conn, res, {"id": sid, "recurring": False}, today=today)
            page = recurring.page(conn, res, today=today)
            self.assertEqual(mine(page), [])
            self.assertEqual([r for r in page["coming_up"]["rows"] if r["id"] == sid], [])
            up = recurring.coming_up(conn, res, today=today)
            self.assertEqual([r for r in up["rows"] if r["id"] == sid], [])
            self.assertEqual([r for r in home.needs_your_eye(conn, res, up)["recurring_off"] if r["id"] == sid], [])
            self.assertEqual([r["id"] for r in page["not_recurring"]], [sid])
            self.assertEqual(recurring.series_view(conn, res, sid, today=today)["state"], "not_recurring")
            # taken back, the edit is gone and the page is as it was
            recurring.save_series(conn, res, {"id": sid, "recurring": None}, today=today)
            self.assertEqual(series.read(conn), [])
            self.assertEqual(recurring.page(conn, res, today=today), before)
        finally:
            conn.close()

    def test_not_recurring_hides_a_series_everywhere(self):
        self.assertEqual(seeds(self.check_hidden), [])

    def check_payee(self, seed):
        """Not recurring on a payee the rules mark recurring takes it off Marked recurring,
        no regular spacing (on Home's count too) and out of what the stat reads as marked,
        hides any series of it a series' own word does not keep, and takes back cleanly."""
        rnd = random.Random(seed * 31 + 7)
        cadence, price, rows, last = bill(seed)
        start = date.fromisoformat(rows[0][2])
        # gaps of 23, 47 and 110 days, which no cadence band holds, whole or in sums, each at its own
        # amount, so no two are one level either
        shift = rnd.randint(0, 40)
        odd = [start + timedelta(days=shift + k) for k in (0, 23, 70, 180)][:rnd.randint(2, 4)]
        extra = [(f"x{i}", ODD, d.isoformat(), f"-{a:.2f}") for i, (d, a) in enumerate(zip(odd, (25, 60, 140, 300)))]
        flagged = [ODD] + ([PAYEE] if seed % 2 else [])
        conn = build(rows + extra + ends(last, 2), flagged=flagged)
        try:
            res = stat(conn)
            today = last + timedelta(days=2)
            before = recurring.page(conn, res, today=today)
            count = home.needs_your_eye(conn, res, before["coming_up"])["irregular"]
            self.assertIn(ODD, [r["payee"] for r in before["irregular"]])
            recurring.save_payee(conn, {"payee": ODD, "recurring": False})
            page = recurring.page(conn, res, today=today)
            self.assertNotIn(ODD, [r["payee"] for r in page["irregular"]])
            self.assertEqual(home.needs_your_eye(conn, res, page["coming_up"])["irregular"], count - 1)
            self.assertEqual([(r["id"], r["payee"]) for r in page["not_recurring"]], [(None, ODD)])
            self.assertFalse(next(p for p in db.snapshot(conn)["payees"] if p["name"] == ODD)["recurring"])
            # the stat run now reads the payee as unmarked
            self.assertNotIn(ODD, stat(conn)[recurring.STAT]["data"]["flag_mismatches"]["flagged_not_detected"])
            # the bill's payee too: its series goes, until the series' own word keeps it
            recurring.save_payee(conn, {"payee": PAYEE, "recurring": False})
            page = recurring.page(conn, res, today=today)
            self.assertEqual(mine(page), [])
            sid = only(before)["id"]
            recurring.save_series(conn, res, {"id": sid, "recurring": True}, today=today)
            self.assertEqual([r["id"] for r in mine(recurring.page(conn, res, today=today))], [sid])
            for name in (ODD, PAYEE):
                recurring.save_payee(conn, {"payee": name, "recurring": None})
            recurring.save_series(conn, res, {"id": sid, "recurring": None}, today=today)
            self.assertEqual(series.read(conn), [])
            self.assertEqual(recurring.page(conn, res, today=today), before)
        finally:
            conn.close()

    def test_not_recurring_on_a_payee_wins_over_the_rules(self):
        self.assertEqual(seeds(self.check_payee, 2), [])

    def check_amount_and_day(self, seed):
        """A usual amount the owner set is what Coming up commits and the band is around it;
        a day they set is the day every payment is expected on."""
        rnd = random.Random(seed * 17 + 3)
        cadence, price, rows, last = bill(seed)
        conn = build(rows + ends(last, 2))
        try:
            res = stat(conn)
            today = last + timedelta(days=2)
            sid = only(recurring.page(conn, res, today=today))["id"]
            amount = round(price * 100 * rnd.choice([0.6, 1.0, 1.4]))
            weekly = cadence in series.DAY_CADENCES
            day = rnd.randint(1, 7) if weekly else rnd.randint(1, 31)
            recurring.save_series(conn, res, {"id": sid, "amount": amount, "day": day}, today=today)
            page = recurring.page(conn, res, today=today)
            r = only(page)
            band = rd.AMOUNT_BAND
            self.assertEqual((r["amount"], r["usual_low"], r["usual_high"]),
                             (amount, round(amount * (1 - band)), round(amount * (1 + band))))
            for x in page["coming_up"]["rows"]:
                if x["id"] != sid or x["status"] == "extra":
                    continue
                d = date.fromisoformat(x["expected"])
                if weekly:
                    self.assertEqual(d.isoweekday(), day, x)
                else:
                    self.assertEqual(d.day, min(day, monthrange(d.year, d.month)[1]), x)
                if x["status"] in ("due", "late"):
                    self.assertEqual(x["amount"], amount)
            if r["state"] == "active":
                nxt = date.fromisoformat(r["next_expected"])
                self.assertEqual(nxt.isoweekday() if weekly else nxt.day,
                                 day if weekly else min(day, monthrange(nxt.year, nxt.month)[1]))
        finally:
            conn.close()

    def test_an_amount_and_a_day_set_the_schedule(self):
        self.assertEqual(seeds(self.check_amount_and_day), [])


class Store(unittest.TestCase):
    """server/finance/series.py on its own: what an edit may be, and how one finds its series."""

    def test_clean_takes_an_edit_and_refuses_what_a_series_cannot_keep(self):
        ok = series.clean({"id": "x", "amount": 1599, "cadence": "monthly", "day": 31, "ended_on": "2026-10-31",
                           "recurring": False}, recurring.CADENCES, "weekly")
        self.assertEqual(ok, {"recurring": False, "amount": 1599, "cadence": "monthly", "day": 31,
                              "ended_on": "2026-10-31"})
        self.assertEqual(series.clean({"id": "x"}, recurring.CADENCES, "monthly"),
                         dict.fromkeys(series.EDITS))
        for body, says in (({"amount": 0}, "whole cents"), ({"amount": 15.5}, "whole cents"),
                           ({"amount": True}, "whole cents"), ({"cadence": "daily"}, "How often"),
                           ({"day": 8}, "weekday"), ({"day": 3, "cadence": "twice-monthly"}, "Twice a month"),
                           ({"day": 32, "cadence": "monthly"}, "1 to 31"), ({"ended_on": "31 Oct"}, "Ended on"),
                           ({"ended_on": "2026-02-30"}, "Ended on"), ({"recurring": "no"}, "true, false"),
                           ({"colour": "red"}, "Not something")):
            with self.subTest(body=body), self.assertRaisesRegex(series.SeriesRefused, says):
                series.clean(body, recurring.CADENCES, "weekly")

    def test_an_edit_goes_by_id_then_by_its_payments_and_never_two_to_one_series(self):
        a, b = {"id": "payee:P:1000"}, {"id": "payee:P:2000"}
        keys = {"payee:P:1000": {"k1", "k2"}, "payee:P:2000": {"k3", "k4"}}
        groups = {("payee", "P"): [a, b]}

        def edit(row, sid, own=()):
            return {"row": row, "series_id": sid, "kind": "payee", "group": "P", "keys": set(own)}
        ident, keys_of = (lambda s: s["id"]), (lambda s: keys[s["id"]])
        found = series.resolve([edit(1, "payee:P:1000")], groups, ident, keys_of)
        self.assertEqual(found, {id(a): edit(1, "payee:P:1000")})
        # an id gone: the series with most of its payments
        found = series.resolve([edit(1, "payee:P:900", {"k3", "k4", "k1"})], groups, ident, keys_of)
        self.assertEqual(list(found), [id(b)])
        # an id match wins over payments; the other edit cannot take that series too
        found = series.resolve([edit(1, "payee:P:900", {"k1", "k2"}), edit(2, "payee:P:1000")], groups, ident, keys_of)
        self.assertEqual({k: v["row"] for k, v in found.items()}, {id(a): 2})
        # of two edits holding one series' payments, the newer
        found = series.resolve([edit(1, "x", {"k1"}), edit(5, "y", {"k1"})], groups, ident, keys_of)
        self.assertEqual({k: v["row"] for k, v in found.items()}, {id(a): 5})
        # nothing shared: it waits
        self.assertEqual(series.resolve([edit(1, "z", {"k9"})], groups, ident, keys_of), {})
        # another group's series is never it
        self.assertEqual(series.resolve([{**edit(1, "payee:P:1000"), "group": "Q"}], groups, ident, keys_of), {})

    def test_what_is_sent_is_set_what_is_not_is_kept_and_nothing_set_takes_the_edit_away(self):
        conn = build([(f"k{i}", PAYEE, f"2026-0{i}-05", "-15.00") for i in range(1, 8)] + [("c", "Club", "2026-07-07", "-12.00")])
        self.addCleanup(conn.close)
        res = stat(conn)
        sid = only(recurring.page(conn, res, today=date(2026, 7, 7)))["id"]
        recurring.save_series(conn, res, {"id": sid, "amount": 1700}, today=date(2026, 7, 7))
        recurring.save_series(conn, res, {"id": sid, "cadence": "quarterly"}, today=date(2026, 7, 7))
        [e] = series.read(conn)
        self.assertEqual((e["series_id"], e["amount"], e["cadence"]), (sid, 1700, "quarterly"))
        self.assertEqual(e["keys"], {f"k{i}" for i in range(1, 8)})
        recurring.save_series(conn, res, {"id": sid, "amount": None}, today=date(2026, 7, 7))
        self.assertEqual([(e["amount"], e["cadence"]) for e in series.read(conn)], [(None, "quarterly")])
        recurring.save_series(conn, res, {"id": sid, "cadence": None}, today=date(2026, 7, 7))
        self.assertEqual(series.read(conn), [])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM series_override_txn").fetchone()[0], 0)
        with self.assertRaisesRegex(series.SeriesRefused, "No series has that id"):
            recurring.save_series(conn, res, {"id": "payee:Nobody:100"})
        with self.assertRaisesRegex(series.SeriesRefused, "No payee has that name"):
            recurring.save_payee(conn, {"payee": "Nobody", "recurring": False})
        with self.assertRaisesRegex(series.SeriesRefused, "only says not recurring"):
            recurring.save_payee(conn, {"payee": PAYEE, "recurring": True})

    def test_a_payee_taken_back_after_the_stat_ran_is_marked_recurring_again_before_it_runs_again(self):
        # the stat ran while the payee was said not recurring, so its result does not name it
        rows = [(f"x{i}", ODD, d, a) for i, (d, a) in enumerate((("2026-01-05", "-25.00"), ("2026-01-28", "-60.00"),
                                                                    ("2026-03-16", "-140.00")))]
        conn = build(rows + [("c", "Club", "2026-07-07", "-12.00")], flagged=[ODD])
        self.addCleanup(conn.close)
        recurring.save_payee(conn, {"payee": ODD, "recurring": False})
        res = stat(conn)
        self.assertNotIn(ODD, res[recurring.STAT]["data"]["flag_mismatches"]["flagged_not_detected"])
        recurring.save_payee(conn, {"payee": ODD, "recurring": None})
        page = recurring.page(conn, res, today=date(2026, 7, 7))
        self.assertIn(ODD, [r["payee"] for r in page["irregular"]])
        self.assertEqual(home.needs_your_eye(conn, res, page["coming_up"])["irregular"], 1)

    def test_a_series_only_the_mark_finds_can_be_brought_back_by_its_own_word(self):
        # a yearly bill seen twice is a series only because the rules mark its payee recurring
        conn = build([("y1", PAYEE, "2025-03-10", "-120.00"), ("y2", PAYEE, "2026-03-10", "-120.00"),
                      ("c", "Club", "2026-07-07", "-12.00")], flagged=[PAYEE])
        self.addCleanup(conn.close)
        today = date(2026, 7, 7)
        res = stat(conn)
        sid = only(recurring.page(conn, res, today=today))["id"]
        recurring.save_payee(conn, {"payee": PAYEE, "recurring": False})
        res = stat(conn)                       # the stat run since reads the payee as unmarked
        self.assertEqual([s for s in res[recurring.STAT]["data"]["series"] if s["payee"] == PAYEE], [])
        self.assertEqual(mine(recurring.page(conn, res, today=today)), [])
        self.assertEqual(recurring.series_view(conn, res, sid, today=today)["state"], "not_recurring")
        recurring.save_series(conn, res, {"id": sid, "recurring": True}, today=today)
        self.assertEqual([r["id"] for r in mine(recurring.page(conn, res, today=today))], [sid])
        self.assertNotIn(PAYEE, [r["payee"] for r in recurring.page(conn, res, today=today)["irregular"]])

    def test_a_paused_series_lists_every_payment_and_expects_nothing(self):
        rows = [(f"m{i}", PAYEE, f"2026-0{i}-05", "-50.00") for i in range(1, 7)] + [("odd", PAYEE, "2026-07-05", "-200.00")]
        conn = build(rows + [("c", "Club", "2026-08-20", "-12.00")])
        self.addCleanup(conn.close)
        res = stat(conn)
        today = date(2026, 8, 20)
        sid = only(recurring.page(conn, res, today=today))["id"]
        view = recurring.series_view(conn, res, sid, today=today)
        self.assertEqual((view["state"], view["late"], view["next_expected"]), ("paused", False, None))
        # the odd charge in July answered July's payment, and is listed; nothing unpaid is
        self.assertEqual([(r["expected"], r["status"], r["paid_on"]) for r in view["schedule"]][:2],
                         [("2026-07-05", "different", "2026-07-05"), ("2026-06-05", "paid", "2026-06-05")])
        self.assertFalse([r for r in view["schedule"] if r["status"] in ("due", "late")])

    def test_a_saved_day_the_cadence_no_longer_takes_never_blocks_another_edit(self):
        # a day of the month saved while the series was monthly; detection now finds it weekly
        weekly = [(f"w{i}", PAYEE, (date(2026, 5, 4) + timedelta(days=7 * i)).isoformat(), "-15.00") for i in range(9)]
        conn = build(weekly + [("c", "Club", "2026-07-07", "-12.00")])
        self.addCleanup(conn.close)
        res = stat(conn)
        today = date(2026, 7, 7)
        sid = only(recurring.page(conn, res, today=today))["id"]
        conn.execute("""INSERT INTO series_override(series_id, group_kind, group_name, day, saved_at)
                        VALUES (?, 'payee', ?, 20, 'x')""", (sid, PAYEE))
        self.assertEqual(recurring.series_view(conn, res, sid, today=today)["day"], 1)   # the payments' Monday
        view = recurring.save_series(conn, res, {"id": sid, "recurring": False}, today=today)
        self.assertEqual((view["state"], view["edit"]["day"], view["edit"]["recurring"]), ("not_recurring", None, False))
        # a day sent now is still held to the cadence
        with self.assertRaisesRegex(series.SeriesRefused, "weekday"):
            recurring.save_series(conn, res, {"id": sid, "day": 20}, today=today)

    def test_a_price_more_than_double_is_a_new_series_and_the_edit_stays_with_the_old_one(self):
        old = [(f"k{i}", PAYEE, f"2026-0{i}-05", "-15.00") for i in range(1, 6)]
        conn = build(old + [("c", "Club", "2026-05-07", "-12.00")])
        self.addCleanup(conn.close)
        res = stat(conn)
        sid = only(recurring.page(conn, res, today=date(2026, 5, 7)))["id"]
        recurring.save_series(conn, res, {"id": sid, "day": 9}, today=date(2026, 5, 7))
        new = [(f"n{i}", PAYEE, f"2026-{i:02d}-05", "-40.00") for i in range(6, 13)]
        load(conn, old + new + [("c", "Club", "2026-12-07", "-12.00")])
        res = stat(conn)
        page = recurring.page(conn, res, today=date(2026, 12, 7))
        ids = {r["id"]: r for r in mine(page)}
        self.assertEqual(len(ids), 2, ids)
        self.assertIn(sid, [r["id"] for r in page["stopped"]])
        [now] = [i for i in ids if i != sid]
        self.assertEqual(recurring.series_view(conn, res, sid, today=date(2026, 12, 7))["edit"]["day"], 9)
        self.assertEqual(recurring.series_view(conn, res, now, today=date(2026, 12, 7))["edit"],
                         dict.fromkeys(series.EDITS))

    def test_a_key_the_parser_changed_is_followed(self):
        conn = build([(f"k{i}", PAYEE, f"2026-0{i}-05", "-15.00") for i in range(1, 8)] + [("c", "Club", "2026-07-07", "-12.00")])
        self.addCleanup(conn.close)
        res = stat(conn)
        sid = only(recurring.page(conn, res, today=date(2026, 7, 7)))["id"]
        recurring.save_series(conn, res, {"id": sid, "amount": 1700})
        conn.execute("UPDATE txn SET txn_key = 'new1' WHERE txn_key = 'k1'")
        conn.execute("INSERT INTO txn_key_alias(old_key, txn_id) SELECT 'k1', id FROM txn WHERE txn_key = 'new1'")
        self.assertIn("new1", series.read(conn)[0]["keys"])
        self.assertNotIn("k1", series.read(conn)[0]["keys"])

    def test_a_day_the_owner_set_is_the_grid_s(self):
        days = [date(2026, m, 5) for m in range(1, 7)]
        self.assertEqual(rd.Grid("monthly", days, day=20).day(rd.Grid("monthly", days).index(days[-1])).day, 20)
        self.assertEqual(rd.Grid("monthly", days, day=31).day(rd.Grid("monthly", days).index(date(2026, 2, 10))),
                         date(2026, 2, 28))
        weekly = [date(2026, 9, 1) + timedelta(days=7 * k) for k in range(6)]          # Tuesdays
        g = rd.Grid("weekly", weekly, day=5)
        self.assertEqual({g.day(n).isoweekday() for n in range(g.index(weekly[0]), g.index(weekly[-1]) + 2)}, {5})
        # a fortnight keeps the week nearer its payments: the Friday after each Tuesday, not the one before
        g = rd.Grid("fortnightly", weekly[::2], day=5)
        self.assertEqual(g.day(g.index(weekly[2]) + 1), weekly[2] + timedelta(days=3))


class Readers(unittest.TestCase):
    """The scam check's new subscription and Plan's insurance and repayments read the
    series as Recurring does: Not recurring and an end the owner set change both."""
    INSURER = "Shieldco"
    TODAY = date(2026, 8, 20)

    def record(self, rows):
        """(key, payee, ISO day, "-amount", category) card rows, with a row long before so the
        record goes back far enough to call a payee new, and one on TODAY so the account's
        statements cover to then."""
        conn = db.connect(":memory:")
        db.import_user_tables(conn, {**{k: [dict(x) for x in v] for k, v in SNAP.items()},
                                     "categories": [dict(c) for c in CATEGORIES + SUBS] + [
                                         {"name": "Insurance", "bucket": "Spend", "group": "Fixed",
                                          "need_class": "Need", "sort_order": 160}]})
        rows = [("early", "Club", "2026-01-02", "-12.00", "Subscriptions"), *rows,
                ("now", "Club", self.TODAY.isoformat(), "-12.00", "Subscriptions")]
        keyed = [row(k, amount, date=day, payee=payee, merchant=payee.upper(), category=cat, card_last4="4242")
                 for k, payee, day, amount, cat in rows]
        db.load_parsed(conn, parsed([block()], keyed),
                       payees=[{"name": p, "kind": "Merchant", "country": "SGP"} for p in sorted({r[1] for r in rows})])
        self.addCleanup(conn.close)
        return conn

    def sid(self, conn, res, payee):
        return next(r["id"] for r in every(recurring.page(conn, res, today=self.TODAY)) if r["payee"] == payee)

    def test_the_new_subscription_check_reads_the_owner_s_edits(self):
        from finance import scam_checks
        conn = self.record([(f"s{m}", PAYEE, f"2026-0{m}-05", "-15.99", "Subscriptions") for m in (5, 6, 7, 8)])
        scam_checks.save_settings(conn, {"new_sub_days": 120})
        res = stat(conn)
        sid = self.sid(conn, res, PAYEE)

        def subs():
            return [f["key"] for f in scam_checks.flags(conn, results=res)
                    if any(x["code"] == "new_subscription" for x in f["reasons"])]
        self.assertEqual(subs(), ["s5"])
        # ended after its last charge: nothing of it runs on
        recurring.save_series(conn, res, {"id": sid, "ended_on": "2026-08-10"}, today=self.TODAY)
        self.assertEqual(subs(), [])
        # ended after its second charge: the two since are a new monthly payment of their own
        recurring.save_series(conn, res, {"id": sid, "ended_on": "2026-06-30"}, today=self.TODAY)
        self.assertEqual(subs(), ["s7"])
        recurring.save_series(conn, res, {"id": sid, "ended_on": None, "recurring": False}, today=self.TODAY)
        self.assertEqual(subs(), [])
        recurring.save_series(conn, res, {"id": sid, "recurring": None}, today=self.TODAY)
        self.assertEqual(subs(), ["s5"])
        # a payee's own Not recurring wins over the charges a month apart, with or without the stat
        recurring.save_payee(conn, {"payee": PAYEE, "recurring": False})
        self.assertEqual((subs(), [f["key"] for f in scam_checks.flags(conn)]), ([], []))
        self.assertEqual(scam_checks.page(conn, res)["flags"], [])

    def test_a_series_that_ended_and_charges_again_is_a_new_subscription(self):
        from finance import scam_checks
        days = ("2026-01-05", "2026-02-05", "2026-03-05", "2026-04-05", "2026-07-05", "2026-08-05")
        conn = self.record([(f"s{d[5:7]}", PAYEE, d, "-15.99", "Subscriptions") for d in days])
        res = stat(conn)
        sid = self.sid(conn, res, PAYEE)

        def subs():
            return [f["key"] for f in scam_checks.flags(conn, results=res)
                    if any(x["code"] == "new_subscription" for x in f["reasons"])]
        self.assertEqual(subs(), [])                   # first charged long before
        recurring.save_series(conn, res, {"id": sid, "ended_on": "2026-04-30"}, today=self.TODAY)
        self.assertEqual(subs(), ["s07"])

    def test_plan_s_insurance_reads_the_owner_s_edits(self):
        from finance import suggestions
        conn = self.record([(f"i{m}", self.INSURER, f"2026-0{m}-10", "-120.00", "Insurance") for m in range(1, 9)])
        res = stat(conn)
        sid = self.sid(conn, res, self.INSURER)

        def insurance():
            with mock.patch.object(suggestions.settings, "text_setting", return_value=""):
                return suggestions.suggestions(conn, res, None, self.TODAY)["insurance"]
        self.assertEqual(insurance(), {"per_month": 12_000, "series": 1})
        # the usual amount the owner set is what it costs
        recurring.save_series(conn, res, {"id": sid, "amount": 15_000}, today=self.TODAY)
        self.assertEqual(insurance(), {"per_month": 15_000, "series": 1})
        # an end still ahead leaves it running; one behind the statements stops it
        recurring.save_series(conn, res, {"id": sid, "amount": None, "ended_on": "2026-12-31"}, today=self.TODAY)
        self.assertEqual(insurance(), {"per_month": 12_000, "series": 1})
        recurring.save_series(conn, res, {"id": sid, "ended_on": "2026-08-15"}, today=self.TODAY)
        self.assertIsNone(insurance())
        recurring.save_series(conn, res, {"id": sid, "ended_on": None, "recurring": False}, today=self.TODAY)
        self.assertIsNone(insurance())
        recurring.save_series(conn, res, {"id": sid, "recurring": None}, today=self.TODAY)
        recurring.save_payee(conn, {"payee": self.INSURER, "recurring": False})
        self.assertIsNone(insurance())

    def test_plan_s_repayments_read_the_owner_s_edits(self):
        from finance import suggestions
        conn = self.record([(f"r{m}", "Lendco", f"2026-0{m}-03", "-450.00", "Subscriptions") for m in range(1, 9)])
        res = stat(conn)
        sid = self.sid(conn, res, "Lendco")
        kept = {"repayment_payee": "Lendco"}

        def repayments():
            with mock.patch.object(suggestions.settings, "text_setting", side_effect=lambda k: kept.get(k, "")):
                return suggestions.suggestions(conn, res, None, self.TODAY)["repayments"]
        self.assertEqual(repayments(), {"per_month": 45_000, "series": 1})
        recurring.save_series(conn, res, {"id": sid, "ended_on": "2026-08-05"}, today=self.TODAY)
        self.assertIsNone(repayments())
        recurring.save_series(conn, res, {"id": sid, "ended_on": None, "recurring": False}, today=self.TODAY)
        self.assertIsNone(repayments())


class Upgrade(unittest.TestCase):
    """Version 6 on a version 5 record: every row kept, the edit tables added, a copy left."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "kopifund.db"
        p = mock.patch.dict(os.environ, {"KOPIFUND_DATA": self.tmp.name, "KOPIFUND_DB": str(self.path)})
        p.start()
        self.addCleanup(p.stop)
        v5 = (Path(__file__).parent / "fixtures" / "record-v5.sql").read_text(encoding="utf-8")
        with closing(__import__("sqlite3").connect(self.path)) as c:
            c.executescript(v5 + """
INSERT INTO currency(code) VALUES ('SGD');
INSERT INTO account(id, name, account_no) VALUES (1, 'POSB', '111-11111-1');
INSERT INTO payee(id, name, kind, recurring) VALUES (1, 'Gym', 'Merchant', 1);
INSERT INTO txn(id, txn_key, account_id, account_no, currency, txn_date, amount_minor, flow_class, payee_id,
                first_seen, last_seen) VALUES
  (1, 'k1', 1, '111-11111-1', 'SGD', '2026-08-05', -5000, 'External', 1, 'x', 'x'),
  (2, 'k2', 1, '111-11111-1', 'SGD', '2026-09-05', -5000, 'External', 1, 'x', 'x');
INSERT INTO txn_marked_fine(txn_key, marked_at) VALUES ('k2', 'x');
PRAGMA user_version = 5;
""")

    def test_a_version_5_record_keeps_every_row_and_gains_the_edit_tables(self):
        with closing(__import__("sqlite3").connect(self.path)) as c:
            before = {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                      for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        with closing(db.connect()) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 7)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            for t, n in before.items():
                if n:          # (the upgrade seeds the shipped buckets, which this made-up record lacks)
                    self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0], n, t)
            self.assertLessEqual({"series_override", "series_override_txn"}, db._tables(conn))
            series.set_payee(conn, "Gym", False)
            self.assertEqual(series.payees_off_in(conn), {"Gym"})
        [copy] = sorted((Path(self.tmp.name) / "backups").glob("kopifund-*.db"))
        self.assertIn("v5-before-v7", copy.name)
        # opened again at the version: nothing more to do, the edit kept
        with closing(db.connect()) as conn:
            self.assertEqual(series.payees_off_in(conn), {"Gym"})


class Api(unittest.TestCase):
    """GET /api/recurring/series and the two writes, through serve's route tables."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        p = mock.patch.dict(os.environ, {"KOPIFUND_DATA": str(root), "KOPIFUND_DB": str(root / "kopifund.db"),
                                         "KOPIFUND_CACHE": str(root / "cache")})
        p.start()
        self.addCleanup(p.stop)
        rows = [(f"k{i}", PAYEE, f"2026-0{i}-05", "-15.00") for i in range(1, 8)] + [("c", "Club", "2026-07-07", "-12.00")]
        with closing(db.connect()) as conn:
            db.import_user_tables(conn, {**{k: [dict(x) for x in v] for k, v in SNAP.items()},
                                         "categories": [dict(c) for c in CATEGORIES + SUBS]})
            load(conn, rows)
            res = stat(conn)
            conn.commit()
        r = mock.patch("finance.results_cache.results", lambda req=None: res)
        r.start()
        self.addCleanup(r.stop)
        self.sid = res[recurring.STAT]["data"]["series"][0]["id"]

    def post(self, path, body):
        return serve.write(path, {}, json.dumps(body).encode())

    def test_the_view_the_edit_and_the_refusals(self):
        view = serve.answer("/api/recurring/series", {"id": self.sid})
        self.assertEqual((view["id"], view["payee"], view["edit"]), (self.sid, PAYEE, dict.fromkeys(series.EDITS)))
        with self.assertRaises(serve.NotFound):
            serve.answer("/api/recurring/series", {"id": "payee:Nobody:1"})
        saved = self.post("/api/recurring/series", {"id": self.sid, "amount": 1650, "cadence": "monthly", "day": 9})
        self.assertEqual((saved["amount"], saved["day"], saved["edit"]["cadence"]), (1650, 9, "monthly"))
        self.assertEqual(serve.answer("/api/recurring", {})["active"][0]["amount"], 1650)
        with self.assertRaisesRegex(serve.Refused, "How often"):
            self.post("/api/recurring/series", {"id": self.sid, "cadence": "daily"})
        with self.assertRaisesRegex(serve.Refused, "No payee"):
            self.post("/api/recurring/payee", {"payee": "Nobody", "recurring": False})
        self.assertEqual(self.post("/api/recurring/payee", {"payee": PAYEE, "recurring": False}),
                         {"payee": PAYEE, "recurring": False})
        self.assertEqual(serve.answer("/api/recurring", {})["active"], [])


if __name__ == "__main__":
    unittest.main()
