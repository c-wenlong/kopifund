#!/usr/bin/env python3
"""The conventions every stat obeys, in one place.

Get any of these wrong and a chart lies, so they live here rather than in each
module:

  * only `flow_class = External` rows are spend or income; the internal legs are
    the same money moving between the owner's own accounts and wallets;
  * Transfer-bucket categories (Investments, Wallet top-up, Person to person,
    Between own accounts) are neither spend nor income. A view that filters
    External and forgets the bucket overstates spend by every investing
    transfer and wallet top-up in the record, tens of thousands over a long one;
  * spend is the **signed** sum, so a refund landing in a spend category
    subtracts. Summing `abs(amount)` inflates a phase's monthly average by
    every refund twice over;
  * every amount is `amount_sgd_minor`, integer SGD cents, never the row
    currency (G20: nothing holds money as float dollars);
  * synthetic rows (the YouTrip balance-jump fillers) are excluded from spend
    and income and are listed by the data-quality stat instead;
  * a month whose statements do not cover every bank account that was open
    is flagged partial, so an average never quietly includes a half month;
    wallets never count, they only hold money the bank statements already
    show leaving;
  * a share of spending states its base. `spend_base` hands back the number
    and the words for it, because 23 per cent of the covered months and 23
    per cent of every month can be thousands apart and read the same;
  * an account is named through `account_labels`, never by its record name
    or number: a statement's account number is never printed in full (G13).
"""
import re
from datetime import date, datetime, timedelta, timezone

# G13: dates in findings read as the app shows them ("3 Oct 2026", "Oct 2026")
from finance.dates import date_label, day_label, month_label, months_label, range_label  # noqa: F401
# G19: the [analytics] reader is finance's, which the app shares (finance never
# imports analytics); the stats read it through these names as before
from finance.settings import setting, settings, text_setting  # noqa: F401
# G19: the row arithmetic server/finance/cadence.py shares (finance never imports analytics)
from finance.figures import by, days_between  # noqa: F401
# G20: the cents arithmetic, shared with server/finance/cadence.py the same way
from finance.figures import div_cents, mean_cents, median_cents, sgd_minor, sum_cents  # noqa: F401
from finance.money import label, stat_cents  # noqa: F401

SGT = timezone(timedelta(hours=8))

TRANSFER_BUCKET = "Transfer"
SPEND_BUCKET = "Spend"
INCOME_BUCKET = "Income"

SPEND_FILTER = "flow_class = External and bucket = Spend and not synthetic"
INCOME_FILTER = "flow_class = External and bucket = Income and not synthetic"


def now():
    return datetime.now(SGT).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- settings
#
# The [analytics] table of config.toml (G10): `settings`, `setting` and
# `text_setting`, imported at the top from server/finance/settings.py.

# The phase every month falls in when the user has named none: the whole record
# as one stretch, so a phase average is simply the record's average.
WHOLE_RECORD = {"name": "Whole record", "from": "0000-01", "to": None}

_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def configured_phases():
    """[[analytics.phases]]: the stretches of time the user has named, oldest
    first, such as a stay abroad and then the months at home. Each needs a name
    and a `from` month; `to` may be left out for a phase that runs on. A
    malformed entry is skipped rather than allowed to break a stat."""
    raw = setting("phases", [])
    out = []
    for p in raw if isinstance(raw, list) else ():
        if not isinstance(p, dict):
            continue
        name, start, end = p.get("name"), str(p.get("from") or ""), p.get("to")
        end = str(end) if end else None
        if (not isinstance(name, str) or not name.strip() or not _MONTH.match(start)
                or (end is not None and (not _MONTH.match(end) or end < start))):
            continue
        out.append({"name": name.strip(), "from": start, "to": end})
    # two names that make the same data key (`Home (2024)`, `Home-2024`) would
    # overwrite each other's figures: the later one is skipped like any other
    # malformed entry
    seen, kept = set(), []
    for p in sorted(out, key=lambda p: p["from"]):
        if phase_key(p) not in seen:
            seen.add(phase_key(p))
            kept.append(p)
    return tuple(kept)


def phases():
    """The configured phases, or the whole record as a single phase."""
    return configured_phases() or (WHOLE_RECORD,)


def latest_phase():
    return phases()[-1]


def phase_key(phase):
    """A phase's name as a data key: `Home` gives `home`."""
    return re.sub(r"[^a-z0-9]+", "_", phase["name"].lower()).strip("_") or "phase"


def __getattr__(name):
    # `PHASES` was a constant before G10. Reading it still works and gives the
    # phases in force now.
    if name == "PHASES":
        return phases()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ---------------------------------------------------------------- row filters

