"""The Recurring page: what is coming up this month, what runs, what stopped,
and which payees the rules mark recurring that show no regular spacing.

Which series exist, their cadence, their typical amount and whether they
stopped all come from the recurring-detector result. The record adds what the
stat cannot know until it runs again: the payments that arrived this month,
on which day and for how much. The detector's cadence bands, amount bands and
merge window are imported, not restated, so the page and the stat agree on
what counts as on schedule. Money leaves as integer cents, and (G20) the
result's money is read in cents too, from `<name>_minor` (server/finance/money.py,
`stat_cents`).

"This month" is the record's last month when the record ends before today,
the same rule Home uses: statements arrive a month or more late, and a payment
is not late just because its statement has not been loaded yet. For the same
reason each series is judged on the last statement date of the account it is
paid from, not the record's latest date across every account (G16).

G16: a series' schedule is a grid of expected days on its anchor day (the
usual day of the month of its recent payments, or the usual weekday), never a
cursor stepped on from the last payment. Each payment answers the expected day
nearest it; a payment in the usual range that answers none is an extra charge,
listed but never counted against next month's bill.

G16: the owner's edits to a series (server/finance/series.py, kept in the record) are
read over what was detected, everywhere the series shows: its usual amount, how
often, its day and the day it ended replace the detected ones for its schedule,
its lateness, Coming up and its totals, and a series marked not recurring is in
none of them (its own view on Recurring, `series_view`, still opens it).
"""
import math
from calendar import monthrange
from collections import Counter
from datetime import date, timedelta

from finance import series
from finance.figures import median_cents
from finance.money import stat_cents
from .cadence import (AMOUNT_BAND, BANDS, CLOSED_AFTER, DAY_STEP, MERGE_DAYS, MONTH_STEP, NOMINAL,
                      TIGHT_BAND, TIGHT_CATEGORIES, Grid, _merged, account_ends,
                      assign_loners, detect_group, group_key, monthly_cost_minor, name_series,
                      normalise, on_schedule, partition, state_after, tolerance)

STAT = "recurring-detector"
CADENCES = tuple(BANDS)   # G16: what an edit's "how often" may be (server/finance/series.py); the record CHECKs it
STATES = ("active", "paused", "stopped")
NOT_RECURRING = "not_recurring"   # G16: the series the owner said are not recurring, in no list but their own
# BANDS: the gaps the detector counts as on schedule, per cadence, in days
PER_YEAR = {"weekly": 52, "fortnightly": 26, "twice-monthly": 24, "monthly": 12, "two-monthly": 6,
            "quarterly": 4, "half-yearly": 2, "yearly": 1}
RECENT = 6          # payments a varying bill's usual range is read from
# G16: a live series is active, or paused (silent one and a half to three cadences: listed, not counted)
SERIES_STATES = ("active", "paused")
MIN_TOLERANCE = 2   # G16: no expected payment is late sooner than this many days after its day
INVESTING = "Transfer"   # the bucket of a series that moves money to investments
# G17: where a payment expected this month stands (slots), as server/finance/types.py types it
STATUSES = ("paid", "due", "late", "different", "extra")   # G16: extra, a charge no expected payment answers
# G16: and in a series' own schedule, an expected payment passed over when a later one came
SLOT_STATUSES = (*STATUSES, "skipped")

# the rows the detector reads: External outflows that are not synthetic fillers,
# not income and not a transfer between the owner's own pockets
SERIES_ROWS = """SELECT txn_key, txn_date, -amount_sgd_minor AS paid, payee_name, account_id, category_name,
                        COALESCE(NULLIF(merchant_raw, ''), NULLIF(counterparty, ''), memo, '') AS text
                 FROM v_txn
                 WHERE flow_class = 'External' AND is_synthetic = 0 AND amount_sgd_minor < 0
                   AND COALESCE(bucket, '') != 'Income' AND COALESCE(need_class, '') != 'Transfer'
                 ORDER BY txn_date, txn_key"""


def usual_cents(s):
    """G20: a series' usual amount as positive integer cents: its median, else its
    mean, else 0, as the detector wrote them (`stat_cents`)."""
    return abs(stat_cents(s, "median_amount") or stat_cents(s, "avg_amount") or 0)


def step(day, cadence):
    """The day one cadence after `day`: seven or fourteen days for weekly and
    fortnightly, about half a month for twice a month, the same day of the
    month one, two, three, six or twelve months on for the rest (the 31st falls
    back to the month's last day)."""
    if cadence not in MONTH_STEP:
        return day + timedelta(days=DAY_STEP.get(cadence, 15 if cadence == "twice-monthly" else 7))
    i = day.year * 12 + day.month - 1 + MONTH_STEP[cadence]
    y, m = divmod(i, 12)
    return date(y, m + 1, min(day.day, monthrange(y, m + 1)[1]))


def judged_on(conn, today):
    """(the day the month is judged on, the record's last date)."""
    last = conn.execute("SELECT MAX(txn_date) FROM v_txn").fetchone()[0]
    return (min(today, date.fromisoformat(last)) if last else today), last


def statement_ends(conn):
    """G16: {account id: the last day its statements cover}, the later of its last
    statement's end and its last row, so a card loaded to 28 October does not make
    a bank account loaded to 30 September look a month behind on its bills."""
    out = {}
    for account, day in conn.execute("""SELECT account_id, MAX(period_end) FROM v_period
                                        WHERE account_id IS NOT NULL AND period_end IS NOT NULL
                                        GROUP BY account_id
                                        UNION ALL
                                        SELECT account_id, MAX(txn_date) FROM v_txn
                                        WHERE account_id IS NOT NULL GROUP BY account_id"""):
        try:
            day = date.fromisoformat(day)
        except (TypeError, ValueError):
            continue
        out[account] = max(day, out.get(account, day))
    return out


def judged_for(pays, ends, today, fallback):
    """G16: the day one series is judged on: the last statement day of the account
    its latest payment in the usual range came from, never past today; the
    record's own day (`fallback`) when the account is unknown. Only the series'
    own payments count: another purchase at the same payee, on a card whose
    statements run later, says nothing about the account the bill is paid from.
    Audit: an account whose statements stopped more than CLOSED_AFTER days before
    `fallback` is closed, and its series are judged on `fallback` (cadence.account_ends)."""
    own = [p for p in pays if p.regular and p.day <= today]
    end = ends.get(own[-1].account) if own else None
    if end and (fallback - end).days > CLOSED_AFTER:
        return fallback
    return min(today, end) if end else fallback


