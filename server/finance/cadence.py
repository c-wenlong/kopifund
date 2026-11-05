"""The recurring engine: charges grouped by payee, split into amount levels,
and fitted to a cadence (G16). server/analytics/stats/recurring_detector.py describes
what it finds; this is the part the Recurring page and Home's Coming up
(server/finance/recurring.py) run on as well as the stat.

G19: it lives in finance because finance never imports analytics. The stat
imports it and re-exports every name, so `recurring_detector.X` still works.

G20: every amount here is integer cents, a charge positive: a row's
`amount_sgd_minor` (`figures.sgd_minor`), with the medians, means and sums of
figures.py and their one rounding rule. Levels, bands and price changes are
ratios and logs of amounts, so they find the same series in cents as they did
in dollars; server/tests/test_recurring_cents.py holds them to what the dollar engine
found.
"""
import math
import unicodedata
from calendar import monthrange
from collections import Counter
from datetime import date

from . import figures as c
from .money import label

# (name, low, high, nominal days per occurrence, occurrences needed). The
# monthly band is 26 to 35 rather than SPEC.md's 28 to 33: months are 28 to 31
# days long and a bill moves to the next working day, so the tighter band misses
# a real monthly rent whose gaps ran anywhere from 27 to 33 days.
#
# Weekly needs six occurrences over at least eight weeks, not three over three.
# Three charges seven days apart is a fortnight of habit: four visits to a
# hawker stall in one month passed the old test and were reported as a
# monthly commitment. Fortnightly and twice a month ask the same of their own
# kind, and every two months asks four (G16): three charges at one price two
# months apart is as often chance as a bill.
CADENCES = (("weekly", 5, 9, 7.0, 6), ("fortnightly", 11, 18, 14.0, 6),
            ("twice-monthly", 10, 20, 15.22, 8), ("monthly", 26, 35, 30.44, 3),
            ("two-monthly", 55, 68, 60.88, 4), ("quarterly", 80, 100, 91.3, 3),
            ("half-yearly", 170, 195, 182.6, 3), ("yearly", 340, 390, 365.0, 3))
BANDS = {name: (lo, hi) for name, lo, hi, _, _ in CADENCES}
NOMINAL = {name: nominal for name, _, _, nominal, _ in CADENCES}
DAY_STEP = {"weekly": 7, "fortnightly": 14}       # a cadence counted in days
MONTH_STEP = {"monthly": 1, "two-monthly": 2, "quarterly": 3, "half-yearly": 6, "yearly": 12}
TWICE = "twice-monthly"                           # two fixed days every month
MIN_SPAN = {"weekly": 56, "fortnightly": 70, TWICE: 84}   # days a series of that cadence must cover
# G16: a payee the rules mark recurring needs only two payments for these, since
# a yearly bill seen twice is two years of record
FLAGGED_NEED = {"half-yearly": 2, "yearly": 2}
STATES = ("active", "paused", "stopped")
# G16: where one buys in, only the cadences the detector always had: a stall or a
# ride at one price every two months or every fortnight is habit, not a bill
HABIT_CADENCES = ("weekly", "monthly", "quarterly", "yearly")

MIN_OCCURRENCES = 3    # the floor; each cadence may ask for more
FIT = 0.6              # share of payments that must land on schedule
# G16: twice a month is two fixed days, so it is held tighter: two top-ups a month on
# loosely spread days otherwise pass for a rent paid in halves
TWICE_FIT, TWICE_TOLERANCE = 0.8, 3
PAUSE_FACTOR = 1.5     # cadences of silence before a series counts as paused
STOP_FACTOR = 3        # and as stopped
# G16: but never sooner than a month, and two to stop: a missed rent half or a missed
# week is a late payment, not a pause
PAUSE_FLOOR, STOP_FLOOR = 30.44, 60.88
MAX_GAP_FACTOR = 3     # a hole longer than this many band widths ends the run
AMOUNT_BAND = 0.25     # a charge is at a level when it is within this of it
TIGHT_BAND = 0.05      # and this much, where the category is somewhere one eats
TIGHT_CATEGORIES = ("Eating out", "Coffee", "Groceries", "Transport (public)", "Transport (private)",
                    "Transport")   # Transport: a record not yet split (finance.py split-transport)
MERGE_DAYS = 2         # two charges this close are one occurrence, not a gap of 1
MIN_AMOUNT_MINOR = 1000   # S$10.00: below this a repeat is noise, not a commitment
RECENT = 6             # the latest charges a series' usual amount is the median of
PRICE_CHANGE = 2       # G16: a price change at most doubles or halves a bill
SHORT_RUN = 5          # G16: a run of fewer payments than this must keep every gap
# Audit: where a payee's other charges near a run number more than CROWD times its own
# payments, the run must keep to its days (REGULAR_SHARE of its payments within
# REGULAR_DAYS of their expected day: days counted, months counted) and hold its prices
# (STEADY_SHARE of its payments within TIGHT_BAND of the one before or after)
CROWD = 0.5
REGULAR_SHARE = 0.8
REGULAR_DAYS = (1, 3)
STEADY_SHARE = 0.6
# Audit: an account with no statement for this many days while the record runs on is
# closed, not late: three monthly statements missed
CLOSED_AFTER = STOP_FACTOR * 30.44

