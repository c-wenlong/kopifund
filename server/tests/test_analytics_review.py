"""An adversarial recomputation of every shipped stat result.

Nothing here imports a stat's arithmetic from `analytics.stats` (only the phase
boundaries, which are configuration). Each figure is recomputed from the raw
snapshot with plain Python in this file and held against the number in
`data/cache/results/<id>-<date>.json`, so a change in the stat modules that
also changes a headline has to be defended rather than absorbed.

The cache is gitignored, so the whole module skips when it is not there. Run
`python3 server/finance.py analyse` (or `--from-csv`) first to populate it.

Ten of these failed on purpose against the 2026-09-04 cache. Each named one of
the review's open defects, and all ten are fixed:

  * `TravelDays.test_the_day_count_is_distinct_days` (trip days double counted)
  * `Denominators.test_every_share_uses_the_same_spend_base`
  * `ForwardView.test_the_spend_rate_is_not_set_by_one_refund_month`
  * `RecurringDetector.test_no_food_stall_is_a_live_recurring_series`
  * `RecurringDetector.test_a_series_is_not_stitched_across_a_long_hole`
  * `SavingsRunway.test_the_headline_cash_is_not_a_carried_balance`
  * the four `BenchmarksSg` like-for-like tests

Four assertions moved with the fixes, and each says in its own docstring what
moved and why: the forward view's ending cash and its month-by-month
arithmetic (a median rate and an other_outflow line), the weekly meal series
(a calendar week count beside the weeks that had a meal), and the spend-rate
test (the window is restated, not filtered, so the assertion is on the rate
the module used rather than on the months surviving a cut).

Delete a test only when the defect it names is fixed.

Nothing here names a payee, a trip, an account or a month from anyone's data:
every assertion is a property that holds for any owner's record. Checks that
can only be stated with a name (this payee is monthly at about this amount,
that trip carries the rent) are read from an optional file in the data folder,
`notes/review-expectations.json`, and skip when it is absent. Its shape, every
key optional:

    {"recurring":            [{"payee": "...", "cadence": "monthly", "amount": 0.0}],
     "active":               ["payee", ...],
     "stopped":              ["payee", ...],
     "seen":                 {"payee": ["YYYY-MM first", "YYYY-MM last"]},
     "not_recurring":        ["payee", ...],
     "flagged_not_detected": ["payee", ...],
     "rent_on_trips":        {"payee": "...", "trip": "...",
                              "trips": ["...", ...], "min_share": 0.2}}
"""
import isolate  # first: no test writes the real record or cache
import datetime as dt
import json
import statistics
import unittest
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
from finance.money import stat_cents  # noqa: E402
# Audit: the real data folder only when the run asks (KOPIFUND_TEST_REAL_DATA=1, server/tests/isolate.py)
REAL_DATA = isolate.real_data_folder()
if REAL_DATA is None:
    raise unittest.SkipTest("reads the real cache: run with KOPIFUND_TEST_REAL_DATA=1 to include it")
CACHE = REAL_DATA / "cache"       # the real cache, only ever read; tests write elsewhere
LATEST = CACHE / "latest.json"


def _load():
    if not LATEST.exists():
        return None, {}
    index = json.loads(LATEST.read_text(encoding="utf-8"))
    snap = json.loads((CACHE / index["snapshot"]).read_text(encoding="utf-8"))
    results = {}
    for stat_id, name in index.get("results", {}).items():
        path = CACHE / "results" / name
        if path.exists():
            results[stat_id] = json.loads(path.read_text(encoding="utf-8"))
    return snap, results


SNAP, RESULTS = _load()


def _expectations():
    """The owner's named checks, or {} when the file is not there."""
    path = REAL_DATA / "notes" / "review-expectations.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


EXPECT = _expectations()


# ---------------------------------------------------------------- recomputed

def sgd(row):
    # G20: the snapshot holds cents only; the recomputation here stays in plain dollars
    return (row["amount_sgd_minor"] or 0) / 100


def out(rows):
    """Money out, signed, so a refund inside the set subtracts."""
    return round(-sum(sgd(r) for r in rows), 2)


def into(rows):
    return round(sum(sgd(r) for r in rows), 2)


def months_between(first, last):
    got, year, month = [], int(first[:4]), int(first[5:7])
    while f"{year:04d}-{month:02d}" <= last:
        got.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return got


def every_month(snap):
    days = [r["date"] for r in snap["transactions"] if r["date"]]
    return months_between(min(days)[:7], max(days)[:7])


def period_month(block):
    parts = block["name"].split(" · ")
    return parts[2] if len(parts) >= 3 else (block["period_end"] or "")[:7]


def covered_months(snap):
    """The months every open bank account covers, and at least one does
    (decision 6). Wallets (Accounts subclass Wallet) are funded from the bank
    accounts and are never expected, so a wallet that printed nothing leaves
    the month covered, and a month only a wallet covers is not. A bank
    statement covers its month and back to its earliest row (a row in the
    last three days of the month just before is posting lag); an empty one
    that opens where the last closed covers the whole gap, and after it the
    next k - 1 months of its k-month cadence (at most 5), short of any later
    statement. An account closes at
    a last statement that leaves 0 in every currency. The months before the
    first phase are partial: they hold card rows only."""
    import calendar
    from analytics.stats.common import PHASES
    first_phase = PHASES[0]["from"]
    wallets = {a["account_no"] for a in snap["accounts"] if a.get("subclass") == "Wallet"}
    printed = defaultdict(lambda: defaultdict(list))
    for b in snap["periods"]:
        if not b["name"].endswith(" · card") and b["account_no"] not in wallets:
            printed[b["account_no"]][period_month(b)].append(b)
    own = {b["name"]: m for by_month in printed.values() for m, bs in by_month.items() for b in bs}
    reach, rows_on = {}, set()
    for r in snap["transactions"]:
        name = r.get("period")
        if name not in own or not r["date"]:
            continue
        rows_on.add(name)
        m = r["date"][:7]
        before = months_between(m, own[name])
        last_day = calendar.monthrange(int(m[:4]), int(m[5:7]))[1]
        if len(before) > 1 and not (len(before) == 2 and int(r["date"][8:10]) > last_day - 3):
            reach[name] = min(reach.get(name, own[name]), m)
    spans = {}
    for account, by_month in printed.items():
        months = sorted(by_month)
        cover, held, idle = set(), {}, 0
        for before, m in zip([None] + months, months):
            blocks = by_month[m]
            if idle > 1:
                # an idle statement covers the months before its next was due, up to this one
                ahead = months_between(before, f"{int(before[:4]) + 1}-{before[5:]}")
                cover |= {x for x in ahead[1:min(idle, 6)] if x < m}
            start, idle = min(reach.get(b["name"], m) for b in blocks), 0
            if before:
                gap = months_between(before, m)[1:]
                start = max(start, gap[0])
                opens = [b for b in blocks if b["opening_minor"] is not None]
                if (not any(b["name"] in rows_on for b in blocks) and opens
                        and all(b["opening_minor"] == held.get(b["currency"], 0) for b in opens)):
                    start, idle = gap[0], len(gap)
            cover |= set(months_between(start, m))
            held.update({b["currency"]: b["closing_minor"] for b in blocks
                         if b["has_balances"] and b["closing_minor"] is not None})
        closed = bool(held) and not any(held.values())
        if not closed and idle > 1:
            ahead = months_between(months[-1], f"{int(months[-1][:4]) + 1}-{months[-1][5:]}")
            cover |= set(ahead[1:min(idle, 6)])
        spans[account] = (min(cover), months[-1] if closed else None, cover)
    full = []
    for m in every_month(snap):
        expect = {a for a, (start, end, _) in spans.items() if start <= m and (end is None or m <= end)}
        covering = {a for a in expect if m in spans[a][2]}
        if not (m < first_phase or expect - covering or not covering):
            full.append(m)
    return full