class Payment:
    """One occurrence: the charges on one day, or within MERGE_DAYS of it,
    which the detector also counts once. Regular when its charges sit in the
    series' usual range."""

    def __init__(self, day, amount, text, regular, key=None, account=None):
        self.day, self.amounts, self.text, self.regular = day, [amount], text, regular
        self.keys = [key] if key else []
        self.account = account

    def charge_in(self, low, high):
        """(the figure this payment answers with, the keys of the rows it adds up): the charge
        inside the usual range closest to its middle, else all of them together."""
        mid = (low + high) // 2
        pairs = list(zip(self.amounts, self.keys)) if len(self.keys) == len(self.amounts) else []
        inside = [p for p in pairs if low <= p[0] <= high]
        if inside:
            amount, key = min(inside, key=lambda p: abs(p[0] - mid))
            return amount, [key]
        inside = [a for a in self.amounts if low <= a <= high]
        if inside:
            return min(inside, key=lambda a: abs(a - mid)), []
        return sum(self.amounts), list(self.keys)

    def amount_in(self, low, high):
        return self.charge_in(low, high)[0]


def merge(charges, regular):
    """[(day, cents, text, key, account, ...)] as Payments, two charges MERGE_DAYS apart being one."""
    out = []
    for day, amount, text, key, *account in charges:
        if out and (day - out[-1].day).days <= MERGE_DAYS:
            out[-1].amounts.append(amount)
            out[-1].keys.append(key)
        else:
            out.append(Payment(day, amount, text, regular, key, account[0] if account else None))
    return out


def _group_keys(payee, paid, text):
    """The detector's grouping (recurring_detector.group_key): payee, else the
    normalised bank text (G16). A row with no payee is also filed under its
    amount rounded to the dollar, the grouping a result from before G16 names."""
    if payee:
        return [("payee", payee)]
    legacy = ("amount", f"{abs(round(paid / 100)):.0f}")
    text = normalise(text)
    return [("text", text), legacy] if text else [legacy]


def _series_key(s):
    """The group one series' charges are in."""
    if s.get("grouped_by") == "amount":
        # "(no payee, about SGD 18)": the amount a result from before G16 grouped on
        return ("amount", s["payee"].rsplit(" ", 1)[-1].rstrip(")"))
    if s.get("grouped_by") == "text":
        return ("text", s.get("group") or "")
    return ("payee", s.get("payee") or "")


def _charges(conn):
    """Every series-eligible charge in the record, (day, cents, bank text, key, account id,
    category), by detector group."""
    out = {}
    for r in conn.execute(SERIES_ROWS):
        for k in _group_keys(r["payee_name"], r["paid"], r["text"]):
            out.setdefault(k, []).append((date.fromisoformat(r["txn_date"]), r["paid"], r["text"], r["txn_key"],
                                          r["account_id"], r["category_name"]))
    return out


def _siblings(data):
    """G16: {group: every series of it the result holds, live or stopped}, so an odd
    charge goes to the one of a payee's several series whose amount it is nearest."""
    out = {}
    for s in [x for state in (*STATES, NOT_RECURRING) for x in data.get(state, [])]:
        out.setdefault(_series_key(s), []).append(s)
    return out


def _band(mine, tight):
    """The detector's amount band for one group (recurring_detector.amount_band): its
    most common category decides."""
    cats = Counter(c[5] or "(uncategorised)" for c in mine)
    return TIGHT_BAND if cats and max(cats, key=cats.get) in tight else AMOUNT_BAND


def _split(s, mine, siblings, tight, stat_end=None):
    """G16: (the series' own charges, the odd ones it may answer with) for a series the
    detector named by id.

    The group's charges are split into amount levels as the detector splits them
    (recurring_detector.partition); the series is the chain of levels whose first charge
    its id names, from the day it was first seen. The page reads every group the stat saw
    a series in again from the record (`_live`), so the ids are the record's own; a series
    no chain holds has no payments, never the payee's whole history. A
    level of one charge (an odd amount, a new price not yet repeated) is an odd charge of
    whichever of the group's series it is nearest in amount. A charge at another
    repeated level (a payee's top-ups beside its subscription) is no part of this
    series."""
    since = date.fromisoformat(s["first_seen"]) if s.get("first_seen") else date.min
    mine = sorted(mine, key=lambda c: (c[0], c[3]))
    if s["id"].endswith(":all"):
        return [c for c in mine if c[0] >= since], []
    charges = [(c[0].isoformat(), c[1]) for c in mine]
    band = _band(mine, tight)
    chains, loners = partition(charges, band)
    by_key = {ch["key"]: ch for ch in chains}
    named_chains = {x["id"]: by_key[x["id"].rsplit(":", 1)[-1]] for x in siblings
                    if not x["id"].endswith(":all") and x["id"].rsplit(":", 1)[-1] in by_key}
    if s["id"] not in named_chains:
        return [], []
    # G16: the lone charges go to the group's series as the detector gave them
    # (recurring_detector.assign_loners), in the order of their chains, so no charge
    # is two series'
    named = sorted((x for x in siblings if x["id"] in named_chains),
                   key=lambda x: (charges[named_chains[x["id"]]["levels"][0][0]][0], x["id"]))
    states = []
    for x in named:
        ch = named_chains[x["id"]]
        start = x.get("first_seen") or ""
        days = sorted((charges[i][0], i) for lv in ch["levels"] for i in lv if charges[i][0] >= start)
        if not days:
            days = sorted((charges[i][0], i) for lv in ch["levels"] for i in lv)
        end = days[-1][1]
        # the usual amount before any lone charge carried it on, as the detector reads it:
        # the latest stretch's last RECENT charges from the day the series was first seen
        latest = [charges[i][1] for i in ch["latest"] if charges[i][0] >= start][-RECENT:]
        usual = median_cents(latest) if latest else usual_cents(x)
        # G16: the series runs to its chain's end unless the stat saw a charge of the chain
        # after the run it reported (a run that ended at a hole); charges loaded since the
        # stat ran carry it on, as the detector will find when it runs again
        seen_to = stat_end or x.get("last_seen") or ""
        running = not any(x.get("last_seen", "") < d <= seen_to for d, _ in days)
        states.append((x.get("cadence") or "monthly", _merged(d for d, _ in days)[-RECENT:], charges[end][1],
                       usual, running))
    owner, _ = assign_loners(charges, loners, states)
    me = next(k for k, x in enumerate(named) if x is s)
    own = [mine[i] for lv in named_chains[s["id"]]["levels"] for i in lv if mine[i][0] >= since]
    odd = [mine[i] for i in loners if mine[i][0] >= since and owner.get(i) == me]
    return sorted(own), odd


def tight_categories(conn):
    """TIGHT_CATEGORIES as the record names them now, following a rename the
    way the detector does (common.category_names)."""
    from finance.db import category_renames
    renames = category_renames(conn)
    return {renames.get(n, n) for n in TIGHT_CATEGORIES}