# The bank labels that mean somebody is pulling money on a schedule rather than
# the owner pushing it. They are listed whether or not the spacing test passes.
AUTO_BILL_TYPES = ("FAST Collection", "Payments / Collections via GIRO", "GIRO")


# ---------------------------------------------------------------- calendar

def month_day(month, anchor):
    """The anchor day in a month counted from year 0 (`year * 12 + month - 1`),
    clamped to the month's end."""
    y, m = divmod(month, 12)
    return date(y, m + 1, min(anchor, monthrange(y, m + 1)[1]))


def month_of(day):
    return day.year * 12 + day.month - 1


def _apart(a, b, size):
    d = abs(a - b) % size
    return min(d, size - d)


def usual(values, size, latest):
    """The value nearest all the others on a circle of `size` (days of the month
    wrap at 31, weekdays at 7), so the 31st and the 1st sit together; of equals,
    `latest`'s."""
    return min(sorted(set(values), key=lambda v: v != latest),
               key=lambda c_: sum(_apart(c_, v, size) for v in values))


def usual_pair(days_of_month):
    """G16: the two days of the month nearest a twice-monthly series' payments,
    earlier first: each payment counts its distance to the nearer of the two."""
    # Audit: the days counted once each (a month has 31), with each day's distance to every
    # candidate read from one table, so a payee of hundreds of charges costs what one of
    # thirty does; the pairs are tried in the same order, so the answer is the same
    count = Counter(days_of_month)
    far = {x: [(_apart(d, x, 31), n) for d, n in count.items()] for x in range(1, 32)}
    best = None
    for x in range(1, 32):
        for y in range(x + 1, 32):
            cost = sum(min(a, b) * n for (a, n), (b, _) in zip(far[x], far[y]))
            if best is None or cost < best[0]:
                best = (cost, (x, y))
    return best[1]