def category_names(snapshot, *names):
    """The names the rows carry now for the shipped categories `names`. A stat
    that looks for Eating out or Salary by name still finds it after the user
    renamed it on the Categories page (or merged it into another): the
    record's old names come in the snapshot's `category_aliases`, old name to
    current name. A set, so a merge never counts a row twice."""
    aliases = snapshot.get("category_aliases") or {}
    return {aliases.get(n, n) for n in names}


def external(snapshot):
    return [r for r in snapshot["transactions"] if r["flow_class"] == "External"]


def spend_rows(snapshot):
    """Every row that counts as spending: External, a Spend-bucket category,
    not a synthetic balance-jump filler."""
    return [r for r in snapshot["transactions"]
            if r["flow_class"] == "External" and r["bucket"] == SPEND_BUCKET
            and not r["synthetic"]]


def income_rows(snapshot):
    return [r for r in snapshot["transactions"]
            if r["flow_class"] == "External" and r["bucket"] == INCOME_BUCKET
            and not r["synthetic"]]


def transfer_rows(snapshot):
    return [r for r in snapshot["transactions"]
            if r["flow_class"] == "External" and r["bucket"] == TRANSFER_BUCKET
            and not r["synthetic"]]


def investing_rows(snapshot):
    return [r for r in transfer_rows(snapshot) if r["need_class"] == "Investment"]


# ---------------------------------------------------------------- months

def month_range(snapshot):
    """Every month from the first transaction to the last, with no gaps: the
    months money moved in, which every flow stat and every phase average
    runs over. balance_month_range is the one that also reaches the balances."""
    days = [r["date"] for r in snapshot["transactions"] if r["date"]]
    if not days:
        return []
    return months_between(min(days)[:7], max(days)[:7])


def balance_month_range(snapshot):
    """Every month from the first transaction or balance block to the last,
    with no gaps: the range a stat that reads balances walks.

    A wallet export runs to the month it was generated whether or not anything
    moved, and an account can have statements later than the last transaction
    anywhere else. month_range ends at the last transaction, so savings-runway
    walking it never carried a balance into those months and could miss an
    account altogether. It is a range of its own rather than month_range
    itself because a month with balances and no transactions holds no flow:
    counted in a phase average it would read as a month of zero spending. A
    block with no balance (a card statement, a YouTrip month that printed
    nothing) holds no figure, so it stretches nothing."""
    months = set(month_range(snapshot))
    months |= {period_month(b) for b in snapshot.get("periods", ())
               if b.get("has_balances") and not is_card_block(b)}
    months.discard("")
    if not months:
        return []
    return months_between(min(months), max(months))


def months_between(first, last):
    out, y, m = [], int(first[:4]), int(first[5:7])
    while f"{y:04d}-{m:02d}" <= last:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def add_months(month, n):
    y, m = int(month[:4]), int(month[5:7]) + n
    return f"{y + (m - 1) // 12:04d}-{(m - 1) % 12 + 1:02d}"


def month_count(first, last):
    return len(months_between(first, last))


def in_phase(month, phase):
    return phase["from"] <= month and (phase["to"] is None or month <= phase["to"])


def phase_of(month):
    for p in phases():
        if in_phase(month, p):
            return p["name"]
    first = phases()[0]["from"]
    return f"Before {month_label(first)}"


def phase_months(snapshot, phase):
    return [m for m in month_range(snapshot) if in_phase(m, phase)]


def last_month(snapshot):
    """The last month with any transaction."""
    months = month_range(snapshot)
    return months[-1] if months else None


def last_date(snapshot):
    days = [r["date"] for r in snapshot["transactions"] if r["date"]]
    return max(days) if days else None


def trailing(snapshot, n, end=None):
    """The n months ending at `end` (the snapshot's last month by default)."""
    end = end or last_month(snapshot)
    if not end:
        return []
    return [m for m in month_range(snapshot) if add_months(end, -(n - 1)) <= m <= end]


# ---------------------------------------------------------------- coverage

def period_month(block):
    """The yyyy-mm a Period balances row belongs to, from its name."""
    parts = block["name"].split(" · ")
    return parts[2] if len(parts) >= 3 else (block["period_end"] or "")[:7]


def is_card_block(block):
    return block["name"].endswith(" · card")


def wallet_accounts(snapshot):
    """Account numbers whose Accounts row says subclass Wallet. They are funded
    from the bank accounts, so their statements add detail, never coverage."""
    return {a.get("account_no") for a in snapshot.get("accounts", ())
            if a.get("subclass") == "Wallet" and a.get("account_no")}