def usual_range(s, history, tight=TIGHT_CATEGORIES):
    """(low, high) in cents for one series' charge. The detector's own band
    around the median: a twentieth where one buys in, a quarter elsewhere. A
    bill whose amount varies has no band, so its last few charges set it."""
    median = usual_cents(s)
    if s.get("amount_varies"):
        recent = [a for p in history[-RECENT:] for a in p.amounts]
        return (min(recent), max(recent)) if recent else (median, median)
    band = TIGHT_BAND if s.get("category") in tight else AMOUNT_BAND
    return round(median * (1 - band)), round(median * (1 + band))


def _prepare(s, charges, start, tight, siblings=None, stat_end=None):
    """(payments, low, high) for one series. Charges inside the usual range are
    merged apart from the ones outside it, as the detector drops an odd charge
    before it merges, so a small extra charge the day before cannot swallow a
    regular payment. A varying bill's range comes from its charges before
    `start`, so this month's cannot widen it.

    G16: a series the detector named by id has its own charges (its chain of
    amount levels) and the odd ones nearest it (`_split`), or none when no chain holds
    it now; only a result from before series had ids is read as its whole group, its
    charges in the usual range its own."""
    mine = charges.get(_series_key(s), [])
    split = _split(s, mine, (siblings or {}).get(_series_key(s), [s]), tight, stat_end) if s.get("id") else None
    if split is not None:
        own, odd = split
        if s.get("amount_varies"):
            # a bill whose price moves at every payment answers with odd charges, so those set its
            # range too, but only the ones on its schedule at about its amount: never a purchase
            before = merge([c for c in own if c[0] < start], True)
            days_ = [p.day for p in before]
            answered = [p for p in merge([c for c in odd if c[0] < start], False) if _answers(s, p, days_)]
            low, high = usual_range(s, sorted(before + answered, key=lambda p: p.day), tight)
        else:
            low, high = usual_range(s, [], tight)
        return sorted(merge(own, True) + merge(odd, False), key=lambda p: (p.day, not p.regular)), low, high
    if s.get("amount_varies"):
        low, high = usual_range(s, merge([c for c in mine if c[0] < start], True), tight)
        return merge(mine, True), low, high
    low, high = usual_range(s, [], tight)
    inside = merge([c for c in mine if low <= c[1] <= high], True)
    outside = merge([c for c in mine if not low <= c[1] <= high], False)
    return sorted(inside + outside, key=lambda p: (p.day, not p.regular)), low, high


# G13: what a payee's link adds to `payee=` so the list holds the payee's payments and no
# other row of it: Transactions hides transfers unless told not to, and a payee is counted
# over every payment it has, Transfer-bucket ones included (an investing series, or a payee
# with payments in more than one category); a made-up filler, a refund and money moved
# between the owner's own accounts are not payments (`synthetic=0`, `out=1`) and what is the payee's by name only (`named=1`)
PAYMENTS = {"hide_transfers": "0", "synthetic": "0", "out": "1"}


def payee_link(payee):
    """A payee's payments in Transactions: the payee as the rules gave it (`named=1`), so a row
    with no payee whose bank text reads the same is not among them."""
    return {"payee": payee, "named": "1", **PAYMENTS}


def transactions_filter(s, pays, payee_rows=None):
    """The Transactions query that lists one series' rows: its payee, exactly.
    A series grouped by amount or bank text has no payee, so its link names its
    own rows by key; one with no rows to name opens its category in the month
    it was last seen.

    G16: a series the detector named by id is the payee's link only when it
    holds every payment of the payee (`payee_rows`, {payee: keys}); a payee
    with several series, or top-ups beside its one, links each series to its
    own rows by key."""
    keys = list(dict.fromkeys(k for p in pays for k in p.keys))
    if (s.get("grouped_by") or "payee") == "payee" and s.get("payee"):
        if not s.get("id") or payee_rows is None or set(keys) == payee_rows.get(s["payee"], set()):
            return payee_link(s["payee"])
    if keys:
        return {"key": ",".join(keys), "hide_transfers": "0"}
    category = s.get("category") or "(uncategorised)"
    return {"month": (s.get("last_seen") or "")[:7],
            "category": "(none)" if category == "(uncategorised)" else category, "hide_transfers": "0"}


def paid_link(keys):
    """G13: the payment a coming-up row's figure is, by key; nothing when the record cannot name it."""
    return {"key": ",".join(keys), "hide_transfers": "0"} if keys else None


def series_id(s):
    """G16: the detector's id for a series; one from a result before G16 is its group."""
    return s.get("id") or ":".join(_series_key(s))


def payee_rows(conn):
    """{payee: the keys of every row its payee link lists} (`payee_link`)."""
    out = {}
    for r in conn.execute("""SELECT payee_name, txn_key FROM v_txn WHERE payee_name IS NOT NULL
                             AND flow_class = 'External' AND amount_minor < 0 AND is_synthetic = 0"""):
        out.setdefault(r["payee_name"], set()).add(r["txn_key"])
    return out


def _row_base(s, pays, rows=None):
    # G16: `id` tells a payee's several series apart; `name` is what the page shows
    return {"id": series_id(s), "name": s.get("name") or s.get("payee") or "", "payee": s.get("payee") or "",
            "link": transactions_filter(s, pays, rows), "cadence": s.get("cadence") or "",
            "category": s.get("category") or "", "investing": s.get("bucket") == INVESTING,
            "amount": usual_cents(s)}


def _same_month(a, b):
    return (a.year, a.month) == (b.year, b.month)