class Grid:
    """G16: the days one series is expected on, numbered. A series counted in
    days (weekly, fortnightly) is expected on its usual day of the cycle; one
    counted in months on its anchor day, the usual day of the month of its
    recent payments (clamped to the month's end), every one, two, three, six or
    twelve months, in the months its payments fall nearest; a twice-monthly
    series on its two usual days of every month.

    G16: `day` is one the owner set (server/finance/series.py) in place of the usual
    one: the day of the month for a cadence counted in months, the weekday
    (1 Monday to 7 Sunday) for one counted in days, where a fortnight takes
    the week of the two nearer the payments' own. Twice a month has none."""

    def __init__(self, cadence, days, day=None):
        self.by_days = cadence in DAY_STEP
        if self.by_days:
            self.every = DAY_STEP[cadence]
            self.phase = usual([d.toordinal() % self.every for d in days], self.every,
                               days[-1].toordinal() % self.every)
            if day:
                # every is whole weeks, so a phase's weekday is its day's (an ordinal of 1 is a Monday)
                on = [p for p in range(self.every) if date.fromordinal(p + self.every).isoweekday() == day]
                self.phase = min(on, key=lambda p: (_apart(p, self.phase, self.every), p))
            return
        if cadence == TWICE:
            self.anchors = usual_pair([d.day for d in days])
            return
        self.anchors = None
        self.every = MONTH_STEP.get(cadence, 1)
        self.anchor = day or usual([d.day for d in days], 31, days[-1].day)
        # each payment's own month: the anchor day nearest it, which may be the month before or after
        months = [min((m - 1, m, m + 1), key=lambda x: abs((month_day(x, self.anchor) - d).days))
                  for d in days for m in [month_of(d)]]
        votes = Counter(m % self.every for m in months)
        self.phase = max(votes, key=lambda p: (votes[p], p == months[-1] % self.every))

    def day(self, n):
        if self.by_days:
            return date.fromordinal(self.phase + self.every * n)
        if self.anchors:
            return month_day(n // 2, self.anchors[n % 2])
        return month_day(self.phase + self.every * n, self.anchor)

    def index(self, day):
        """The number of the last expected day on or before `day`."""
        if self.by_days:
            return (day.toordinal() - self.phase) // self.every
        if self.anchors:
            n = 2 * month_of(day) + 1
        else:
            n = (month_of(day) - self.phase) // self.every + 1
        while self.day(n) > day:
            n -= 1
        return n

    def nearest(self, day):
        """The number of the expected day nearest `day`, the earlier of two as near."""
        n = self.index(day)
        return min((n, n + 1), key=lambda m: abs((self.day(m) - day).days))


def tolerance(cadence):
    """Days a payment may sit off its expected day and still be on schedule:
    the cadence band's reach either side of its nominal gap; three days for
    twice a month, whose two days are fixed."""
    if cadence == TWICE:
        return TWICE_TOLERANCE
    lo, hi = BANDS[cadence]
    return max(hi - NOMINAL[cadence], NOMINAL[cadence] - lo)


# ---------------------------------------------------------------- fitting a cadence

def _walk(cadence, days):
    """G16: (payments on schedule, the count they are out of) for one cadence.

    A payment is on schedule when it came one cadence after the payment
    before it. For a cadence of a month or longer, a payment after one that
    was not (paid late, or a second charge in between) is on schedule too when
    it came whole cadences after the last payment that was, within the
    tolerance, so one late payment costs its own gap and not the next one's as
    well; the cadences it spans are counted against it. The count is the
    larger of the gaps and the cadences passed. A weekly or fortnightly series
    has no such allowance: at a few days either side of a short cadence, any
    run of purchases would land on schedule."""
    lo, hi = BANDS[cadence]
    tol, nominal = tolerance(cadence), NOMINAL[cadence]
    anchor, missed, hits, back, slots = days[0], False, 0, 0, 0
    for prev, d in zip(days, days[1:]):
        since = (d - anchor).days
        k = round(since / nominal)
        if lo <= (d - prev).days <= hi:
            hits, slots, anchor, missed = hits + 1, slots + 1, d, False
        elif missed and cadence in MONTH_STEP and k >= 1 and abs(since - k * nominal) <= tol:
            back, slots, anchor, missed = back + 1, slots + k, d, False
        else:
            missed = True
    # a payment back on schedule only makes up for one that was not: most of the
    # gaps must be one cadence on their own, or a run of purchases a month or two
    # apart would pass
    out_of = max(len(days) - 1, slots)
    if cadence in DAY_STEP:
        # Audit: a weekly or fortnightly run is out of every payment it spans, so one with a
        # week missing in three is not on schedule (a cadence of a month or longer already
        # counts the cadences a payment back on schedule spans)
        out_of = max(out_of, round((days[-1] - days[0]).days / nominal))
    return (hits + back if hits / out_of >= 0.5 else hits), out_of


def _twice(days):
    """G16: (payments on schedule, the count they are out of) for twice a month:
    each payment answers the nearest of the two usual days of the month, once."""
    g = Grid(TWICE, days)
    tol = tolerance(TWICE)
    claimed = set()
    for d in days:
        n = g.nearest(d)
        if abs((g.day(n) - d).days) <= tol and n not in claimed:
            claimed.add(n)
    expected = g.nearest(days[-1]) - g.nearest(days[0]) + 1
    return max(0, len(claimed) - 1), max(len(days) - 1, expected - 1)


def score(cadence, days):
    return _twice(days) if cadence == TWICE else _walk(cadence, days)


def runs_at(days, limit):
    """The date list split wherever the hole between two dates is longer than
    `limit`. A series is only a series while it is running."""
    out = []
    for day in days:
        if out and (day - out[-1][-1]).days <= limit:
            out[-1].append(day)
        else:
            out.append([day])
    return out


# of two cadences that fit as well, the first here: two fixed days a month is a
# better schedule for rent paid in halves than a fortnight that drifts off them
PREFER = (TWICE,) + tuple(n for n, *_ in CADENCES if n != TWICE)


def cadence_for(days, flagged=False, habit=False):
    """(name, nominal days, the run of dates that fits) for the cadence the
    payments keep to best, or None. `days` are ISO dates or dates.

    Six monthly charges, a hole of more than a year and two strays afterwards
    are one series that stopped and one that started, not a single series of
    eight running to today. So the dates are cut wherever the hole is more than
    MAX_GAP_FACTOR band widths long and the run with the most payments on
    schedule is what the cadence is measured on."""
    days = [d if isinstance(d, date) else date.fromisoformat(d) for d in days]
    best = None
    for name, lo, hi, nominal, need in CADENCES:
        if habit and name not in HABIT_CADENCES:
            continue
        need = min(need, FLAGGED_NEED[name]) if flagged and name in FLAGGED_NEED else max(need, MIN_OCCURRENCES)
        for run in runs_at(days, hi * MAX_GAP_FACTOR):
            if len(run) < need or (run[-1] - run[0]).days < MIN_SPAN.get(name, 0):
                continue
            hits, out_of = score(name, run)
            # G16: a short run has no room for a miss: four charges at one price with two gaps
            # in three on schedule is as likely chance as a bill
            if len(run) < SHORT_RUN and hits < len(run) - 1:
                continue
            if out_of and hits / out_of >= (TWICE_FIT if name == TWICE else FIT):
                rank = (hits, hits / out_of, -PREFER.index(name))
                if best is None or rank > best[0]:
                    best = (rank, name, nominal, run)
    return (best[1], best[2], [d.isoformat() for d in best[3]]) if best else None


# ---------------------------------------------------------------- amount levels

def dominant_category(rows):
    """The category most of these rows are in."""
    cats = c.by(rows, lambda r: r["category"] or "(uncategorised)")
    return max(cats, key=lambda k: len(cats[k]))


def by_habit(rows, tight=TIGHT_CATEGORIES):
    """True where this is a category one buys in rather than is billed by.
    `tight` is TIGHT_CATEGORIES as the record names them now (run passes
    c.category_names, so a renamed Eating out still counts)."""
    return dominant_category(rows) in tight


def amount_band(rows, tight=TIGHT_CATEGORIES):
    """How far from a level a charge may sit and still be at it.

    A quarter either way is right for a bill that drifts. It is far too wide
    for a category one buys in: at a quarter of six dollars every hawker lunch
    in a month is the same charge, so somewhere one eats gets a twentieth.
    """
    return TIGHT_BAND if by_habit(rows, tight) else AMOUNT_BAND


def normalise(text):
    """G16: bank text as a grouping key: lower case, no digits (a reference or a
    card number changes every month), no punctuation, single spaces. Letters of
    every script stay, with their marks (Chinese, Thai, Tamil), so a bill printed
    in one is still grouped; a lone Latin letter goes, as it is usually a stray
    code."""
    kept = "".join(ch if ch.isalpha() or unicodedata.category(ch).startswith("M") else " "
                   for ch in (text or "").lower())
    return " ".join(w for w in kept.split() if len(w) > 1 or not w.isascii())


def row_text(row):
    """The text a row's statement printed, as server/finance/recurring.py reads it too."""
    return row.get("merchant_raw") or row.get("counterparty") or row.get("memo") or ""


def group_key(row):
    """(kind, name): the payee when there is one, else the normalised bank text.
    None for a row with neither, which cannot be told apart from any other."""
    if row["payee"]:
        return ("payee", row["payee"])
    text = normalise(row_text(row))
    return ("text", text) if text else None


def _log(cents):
    """G20: an amount's log on the dollar scale the levels have always been read
    on: cents over 100, at least a cent. The log of the cents differs only by a
    constant, which changes nothing in exact arithmetic, but a charge exactly at
    a band's edge (S$10.00 and S$12.50 a quarter apart) can then fall the other
    side of it by the float's last digit; on this scale it falls where it did."""
    return math.log(max(cents, 1) / 100)


def _levels(charges, band):
    """G16: the charges split into amount levels, densest first: the amount with
    the most charges within `band` of it on a log scale takes all of those, then
    the same again on what is left. `charges` are (day, amount, ...); the answer
    is lists of indexes into it. Of two amounts as dense, the smaller first.
    Amounts are cents, and a charge of nothing is read as one cent."""
    width = math.log1p(band)
    order = sorted(range(len(charges)), key=lambda i: (charges[i][1], charges[i][0]))
    logs = [_log(charges[i][1]) for i in order]
    left = list(range(len(order)))
    out = []
    while left:
        best, lo, hi = None, 0, 0
        for pos, i in enumerate(left):
            while logs[left[lo]] < logs[i] - width:
                lo += 1
            hi = max(hi, pos)
            while hi + 1 < len(left) and logs[left[hi + 1]] <= logs[i] + width:
                hi += 1
            if best is None or hi - lo + 1 > best[0]:
                best = (hi - lo + 1, lo, hi)
        _, lo, hi = best
        out.append(sorted((order[k] for k in left[lo:hi + 1]), key=lambda i: (charges[i][0], i)))
        left = left[:lo] + left[hi + 1:]
    return out


def _merged(days):
    """Distinct days, a day within MERGE_DAYS of the last kept one folded into it."""
    out = []
    for day in sorted(set(days)):
        if out and c.days_between(out[-1], day) <= MERGE_DAYS:
            continue
        out.append(day)
    return out


def _together(charges, xs, ys, bridges=()):
    """G16: True when two sets of amount levels (lists of indexes) are one series
    whose price changed, however often and in whichever direction: their nearest
    amounts are within PRICE_CHANGE times each other, their charges
    together keep to one cadence over both, no expected payment of that cadence's
    schedule is answered by a charge of both (two plans, one started while the
    other ran, are two series), and that cadence
    is not a shorter one than either kept alone unless it is a month or longer. So
    S$10, S$13 and S$10 again is one bill, a price that alternates month by month
    is one bill whose amount varies, and two plans billed on the 12th and the 20th
    stay two."""
    # a price change, not two things sold at the payee: the nearest amounts of the two are
    # within PRICE_CHANGE times each other, so a top-up and a one-off purchase never make a
    # bill together, however their dates happen to fall (a long walk of prices joins step by
    # step, each level to the one beside it)
    ax = sorted(charges[i][1] for i in xs)
    ay = sorted(charges[i][1] for i in ys)
    if not (ax[0] <= ay[-1] and ay[0] <= ax[-1]):
        low, high = (ax, ay) if ax[-1] < ay[0] else (ay, ax)
        # a price seen once on the way (a loner between them in time) bridges the step
        first, last = min(charges[i][0] for i in xs + ys), max(charges[i][0] for i in xs + ys)
        steps = sorted([low[-1], high[0]] + [charges[i][1] for i in bridges
                                              if first <= charges[i][0] <= last and low[-1] < charges[i][1] < high[0]])
        if any(b > PRICE_CHANGE * max(a, 1) for a, b in zip(steps, steps[1:])):
            return False
    dx = sorted(charges[i][0] for i in xs)
    dy = sorted(charges[i][0] for i in ys)
    found = cadence_for(_merged(dx + dy))
    if not found:
        return False
    name, nominal, run = found
    # G16: which expected payment each charge answers is read off the joined schedule itself
    # (its anchor day, or its two days for twice a month), so the two halves of a month's
    # rent, eleven days apart, are two payments, and a plan billed on the 28th beside one on
    # the 12th answers the 12th's payments
    g = Grid(name, [date.fromisoformat(d) for d in _merged(dx + dy)])
    slots = {}
    for side, ds in ((0, dx), (1, dy)):
        for d in ds:
            slots.setdefault(g.nearest(date.fromisoformat(d)), set()).add(side)
    if any(len(sides) > 1 for sides in slots.values()):
        return False
    if not all(sum(run[0] <= d <= run[-1] for d in side) >= min(2, len(side)) for side in (dx, dy)):
        return False
    if name in MONTH_STEP:
        return True
    alone = [cadence_for(_merged(side)) for side in (dx, dy)]
    return all(f is None or nominal >= f[1] * 0.75 for f in alone)


def partition(charges, band):
    """G16: (series, loners) for one group's charges, each `(day ISO, cents,
    ...)`. A series is a list of levels, each a list of indexes in date order,
    with its `key`: the cents of its first charge, so it reads the same on
    every run over the same rows, and `latest`: the charges its usual amount is
    read from. A level of one charge is a loner: an odd amount, or a new price
    that has not lasted yet.

    Levels of two charges or more join into one series while they are one bill
    at changing prices (`_together`). Where one buys in (a band of a twentieth)
    only the levels joining the densest are tried, which also keeps the work
    small for a payee with hundreds of rides. A series' `latest` is its last
    stretch at one price when that stretch has two charges or more (the new
    normal), else its last RECENT charges whatever their price (a price that
    alternates)."""
    levels = _levels(charges, band)
    densest = levels[0] if levels else None
    loners = sorted((lv[0] for lv in levels if len(lv) == 1), key=lambda i: (charges[i][0], i))
    groups = [[lv] for lv in sorted((lv for lv in levels if len(lv) > 1), key=lambda lv: (charges[lv[0]][0], lv[0]))]
    habit = band == TIGHT_BAND
    joined = True
    while joined:
        joined = False
        for x in range(len(groups)):
            for y in range(x + 1, len(groups)):
                if habit and not any(lv is densest for lv in groups[x] + groups[y]):
                    continue
                if _together(charges, [i for lv in groups[x] for i in lv], [i for lv in groups[y] for i in lv], loners):
                    groups[x] = sorted(groups[x] + groups[y], key=lambda lv: (charges[lv[0]][0], lv[0]))
                    del groups[y]
                    joined = True
                    break
            if joined:
                break
    series = []
    for chain in sorted(groups, key=lambda g: (charges[g[0][0]][0], g[0][0])):
        level_of = {i: k for k, lv in enumerate(chain) for i in lv}
        order = sorted(level_of, key=lambda i: (charges[i][0], i))
        stretch = [i for i in reversed(order)]
        stretch = stretch[:next((n for n, i in enumerate(stretch) if level_of[i] != level_of[order[-1]]), len(stretch))]
        latest = sorted(stretch) if len(stretch) >= 2 else order[-RECENT:]
        series.append({"levels": chain, "key": f"{charges[chain[0][0]][1]}",
                       "densest": any(lv is densest for lv in chain),
                       "latest": sorted(latest, key=lambda i: (charges[i][0], i))})
    return series, loners


def on_schedule(cadence, paid, day):
    """G16: (expected payments on, days off the one it answers) when `day` lands on the
    schedule of a series last paid on `paid` (its recent payment days, dates, oldest
    first), or None. One answer for every cadence, so a new price, a revival and a
    skipped payment are judged alike.

    Twice a month is its two usual days: the expected day nearest `day`, within the
    fixed tolerance, counted on from the expected day of the last payment (the 10th
    to the next 1st is one on, though 22 days). Every other cadence counts from the
    last payment: one on when the gap is in the band, else whole cadences on within
    the tolerance of that many nominal gaps."""
    last = paid[-1]
    if day <= last or cadence not in BANDS:
        return None
    if cadence == TWICE:
        g = Grid(TWICE, paid[-RECENT:])
        n = g.nearest(day)
        off = abs((g.day(n) - day).days)
        k = n - g.nearest(last)
        return (k, off) if k >= 1 and off <= tolerance(TWICE) else None
    lo, hi = BANDS[cadence]
    gap = (day - last).days
    if lo <= gap <= hi:
        return 1, abs(gap - NOMINAL[cadence])
    k = round(gap / NOMINAL[cadence])
    off = abs(gap - k * NOMINAL[cadence])
    return (k, off) if k >= 1 and off <= tolerance(cadence) else None


def assign_loners(charges, loners, states):
    """G16: which series each lone charge answers for, ({loner: series}, {series:
    [loners carrying it on, in date order]}), so no charge is two series'.
    `states` are the group's series in order, each (cadence, its recent payment
    days (ISO, oldest first, the last its last charge's), that charge's amount, its
    usual amount, whether it runs to its last level's end).

    In date order, a loner that lands on a running series' next expected payment
    (`on_schedule`), or for twice a month or a cadence of a month or longer one of the
    next STOP_FACTOR (payments skipped, a pause), at half to twice its last charge's
    price, carries that
    series on; of several series it could, the one whose cadence it keeps best
    and whose price it moves least takes it, the earlier on a tie. Every other
    loner (an odd amount mid-history, a purchase) belongs to the series whose
    usual amount it is nearest. The Recurring page assigns the same way."""
    owner, trail = {}, {k: [] for k in range(len(states))}
    at = [[[date.fromisoformat(d) for d in paid], price] for _, paid, price, _, _ in states]
    for i in sorted(loners, key=lambda i: (charges[i][0], i)):
        day, amount = date.fromisoformat(charges[i][0]), charges[i][1]
        fits = []
        for k, (cadence, _, _, _, running) in enumerate(states):
            paid, prev = at[k]
            if not running or not paid or not prev or not 0.5 <= amount / prev <= 2:
                continue
            on = on_schedule(cadence, paid, day)
            if on and (on[0] == 1 or (on[0] <= STOP_FACTOR and (cadence in MONTH_STEP or cadence == TWICE))):
                # payments skipped (a pause), then the new price: a worse fit than the next payment;
                # a series quiet for longer than it takes to stop is not resumed by one charge
                fits.append((on[0] - 1 + on[1] / NOMINAL[cadence] + abs(math.log(amount / prev)), k))
        if fits:
            k = min(fits)[1]
            owner[i] = k
            trail[k].append(i)
            at[k] = [at[k][0] + [day], amount]
    usual = [u for _, _, _, u, _ in states]
    for i in loners:
        if i not in owner and usual:
            owner[i] = nearest_series(charges[i][1], usual)
    return owner, trail


def nearest_series(amount, levels):
    """Which of several current amounts (`levels`) a charge sits nearest on a
    log scale, by position, or None when there are none."""
    if not levels:
        return None
    return min(range(len(levels)), key=lambda k: abs(_log(amount) - _log(levels[k])))


def spread_varies(amounts, band):
    """G16: True when the amounts (cents) are further apart than the band, the
    test of `amount_varies`, which no longer depends on which path found the
    series."""
    if not amounts:
        return False
    mid = c.median_cents(amounts)
    return mid > 0 and max(amounts) - min(amounts) > mid * band


# ---------------------------------------------------------------- the stat

def account_ends(snapshot, end):
    """G16: {account number: the last day its statements cover}, the later of its
    last statement's end and its last row, never past `end`; a series is judged
    paused or stopped on its own account's, so a lagging statement is not silence.

    Audit: but an account whose statements stop more than CLOSED_AFTER days before
    `end` is closed (a card cancelled, a bank left), not lagging: its series are
    judged on `end`, or a bill on a closed card would stay active for good."""
    out = {}
    for r in snapshot["transactions"]:
        if r.get("account_no") and r.get("date"):
            out[r["account_no"]] = max(r["date"], out.get(r["account_no"], r["date"]))
    for p in snapshot.get("periods") or ():
        if p.get("account_no") and p.get("period_end"):
            out[p["account_no"]] = max(p["period_end"], out.get(p["account_no"], p["period_end"]))
    return {k: end if v >= end or c.days_between(v, end) > CLOSED_AFTER else v for k, v in out.items()}


def _found(rows, flagged, habit=False):
    """(cadence, nominal days, the rows in the run that fits, its occurrences)
    for `rows`, one chain of levels or a whole group, or None when they keep
    to no cadence."""
    days = _merged(r["date"] for r in rows)
    found = cadence_for(days, flagged, habit)
    if not found:
        return None
    name, nominal, run = found
    # Only the rows inside the run that fitted are the series; anything
    # before the hole belonged to the run that ended there.
    return name, nominal, [r for r in rows if run[0] <= r["date"] <= run[-1]], len(run)


def _crowded(group, found, taken=frozenset()):
    """Audit: True when the group's other charges within one cadence either side of a
    series' run number more than CROWD times its own payments (each counted as days,
    `_merged`). At a payee that charges often at many amounts, some stretch of
    purchases at one level always happens to fall a week or a month apart. `taken`
    are the rows (ids) of the group's other runs that keep to a cadence: a second
    plan at the same payee is no crowd."""
    name, nominal, mine, _ = found
    reach = math.ceil(nominal)
    first = date.fromordinal(date.fromisoformat(mine[0]["date"]).toordinal() - reach).isoformat()
    last = date.fromordinal(date.fromisoformat(mine[-1]["date"]).toordinal() + reach).isoformat()
    own = {id(r) for r in mine}
    others = _merged(r["date"] for r in group
                     if first <= r["date"] <= last and id(r) not in own and id(r) not in taken)
    return len(others) > CROWD * len(_merged(r["date"] for r in mine))


def _regular(cadence, days):
    """Audit: True when at least REGULAR_SHARE of a series' payment days (ISO, merged)
    each land within REGULAR_DAYS of a different expected day of its schedule (`Grid`):
    the same weekday for a cadence counted in days, the same day of the month for one
    counted in months. A bill is charged on its day; purchases that only happen to be
    about a week or a month apart drift off it."""
    ds = [date.fromisoformat(d) for d in days]
    g = Grid(cadence, ds)
    near = REGULAR_DAYS[0] if cadence in DAY_STEP else REGULAR_DAYS[1]
    hit = {n for d in ds for n in [g.nearest(d)] if abs((g.day(n) - d).days) <= near}
    return len(hit) >= REGULAR_SHARE * len(ds)


def _steady(amounts):
    """Audit: True when at least STEADY_SHARE of the payments (cents, in date order)
    are within TIGHT_BAND of the payment before or after them: a bill holds each
    price for a while, so a price change or two still passes, but purchases at any
    amount within a level's quarter rarely sit next to one at their own price."""
    def near(a, b):
        return abs(a - b) <= TIGHT_BAND * max(a, b)
    held = sum(any(near(a, amounts[j]) for j in (i - 1, i + 1) if 0 <= j < len(amounts))
               for i, a in enumerate(amounts))
    # as with SHORT_RUN's gaps, a short run has no room for a payment at an odd price
    return held >= (len(amounts) if len(amounts) < SHORT_RUN else STEADY_SHARE * len(amounts))


def _by_chance(group, found, taken=frozenset(), whole=False):
    """Audit: True when a run that keeps to a cadence is more likely purchases that
    happen to fall on it than a bill. A bill is charged on its day at its price: in a
    crowded group (`_crowded`) a run must keep to its schedule's days (`_regular`) and
    hold its prices (`_steady`); the group taken whole, whose amounts move by
    definition, must always keep to its days."""
    name, _, mine, _ = found
    days = _merged(r["date"] for r in mine)
    if whole:
        return not _regular(name, days)
    return (_crowded(group, found, taken)
            and not (_regular(name, days) and _steady([-c.sgd_minor(r) for r in mine])))


def _standing(rows, runs):
    """Audit: the runs ((chain, members, recent, found)) that are not purchases by chance
    (`_by_chance`). A run's crowd leaves out the charges of the group's other runs, but
    only of runs still standing: one dropped (by chance, or under the floor) is purchases
    again, so the rest are judged once more until none falls."""
    while True:
        keep = [x for x in runs
                if not _by_chance(rows, x[3], {id(r) for y in runs if y is not x for r in y[3][2]})]
        if len(keep) == len(runs):
            return runs
        runs = keep


def state_after(since, nominal):
    """G16: active, paused or stopped after `since` days of silence on a cadence of
    `nominal` days: paused past one and a half cadences and at least a month,
    stopped past three cadences and at least two months."""
    if since > max(nominal * STOP_FACTOR, STOP_FLOOR):
        return "stopped"
    return "paused" if since > max(nominal * PAUSE_FACTOR, PAUSE_FLOOR) else "active"


def monthly_cost_minor(usual, nominal):
    """G20: a usual charge of `usual` cents every `nominal` days as cents a
    month (30.44 days), to the cent, half up (`figures.div_cents`). Every
    nominal gap is whole hundredths of a day, so the sum is integer cents."""
    return c.div_cents(usual * round(NOMINAL["monthly"] * 100), round(nominal * 100))


def _series_row(kind, group, key, rows, recent, found, flagged, band, ends, end):
    name, nominal, rows, count = found
    amounts = [-c.sgd_minor(r) for r in rows]
    median = c.median_cents(-c.sgd_minor(r) for r in recent)
    last = max(r["date"] for r in rows)
    account = max(rows, key=lambda r: r["date"]).get("account_no")
    as_of = ends.get(account, end) if end else last
    since = c.days_between(last, as_of) if as_of and as_of > last else 0
    status = state_after(since, nominal)
    cats = c.by(rows, lambda r: r["category"] or "(uncategorised)")
    buckets = c.by(rows, lambda r: r["bucket"])
    return {
        "id": f"{kind}:{group}:{key}",
        "payee": group if kind == "payee" else f"(no payee) {group.upper()}",
        "grouped_by": kind, "group": group,
        "category": max(cats, key=lambda k: len(cats[k])),
        "bucket": max(buckets, key=lambda k: len(buckets[k])),
        "cadence": name, "cadence_days": nominal,
        "occurrences": count, "rows": len(rows),
        "amount_varies": spread_varies([-c.sgd_minor(r) for r in recent], band),
        "first_seen": min(r["date"] for r in rows), "last_seen": last,
        "avg_amount_minor": c.mean_cents(amounts), "median_amount_minor": median,
        "total_minor": c.sum_cents(amounts),
        "monthly_cost_minor": monthly_cost_minor(median, nominal),
        "as_of": as_of, "days_since_last": since,
        "status": status, "paused": status == "paused", "stopped": status == "stopped",
        "flagged_recurring": flagged,
    }


def detect_group(kind, group, rows, flagged, tight, ends, end):
    """G16: every series in one group's charges: each chain of amount levels
    that keeps to a cadence, at the median of its latest level's last RECENT
    charges; failing every one, the group whole, as a bill whose amount moves
    does (never where one buys in)."""
    rows = sorted(rows, key=lambda r: (r["date"], r.get("key") or ""))
    band = amount_band(rows, tight)
    charges = [(r["date"], -c.sgd_minor(r)) for r in rows]
    chains, loners = partition(charges, band)
    habit = by_habit(rows, tight)
    if habit:
        # where one buys in, one level: the amount most charges are at, and the
        # levels it took over from or that took over from it
        chains = [ch for ch in chains if ch["densest"]]
    runs = []
    for chain in chains:
        members = sorted((i for lv in chain["levels"] for i in lv), key=lambda i: (charges[i][0], i))
        found = _found([rows[i] for i in members], flagged, habit)
        if found:
            runs.append((chain, members, found))
    out = []
    for chain, members, found in runs:
        # the usual amount: the median of the last few charges at the latest level the run reaches
        run = {id(r) for r in found[2]}
        latest = [rows[i] for i in chain["latest"] if id(rows[i]) in run] or found[2]
        recent = latest[-RECENT:]
        if c.median_cents(-c.sgd_minor(r) for r in recent) >= MIN_AMOUNT_MINOR or flagged:
            out.append((chain, members, recent, found))
    out = _standing(rows, out)
    # G16: the lone charges after each series' last carry it on, each to one series only;
    # a series that then falls under the floor is dropped and the rest are assigned again,
    # so the page, which assigns among the series the result holds, does the same
    while True:
        states = [(found[0], _merged(r["date"] for r in found[2])[-RECENT:], -c.sgd_minor(found[2][-1]),
                   c.median_cents(-c.sgd_minor(r) for r in recent), charges[members[-1]][0] <= found[2][-1]["date"])
                  for _, members, recent, found in out]
        _, trail = assign_loners(charges, loners, states)
        series, keep = [], []
        for k, (chain, members, recent, found) in enumerate(out):
            name, nominal, mine, count = found
            extra = [rows[i] for i in trail[k]]
            mine, count = mine + extra, count + len(extra)
            # one new price is a different amount until it repeats; several in a row are a bill that moves
            if len(extra) > 1:
                recent = mine[-RECENT:]
            # the floor once more, on the amount the series reports: a price that fell under it is noise now
            if c.median_cents(-c.sgd_minor(r) for r in recent) < MIN_AMOUNT_MINOR and not flagged:
                continue
            keep.append(k)
            series.append(_series_row(kind, group, chain["key"], mine, recent, (name, nominal, mine, count),
                                      flagged, band, ends, end))
        if len(keep) == len(out):
            break
        # a series dropped here is no longer another series: the rest are judged again
        out = _standing(rows, [out[k] for k in keep])
    if series or habit:
        # A coffee shop or a bus fare is not retried whole: at any amount at
        # all, twice a week is habit, and the retry is exactly how Grab,
        # Bus/MRT and a coffee stall got in.
        return series
    found = _found(rows, flagged)
    if found and _by_chance(rows, found, whole=True):
        found = None
    # G16: the floor is held against the run the retry reports, whose usual amount the
    # series carries, not the group's latest charges, which may lie outside it
    if found and (c.median_cents(-c.sgd_minor(r) for r in found[2][-RECENT:]) >= MIN_AMOUNT_MINOR or flagged):
        series.append(_series_row(kind, group, "all", found[2], found[2][-RECENT:], found,
                                  flagged, band, ends, end))
    return series


def name_series(series):
    """G16: a display name: the payee alone when it has one series, the payee
    and the series' usual amount when it has several."""
    per = Counter(s["id"].rsplit(":", 1)[0] for s in series)
    for s in series:
        many = per[s["id"].rsplit(":", 1)[0]] > 1
        s["name"] = f"{s['payee']} ({label(s['median_amount_minor'])})" if many else s["payee"]
    return series
