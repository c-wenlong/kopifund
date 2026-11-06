"""G16: the recurring detector and the Recurring page against a few hundred seeded,
made-up histories at one payee: one or two subscriptions with overlapping lives,
billed monthly, twice a month or quarterly, price rises, drops and returns, new prices seen once at
the end that either bill could take, top-ups and one-off purchases beside them, and
payments skipped, early or late. Each history is checked for what must hold
whatever its shape, not for one expected answer."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import itertools
import random
import unittest
from collections import Counter
from datetime import date, timedelta

from finance import home, recurring
from analytics.stats import recurring_detector as rd
from test_recurring import detected, detected_result

SEEDS = 300                      # the histories history() makes; test_recurring_cents holds a result for each
PAYEE = "Multico"

# The seeds each property runs on. Each property once ran on every seed of a
# stride of these 300 (all of them, the odd ones, every third, the even ones),
# nine seconds together. Each list keeps the seeds of its stride that between
# them ran every step from one line to the next of the recurring code
# (finance/cadence.py, recurring.py, series.py, home.py and the detector) that
# the whole stride ran, traced once over the old run, and enough more that
# each pair of the shapes `shape` names shows at least twice (or as often as
# the stride has it). test_the_seeds_kept_cover_every_pair_of_shapes holds the
# second whatever history() becomes; after a change to it, trace and pick again.
KEPT = {
    "histories": [
        3, 4, 5, 6, 7, 8, 15, 16, 21, 22, 23, 30, 35, 39, 47, 48, 50, 55, 58, 59, 61, 63, 65, 67, 68, 73, 77,
        81, 82, 83, 86, 103, 117, 120, 121, 130, 134, 139, 155, 162, 178, 184, 199, 205, 206, 212, 216, 217,
        219, 229, 233, 234, 249, 250, 251, 258, 260, 267, 275, 281, 298],
    "backfill": [
        3, 5, 7, 13, 15, 21, 23, 29, 31, 33, 35, 43, 47, 53, 55, 59, 61, 63, 65, 75, 77, 79, 81, 83, 103,
        117, 121, 125, 139, 155, 175, 177, 187, 191, 195, 199, 205, 207, 219, 229, 233, 241, 245, 249, 251,
        267, 269, 275, 281, 283, 291],
    "page": [
        3, 6, 12, 15, 18, 21, 30, 42, 48, 63, 72, 75, 78, 81, 99, 108, 117, 120, 123, 129, 138, 162, 171,
        177, 186, 195, 204, 207, 210, 216, 219, 225, 228, 231, 234, 246, 249, 258, 264, 267, 270, 279, 282,
        285, 291, 297],
    "stale": [
        0, 4, 12, 14, 16, 18, 22, 30, 42, 44, 46, 48, 50, 54, 58, 68, 70, 78, 82, 86, 98, 108, 120, 128, 130,
        134, 158, 162, 172, 176, 178, 184, 186, 204, 206, 212, 214, 216, 218, 230, 234, 236, 244, 248, 250,
        258, 272, 282, 296, 298],
}
STRIDES = {"histories": range(SEEDS), "backfill": range(1, SEEDS, 2), "page": range(0, SEEDS, 3),
           "stale": range(0, SEEDS, 2)}


def month_day(i, day):
    return date(i // 12, i % 12 + 1, day)


def history(seed):
    """(rows, subs, end): the charges of one made-up payee, as (payee, ISO day, "-amount")
    rows; each subscription as {day, prices: {ISO day: amount}}; and the record's last day."""
    rnd = random.Random(seed)
    start = 2024 * 12 + rnd.randint(0, 5)
    last = start + rnd.randint(14, 26)                   # the record's last month
    subs, rows = [], []
    base = rnd.choice([15.0, 18.0, 25.0, 40.0])
    for n in range(rnd.choice([1, 1, 2])):
        # the first plan is billed monthly, or twice a month (rent in halves); a second costs
        # at least twice the first's dearest price, on another day, monthly or quarterly
        price = base if n == 0 else round(max(s["top"] for s in subs) * rnd.uniform(2.2, 3.5), 2)
        # (quarterly only where the record leaves room for six charges of it)
        cadence = (rnd.choice(["monthly", "monthly", "monthly", "twice-monthly"]) if n == 0
                   else rnd.choice(["monthly", "monthly", "quarterly"]) if last - start >= 18 else "monthly")
        every = 3 if cadence == "quarterly" else 1
        day = rnd.randint(1, 28 if cadence != "twice-monthly" else 14)
        days = [day, day + rnd.randint(9, 14)] if cadence == "twice-monthly" else [day]
        first = start if n == 0 else rnd.randint(start, max(start, last - 6 * every))
        billed = [(m, d) for m in range(first, last + 1, every) for d in days]
        prices, levels = {}, [price]
        inner = list(range(3 * len(days), len(billed) - 1))
        changes = set(rnd.sample(inner, k=min(len(inner), rnd.choice([0, 1, 1, 2]))))
        # a new price seen only in the last charge or two, each its own: the lone charges
        # either of two bills could take
        tail = set(range(len(billed) - rnd.choice([1, 2]), len(billed))) if len(billed) >= 6 and rnd.random() < 0.35 else set()
        # the second plan never comes within 1.6 times the first's dearest price: two plans
        # at about one amount are told apart by nothing but their days, which no amount level sees
        floor = 13.0 if n == 0 else round(max(x["top"] for x in subs) * 1.6, 2)
        current, skips, skipped = price, 0, None
        for j, (m, d) in enumerate(billed):
            if j in changes:
                # a rise or a drop of a sixth to a quarter, or back to the first price
                current = (price if current != price and rnd.random() < 0.5
                           else max(floor, round(current * rnd.choice([0.8, 0.85, 1.18, 1.25]), 2)))
                levels.append(current)
            if j in tail:
                current = max(floor, round(current * rnd.choice([0.7, 1.3, 1.45]), 2))
                levels.append(current)
            if (0 < j < len(billed) - 1 and len(billed) >= 8 and skips < max(1, len(billed) // (12 * len(days)))
                    and rnd.random() < 0.05 and j not in changes and j not in tail and j - 1 not in changes
                    and j - 1 != skipped):
                skips, skipped = skips + 1, j             # a payment skipped, once a year at most, never two in a row
                continue
            when = month_day(m, d) + timedelta(days=rnd.choice([0, 0, 0, 1, 2, -1]))
            prices[when.isoformat()] = current
        subs.append({"day": day, "prices": prices, "low": min(levels), "top": max(levels), "cadence": cadence})
    for d, a in (p for s in subs for p in s["prices"].items()):
        rows.append((PAYEE, d, f"-{a:.2f}"))
    span = (month_day(last, 28) - month_day(start, 1)).days
    # top-ups at S$10, under every plan's band, and one-off purchases far dearer than any
    # plan, on days that keep to no cadence by the detector's own test (charges that do are
    # a bill whoever means them as top-ups; what is checked here is that they never join a
    # plan, take its payments or hide it)
    for count, amount in ((rnd.randint(0, 10), lambda: 10.0), (rnd.randint(0, 3), lambda: max(x["top"] for x in subs) * rnd.uniform(4, 9))):
        while True:
            when = sorted(month_day(start, 1) + timedelta(days=rnd.randint(0, span)) for _ in range(count))
            if not rd.cadence_for(rd._merged(d.isoformat() for d in when)):
                break
        rows += [(PAYEE, d.isoformat(), f"-{amount():.2f}") for d in when]
    end = month_day(last, 28)
    rows.append(("Club", end.isoformat(), "-12.00"))
    return rows, subs, end


def shape(seed, prop):
    """What a seed's history holds, as (name, value) pairs, for the property
    `prop` (a KEPT key): its plans and cadences, each plan's prices (how many,
    rising, falling, back to the first, new at the end, still billing at the
    end), top-ups, one-off purchases and the record's length, and what the
    property does with the seed (how far back the stale stat ran, marked
    recurring or not)."""
    rows, subs, end = history(seed)
    mine = [r for r in rows if r[0] == PAYEE]
    billed = {d for s in subs for d in s["prices"]}
    extra = [r for r in mine if r[1] not in billed]
    first = min(date.fromisoformat(r[1]) for r in mine)
    out = {"plans": len(subs), "cadences": "+".join(s["cadence"] for s in subs),
           "top-ups": any(r[2] == "-10.00" for r in extra), "purchases": any(r[2] != "-10.00" for r in extra),
           "long": (end.year * 12 + end.month) - (first.year * 12 + first.month) >= 18}
    for i, sub in enumerate(subs):
        prices = list(sub["prices"].values())
        out |= {f"{i} levels": min(len(set(prices)), 3),
                f"{i} rises": any(b > a for a, b in zip(prices, prices[1:])),
                f"{i} drops": any(b < a for a, b in zip(prices, prices[1:])),
                f"{i} returns": any(b == prices[0] != a for a, b in zip(prices, prices[1:])),
                f"{i} new at the end": len(prices) > 1 and prices[-1] not in prices[:-1],
                f"{i} billing at the end": any(d[:7] == end.isoformat()[:7] for d in sub["prices"])}
    if prop == "stale":
        out["back"] = 1 + seed % 2
    if prop == "page":
        out |= {"cut": "first quarter" if seed % 3 == 0 else f"back {1 + seed % 2}", "flagged": bool(seed % 2)}
    return set(out.items())


def pairs(seed, prop):
    """Every shape of the seed, and every two of them together."""
    one = sorted(shape(seed, prop), key=str)
    return {(a,) for a in one} | set(itertools.combinations(one, 2))


class Histories(unittest.TestCase):
    def check(self, seed):
        rows, subs, end = history(seed)
        conn, res = detected(*rows)
        try:
            data = res["recurring-detector"]["data"]
            series = [s for s in data["series"] if s["payee"] == PAYEE]
            # one series per subscription: top-ups and purchases are none, a price change splits none
            self.assertEqual(len(series), len(subs), [(s["median_amount_minor"], s["cadence"], s["first_seen"]) for s in series])
            for s in series:
                # its amount is one its plan really charged, and over the floor
                # G20: in cents, within a cent of a price it charged (a median of two rounds half up)
                self.assertTrue(any(round(sub["low"] * 100) - 1 <= s["median_amount_minor"] <= round(sub["top"] * 100) + 1
                                    for sub in subs),
                                (s["median_amount_minor"], [(x["low"], x["top"]) for x in subs]))
                self.assertGreaterEqual(s["median_amount_minor"], rd.MIN_AMOUNT_MINOR)
                self.assertIn(s["cadence"], {sub["cadence"] for sub in subs})
            # no charge is two series': every transaction answers for one bill at most
            rec = recurring.Record(conn, data)
            seen = {}
            for s in rec.data["active"] + rec.data["paused"] + rec.data["stopped"]:
                if s["payee"] != PAYEE:
                    continue
                for p in rec.prepare(s, end)[0]:
                    for k in p.keys:
                        self.assertNotIn(k, seen, (k, s["id"], seen.get(k)))
                        seen[k] = s["id"]
            page = recurring.page(conn, res, today=end)
            up = page["coming_up"]["rows"]
            for r in up:
                if r["status"] == "paid" and r["payee"] == PAYEE:
                    # paid only by a charge in its usual range, near the day it was expected
                    self.assertTrue(r["usual_low"] <= r["paid_amount"] <= r["usual_high"], r)
                    gap = abs((date.fromisoformat(r["paid_on"]) - date.fromisoformat(r["expected"])).days)
                    self.assertLessEqual(gap, rd.BANDS["monthly"][1] - 26, r)
            # every plan still billing in the last month is on Coming up, and in the committed total
            billing = [s for s in subs if any(d[:7] == end.isoformat()[:7] for d in s["prices"])]
            live = [r for r in up if r["payee"] == PAYEE and r["status"] != "extra"]
            self.assertGreaterEqual(len({r["id"] for r in live}), len(billing), (live, billing))
        finally:
            conn.close()

    def check_stale(self, seed):
        rows, subs, end = history(seed)
        back = 1 + seed % 2
        cut = date(end.year + (end.month - 1 - back) // 12, (end.month - 1 - back) % 12 + 1, 28).isoformat()
        older = [r for r in rows if r[1] <= cut and r[0] == PAYEE] + [("Club", cut, "-12.00")]
        stale = detected_result(*older)
        conn, fresh = detected(*rows)
        try:
            def credit(res):
                data = res["recurring-detector"]["data"]
                rec = recurring.Record(conn, data)
                return {k: s["id"] for s in rec.data["active"] + rec.data["paused"] + rec.data["stopped"] if s["payee"] == PAYEE
                        for p in rec.prepare(s, end)[0] for k in p.keys}
            was, now = credit(stale), credit(fresh)
            ids = set(was.values()) & set(now.values())
            # where both results hold the series, a charge goes to the same one
            differ = {k: (was[k], now.get(k)) for k in was if was[k] in ids and now.get(k) in ids and was[k] != now[k]}
            self.assertEqual(differ, {})
        finally:
            conn.close()

    def check_backfill(self, seed):
        """The stat ran before the record's first months were loaded: reading the page
        with it afterwards still credits no charge to two series."""
        rows, subs, end = history(seed)
        mine = sorted(r for r in rows if r[0] == PAYEE)
        cut = mine[len(mine) // 4][1] if mine else end.isoformat()
        stat = detected_result(*[r for r in rows if r[1] >= cut or r[0] != PAYEE])
        conn, _ = detected(*rows)
        try:
            data = stat["recurring-detector"]["data"]
            rec = recurring.Record(conn, data)
            seen = {}
            for s in rec.data["active"] + rec.data["paused"] + rec.data["stopped"]:
                if s["payee"] != PAYEE:
                    continue
                for p in rec.prepare(s, end)[0]:
                    for k in p.keys:
                        self.assertNotIn(k, seen, (k, s["id"], seen.get(k)))
                        seen[k] = s["id"]
        finally:
            conn.close()

    def test_a_backfilled_record_credits_no_charge_twice(self):
        failed = []
        for seed in KEPT["backfill"]:
            try:
                self.check_backfill(seed)
            except AssertionError as e:
                failed.append((seed, str(e)[:300]))
        self.assertEqual(failed, [], f"{len(failed)} of {len(KEPT['backfill'])} seeds failed")

    def check_page_equal(self, seed):
        """The page read with a stale stat (one or two months of rows loaded since it ran, or
        the first quarter of the record loaded after it) is the page read with a fresh one."""
        rows, subs, end = history(seed)
        mine = sorted(r for r in rows if r[0] == PAYEE)
        if seed % 3 == 0:
            cut = mine[len(mine) // 4][1]
            older = [r for r in rows if r[1] >= cut or r[0] != PAYEE]
        else:
            back = 1 + seed % 2
            cut = date(end.year + (end.month - 1 - back) // 12, (end.month - 1 - back) % 12 + 1, 28).isoformat()
            older = [r for r in rows if r[1] <= cut and r[0] == PAYEE] + [("Club", cut, "-12.00")]
        # every other history has its payee marked recurring by the rules, so the list of
        # marked payees with no regular spacing is compared too
        flagged = [PAYEE] if seed % 2 else []
        stale = detected_result(*older, flagged=flagged)
        conn, fresh = detected(*rows, flagged=flagged)
        try:
            was = recurring.page(conn, stale, today=end)
            now = recurring.page(conn, fresh, today=end)
            for part in ("active", "paused", "stopped", "coming_up", "totals", "irregular"):
                self.assertEqual(was[part], now[part], part)
            eye = [home.needs_your_eye(conn, res, recurring.coming_up(conn, res, today=end))["irregular"]
                   for res in (stale, fresh)]
            self.assertEqual(eye[0], eye[1], "Home's irregular count")
        finally:
            conn.close()

    def test_a_stale_stat_reads_as_a_fresh_one_on_the_page(self):
        failed = []
        for seed in KEPT["page"]:
            try:
                self.check_page_equal(seed)
            except AssertionError as e:
                failed.append((seed, str(e)[:300]))
        self.assertEqual(failed, [], f"{len(failed)} of {len(KEPT['page'])} seeds failed")

    def test_a_stale_result_credits_charges_as_a_fresh_one_does(self):
        failed = []
        for seed in KEPT["stale"]:
            try:
                self.check_stale(seed)
            except AssertionError as e:
                failed.append((seed, str(e)[:300]))
        self.assertEqual(failed, [], f"{len(failed)} of {len(KEPT['stale'])} seeds failed")

    def test_seeded_histories(self):
        failed = []
        for seed in KEPT["histories"]:
            try:
                self.check(seed)
            except AssertionError as e:
                failed.append((seed, str(e)[:300]))
        self.assertEqual(failed, [], f"{len(failed)} of {len(KEPT['histories'])} seeds failed")

    def test_the_seeds_kept_cover_every_pair_of_shapes(self):
        for prop, stride in STRIDES.items():
            with self.subTest(prop):
                self.assertLessEqual(set(KEPT[prop]), set(stride))
                every = Counter(p for seed in stride for p in pairs(seed, prop))
                kept = Counter(p for seed in KEPT[prop] for p in pairs(seed, prop))
                short = {p for p, n in every.items() if kept[p] < min(2, n)}
                self.assertEqual(short, set(), "pick the seeds again (KEPT): these shapes are run too seldom")


if __name__ == "__main__":
    unittest.main()