class Schedule:
    """G16: one series' expected days from its recent payments up to `until`,
    each answered by at most one payment, and the extra charges.

    A payment answers the expected day nearest it when it is within that day's
    tolerance; one in the usual range wins over one outside it, then the
    nearer. What is left answers, in turn, the expected day before it (paid
    late), a later one in its own calendar month (paid early), or the one before
    that (a missed payment caught up). A charge outside the usual range only
    ever answers a day in its own month, so an odd charge on the 31st cannot
    stand in for a bill due on the 1st, and one that answers nothing is just
    another purchase; a charge in the usual range that answers nothing is
    extra. So a charge never fills a later month's payment unless it came
    within the tolerance of it.

    A day's tolerance is the cadence band's longest gap less the days since the
    expected day before it (35 days monthly, so 4 after a 31-day month). An
    expected day with no payment is late once today is past its tolerance, due
    before that; one before the last paid was skipped."""

    def __init__(self, s, pays, ref, until, since):
        cadence = self.cadence = s.get("cadence") or "monthly"
        self.hi = BANDS.get(cadence, BANDS["monthly"])[1]
        self.ref = ref
        pays = [p for p in pays if p.day <= ref]
        regular = [p for p in pays if p.regular]
        recent = regular[-RECENT:]
        self.ok = bool(recent)
        self.filled, self.extras, self.slots = {}, [], []
        if not self.ok:
            return
        # the recent payments choose the anchor; the expected days start early enough to
        # cover everything from `since` on (the last regular payment before it, else the
        # first), so a month with more charges than RECENT still has its first payment
        # G16: on the owner's day when the series' edit sets one
        g = self.grid = Grid(cadence, [p.day for p in recent], day=s.get("day"))
        before = [p for p in regular if p.day < since]
        first = min(recent[0].day, (before[-1] if before else regular[0]).day)
        self.first = min((g.index(first), g.index(first) + 1), key=lambda n: abs((g.day(n) - first).days))
        self.last = max(g.index(until), g.index(ref) + 1)
        # G16: and nothing is expected after the day the owner says it ended
        self.ended = date.fromisoformat(s["ended_on"]) if s.get("ended_on") else None
        if self.ended:
            self.last = min(self.last, g.index(self.ended))
        pays = [p for p in pays if p.day >= g.day(self.first - 1)]
        self._match(pays)
        self.paid_to = max(self.filled, default=self.first - 1)   # the last expected day answered
        self.slots = [self._slot(n) for n in range(self.first, self.last + 1)]

    def tolerance(self, n):
        """G16: days expected day `n` may be paid after and still be on time: the band's
        longest gap less the days since the expected day before it, never under
        MIN_TOLERANCE. Twice a month has its own fixed tolerance, as its two days of the
        month can sit far apart (the 10th to the 1st is 22 days, the band's longest 20)."""
        if self.cadence == "twice-monthly":
            return tolerance(self.cadence)
        return max(MIN_TOLERANCE, self.hi - (self.grid.day(n) - self.grid.day(n - 1)).days)

    def _fits(self, p, n):
        return self.first <= n <= self.last and (p.regular or _same_month(p.day, self.grid.day(n)))

    def _match(self, pays):
        g, claims = self.grid, {}
        for p in pays:
            n = min((g.index(p.day), g.index(p.day) + 1), key=lambda m: abs((g.day(m) - p.day).days))
            if abs((g.day(n) - p.day).days) <= self.tolerance(n) and self._fits(p, n):
                claims.setdefault(n, []).append(p)
        for n, ps in claims.items():
            self.filled[n] = min(ps, key=lambda p: (not p.regular, abs((g.day(n) - p.day).days)))
        used = {id(p) for p in self.filled.values()}
        for p in pays:
            if id(p) in used:
                continue
            n = g.index(p.day)
            options = [n] + ([n + 1] if not g.by_days and _same_month(p.day, g.day(n + 1)) else []) + ([n - 1] if p.regular else [])
            m = next((m for m in options if m not in self.filled and self._fits(p, m)), None)
            if m is not None:
                self.filled[m] = p
            elif p.regular:
                self.extras.append(p)

    def _slot(self, n):
        expected, p = self.grid.day(n), self.filled.get(n)
        if p is not None:
            status = "paid"
        else:
            status = "late" if (self.ref - expected).days > self.tolerance(n) else "due"
        return {"expected": expected, "payment": p, "status": status, "skipped": p is None and n < self.paid_to}

    def unpaid(self):
        """The expected days since the last one paid that nothing has answered yet, earliest first."""
        return [x for x in self.slots if x["payment"] is None and not x["skipped"]]

    def next_expected(self):
        """The first expected day nothing has answered; None when the series ended before it."""
        unpaid = self.unpaid()
        nxt = unpaid[0]["expected"] if unpaid else self.grid.day(self.paid_to + 1)
        return None if self.ended and nxt > self.ended else nxt


def _paid_row(p, low, high):
    amount, keys = p.charge_in(low, high)
    return {"status": "paid" if low <= amount <= high else "different", "paid_on": p.day.isoformat(),
            "paid_amount": amount, "link": paid_link(keys)}


def slots(s, pays, low, high, start, end, ref):
    """Every payment one series expected that this month (`start` to `end`)
    shows, with its status, and the month's extra charges.

    A month shows the payments expected in it; one expected earlier and paid
    in it (late); and every one since the last paid that nothing has answered
    yet, which stays late until it or a later one is paid. One skipped before a
    later paid one is history, in this month too (a weekly cleaner who missed
    5 September and came on the 12th owes nothing for the 5th), so the list, the
    series' late flag and its next day agree. A payment made early
    for next month shows in next month, not this one. Paid when the charge is in
    the usual range, different when it is not, extra for a charge in the usual
    range that answers no expected payment."""
    sched = Schedule(s, pays, ref, end, start)
    out = []
    for x in sched.slots:
        expected, p = x["expected"], x["payment"]
        if p is not None:
            if not (start <= expected <= end or (expected < start and start <= p.day <= end)):
                continue
            row = _paid_row(p, low, high)
        elif not x["skipped"] and expected <= end:
            row = {"status": x["status"], "paid_on": None, "paid_amount": None, "link": None}
        else:
            continue
        out.append({"expected": expected.isoformat(), "estimated": True, **row})
    for p in sched.extras:
        if start <= p.day <= end:
            out.append({"expected": p.day.isoformat(), "estimated": False, **_paid_row(p, low, high), "status": "extra"})
    return out


# which of a series' payments this month speaks for it (Recurring's chip, Home's
# "Needs your eye"): the one that wants a look first, and of several paid, the latest
URGENT = {"late": 0, "different": 1, "extra": 2, "due": 3, "paid": 4}


def _series_of(r):
    return r["id"]


def most_urgent(rows):
    """G16: one row per series, the one that speaks for it this month, in date order.
    Recurring.tsx's mergeSeries picks the same way."""
    def rank(r):
        day = date.fromisoformat(r["expected"]).toordinal()
        return URGENT[r["status"]], -day if r["status"] == "paid" else day
    out = {}
    for r in rows:
        k = _series_of(r)
        if k not in out or rank(r) < rank(out[k]):
            out[k] = r
    return sorted(out.values(), key=lambda r: (r["expected"], r["payee"]))


def _results_data(results):
    return ((results or {}).get(STAT) or {}).get("data") or {}


def _answers(s, p, own_days):
    """G16: True when odd charge `p` stands for one of the series' payments: it lands on
    the series' schedule after its own recent payments (`own_days`), whole expected
    payments on (recurring_detector.on_schedule, the detector's own test), at half to
    twice its usual amount. Anything else at the payee (a big purchase, a top-up) is
    another charge, never the bill."""
    usual = abs(stat_cents(s, "median_amount") or 0)
    cadence = s.get("cadence") or "monthly"
    own = [d for d in own_days if d < p.day][-RECENT:]
    if not usual or not own or cadence not in BANDS or not 0.5 <= sum(p.amounts) / usual <= 2:
        return False
    return on_schedule(cadence, own, p.day) is not None