def external(snap):
    return [r for r in snap["transactions"] if r["flow_class"] == "External"]


def spend_rows(snap):
    return [r for r in external(snap)
            if r["bucket"] == "Spend" and not r["synthetic"]]


def income_rows(snap):
    return [r for r in external(snap)
            if r["bucket"] == "Income" and not r["synthetic"]]


def transfer_rows(snap):
    return [r for r in external(snap)
            if r["bucket"] == "Transfer" and not r["synthetic"]]


def investing_rows(snap):
    return [r for r in transfer_rows(snap) if r["need_class"] == "Investment"]


# The cadence bands the recurring detector documents, and the categories it
# treats as habit. Configuration, like PHASES, not arithmetic.
BANDS = {"weekly": (5, 9), "monthly": (26, 35), "quarterly": (80, 100),
         "yearly": (340, 390)}
NEED = {"weekly": 6}                 # occurrences a run needs; three elsewhere
SPAN = {"weekly": 56}                # days a run of that cadence must cover
HABIT = ("Eating out", "Coffee", "Groceries", "Transport (public)", "Transport (private)", "Transport")


def recurring_outflow(snap):
    """{payee: rows} for the outflow the recurring detector looks at."""
    got = defaultdict(list)
    for r in snap["transactions"]:
        if (r["flow_class"] == "External" and not r["synthetic"] and r["date"]
                and sgd(r) < 0 and r["bucket"] != "Income"
                and r["need_class"] != "Transfer" and r["payee"]):
            got[r["payee"]].append(r)
    return got


def charge_days(rows, merge=2):
    """Sorted distinct dates, a charge within `merge` days of the last kept
    one folded into it."""
    days = []
    for day in sorted({r["date"] for r in rows}):
        if days and (dt.date.fromisoformat(day)
                     - dt.date.fromisoformat(days[-1])).days <= merge:
            continue
        days.append(day)
    return days