class Labels:
    """The name each account is shown by, with no full number in it.

    `of(account_no, name)` answers for an account number or a record name; an
    account the snapshot does not list is named from what it has, its number
    cut to the last four digits. `period(name)` is a period block's name
    ("<account_no> · <ccy> · <yyyy-mm>") with the account's name in front."""

    def __init__(self, accounts):
        from finance import account_names
        self._an = account_names
        accounts = list(accounts or ())
        shown = [a.get("label") for a in accounts]
        if not all(shown):
            shown = [d["label"] for d in account_names.describe(accounts)]
        self.by_no, self.by_name = {}, {}
        for a, label in zip(accounts, shown):
            if a.get("account_no"):
                self.by_no[a["account_no"]] = label
            if a.get("name"):
                self.by_name.setdefault(a["name"], label)

    def of(self, account_no="", name=""):
        if account_no and account_no in self.by_no:
            return self.by_no[account_no]
        if name and name in self.by_name:
            return self.by_name[name]
        return self._an.label(name, account_no)

    def period(self, name):
        parts = str(name or "").split(" · ")
        return " · ".join([self.of(parts[0])] + parts[1:]) if parts[0] else str(name or "")


def account_labels(snapshot):
    """`Labels` for the snapshot's accounts. server/finance/snapshot.py stamps each
    account's `label` from the config's statement folders; a snapshot without
    them is named from the record alone."""
    return Labels(snapshot.get("accounts"))


def coverage(snapshot):
    """{month: {accounts, expected, missing, card_only, before_phases, partial}}.

    A bank account is expected in every month from its first deposit block
    onward, so a month where one of them printed no statement is a hole rather
    than a zero. Wallets (Accounts subclass Wallet: PayLah, YouTrip, Revolut,
    Wise) are never expected: money reaches them only from the bank accounts,
    so the bank statements already carry every dollar that left, and a wallet
    that printed nothing in a month is a wallet that was not used. Months before
    the first configured phase are partial too: a user starts their first phase
    where the deposit-account statements start, and everything earlier is card
    and wallet rows with no income beside them. With no phase configured no
    month is before one.
    """
    deposit = [b for b in snapshot["periods"] if not is_card_block(b)]
    wallets = wallet_accounts(snapshot)
    first, present = {}, {}
    for b in deposit:
        m = period_month(b)
        present.setdefault(m, set()).add(b["account_no"])
        if b["account_no"] in wallets:
            continue
        first[b["account_no"]] = min(first.get(b["account_no"], m), m)
    any_block = {}
    for b in snapshot["periods"]:
        any_block.setdefault(period_month(b), set()).add(b["account_no"])
    out = {}
    # every month a balance reaches, so savings-runway can ask about each of
    # its months; the flow stats only ever ask about month_range's
    for m in balance_month_range(snapshot):
        have = present.get(m, set())
        expect = {a for a, start in first.items() if start <= m}
        card_only = bool(any_block.get(m)) and not have
        before = m < phases()[0]["from"]
        out[m] = {"accounts": sorted(have), "expected": sorted(expect),
                  "missing": sorted(expect - have), "card_only": card_only,
                  "before_phases": before,
                  "partial": before or card_only or bool(expect - have) or not have}
    return out


def full_months(snapshot):
    """The months an average may safely be taken over."""
    cov = coverage(snapshot)
    return [m for m in month_range(snapshot) if not cov[m]["partial"]]


# ---------------------------------------------------------------- maths

def share(part, whole):
    return round(part / whole, 4) if whole else 0.0


def clamp_share(x):
    """A share forced back inside 0 to 1, for a chart series only."""
    return None if x is None else round(min(1.0, max(0.0, x)), 4)


def share_note_minor(part, whole, what="A share"):
    """The caveat a share outside 0 to 1 needs, or None; `part` and `whole`
    are cents, printed by `label`.

    Spend is a signed sum, so a refund inside the base can leave the base
    smaller than the part measured against it, and a category whose refunds
    beat its charges is negative. Either way the percentage is arithmetic
    rather than an error, but a reader who is not told reads it as a mistake,
    so the sentence goes in the caveats and the chart draws the clamped value
    beside the raw one in the table.
    """
    if not whole:
        return None
    ratio = part / whole
    if 0.0 <= ratio <= 1.0:
        return None
    return (f"{what} works out at {ratio * 100:,.0f} per cent, outside 0 to 100: "
            f"{label(part)} against a base of {label(whole)}. A refund inside the "
            f"base does that. The chart clamps the value; the table keeps it raw.")


def fmt(x):
    """A number that is not money (months of cash, a percentage) with a
    thousands separator and two places; money in a finding is `label`."""
    return f"{x:,.2f}"


# ---------------------------------------------------------------- cents (G20)
#
# A stat that works in cents adds, averages and takes medians of integer cents
# with `sum_cents`, `mean_cents`, `median_cents` and `div_cents`, imported at
# the top from server/finance/figures.py (the recurring engine shares them), with one
# rounding rule: a quotient that is not a whole cent rounds half up, away from
# zero, exactly as server/finance/money.py's `to_cents` rounds dollars. A row's SGD
# cents are `sgd_minor`.