def _resumed(s, p, pays):
    """G16: True when payment `p` brings a paused series back: one of its own, or an odd
    charge that answers for it (it resumed at a new price, and shows as a different
    amount until the stat sees it twice)."""
    return p.regular or _answers(s, p, [q.day for q in pays if q.regular])


# G16: what the detector reads of a row, as the snapshot gives it (db.snapshot)
LIVE_ROWS = """SELECT txn_key, txn_date, amount_sgd_minor, payee_name, category_name, bucket, need_class,
                      merchant_raw, counterparty, memo, account_no
               FROM v_txn
               WHERE flow_class = 'External' AND is_synthetic = 0 AND amount_sgd_minor < 0
                 AND COALESCE(bucket, '') != 'Income' AND COALESCE(need_class, '') != 'Transfer'
               ORDER BY txn_date, txn_key"""


def _live(conn, data, tight, off=frozenset()):
    """G16: the result with every group the stat found a series in, and every group with a
    row it never saw (dated after its `as_of`), detected again from the record as it is now
    (recurring_detector.detect_group, the stat's own code, on that group's rows). So a
    statement loaded since the stat ran, older or newer, reads on the page as it will after
    the next run, and no saved series has to be matched to a record that moved under it. A
    result from before series had ids is read as it is.

    `off` are the payees the owner said are not recurring (server/finance/series.py). They are
    detected again too, still as the rules mark them, so a series the mark alone finds (a
    yearly bill seen twice, a bill under S$10) is still there for its own Recurring to bring
    back; `Record._edited` hides the rest and takes the payee off Marked recurring, no
    regular spacing. The stat itself reads such a payee as unmarked (db.snapshot)."""
    saved = [*data.get("active", []), *data.get("paused", []), *data.get("stopped", [])]
    if not data or any(not x.get("id") for x in saved):
        return data
    groups = {}
    for r in conn.execute(LIVE_ROWS):
        row = {"key": r["txn_key"], "date": r["txn_date"], "amount_sgd_minor": r["amount_sgd_minor"],
               "payee": r["payee_name"] or "", "category": r["category_name"] or "", "bucket": r["bucket"],
               "need_class": r["need_class"], "merchant_raw": r["merchant_raw"] or "",
               "counterparty": r["counterparty"] or "", "memo": r["memo"] or "", "account_no": r["account_no"]}
        k = group_key(row)
        if k:
            groups.setdefault(k, []).append(row)
    seen_to = data.get("as_of") or ""
    flagged = {r[0] for r in conn.execute("SELECT name FROM payee WHERE recurring = 1")}
    # G16: and every payee the rules mark recurring, so a payee's Not recurring set or taken back since the
    # stat ran (which read the payee as the owner had it then) reads the same on the page either way
    want = ({_series_key(x) for x in saved} | {k for k, rows in groups.items() if any(r["date"] > seen_to for r in rows)}
            | {("payee", p) for p in flagged | set(off)})
    end = conn.execute("SELECT MAX(txn_date) FROM v_txn").fetchone()[0]
    ends = account_ends({"transactions": [{"account_no": a, "date": d} for a, d in conn.execute(
                             "SELECT account_no, MAX(txn_date) FROM v_txn WHERE account_no IS NOT NULL GROUP BY account_no")],
                         "periods": [{"account_no": a, "period_end": d} for a, d in conn.execute(
                             "SELECT account_no, MAX(period_end) FROM v_period WHERE account_no IS NOT NULL GROUP BY account_no")]},
                        end) if end else {}
    fresh = []
    for kind, name in sorted(want & groups.keys()):
        fresh += detect_group(kind, name, groups[(kind, name)], kind == "payee" and name in flagged, tight, ends, end)
    fresh = name_series(fresh)
    out = {**data}
    for state in ("active", "paused", "stopped"):
        out[state] = ([x for x in data.get(state, []) if _series_key(x) not in want]
                      + [x for x in fresh if x["status"] == state])
    # G16: and what the rules' recurring flag says of those payees, as the stat would now: a
    # marked payee with a series leaves the list of marked payees with no regular spacing, one
    # with rows and none joins it
    redone = {name for kind, name in want if kind == "payee"}
    found = {x["payee"] for x in fresh if x["grouped_by"] == "payee"}
    live = {x["payee"] for x in fresh if x["grouped_by"] == "payee" and x["status"] == "active"}
    old = data.get("flag_mismatches") or {}
    out["flag_mismatches"] = {
        "flagged_not_detected": sorted({p for p in old.get("flagged_not_detected", ()) if p not in redone}
                                       | {p for p in redone & flagged if p not in found}),
        "detected_not_flagged": sorted({p for p in old.get("detected_not_flagged", ()) if p not in redone}
                                       | {p for p in live if p not in flagged})}
    return out


def refreshed(conn, results):
    """G16: the recurring result as the page reads it (`_live`), for Home's lines."""
    return Record(conn, _results_data(results)).data


def live(conn, results, today=None):
    """G16: the result as Recurring and Coming up read it on `today`: the owner's edits read
    over what was detected, a paused series that paid again active, and one whose end has
    passed stopped. Plan's suggestions read its active series (server/finance/suggestions.py)."""
    today = today or date.today()
    ref, _ = judged_on(conn, today)
    rec = Record(conn, _results_data(results))
    return rec.settle(rec.revive(rec.data, ref), ref, today)


def edited_out(conn, results):
    """G16: the keys of the payments the owner's edits say are no running recurring charge,
    which the scam check's new subscription leaves out (server/finance/scam_checks.py): every payment
    of a series marked not recurring, and a series' payments on or before the day the owner
    says it ended. A payee marked not recurring on its own line is series.payees_off_in."""
    if not conn.execute("SELECT 1 FROM series_override WHERE series_id IS NOT NULL").fetchone():
        return set()
    rec = Record(conn, _results_data(results))
    out = set()
    for state in (*STATES, NOT_RECURRING):
        for s in rec.data.get(state, []):
            e = s.get("edit") or {}
            if e.get("recurring") is False:
                out |= {k for p in rec.prepare(s, date.max)[0] for k in p.keys}
            elif e.get("ended_on"):
                end = date.fromisoformat(e["ended_on"])
                out |= {k for p in rec.prepare(s, date.max)[0] if p.day <= end for k in p.keys}
    return out