def gaps(days):
    return [(dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days
            for a, b in zip(days, days[1:])]


def fits_a_cadence(days):
    """True when some run of these dates, cut at holes longer than three band
    widths, is long enough and has 60 per cent of its gaps in one band."""
    for name, (lo, hi) in BANDS.items():
        runs = [[days[0]]] if days else []
        for a, b in zip(days, days[1:]):
            if gaps([a, b])[0] > hi * 3:
                runs.append([b])
            else:
                runs[-1].append(b)
        for run in runs:
            if (len(run) < NEED.get(name, 3)
                    or gaps([run[0], run[-1]])[0] < SPAN.get(name, 0)):
                continue
            if sum(1 for g in gaps(run) if lo <= g <= hi) / (len(run) - 1) >= 0.6:
                return True
    return False


def top_category(rows):
    """The one category most of these rows carry, or None on a tie."""
    counts = defaultdict(int)
    for r in rows:
        counts[r["category"] or "(uncategorised)"] += 1
    ranked = sorted(counts.values(), reverse=True)
    if len(ranked) > 1 and ranked[0] == ranked[1]:
        return None
    return max(counts, key=counts.get)


def half_up(x):
    """A Decimal amount of cents rounded half up to a whole cent."""
    return int(Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def cents(dollars):
    """A stat's dollar figure as integer cents."""
    return half_up(Decimal(str(dollars)) * 100)


def plan_pay(segments, month):
    """What the plan's income rows pay in `month`, in integer cents: gross,
    the Ordinary Wage of the rows marked CPF yes, each row prorated by the
    calendar days of the month it covers, plus whether a bonus lands this
    month or an open-ended employment row covers it."""
    import calendar
    y, m = int(month[:4]), int(month[5:7])
    days = calendar.monthrange(y, m)[1]
    first, last = dt.date(y, m, 1), dt.date(y, m, days)
    got = {"gross": 0, "ow": 0, "bonus": False, "open_job": False}
    for seg in segments:
        start = dt.date.fromisoformat(str(seg["from"])[:10])
        end = dt.date.fromisoformat(str(seg["to"])[:10]) if seg.get("to") else last
        a, b = max(first, start), min(last, end)
        if a > b:
            continue
        # G20: the stat's segment holds its gross in cents, a result from before in dollars
        cents = Decimal(stat_cents(seg, "gross"))
        part = half_up(cents * ((b - a).days + 1) / days)
        got["gross"] += part
        if seg.get("cpf"):
            got["ow"] += part
        employment = seg.get("type", "employment") == "employment"
        if employment and seg.get("bonus_months") and int(seg.get("bonus_month") or 0) == m:
            got["bonus"] = True
        if employment and not seg.get("to"):
            got["open_job"] = True
    return got


def prev_month(m):
    y, mo = int(m[:4]), int(m[5:7])
    return f"{y - 1:04d}-12" if mo == 1 else f"{y:04d}-{mo - 1:02d}"


def cash_by_month(snap):
    """{month: SGD across the deposit accounts}, last balance carried per
    account and currency: a wallet pocket that printed nothing this month
    (a Revolut EUR balance sitting untouched) still holds its last figure."""
    closings = defaultdict(dict)
    for b in snap["periods"]:
        if b["name"].endswith(" · card") or not b["has_balances"]:
            continue
        value = b["closing_sgd_minor"]
        if value is None:
            value = b["closing_minor"] if b["currency"] == "SGD" else None
        if value is None:
            continue
        m, key = period_month(b), (b["account_no"], b["currency"])
        closings[m][key] = closings[m].get(key, 0.0) + value / 100
    held, series = {}, {}
    for m in every_month(snap):
        held.update(closings.get(m, {}))
        series[m] = round(sum(held.values()), 2)
    return series


class Recomputation(unittest.TestCase):
    """Base class: skips the whole file when the cache has not been built."""

    @classmethod
    def setUpClass(cls):
        if SNAP is None:
            raise unittest.SkipTest("the data folder's cache is empty; run finance.py analyse")

    def result(self, stat_id):
        if stat_id not in RESULTS:
            self.skipTest(f"no cached result for {stat_id}")
        return RESULTS[stat_id]

    def near(self, got, want, places=2, msg=""):
        self.assertAlmostEqual(got, want, places=places, msg=msg)

    def dollars(self, obj, name):
        """G20: a stat's money field, its `<name>_minor` cents, as dollars for the recomputation."""
        cents = stat_cents(obj, name)
        return None if cents is None else cents / 100

    def expected(self, key):
        """A named check from notes/review-expectations.json, or a skip."""
        if key not in EXPECT:
            self.skipTest(f"no {key!r} in notes/review-expectations.json")
        return EXPECT[key]


# ---------------------------------------------------------------- 1. cash flow

def per_count(total, n):
    """G20: dollars over a count to the cent, half up, as the stats divide cents."""
    return float((Decimal(repr(round(total, 2))) / n).quantize(Decimal("0.01"), ROUND_HALF_UP))


class MonthlyCashflow(Recomputation):

    def test_every_month_matches_an_independent_sum(self):
        res = self.result("monthly-cashflow")
        inc, spd, inv = income_rows(SNAP), spend_rows(SNAP), investing_rows(SNAP)
        claimed = {x["month"]: x for x in res["data"]["months"]}
        self.assertEqual(sorted(claimed), sorted(every_month(SNAP)))
        for m in every_month(SNAP):
            with self.subTest(m):
                income = into([r for r in inc if r["month"] == m])
                spend = out([r for r in spd if r["month"] == m])
                investing = out([r for r in inv if r["month"] == m])
                self.near(self.dollars(claimed[m], "income"), income)
                self.near(self.dollars(claimed[m], "spend"), spend)
                self.near(self.dollars(claimed[m], "investing"), investing)
                self.near(self.dollars(claimed[m], "net"), round(income - spend, 2))

    def test_internal_legs_and_transfer_buckets_are_out_of_spend(self):
        rows = spend_rows(SNAP)
        self.assertTrue(all(r["flow_class"] == "External" for r in rows))
        self.assertTrue(all(r["bucket"] == "Spend" for r in rows))
        self.assertTrue(all(not r["synthetic"] for r in rows))
        buckets = {r["category"] for r in transfer_rows(SNAP)}
        self.assertTrue(buckets <= {"Investments", "Wallet top-up",
                                    "Person to person", "Between own accounts"})

    def test_a_refund_inside_a_spend_category_subtracts(self):
        """Every month whose spend rows carry a refund reports the signed sum,
        below the month's gross charges, never the charges alone."""
        refunded = sorted({r["month"] for r in spend_rows(SNAP) if sgd(r) > 0})
        if not refunded:
            self.skipTest("no refund inside a spend category in this record")
        claimed = {x["month"]: x for x in
                   self.result("monthly-cashflow")["data"]["months"]}
        for m in refunded:
            with self.subTest(m):
                rows = [r for r in spend_rows(SNAP) if r["month"] == m]
                gross = round(sum(-sgd(r) for r in rows if sgd(r) < 0), 2)
                signed = out(rows)
                self.assertLess(signed, gross)
                self.near(self.dollars(claimed[m], "spend"), signed)

    def test_investments_are_neither_spend_nor_income(self):
        cats = {r["category"] for r in investing_rows(SNAP)}
        self.assertEqual(cats, {"Investments"})
        self.assertFalse([r for r in spend_rows(SNAP) if r["category"] == "Investments"])
        self.assertFalse([r for r in income_rows(SNAP) if r["category"] == "Investments"])

    def test_amount_sgd_is_the_field_used(self):
        """A non-SGD row must never be counted at its face value."""
        foreign = [r for r in spend_rows(SNAP)
                   if r["currency"] != "SGD" and r["amount_minor"] != r["amount_sgd_minor"]]
        self.assertTrue(foreign, "expected foreign rows with a converted amount")
        claimed = {x["month"]: x for x in
                   self.result("monthly-cashflow")["data"]["months"]}
        m = foreign[0]["month"]
        self.near(self.dollars(claimed[m], "spend"), out([r for r in spend_rows(SNAP)
                                            if r["month"] == m]))

    def test_the_phase_averages(self):
        from analytics.stats.common import PHASES
        res = self.result("monthly-cashflow")
        for phase, first, last in ((p["name"], p["from"], p["to"]) for p in PHASES):
            with self.subTest(phase):
                got = next(p for p in res["data"]["phases"] if p["name"] == phase)
                ms = [m for m in every_month(SNAP)
                      if first <= m and (last is None or m <= last)]
                # decision 6: a figure a month divides the full months' money
                # by their number, or every month's when none is full
                partial = {x["month"] for x in res["data"]["months"] if x["partial"]}
                full = [m for m in ms if m not in partial]
                averaged = full or ms
                n = len(averaged)
                income = into([r for r in income_rows(SNAP) if r["month"] in averaged])
                spend = out([r for r in spend_rows(SNAP) if r["month"] in averaged])
                investing = out([r for r in investing_rows(SNAP) if r["month"] in averaged])
                self.assertEqual(got["months"], len(ms))
                self.assertEqual(got["full_months"], len(full))
                self.near(self.dollars(got, "income_per_month"), per_count(income, n))
                self.near(self.dollars(got, "spend_per_month"), per_count(spend, n))
                self.near(self.dollars(got, "investing_per_month"), per_count(investing, n))

    def test_the_ledger_reconciles_with_the_cash_line(self):
        """Over a window where every account prints, every row in the
        snapshot has to add up to the movement in the closing balances. The
        window opens once every deposit account, wallets included, has printed
        its first balance (before that a wallet's money is in no closing
        figure), and is the longest run of consecutive covered months after
        that whose month before is covered too, so both ends are real
        closing balances."""
        started = {}
        for b in SNAP["periods"]:
            if b["name"].endswith(" · card") or not b["has_balances"]:
                continue
            m = period_month(b)
            started[b["account_no"]] = min(started.get(b["account_no"], m), m)
        if not started:
            self.skipTest("no deposit account prints a balance")
        start = max(started.values())
        full = {m for m in covered_months(SNAP) if m >= start}
        runs = []
        for m in sorted(full):
            if prev_month(m) in full and runs and runs[-1][-1] == prev_month(m):
                runs[-1].append(m)
            elif prev_month(m) in full:
                runs.append([m])
        if not runs:
            self.skipTest("no two consecutive covered months once every account prints")
        window = max(runs, key=len)
        moved = round(sum(sgd(r) for r in SNAP["transactions"]
                          if r["month"] in window), 2)
        cash = cash_by_month(SNAP)
        delta = round(cash[window[-1]] - cash[prev_month(window[0])], 2)
        self.assertLess(abs(delta - moved), 50.0,
                        f"cash moved {delta} but the rows add to {moved}")


# ---------------------------------------------- 2. categories, needs and wants

class CategoryBreakdown(Recomputation):

    def test_the_shares_add_to_one(self):
        res = self.result("category-breakdown")
        total = sum(x["share"] for x in res["data"]["categories"])
        self.assertAlmostEqual(total, 1.0, places=2)

    def test_every_category_total_matches(self):
        res = self.result("category-breakdown")
        full = covered_months(SNAP)
        by_cat = defaultdict(list)
        for r in spend_rows(SNAP):
            by_cat[r["category"] or "(uncategorised)"].append(r)
        for x in res["data"]["categories"]:
            with self.subTest(x["category"]):
                rows = [r for r in by_cat[x["category"]] if r["month"] in full]
                self.near(self.dollars(x, "total"), out(rows))


class NeedsWantsSavings(Recomputation):

    def test_the_need_class_mapping_matches_categories_json(self):
        fallback = json.loads((ROOT / "analytics" / "categories.json")
                              .read_text(encoding="utf-8"))
        rows = fallback["categories"] + fallback.get("retired_categories", []) if isinstance(fallback, dict) else fallback
        want = {c["name"]: (c["bucket"], c["need_class"]) for c in rows}
        for c in SNAP["categories"]:
            with self.subTest(c["name"]):
                self.assertEqual((c["bucket"], c["need_class"]), want[c["name"]])
        for r in spend_rows(SNAP):
            self.assertEqual(r["need_class"], want[r["category"]][1])

    def test_the_split_is_of_income_as_the_spec_asks(self):
        res = self.result("needs-wants-savings")
        summary = res["data"]["summary"]
        full = covered_months(SNAP)
        income = into([r for r in income_rows(SNAP) if r["month"] in full])
        need = out([r for r in spend_rows(SNAP)
                    if r["need_class"] == "Need" and r["month"] in full])
        want = out([r for r in spend_rows(SNAP)
                    if r["need_class"] == "Want" and r["month"] in full])
        save = out([r for r in transfer_rows(SNAP)
                    if r["need_class"] in ("Investment", "Savings")
                    and r["month"] in full])
        self.near(self.dollars(summary, "income"), income)
        self.near(self.dollars(summary, "need"), need)
        self.near(self.dollars(summary, "want"), want)
        self.near(self.dollars(summary, "savings"), save)
        self.near(summary["want_share"], round(want / income, 4), places=4)

    def test_the_three_shares_do_not_add_to_one_and_that_is_the_denominator(self):
        """Wants as a share of income and wants as a share of spend are two
        different numbers. The chart and the 50/30/20 sentence must not be read
        as a split of spending."""
        summary = self.result("needs-wants-savings")["data"]["summary"]
        summary = {**summary, **{k: self.dollars(summary, k) for k in ("need", "want", "savings", "income")}}
        of_income = summary["need_share"] + summary["want_share"] + summary["savings_share"]
        # the three shares are each a share of income, so together they are
        # (need + want + savings) / income: below one when some income was
        # left over, above one when the months spent more than they earned
        self.near(of_income, round((summary["need"] + summary["want"] + summary["savings"])
                                   / summary["income"], 4), places=3)
        # and the share of spending is the same wants figure over a different
        # denominator, so the two differ by exactly income over spending
        of_spend = summary["want"] / (summary["need"] + summary["want"])
        self.near(of_spend, summary["want_share"] * summary["income"]
                  / (summary["need"] + summary["want"]), places=3)
        self.assertNotAlmostEqual(of_spend, summary["want_share"], places=2)


# ---------------------------------------------------------------- 3. recurring

class RecurringDetector(Recomputation):

    def series(self):
        return {s["payee"]: s for s in self.result("recurring-detector")["data"]["series"]}

    def test_the_known_series_are_found(self):
        """A bill that comes monthly has to be found, whoever pays it. Worked
        out here from the documented rules alone: a payee billed at least ten
        dollars, outside the habit categories, whose charges within a quarter
        of its median size, cut wherever a hole is longer than three monthly
        band widths, leave a run of three or more dates with at least 60 per
        cent of its gaps in the monthly band, and more monthly gaps in that run
        than the payee has gaps in every other band put together. Each such
        payee must come back monthly over that run at its median amount."""
        got = self.series()
        lo, hi = BANDS["monthly"]
        other = [b for name, b in BANDS.items() if name != "monthly"]
        plain = {}
        for payee, rows in recurring_outflow(SNAP).items():
            if top_category(rows) in HABIT or top_category(rows) is None:
                continue
            mid = round(statistics.median(-sgd(r) for r in rows), 2)
            if mid < 10.0:
                continue
            kept = [r for r in rows if abs(-sgd(r) - mid) <= mid * 0.25]
            days = charge_days(kept)
            if len(days) < 3:
                continue
            runs = [[days[0]]]
            for a, b in zip(days, days[1:]):
                if gaps([a, b])[0] > hi * 3:
                    runs.append([b])
                else:
                    runs[-1].append(b)
            scored = sorted(((sum(1 for g in gaps(run) if lo <= g <= hi), run)
                             for run in runs if len(run) >= 3), key=lambda x: -x[0])
            scored = [(h, run) for h, run in scored if h / (len(run) - 1) >= 0.6]
            elsewhere = sum(1 for g in gaps(days) for a, b in other if a <= g <= b)
            if not scored or scored[0][0] <= elsewhere:
                continue
            if len(scored) > 1 and scored[1][0] == scored[0][0]:
                continue                     # two equal runs: either may be reported
            run = scored[0][1]
            inside = [-sgd(r) for r in sorted(kept, key=lambda r: r["date"]) if run[0] <= r["date"] <= run[-1]]
            # G16: the usual amount is the median of the latest charges, so a price rise is the new normal
            plain[payee] = (round(statistics.median(inside[-6:]), 2), run)
        if not plain:
            self.skipTest("no payee billed monthly in this record")
        for payee, (mid, run) in plain.items():
            with self.subTest(payee):
                self.assertIn(payee, got)
                self.assertEqual(got[payee]["cadence"], "monthly")
                self.near(self.dollars(got[payee], "median_amount"), mid, places=0)
                self.assertEqual((got[payee]["first_seen"], got[payee]["last_seen"]),
                                 (run[0], run[-1]))

    def test_a_monthly_series_is_spaced_monthly(self):
        """A rent whose gaps drifted from 27 to 33 days was missed while the
        band was 28 to 33. Every series called monthly, rent first among them,
        has the payee's charges inside its span spaced in the 26 to 35 day
        band at the median, and a median amount its charges actually reach."""
        rows_by_payee = recurring_outflow(SNAP)
        monthly = [s for s in self.series().values()
                   if s["cadence"] == "monthly" and s["grouped_by"] == "payee"]
        if not monthly:
            self.skipTest("no monthly series in this record")
        lo, hi = BANDS["monthly"]
        for s in monthly:
            with self.subTest(s["payee"], category=s["category"]):
                rows = [r for r in rows_by_payee[s["payee"]]
                        if s["first_seen"] <= r["date"] <= s["last_seen"]]
                self.assertGreaterEqual(len(charge_days(rows)), 3)
                amounts = [-sgd(r) for r in rows]
                self.assertTrue(min(amounts) - 0.01 <= self.dollars(s, "median_amount")
                                <= max(amounts) + 0.01)
                if s["category"] == "Rent":
                    spacing = statistics.median(gaps(charge_days(rows)))
                    self.assertTrue(lo <= spacing <= hi, spacing)

    def test_the_owners_named_series(self):
        """The series the owner knows about, from the optional expectations
        file: each found at its cadence and about its amount."""
        got = self.series()
        for want in self.expected("recurring"):
            with self.subTest(want["payee"]):
                self.assertIn(want["payee"], got)
                self.assertEqual(got[want["payee"]]["cadence"], want["cadence"])
                self.near(self.dollars(got[want["payee"]], "median_amount"), want["amount"], places=0)

    def test_the_owners_named_stops_and_spans(self):
        got = self.series()
        if not any(k in EXPECT for k in ("stopped", "active", "seen")):
            self.skipTest("no 'stopped', 'active' or 'seen' in "
                          "notes/review-expectations.json")
        for payee in EXPECT.get("stopped", ()):
            with self.subTest(payee):
                self.assertTrue(got[payee]["stopped"])
        for payee in EXPECT.get("active", ()):
            with self.subTest(payee):
                self.assertFalse(got[payee]["stopped"])
        for payee, (first, last) in EXPECT.get("seen", {}).items():
            with self.subTest(payee):
                self.assertEqual(got[payee]["first_seen"][:7], first)
                self.assertEqual(got[payee]["last_seen"][:7], last)

    def test_stopped_detection(self):
        """A series is paused once nothing has come in for PAUSE_FACTOR
        cadences (and a month) and stopped after STOP_FACTOR (and two months),
        G16; which payees that names
        changes as statements land, so the rule is checked on every series
        rather than on a fixed name."""
        from analytics.stats import recurring_detector as rd
        got = self.series()
        self.assertTrue(got, "no recurring series at all")
        for payee, s in got.items():
            with self.subTest(payee):
                stop = max(s["cadence_days"] * rd.STOP_FACTOR, rd.STOP_FLOOR)
                pause = max(s["cadence_days"] * rd.PAUSE_FACTOR, rd.PAUSE_FLOOR)
                self.assertEqual(s["stopped"], s["days_since_last"] > stop)
                self.assertEqual(s["paused"], not s["stopped"] and s["days_since_last"] > pause)
        self.assertTrue(any(s["stopped"] for s in got.values()))
        self.assertTrue(any(not s["stopped"] for s in got.values()))
        # G16: measured to the last day the series' own account's statements cover,
        # never past the snapshot's last transaction date
        last = max(r["date"] for r in SNAP["transactions"] if r["date"])
        for payee, s in got.items():
            with self.subTest(payee, measure="days_since_last"):
                self.assertLessEqual(s["as_of"], last)
                self.assertEqual(s["days_since_last"],
                                 max(0, (dt.date.fromisoformat(s["as_of"])
                                         - dt.date.fromisoformat(s["last_seen"])).days))

    def test_a_flagged_payee_with_no_regular_spacing_has_no_series(self):
        """A handful of charges with gaps far wider than a month: the recurring
        flag is wrong, not the detector. Every payee marked recurring whose
        charges never sit one cadence apart is listed as flagged and not
        detected, and the list is exactly the flagged payees with no series."""
        data = self.result("recurring-detector")["data"]
        mismatch = data["flag_mismatches"]
        flagged = {p["name"] for p in SNAP["payees"] if p["recurring"]}
        detected = {s["payee"] for s in data["series"] if s["grouped_by"] == "payee"}
        self.assertEqual(mismatch["flagged_not_detected"], sorted(flagged - detected))
        rows_by_payee = recurring_outflow(SNAP)
        irregular = [p for p in sorted(flagged) if rows_by_payee.get(p)
                     and not any(lo <= g <= hi
                                 for g in gaps(charge_days(rows_by_payee[p]))
                                 for lo, hi in BANDS.values())]
        for payee in irregular:
            with self.subTest(payee):
                self.assertIn(payee, mismatch["flagged_not_detected"])
                self.assertNotIn(payee, self.series())
        for payee in EXPECT.get("flagged_not_detected", ()):
            with self.subTest(payee, source="expectations"):
                self.assertIn(payee, mismatch["flagged_not_detected"])

    def test_a_habit_payee_is_not_called_recurring(self):
        """A ride-hailing app, a bus fare or a coffee stall is bought often at
        sizes that move, and the retry on the whole group is how they once got
        in. A payee whose rows are mostly in a habit category is a series only
        through its charges within a twentieth of its median amount; when
        those fall into no cadence, it cannot be a series."""
        series = self.series()
        checked = 0
        for payee, rows in recurring_outflow(SNAP).items():
            if top_category(rows) not in HABIT:
                continue
            mid = round(statistics.median(-sgd(r) for r in rows), 2)
            steady = [r for r in rows if abs(-sgd(r) - mid) <= mid * 0.05]
            if fits_a_cadence(charge_days(steady)):
                continue
            checked += 1
            with self.subTest(payee):
                self.assertNotIn(payee, series)
        if not checked:
            self.skipTest("no habit payee with varying amounts in this record")
        for payee in EXPECT.get("not_recurring", ()):
            with self.subTest(payee, source="expectations"):
                self.assertNotIn(payee, series)

    def test_the_transfer_and_the_spend_totals_are_separated(self):
        data = self.result("recurring-detector")["data"]
        fixed = self.dollars(data["totals"], "monthly_fixed_cost")
        committed = self.dollars(data["totals"], "monthly_committed")
        spend_side = [s for s in data["active"] if s["bucket"] == "Spend"]
        other_side = [s for s in data["active"] if s["bucket"] != "Spend"]
        self.near(fixed, round(sum(self.dollars(s, "monthly_cost") for s in spend_side), 2))
        # what separates the two is the standing transfers alone, such as a
        # monthly investing transfer; no income series is ever in either
        self.near(committed - fixed, round(sum(self.dollars(s, "monthly_cost") for s in other_side), 2))
        self.assertTrue(all(s["bucket"] == "Transfer" for s in other_side),
                        {s["bucket"] for s in other_side})
        if other_side:
            self.assertGreater(committed, fixed)

    def test_no_food_stall_is_a_live_recurring_series(self):
        """A hawker stall visited four times in one month is not a commitment.
        This failed while one food stall carried most of the fixed-cost headline."""
        food = [s["payee"] for s in self.result("recurring-detector")["data"]["active"]
                if s["category"] in ("Eating out", "Coffee", "Groceries")]
        self.assertEqual(food, [], f"food stalls called recurring: {food}")

    def test_a_series_is_not_stitched_across_a_long_hole(self):
        """A series of N occurrences at cadence D should span about (N-1)*D days.
        This failed on a subscription with a gap of more than a year between its
        monthly charges and two strays after it, reported as one monthly series,
        and on the coffee and hawker rows that only look weekly."""
        for payee, s in self.series().items():
            with self.subTest(payee):
                span = (dt.date.fromisoformat(s["last_seen"])
                        - dt.date.fromisoformat(s["first_seen"])).days
                expected = (s["occurrences"] - 1) * s["cadence_days"]
                self.assertLessEqual(span, expected * 2,
                                     f"{payee}: {s['occurrences']} {s['cadence']} "
                                     f"occurrences spread over {span} days")


# ---------------------------------------------------------------- 4. eating out

class EatingOut(Recomputation):

    def test_the_headline_counts(self):
        summary = self.result("eating-out")["data"]["summary"]
        meals = [r for r in spend_rows(SNAP) if r["category"] == "Eating out"]
        charges = [r for r in meals if sgd(r) < 0]
        self.assertEqual(summary["count"], len(charges))
        self.near(self.dollars(summary, "spend"), out(meals))
        self.near(self.dollars(summary, "avg_ticket"),
                  round(sum(-sgd(r) for r in charges) / len(charges), 2))

    def test_coffee_is_beside_eating_out_and_not_inside_it(self):
        meals = [r for r in spend_rows(SNAP) if r["category"] == "Eating out"]
        self.assertFalse([r for r in meals if r["category"] == "Coffee"])
        summary = self.result("eating-out")["data"]["summary"]
        self.near(self.dollars(summary, "coffee"),
                  out([r for r in spend_rows(SNAP) if r["category"] == "Coffee"]))

    def test_the_week_count_is_weeks_with_a_meal_not_calendar_weeks(self):
        """Meals per week is counted over the weeks that had a meal. Over the
        calendar weeks the meals actually span it is lower. Both are reported, and the series
        itself runs across every calendar week so a fortnight away draws as two
        weeks at zero rather than closing up."""
        meals = [r for r in spend_rows(SNAP) if r["category"] == "Eating out"]

        def monday(day):
            d = dt.date.fromisoformat(day)
            return d - dt.timedelta(days=d.weekday())

        weeks = sorted({monday(r["date"]) for r in meals})
        calendar = (weeks[-1] - weeks[0]).days // 7 + 1
        data = self.result("eating-out")["data"]
        summary = data["summary"]
        self.assertEqual(len(data["weeks"]), calendar)
        self.assertEqual(summary["weeks_with_a_meal"], len(weeks))
        self.assertEqual([w["week"] for w in data["weeks"]],
                         sorted(w["week"] for w in data["weeks"]))
        self.near(summary["per_week"], round(len(meals) / len(weeks), 2))
        self.near(summary["per_calendar_week"], round(len(meals) / calendar, 2))
        self.assertLess(summary["per_calendar_week"], summary["per_week"])

    def test_the_weekday_split(self):
        meals = [r for r in spend_rows(SNAP)
                 if r["category"] == "Eating out" and sgd(r) < 0]
        week = [r for r in meals if dt.date.fromisoformat(r["date"]).weekday() < 5]
        end = [r for r in meals if dt.date.fromisoformat(r["date"]).weekday() >= 5]
        got = {x["bucket"]: x for x in self.result("eating-out")["data"]["weekday"]}
        self.assertEqual(got["weekday"]["count"], len(week))
        self.assertEqual(got["weekend"]["count"], len(end))
        self.assertEqual(len(week) + len(end), len(meals))

    def test_the_top_places(self):
        meals = [r for r in spend_rows(SNAP) if r["category"] == "Eating out"]
        by_payee = defaultdict(list)
        for r in meals:
            by_payee[r["payee"] or r["merchant_raw"] or "(no name)"].append(r)
        mine = sorted(((k, out(v)) for k, v in by_payee.items()),
                      key=lambda x: -x[1])[:15]
        theirs = [(p["payee"], self.dollars(p, "spend"))
                  for p in self.result("eating-out")["data"]["top_places"]]
        self.assertEqual(theirs, mine)


# ------------------------------------------------------- 5. one-offs and travel

class OneOffs(Recomputation):

    def test_the_outliers_recompute(self):
        charges = [r for r in spend_rows(SNAP) if sgd(r) < 0]
        medians = {}
        by_cat = defaultdict(list)
        for r in charges:
            by_cat[r["category"]].append(-sgd(r))
        for cat, amounts in by_cat.items():
            medians[cat] = round(statistics.median(amounts), 2)
        mine = [r for r in charges
                if medians[r["category"]] > 0 and -sgd(r) >= 150.0
                and -sgd(r) >= medians[r["category"]] * 5.0]
        summary = self.result("one-offs")["data"]["summary"]
        self.assertEqual(summary["count"], len(mine))
        self.near(self.dollars(summary, "spend"), round(sum(-sgd(r) for r in mine), 2))


class TravelDays(Recomputation):

    def test_the_trip_spend_recomputes(self):
        """A trip row with no SGD value (its statement printed no FX rate) has
        no figure to add, so the stat leaves it out of the rows as the Spending
        page does, and says how many it left out in a caveat."""
        tagged = [r for r in spend_rows(SNAP) if r["trip"]]
        on_trip = [r for r in tagged if r["amount_sgd_minor"] is not None]
        res = self.result("travel")
        summary = res["data"]["summary"]
        self.near(self.dollars(summary, "spend"), out(on_trip))
        self.assertEqual(summary["rows"], len(on_trip))
        unpriced = len(tagged) - len(on_trip)
        if unpriced:
            self.assertTrue(any(f"{unpriced} trip row(s) have no SGD value" in note
                                for note in res["caveats"]), res["caveats"])

    def test_rent_inside_a_long_stay_is_not_a_travel_cost(self):
        """A large share of 'travel' is rent paid during a long stay abroad,
        which is what living there cost, not what the trips cost."""
        res = self.result("travel")
        data = res["data"]
        rent = [r for r in spend_rows(SNAP) if r["trip"] and r["category"] == "Rent"]
        if not rent:
            self.skipTest("no rent paid on a trip in this record")
        cats = {x["category"]: x for x in data["by_category"]}
        self.assertIn("Rent", cats)
        self.near(self.dollars(cats["Rent"], "spend"), out(rent))
        self.near(cats["Rent"]["share"], round(out(rent) / self.dollars(data["summary"], "spend"), 4),
                  places=3)
        self.assertNotIn("Rent", {x["category"]
                                  for x in data["by_category_excluding_rent"]})
        # every rent row on a trip is the cost of living there: each trip that
        # carries rent is a trip in the snapshot, and a stay longer than the
        # stat's long-stay limit rather than a holiday
        trips = {t["name"]: t for t in SNAP["trips"]}
        limit = res["params"]["long_stay_days"]
        for name in sorted({r["trip"] for r in rent}):
            with self.subTest(name):
                self.assertIn(name, trips)
                t = trips[name]
                self.assertTrue(t["start"] and t["end"])
                days = (dt.date.fromisoformat(t["end"])
                        - dt.date.fromisoformat(t["start"])).days + 1
                self.assertGreater(days, limit)
        named = EXPECT.get("rent_on_trips")
        if named:
            on_payee = [r for r in rent if r["payee"] == named["payee"]]
            self.assertTrue(on_payee)
            self.assertEqual({r["trip"] for r in on_payee}, {named["trip"]})
            self.assertTrue(all(r["trip"] in named["trips"] for r in rent),
                            {(r["payee"], r["trip"]) for r in rent})
            self.assertGreater(cats["Rent"]["share"], named.get("min_share", 0.0))

    def test_the_day_count_is_distinct_days(self):
        """This failed while the day count was the sum of trip lengths: side
        trips nested inside a long stay had their days counted twice."""
        days = set()
        for t in SNAP["trips"]:
            if not (t["start"] and t["end"]):
                continue
            day = dt.date.fromisoformat(t["start"])
            while day <= dt.date.fromisoformat(t["end"]):
                days.add(day)
                day += dt.timedelta(days=1)
        self.assertEqual(self.result("travel")["data"]["summary"]["days"], len(days))


class Denominators(Recomputation):

    def test_every_share_uses_the_same_spend_base(self):
        """This failed while category-breakdown divided by the covered months
        and travel, one-offs, merchant-concentration and data-quality divided by
        every month, so a 'per cent of spend' from one and a 'per cent of all
        spending' from another were not comparable."""
        full = covered_months(SNAP)
        counted = out([r for r in spend_rows(SNAP) if r["month"] in full])
        every = out(spend_rows(SNAP))
        self.assertNotAlmostEqual(counted, every, places=0)   # the two bases differ
        travel = self.result("travel")["data"]["summary"]
        # the stat names its base outright; dividing spend by a share rounded
        # to four places would drift by more than a dollar on a five-figure base
        self.near(self.dollars(travel, "base"), counted, places=0)
        self.near(self.dollars(travel, "spend") / self.dollars(travel, "base"), travel["share_of_all_spend"], places=3)


# ------------------------------------ 6. income, runway and the forward view

class IncomeStability(Recomputation):

    def test_the_volatility_block(self):
        vol = self.result("income-stability")["data"]["volatility"]
        full = covered_months(SNAP)
        totals = [into([r for r in income_rows(SNAP) if r["month"] == m])
                  for m in full]
        self.assertEqual(vol["months"], len(full))
        self.near(self.dollars(vol, "mean"), per_count(sum(totals), len(totals)))
        # the spread of the months' cents, to the cent, half up
        from finance.money import to_cents
        spread = Decimal(repr(statistics.pstdev([to_cents(t) for t in totals])))
        self.near(self.dollars(vol, "stdev"), int(spread.quantize(Decimal(1), ROUND_HALF_UP)) / 100)

    def test_the_months_without_salary(self):
        vol = self.result("income-stability")["data"]["volatility"]
        full = covered_months(SNAP)
        salary = [into([r for r in income_rows(SNAP)
                        if r["month"] == m
                        and r["category"] in ("Salary", "Freelance income")])
                  for m in full]
        self.assertEqual(vol["months_without_salary"], sum(1 for x in salary if x <= 0))
        longest = run = 0
        for x in salary:
            run = 0 if x > 0 else run + 1
            longest = max(longest, run)
        self.assertEqual(vol["longest_gap_without_salary"], longest)


class SavingsRunway(Recomputation):

    def test_which_accounts_count_as_cash(self):
        """Cash is every account that prints a closing balance on a deposit
        statement, wallets included. An account with no such statement (a
        brokerage's cash wallet, which the bank statements never print) is
        not cash.

        No stat prints a full account number, so the stat names each account
        by its masked last four digits and the comparison is on those, one
        entry per account so two accounts sharing a last four still count
        twice."""
        from finance.account_names import mask   # display only, no arithmetic
        accounts = sorted(a["account_no"] for a in
                          self.result("savings-runway")["data"]["accounts"])
        printing = set()
        for b in SNAP["periods"]:
            if b["name"].endswith(" · card") or not b["has_balances"]:
                continue
            if b["closing_sgd_minor"] is not None or b["currency"] == "SGD":
                printing.add(b["account_no"])
        self.assertEqual(accounts, sorted(mask(a) for a in printing))
        wallets = {a["account_no"] for a in SNAP["accounts"]
                   if a.get("subclass") == "Wallet"}
        self.assertTrue(wallets & printing, "no wallet prints a balance")
        # an account with no deposit statement is not in the list; its masked
        # number is only checked when no printing account shares it
        silent = ({mask(a["account_no"]) for a in SNAP["accounts"]
                   if a["account_no"] not in printing}
                  - {mask(a) for a in printing})
        self.assertFalse(silent & set(accounts))

    def test_the_cash_line_recomputes(self):
        mine = cash_by_month(SNAP)
        for row in self.result("savings-runway")["data"]["months"]:
            with self.subTest(row["month"]):
                self.near(self.dollars(row, "cash"), mine[row["month"]])

    def test_the_runway(self):
        summary = self.result("savings-runway")["data"]["summary"]
        full = covered_months(SNAP)
        rate = [out([r for r in spend_rows(SNAP) if r["month"] == m]) for m in full]
        all_rate = per_count(sum(rate), len(rate))
        rate6 = per_count(sum(rate[-6:]), 6)
        self.near(self.dollars(summary, "spend_avg_all"), all_rate)
        self.near(self.dollars(summary, "spend_avg_6m"), rate6)
        self.assertEqual(summary["runway_6m"], round(self.dollars(summary, "cash") / rate6, 1))

    def test_the_headline_cash_is_not_a_carried_balance(self):
        """When one account has no statement for the last month, its balance is
        carried from an earlier one, and the finding names the month the cash is
        as at. Most of the headline must still be this month's balances."""
        data = self.result("savings-runway")["data"]
        last = data["months"][-1]
        carried = round(sum(self.dollars(last, name) or 0.0 for name in last["carried"]), 2)
        cash = self.dollars(last, "cash")
        self.assertLess(carried, cash * 0.25,
                        f"{carried} of {cash} is carried from an "
                        f"earlier month: {last['carried']}")


class ForwardView(Recomputation):
    """G20: forward-view counts in cents, so these recompute in cents and read
    the result's money through `stat_cents`, a result from before in dollars."""

    def test_the_arithmetic_of_every_month(self):
        """CPF is charged on the CPF-liable wage, not on gross: since G14 gross
        holds every income row, and side income marked CPF no is in gross but
        attracts no CPF. The liable wage is recomputed here from the plan's
        income rows, each prorated by the days of the month it covers. A month
        where a bonus lands, or an open-ended job's pay may have grown, is left
        to test_the_shape_of_the_plan: the Additional Wage ceiling and the
        growth anchor are the income module's arithmetic, not this file's.

        The running cash moves on before any assertion, so one wrong month
        fails alone instead of every month after it."""
        data = self.result("forward-view")
        p, months = data["params"], data["data"]["months"]
        cash = stat_cents(data["data"], "starting_cash")
        segments = p.get("income_segments") or []
        growth = str(p.get("salary_growth_pct") or "0") not in ("0", "0.0")
        m_ = stat_cents
        for row in months:
            cash += m_(row, "net")
            with self.subTest(row["month"]):
                pay = plan_pay(segments, row["month"])
                if not pay["bonus"] and not (growth and pay["open_job"]):
                    self.assertEqual(m_(row, "gross"), pay["gross"])
                    liable = min(pay["ow"], m_(p, "cpf_ow_ceiling"))
                    self.assertEqual(m_(row, "cpf_employee"),
                                     half_up(liable * Decimal(str(p["cpf_employee_rate"]))))
                self.assertEqual(m_(row, "take_home"), m_(row, "gross") - m_(row, "cpf_employee"))
                self.assertEqual(m_(row, "net"), m_(row, "take_home") + m_(p, "other_income")
                                 - m_(row, "spend") - (m_(row, "trips") or 0)
                                 - (m_(row, "one_offs") or 0)
                                 - m_(row, "investing")
                                 - m_(row, "insurance")
                                 - m_(row, "other_outflow")
                                 - m_(row, "repayments"))
                self.assertEqual(m_(row, "cash"), cash)

    def test_the_shape_of_the_plan(self):
        """The ending figure once looked healthy because the rate was a mean
        pulled down by one refunded month and a monthly outflow was missing from
        the arithmetic entirely. On the median rate, with the part of that
        outflow which is not repayment subtracted and one more month
        projected, the ending figure moved a long way down."""
        from analytics.stats import forward_view
        res = self.result("forward-view")
        data, params = res["data"], res["params"]
        months = {x["month"]: x for x in data["months"]}
        m_ = stat_cents
        # the stat's helpers take cents; a result from before G20 line 5 holds a
        # segment's gross and the single transfer in dollars, read through stat_cents
        segments = [{**s, "gross_minor": m_(s, "gross")} for s in params.get("income_segments") or ()]
        plan = {**params, "dca_amount_minor": m_(params, "dca_amount") or 0}
        # gross per month follows the plan's income rows; with none, nothing
        for m, row in months.items():
            with self.subTest(m):
                want = forward_view.month_gross(m, segments)[0] if segments else 0
                self.assertEqual(m_(row, "gross"), want)
        # the projection runs to the plan's last month, investing the stated
        # amount (or nothing when it is switched off) to the end
        last = params["to"]
        self.assertEqual(max(months), last)
        self.assertEqual(m_(months[last], "investing"), forward_view.investing_at(plan, last))
        scenarios = {s["name"]: s for s in data["scenarios"]}
        self.assertEqual(m_(scenarios["as stated"], "ending_cash"), m_(months[last], "cash"))
        # the rate scenarios order the other way round from their rates: the
        # dearer monthly spend ends with less cash, whichever of the two the
        # record's window makes the larger this time
        median, mean = m_(params, "spend_median"), m_(params, "spend_mean")
        end_median = m_(scenarios["spend at window median"], "ending_cash")
        end_mean = m_(scenarios["spend at window mean"], "ending_cash")
        self.assertNotEqual(median, mean)
        if median < mean:
            self.assertGreater(end_median, end_mean)
        else:
            self.assertLess(end_median, end_mean)
        # in cents nothing compounds, so the gap is the rates' gap every month, exactly
        self.assertEqual(end_median - end_mean, (mean - median) * len(months))

    def test_the_spend_rate_is_not_set_by_one_refund_month(self):
        """A month can net to almost nothing because a large refund landed in
        it, and such a month is one of the six the projection used to average
        flat. The rate is the window's median now, and a window month whose
        refunds beat the limit enters it at its gross charges, so that one month
        cannot set the projection. A rate at or below the flat mean of the
        signed window is the defect back again.

        The window is not filtered instead: a genuinely cheap month is not an
        artefact, and dropping every month below half the median would leave
        the four dearest and call that the rate.
        """
        params = self.result("forward-view")["params"]
        m_ = stat_cents
        window = params["spend_window_all"]
        limit = m_(params, "refund_limit")
        # rows the owner has named as one-off purchases are out of every rate,
        # so the independent recomputation has to leave them out too
        skip = set(params["spend_exclude_keys"])
        signed, restated = {}, {}
        for m in window:
            rows = [r for r in spend_rows(SNAP)
                    if r["month"] == m and r["key"] not in skip]
            amounts = [stat_cents(r, "amount_sgd") or 0 for r in rows]
            refunds = sum(a for a in amounts if a > 0)
            signed[m] = -sum(amounts)
            restated[m] = -sum(a for a in amounts if a < 0) if refunds > limit else signed[m]
        self.assertEqual([m for m in window if restated[m] != signed[m]],
                         params["spend_window_restated"] and
                         [x["month"] for x in params["spend_window_restated"]])
        values = sorted(restated.values())
        mid = len(values) // 2
        median = values[mid] if len(values) % 2 else half_up(Decimal(values[mid - 1] + values[mid]) / 2)
        self.assertEqual(m_(params, "spend_median"), median)
        self.assertEqual(m_(params, "spend_mean"), half_up(Decimal(sum(values)) / len(window)))
        # when a refund does land in the window, the rate must not be the flat
        # mean of the signed months; the refund named in the docstring is now
        # left out of every rate by [plan] spend_exclude_keys, so this only
        # binds when another one arrives
        if params["spend_window_restated"]:
            self.assertGreater(m_(params, "spend_median"), sum(signed.values()) / len(window))
        # the projected rate is whichever the owner's plan names, one of the four
        base = {"window": m_(params, "spend_median"), "mean": m_(params, "spend_mean"),
                "home": m_(params, "spend_ex_all_trips"), "phase": m_(params, "spend_phase_rate")}
        self.assertEqual(m_(params, "spend"), base[params["spend_rate"]])


    def test_the_projection_ignores_every_outflow_that_is_not_spend_or_the_dca(self):
        """Person to person repayments and uncategorised outflows also leave the
        bank every month over the same window, and none of it is projected."""
        window = self.result("forward-view")["params"]["spend_window"]
        other = [r for r in external(SNAP)
                 if r["month"] in window and not r["synthetic"] and sgd(r) < 0
                 and (r["bucket"] is None
                      or (r["bucket"] == "Transfer" and r["need_class"] != "Investment"))]
        missed = round(sum(-sgd(r) for r in other) / len(window), 2)
        self.assertGreater(missed, 100.0)      # documents the size of the hole


# ---------------------------------------------------------- 7. benchmarks

class BenchmarksSg(Recomputation):

    def comparisons(self):
        return self.result("benchmarks-sg")["data"]["comparisons"]

    def test_no_net_figure_is_held_against_a_gross_one(self):
        """Fails on the shipped result if the gross gate is not added: the
        owner's salary is what reached the bank, the MOM and GES references are
        gross."""
        bad = [x for x in self.comparisons()
               if "gross" in str(x["unit"]).lower()]
        self.assertEqual([x["benchmark_id"] for x in bad], [],
                         "net owner figures compared with gross references")

    def test_no_individual_figure_is_held_against_a_household_one(self):
        household = [x["benchmark_id"] for x in self.comparisons()
                     if "household" in str(x["unit"]).lower()
                     and "1person" not in str(x["benchmark_id"])]
        self.assertEqual(household, [],
                         "multi-person household references matched to one person")

    def test_the_food_headline_compares_totals_not_a_sub_component(self):
        """The owner's food is eating out plus groceries plus coffee. The
        headline reference is food services alone (410.20); the comparable total
        is food services plus food at home (577.50)."""
        headline = {x["metric"]: x for x in self.result("benchmarks-sg")["data"]["headline"]}
        self.assertNotEqual(headline["food_spend"]["benchmark_id"],
                            "hes-2023-1person-food-services")

    def test_the_savings_rate_denominators_agree(self):
        metrics = {m["key"]: m for m in self.result("benchmarks-sg")["data"]["metrics"]}
        owner_unit = metrics["savings_rate"]["unit"]
        for x in self.comparisons():
            if x["metric"] == "savings_rate":
                with self.subTest(x["benchmark_id"]):
                    self.assertEqual(str(x["unit"]).lower(), owner_unit.lower())


# ---------------------------------------------------------- 8. data quality

class DataQuality(Recomputation):

    def test_the_counts_agree_with_the_snapshot(self):
        counts = self.result("data-quality")["data"]["counts"]
        self.assertEqual(counts["rows"], len(SNAP["transactions"]))
        self.assertEqual(counts["external"], len(external(SNAP)))
        self.assertEqual(counts["uncategorised"],
                         len([r for r in external(SNAP) if not r["category"]]))
        self.assertEqual(counts["synthetic"],
                         len([r for r in SNAP["transactions"] if r["synthetic"]]))
        self.assertEqual(counts["transfer_bucket_external"],
                         len([r for r in external(SNAP) if r["bucket"] == "Transfer"]))

    def test_the_uncategorised_rows_are_out_of_every_spend_figure(self):
        """Uncategorised rows carry real outflow but sit in no bucket, so they
        are in neither spend nor income anywhere else."""
        uncategorised = [r for r in external(SNAP) if not r["category"]]
        self.assertTrue(all(r["bucket"] is None for r in uncategorised))
        self.assertFalse(set(id(r) for r in uncategorised)
                         & set(id(r) for r in spend_rows(SNAP)))

    def test_the_fix_list_is_sorted_by_size(self):
        fix = self.result("data-quality")["data"]["fix_list"]
        sizes = [abs(self.dollars(x, "amount_sgd")) for x in fix]
        self.assertEqual(sizes, sorted(sizes, reverse=True))


if __name__ == "__main__":
    unittest.main()