def spend_minor(rows):
    """Positive money out in cents, signed: a refund inside a spend category subtracts."""
    return -sum(sgd_minor(r) for r in rows)


def income_minor(rows):
    """Money in, in cents, signed."""
    return sum(sgd_minor(r) for r in rows)


# ---------------------------------------------------------------- denominators

def spend_base_minor(snapshot, rows=None, months=None):
    """(total cents, label): the denominator under any share of spending, named.

    A share means nothing until its base is stated. Twenty-three per cent of
    the covered months' spending and twenty-three per cent of every month's
    are different claims, and the two bases can differ by thousands, so a
    stat that quietly picks one is not comparable with the stat beside it.
    Every share of spending takes both the number and the label from here and
    puts the label in the sentence.

    `rows` defaults to every spend row, `months` to the covered months. Pass
    `months=common.month_range(snapshot)` for the all-month base. The label
    prints the total with `label`: "of covered-month spending S$30,000.00".
    """
    rows = spend_rows(snapshot) if rows is None else rows
    covered, every = full_months(snapshot), month_range(snapshot)
    months = list(covered if months is None else months)
    kind = ("covered-month" if set(months) == set(covered)
            else "all-month" if set(months) == set(every)
            else f"{len(months)}-month")
    keep = set(months)
    total = spend_minor([r for r in rows if r["month"] in keep])
    return total, f"of {kind} spending {label(total)}"


# ---------------------------------------------------------------- result

def source(database, rows_used, filter_):
    return {"database": database, "rows_used": rows_used, "filter": filter_}


def result(module, snapshot, sources, params, data, findings, caveats=(),
           benchmarks=None, title=None, links=None):
    """The result envelope SPEC.md documents, in its documented key order.

    `module` is the stat module itself (`sys.modules[__name__]` inside `run`, or
    simply the imported module in a test). `chart` carries its CHART hint
    verbatim so the renderer never has to import the stat modules. `title`
    replaces the module's TITLE when the title depends on a setting.

    `links` (G13) says, for each plotted series that adds up rows, the
    Transactions filters that list exactly those rows: `{series: {filter:
    value}}`, where `{x}` in a value stands for the bar's x field and `{name}`
    for another field of the same row. A series
    that is an average, a ratio or a balance has no entry, so its bar does not
    link. The chart hint carries it, so the page never has to guess.
    """
    chart = dict(module.CHART)
    if links:
        chart["links"] = {s: dict(f) for s, f in links.items()}
    out = {"id": module.ID, "title": title or module.TITLE, "generated_at": now(),
           "snapshot": snapshot.get("meta", {}).get("file", ""),
           "chart": chart, "sources": list(sources), "params": dict(params)}
    if benchmarks is not None:
        out["benchmarks"] = list(benchmarks)
    out["data"] = data
    out["findings"] = list(findings)
    out["caveats"] = list(caveats)
    return out


def no_benchmarks_caveat(benchmarks):
    """The one caveat every benchmark-using stat adds when the file is absent."""
    if benchmarks:
        return []
    return ["server/analytics/benchmarks.json was not found, so the Singapore reference "
            "numbers are missing and only the owner's own figures are shown."]


def weekday(day):
    return date.fromisoformat(day).weekday()


def is_weekend(day):
    return weekday(day) >= 5


def week_of(day):
    """The ISO week's Monday, which is how the weekly series is keyed."""
    d = date.fromisoformat(day)
    return (d - timedelta(days=d.weekday())).isoformat()


def dated(rows):
    """The rows that carry a date. A row without one cannot be put in a week or
    a span, and a snapshot built from a bad export has them."""
    return [r for r in rows if r.get("date")]


def date_span(rows):
    """(first date, last date) over the rows that have one, or (None, None)."""
    days = [r["date"] for r in rows if r.get("date")]
    return (min(days), max(days)) if days else (None, None)


def weeks_between(first, last):
    """Every ISO week's Monday from one day to another, with no gaps.

    A weekly series drawn only from the weeks that had a row draws a flat line
    across a fortnight away and calls it normal.
    """
    day, end = date.fromisoformat(week_of(first)), date.fromisoformat(week_of(last))
    out = []
    while day <= end:
        out.append(day.isoformat())
        day += timedelta(days=7)
    return out


def days_in(start, end):
    """Every calendar day from start to end inclusive, as ISO strings.

    Ranges that overlap are what a set of these is for: two trips inside a
    third are not three separate stretches of time away.
    """
    day, last = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    while day <= last:
        out.append(day.isoformat())
        day += timedelta(days=1)
    return out