def _day_fits(cadence, day):
    """G16: whether a saved day can be a series' of this cadence: a weekday for one
    counted in days, a day of the month for the rest, none for twice a month (the
    detected cadence can move under an edit that set only the day)."""
    if day is None or cadence in series.NO_DAY:
        return False
    return 1 <= day <= (7 if cadence in series.DAY_CADENCES else 31)


def _with_edit(s, e):
    """G16: series `s` as the owner's edit `e` has it (server/finance/series.py): the usual amount,
    how often, the day and the day it ended in place of the detected ones, which stay under
    `detected`. A usual amount the owner set is one amount, so the band is around it."""
    out = {**s, "edit": series.public(e), "edit_row": e["row"],
           "detected": {"cadence": s.get("cadence") or "",
                        "amount": usual_cents(s)}}
    if e["cadence"]:
        out["cadence"], out["cadence_days"] = e["cadence"], NOMINAL[e["cadence"]]
    if e["amount"] is not None:
        # G20: in cents, the shape the stat writes; a result from before it loses its dollars here
        for name in ("median_amount", "avg_amount"):
            out.pop(name, None)
            out[name + "_minor"] = e["amount"]
        out["amount_varies"] = False
    if _day_fits(out.get("cadence") or "monthly", e["day"]):
        out["day"] = e["day"]
    if e["ended_on"]:
        out["ended_on"] = e["ended_on"]
    if (e["cadence"] or e["amount"] is not None) and usual_cents(out) and out.get("cadence") in NOMINAL:
        # the detector's own monthly figure (cadence.py), at the amount and cadence the owner set
        out.pop("monthly_cost", None)
        out["monthly_cost_minor"] = monthly_cost_minor(usual_cents(out), NOMINAL[out["cadence"]])
    return out


def _state_now(s, state):
    """G16: active, paused or stopped for a series whose cadence the owner set: the
    detector's own test (recurring_detector.state_after) on its silence before the last
    day its account's statements cover, at that cadence."""
    if not (s.get("edit") or {}).get("cadence") or not s.get("as_of") or not s.get("last_seen"):
        return state
    since = max(0, (date.fromisoformat(s["as_of"]) - date.fromisoformat(s["last_seen"])).days)
    return state_after(since, NOMINAL[s["cadence"]])


class Record:
    """What every series is read against, read from the record once per answer. `data` is
    the result as the page reads it: the stat's, with the groups the record moved under it
    detected again (`_live`) and the owner's edits read over it (`_edited`)."""

    def __init__(self, conn, data):
        self.charges, self.tight, self.ends = _charges(conn), tight_categories(conn), statement_ends(conn)
        self.edits = series.read(conn)
        self.off = series.payees_off(self.edits)
        data = _live(conn, data, self.tight, self.off)
        self.siblings, self.payees = _siblings(data), payee_rows(conn)
        # G16: the record's last day when the stat last ran: a charge after it is one it never saw
        self.stat_end = data.get("as_of")
        self.data = data = self._edited(data)
        self.siblings = _siblings(data)

    def prepare(self, s, start):
        return _prepare(s, self.charges, start, self.tight, self.siblings, self.stat_end)

    def own_keys(self, s):
        """G16: the keys of a series' own payments (its chain's charges, not the odd ones)."""
        return {k for p in self.prepare(s, date.max)[0] if p.regular for k in p.keys}

    def _edited(self, data):
        """G16: the result with each saved edit read over the series it is for
        (series.resolve): a series marked not recurring, or of a payee marked so with no
        word of its own, moves to `not_recurring`; one whose cadence the owner set is
        active, paused or stopped by that cadence. A payee marked not recurring leaves
        Marked recurring, no regular spacing."""
        if not self.edits or not data:
            return data
        every = [(state, x) for state in STATES for x in data.get(state, [])]
        groups = {}
        for _, x in every:
            groups.setdefault(_series_key(x), []).append(x)
        found = series.resolve(self.edits, groups, series_id, self.own_keys)
        out = {**data, **{state: [] for state in (*STATES, NOT_RECURRING)}}
        for state, x in every:
            e = found.get(id(x))
            y = _with_edit(x, e) if e else x
            said = e["recurring"] if e else None
            hidden = said is False or (said is None and x.get("grouped_by", "payee") == "payee"
                                       and (x.get("payee") or "") in self.off)
            out[NOT_RECURRING if hidden else _state_now(y, state)].append(y)
        mismatches = data.get("flag_mismatches") or {}
        out["flag_mismatches"] = {**mismatches, "flagged_not_detected": [
            p for p in mismatches.get("flagged_not_detected") or () if p not in self.off]}
        return out

    def settle(self, data, ref, today):
        """G16: the result with a series whose owner set the day it ended moved to the
        stopped ones once that day is behind its account's statements, or once nothing
        more is expected on or before it."""
        done = []
        for state in ("active", "paused"):
            for x in data.get(state, []):
                if not x.get("ended_on"):
                    continue
                end = date.fromisoformat(x["ended_on"])
                pays = self.prepare(x, ref + timedelta(days=1))[0]
                judged = judged_for(pays, self.ends, today, ref)
                sched = Schedule(x, pays, judged, judged, judged.replace(day=1))
                if sched.ok:
                    nxt = sched.next_expected()
                else:
                    seen = [p.day for p in pays if p.regular and p.day <= judged]
                    nxt = step(max(seen), x.get("cadence") or "monthly") if seen else None
                if end < judged or nxt is None or nxt > end:
                    done.append(x)
        if not done:
            return data
        # `ended`: the ones stopped by their end, whose payments this month before it still count in Coming up
        return {**data, **{state: [x for x in data.get(state, []) if not any(x is d for d in done)]
                           for state in ("active", "paused")},
                "stopped": [*data.get("stopped", []), *done], "ended": [series_id(d) for d in done]}

    def revive(self, data, ref):
        """G16: the result with a paused series the record has seen pay again since the
        stat last ran moved back among the active ones: a cleaner who missed a week
        and came the next is not paused until the stat runs again."""
        def resumed(x):
            pays = self.prepare(x, ref)[0]
            since = date.fromisoformat(x["last_seen"])
            return any(_resumed(x, p, pays) for p in pays if since < p.day <= ref)
        back = [x for x in data.get("paused", []) if x.get("last_seen") and resumed(x)]
        if not back:
            return data
        return {**data, "active": [*data.get("active", []), *back],
                "paused": [x for x in data.get("paused", []) if not any(x is b for b in back)]}


def _coming_up(data, rec, ref, today):
    """`ref` is the day the month is judged on; each series is judged on its
    own account's last statement day (`rec.ends`), never past `today`. Only an
    active series is expected: a paused one is listed on Recurring but owes
    nothing this month."""
    start = ref.replace(day=1)
    end = ref.replace(day=monthrange(ref.year, ref.month)[1])
    rows = []
    # G16: and a series the owner says ended this month, for what it was expected before its end
    ended = [x for x in data.get("stopped", []) if series_id(x) in data.get("ended", ()) and x["ended_on"] >= start.isoformat()]
    for s in [*data.get("active", []), *ended]:
        pays, low, high = rec.prepare(s, start)
        base = _row_base(s, pays, rec.payees)
        judged = judged_for(pays, rec.ends, today, ref)
        for slot in slots(s, pays, low, high, start, end, judged):
            rows.append({**base, **slot, "usual_low": low, "usual_high": high})
    rows.sort(key=lambda r: (r["expected"], r["payee"]))

    def owed(r):
        return r["paid_amount"] if r["paid_amount"] is not None else r["amount"]
    # an extra charge is listed, but it is not one of the month's bills
    bills = [r for r in rows if r["status"] != "extra"]
    spend = [r for r in bills if not r["investing"]]
    invest = [r for r in bills if r["investing"]]
    extra = [r for r in rows if r["status"] == "extra"]
    return {"month": start.strftime("%Y-%m"), "as_of": ref.isoformat(), "rows": rows,
            "totals": {"committed": sum(owed(r) for r in spend),
                       "paid": sum(r["paid_amount"] or 0 for r in spend),
                       "investing": sum(owed(r) for r in invest),
                       "investing_paid": sum(r["paid_amount"] or 0 for r in invest),
                       "extra": sum(r["paid_amount"] for r in extra if not r["investing"]),
                       "investing_extra": sum(r["paid_amount"] for r in extra if r["investing"])}}


def coming_up(conn, results, today=None):
    """This month's expected recurring payments, each paid, due, late, a
    different amount or extra, in the shape Home can show: {month, as_of, rows, totals}."""
    today = today or date.today()
    ref, _ = judged_on(conn, today)
    rec = Record(conn, _results_data(results))
    return _coming_up(rec.settle(rec.revive(rec.data, ref), ref, today), rec, ref, today)


def _active(data, rec, ref, today, state="active"):
    """The live series of one state, active or paused (G16), each with its last and next payment."""
    out = []
    for s in data.get(state, []):
        # which payments are the series' own does not hang on the day, so the account can be read first
        judged = judged_for(rec.prepare(s, ref + timedelta(days=1))[0], rec.ends, today, ref)
        pays, low, high = rec.prepare(s, judged + timedelta(days=1))
        base = _row_base(s, pays, rec.payees)
        # the record's latest regular payment, which a load since the stat last
        # ran can have moved on, else the stat's own last date
        sched = Schedule(s, pays, judged, judged, judged.replace(day=1))
        # G16: and a charge at another amount that answered an expected payment, which is
        # how a paused series that came back at a new price is paid
        seen = [p.day for p in pays if p.regular and p.day <= judged]
        seen += [p.day for p in sched.filled.values() if p.day <= judged]
        if s.get("last_seen"):
            seen.append(date.fromisoformat(s["last_seen"]))
        if not seen:
            continue
        last = max(seen)
        if sched.ok and sched.next_expected():
            # G16: late when an expected payment since the last one paid is past its tolerance
            late = any(x["status"] == "late" for x in sched.unpaid())
            nxt = sched.next_expected()
        else:
            late = (judged - last).days > BANDS.get(base["cadence"], BANDS["monthly"])[1]
            nxt = step(last, base["cadence"])
        out.append({**base, "usual_low": low, "usual_high": high,
                    "amount_varies": bool(s.get("amount_varies")),
                    "occurrences": s.get("occurrences") or 0,
                    "last_paid": last.isoformat(), "next_expected": nxt.isoformat(),
                    # G16: a paused series is not late: it has gone quiet, and owes nothing until it charges again
                    "late": late and state == "active", "state": state,
                    "yearly": base["amount"] * PER_YEAR.get(base["cadence"], 12),
                    "flagged": bool(s.get("flagged_recurring"))})
    out.sort(key=lambda r: (r["investing"], -r["yearly"], r["payee"]))
    return out


def _stopped(data, rec):
    out = []
    for s in data.get("stopped", []):
        base = _row_base(s, rec.prepare(s, date.max)[0], rec.payees)
        out.append({**base, "first_seen": s.get("first_seen"), "last_seen": s.get("last_seen"),
                    "ended_on": s.get("ended_on"), "occurrences": s.get("occurrences") or 0,
                    "yearly": base["amount"] * PER_YEAR.get(base["cadence"], 12)})
    out.sort(key=lambda r: (r["last_seen"] or ""), reverse=True)
    return out


def _irregular(conn, data):
    """The payees the rules mark recurring that the detector found no regular
    spacing for, each with its payments, so the owner can unflag or fix them."""
    out = []
    for payee in (data.get("flag_mismatches") or {}).get("flagged_not_detected", []):
        rows = conn.execute("""SELECT txn_date, amount_sgd_minor FROM v_txn
                               WHERE payee_name = ? AND flow_class = 'External' AND is_synthetic = 0
                                 AND amount_minor < 0 ORDER BY txn_date""", (payee,)).fetchall()
        dates = [r["txn_date"] for r in rows]
        # every External outflow of the payee is counted, Transfer-bucket rows included
        out.append({"payee": payee, "link": payee_link(payee), "payments": len(rows),
                    "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
                    "dates": dates, "total": -sum(r["amount_sgd_minor"] or 0 for r in rows)})
    out.sort(key=lambda r: (-r["payments"], r["payee"]))
    return out


def _not_recurring(data, off):
    """G16: what the owner said is not recurring, so each can be taken back: the payees
    marked so on their own line, then the series marked so on theirs (a series hidden only
    by its payee's line goes with that line)."""
    out = [{"id": None, "name": p, "payee": p, "cadence": "", "category": "", "amount": None, "last_seen": None}
           for p in sorted(off)]
    out += sorted(({"id": series_id(s), "name": s.get("name") or s.get("payee") or "", "payee": s.get("payee") or "",
                    "cadence": s.get("cadence") or "", "category": s.get("category") or "",
                    "amount": usual_cents(s), "last_seen": s.get("last_seen")}
                   for s in data.get(NOT_RECURRING, []) if (s.get("edit") or {}).get("recurring") is False),
                  key=lambda r: (r["name"], r["id"]))
    return out


def page(conn, results, today=None):
    """GET /api/recurring: {ready, as_of, coming_up, active, paused, stopped, irregular, not_recurring, totals}."""
    today = today or date.today()
    ref, last = judged_on(conn, today)
    rec = Record(conn, _results_data(results))
    data = rec.settle(rec.revive(rec.data, ref), ref, today)
    active = _active(data, rec, ref, today)
    spend = [r for r in active if not r["investing"]]
    return {"ready": bool(data), "as_of": last, "generated_at": ((results or {}).get(STAT) or {}).get("generated_at"),
            "coming_up": _coming_up(data, rec, ref, today),
            "active": active, "paused": _active(data, rec, ref, today, "paused"),
            "stopped": _stopped(data, rec), "irregular": _irregular(conn, data),
            "not_recurring": _not_recurring(data, rec.off),
            # G16: a paused series is listed but in no total
            "totals": {"yearly_spending": sum(r["yearly"] for r in spend),
                       "yearly_investing": sum(r["yearly"] for r in active if r["investing"]),
                       "active": len(active), "paused": len(data.get("paused", [])),
                       "stopped": len(data.get("stopped", []))}}


# ---------------------------------------------------------------- G16: one series' own view

def _find(data, sid):
    """(the list a series is in, the series) for the series with id `sid`, else (None, None)."""
    for state in (*STATES, NOT_RECURRING):
        for s in data.get(state, []):
            if series_id(s) == sid:
                return state, s
    return None, None


def _grid_day(g):
    """The day a schedule falls on: the weekday (1 Monday) for one counted in days, the day
    of the month for one counted in months, None for twice a month."""
    if g.by_days:
        return g.day(1).isoweekday()
    return None if g.anchors else g.anchor


def _schedule_rows(sched, live, low, high):
    """Every expected payment of a series' schedule, newest first: paid or a different
    amount with its payment, skipped (a later one came), and for an active series due or
    late; then its extra charges, each dated by its day."""
    out = []
    for x in sched.slots:
        p = x["payment"]
        if p is not None:
            row = _paid_row(p, low, high)
        elif x["skipped"]:
            row = {"status": "skipped", "paid_on": None, "paid_amount": None, "link": None}
        elif live:
            row = {"status": x["status"], "paid_on": None, "paid_amount": None, "link": None}
        else:
            continue
        out.append({"expected": x["expected"].isoformat(), **row})
    out += [{"expected": p.day.isoformat(), **_paid_row(p, low, high), "status": "extra"} for p in sched.extras]
    return sorted(out, key=lambda r: r["expected"], reverse=True)


def series_view(conn, results, sid, today=None):
    """GET /api/recurring/series?id=: one series as its own view on Recurring shows it
    (G16): what it is now (state, cadence, usual amount and range, day, next and last
    payment, late), what was detected and what the owner set, every expected payment of
    its schedule with its status, and every payment of it. None when no series has that
    id now."""
    today = today or date.today()
    ref, _ = judged_on(conn, today)
    rec = Record(conn, _results_data(results))
    data = rec.settle(rec.revive(rec.data, ref), ref, today)
    state, s = _find(data, sid)
    if s is None:
        return None
    judged = judged_for(rec.prepare(s, ref + timedelta(days=1))[0], rec.ends, today, ref)
    pays, low, high = rec.prepare(s, judged + timedelta(days=1))
    base = _row_base(s, pays, rec.payees)
    # only an active series expects anything: a paused one owes nothing until it charges again (as on
    # Recurring, where it shows its last payment, not a next), so its schedule, like a stopped one's,
    # lists every payment to the day it is judged on and no unpaid one (_schedule_rows)
    live = state == "active"
    regular = [p for p in pays if p.regular and p.day <= judged]
    last = max([p.day for p in regular] + ([date.fromisoformat(s["last_seen"])] if s.get("last_seen") else []),
               default=None)
    sched = Schedule(s, pays, judged, judged, date.min)
    nxt = None
    if live and sched.ok:
        nxt = sched.next_expected()
    elif live and last:
        nxt = step(last, base["cadence"] or "monthly")
    late = live and sched.ok and any(x["status"] == "late" for x in sched.unpaid())
    recent = [p.day for p in regular][-RECENT:]
    detected = s.get("detected") or {"cadence": s.get("cadence") or "", "amount": base["amount"]}
    seen = detected["cadence"] or "monthly"
    return {**base, "state": state, "late": late, "usual_low": low, "usual_high": high,
            "amount_varies": bool(s.get("amount_varies")),
            "day": _grid_day(sched.grid) if sched.ok else None,
            "first_seen": s.get("first_seen"), "last_paid": last.isoformat() if last else None,
            "next_expected": nxt.isoformat() if nxt else None, "ended_on": s.get("ended_on"),
            "occurrences": s.get("occurrences") or 0,
            "yearly": base["amount"] * PER_YEAR.get(base["cadence"], 12),
            "detected": {"cadence": detected["cadence"], "amount": detected["amount"],
                         "day": _grid_day(Grid(seen, recent)) if recent and seen in BANDS else None},
            "edit": series.public(s.get("edit")),
            "schedule": _schedule_rows(sched, live, low, high) if sched.ok else [],
            "payments": [{"date": p.day.isoformat(), "amount": sum(p.amounts), "regular": p.regular,
                          "link": paid_link(p.keys)} for p in sorted(pays, key=lambda p: p.day, reverse=True)]}


def save_series(conn, results, body, today=None):
    """POST /api/recurring/series: the owner's edit of one series ({id, recurring, amount
    (cents), cadence, day, ended_on}): each one sent is set, null for as detected, and each
    one not sent keeps what was saved; with none set the edit goes. Saved in the record with
    its own payments' keys (server/finance/series.py), answered with the series' view."""
    sid = body.get("id")
    if not isinstance(sid, str) or not sid:
        raise series.SeriesRefused("Name the series by its id")
    rec = Record(conn, _results_data(results))
    _, s = _find(rec.data, sid)
    if s is None:
        raise series.SeriesRefused("No series has that id now: open it again from Recurring")
    detected = (s.get("detected") or {}).get("cadence") or s.get("cadence") or "monthly"
    kept = series.public(s.get("edit"))
    # a saved day the cadence no longer takes (detection moved it under an edit that set only the day) is
    # not read (_with_edit), so it is not kept either: only a day sent now is held to the cadence
    cadence = (body["cadence"] if "cadence" in body else kept["cadence"]) or detected
    if "day" not in body and not _day_fits(cadence, kept["day"]):
        kept["day"] = None
    edit = series.clean({**kept, **body}, CADENCES, detected)
    series.save(conn, sid, _series_key(s), edit, rec.own_keys(s), replace=s.get("edit_row"))
    return series_view(conn, results, sid, today)


def save_payee(conn, body):
    """POST /api/recurring/payee: {payee, recurring: false} says a payee the rules mark
    recurring is not (null takes that back), saved in the record (server/finance/series.py)."""
    series.set_payee(conn, body.get("payee"), body.get("recurring"))
    return {"payee": body.get("payee"), "recurring": body.get("recurring")}
