#!/usr/bin/env python3
"""What is held at Tiger Brokers, what was put in, and the two added together.

The brokerage is the one asset the bank statements cannot see. Money leaves the
bank as a transfer to Tiger Brokers and stops being cash; nothing that comes
back says what it bought. So the figures here come from the annual statements
themselves, parsed out of `statements/tiger` in the data folder into `snapshot["portfolio"]`,
and the stat's job is to line them up against the rows the bank did print.

Three things follow from the statements being annual.

  * The value is the value **on the statement's last day**, not today. A
    statement exported in August is an August valuation for as long as it is
    the newest one, so the as-of date goes in the headline and a statement more
    than four months old asks to be replaced.
  * Everything is in the account's base currency, US dollars. Each statement
    prints its own period-end exchange rates, so a period is converted at its
    own rate rather than at today's: `sgd_per_base` is one over the SGD rate
    the statement printed. A holding in a third currency goes through the same
    table.
  * Contributions are cumulative across statements, because the gain is the
    value now against everything ever put in. Nothing has been withdrawn, so
    contributed is the running total of the deposits.

A year can be exported twice: a year-to-date file kept beside the full year
that replaced it (the runbook says to keep both), or still held by the record
from an earlier load. A statement whose whole period another statement of the
same account covers is left out, and a finding names it, so the latest
statement is the one that ends last. Two that only partly overlap both stay,
and a deposit dated inside an earlier statement's period is that statement's,
so each deposit is counted once however many statements print it.

G20: every amount is integer cents of its own currency under a `_minor` name,
with the currency beside it (`base_currency` for the `_base_minor` figures,
`currency` on a holding or a deposit), and the `_sgd_minor` figures are SGD
cents. A statement's rate to SGD is scaled by 10^9 (`fx_to_sgd_e9`), and an
amount goes to SGD cents by `server/finance/money.py`'s `fx_minor`, the record's own
rule for a foreign row (v_txn's `amount_sgd_minor`).

Deposits are matched back to the bank: a deposit is `matched` when a
Transactions row to the Tiger Brokers payee, within three days of it and for
the same amount in SGD, to the cent, exists. A deposit outside the months the Transactions cover, older than the
first or newer than the last, cannot be matched either way and is `null` rather
than false, because the bank rows for it were never loaded.

data:
  periods    one per statement read, oldest first:
             [{period_from, period_to, base_currency, deposits_sgd_minor,
               deposits_base_minor, contributed_sgd_minor, value_base_minor,
               sgd_per_base, fx_to_sgd_e9, value_sgd_minor, cash_base_minor,
               holdings_base_minor, dividends_base_minor, fees_base_minor,
               withholding_base_minor, gain_sgd_minor}]; deposits_sgd_minor
             is what the statement adds to contributed, its deposits less any
             an earlier statement's period already counted
  holdings   the latest statement's positions: symbol, name, quantity,
             currency, cost_price and close_price (a price per share, as the
             statement prints it), value_minor and unrealized_pl_minor in the
             holding's currency, and value_sgd_minor
  deposits   [{date, amount_minor, currency, matched}]
  summary    {value_sgd_minor, value_base_minor, base_currency, as_of,
              contributed_sgd_minor, gain_sgd_minor, gain_pct,
              dividends_base_latest_minor, statements, cash_sgd_minor,
              cash_as_of, net_worth_sgd_minor, unmatched_deposits}
"""
import sys

from finance.money import E9, fx_minor, label, rate_e9, stat_cents, to_cents

from . import common as c, savings_runway

ID = "portfolio"
TITLE = "Investments at Tiger Brokers"
CHART = {"type": "line", "x": "period_to",
         "series": ["contributed_sgd_minor", "value_sgd_minor"],
         "source": "periods", "unit": "sgd_minor"}

# The payee the rules put on the transfers out of the bank. It is a company
# name rather than a person's, so it stays here rather than in config.toml.
PAYEE = "Tiger Brokers"

# How far a bank row may sit from the deposit date and still be the same money.
# The transfer is pulled by FAST Collection and shows up at the broker the same
# day or the next working day, so three days covers a weekend.
MATCH_DAYS = 3

# How old the newest statement may be before the stat asks for a new export.
STALE_MONTHS = 4

# A position's money, in its own currency. Its prices per share stay as the
# statement prints them: a price, not an amount, and often to four places.
HOLDING_MONEY = ("value", "unrealized_pl")


def plural(n, word):
    """`1 statement`, `5 statements`: a count with its noun."""
    return f"{n} {word}" + ("" if n == 1 else "s")


def cents(amount):
    """A statement's figure (the parser's dollars) as integer cents of its own
    currency; 0 when the statement printed none."""
    return 0 if amount is None else to_cents(amount)


def base_of(st):
    return st.get("base_currency") or "USD"


def rates(st):
    """{currency: SGD for one unit of it, scaled by 10^9} from one statement's
    own period-end table, or {} when it prints no SGD rate.

    The table gives one unit of each currency in the base currency, so a
    currency goes to SGD through the base: its rate over the SGD rate. The base
    is 1 in the table, and US dollars count as the base when the table leaves
    them out."""
    fx = st.get("fx_to_base") or {}
    sgd = fx.get("SGD")
    if not sgd:
        return {}
    out = {k: rate_e9(v / sgd) for k, v in fx.items() if v}
    for k in (base_of(st), "USD"):
        out.setdefault(k, rate_e9(1 / sgd))
    out["SGD"] = E9
    return out


def to_sgd(minor, currency, fx):
    """`minor` cents of `currency` as SGD cents at one statement's own rates
    (`rates`), by the record's rule (money.fx_minor). A currency the table does
    not print keeps its face value, which is what a missing FX rate means
    everywhere else in this repo."""
    if minor is None:
        return 0
    e9 = fx.get(currency)
    return minor if currency == "SGD" or not e9 else fx_minor(minor, e9)


def covers(a, b):
    """Whether statement `a`'s period holds the whole of `b`'s, for the same
    account. Two accounts' statements never stand in for each other."""
    return (a.get("account") == b.get("account")
            and a["period"]["from"] <= b["period"]["from"]
            and b["period"]["to"] <= a["period"]["to"])


def read(snapshot):
    """(statements, left_out): the parsed statements to count, oldest first,
    and [(statement, the one that covers it)] for each left out.

    A statement is left out when another one covers its whole period: a
    year-to-date export beside the full year, or the same period exported
    twice, of which the last by file name stays. The ones kept are in order of
    their start and then their end, so the last is the one that ends last.
    Empty when the key is absent, which is what every snapshot written before
    the brokerage was loaded looks like."""
    block = snapshot.get("portfolio") or {}
    rows = sorted(block.get("statements") or [],
                  key=lambda st: (st["period"]["from"], st["period"]["to"], st.get("file") or ""))

    def replaced(i, st):
        return any(covers(o, st) and (o["period"] != st["period"] or j > i)
                   for j, o in enumerate(rows) if j != i)

    kept = [st for i, st in enumerate(rows) if not replaced(i, st)]
    left_out = [(st, next(k for k in kept if covers(k, st)))
                for i, st in enumerate(rows) if replaced(i, st)]
    return kept, left_out


def statements(snapshot):
    """The parsed statements to count, oldest first (`read`)."""
    return read(snapshot)[0]


def own_deposits(rows):
    """[(statement, the deposits it adds)] down the list. A deposit dated
    inside an earlier statement's period, for the same account, is printed by
    both and was counted with the earlier one. Going by the period rather than
    by the date and amount keeps two real deposits of the same sum on one day."""
    out = []
    for i, st in enumerate(rows):
        earlier = [o["period"] for o in rows[:i] if o.get("account") == st.get("account")]
        out.append((st, [d for d in st.get("deposits") or ()
                         if not any(p["from"] <= d["date"] <= p["to"] for p in earlier)]))
    return out


def periods(rows):
    """One row per statement, with contributions accumulating down the list."""
    out, contributed = [], 0
    for st, own in own_deposits(rows):
        fx, base = rates(st), base_of(st)
        sgd = (st.get("fx_to_base") or {}).get("SGD")
        end = (st.get("overview") or {}).get("end", {})
        deposits_sgd = c.sum_cents(to_sgd(cents(d["amount"]), d["currency"], fx)
                                   for d in own)
        contributed += deposits_sgd
        value_base = cents(end.get("total"))
        value_sgd = to_sgd(value_base, base, fx)
        out.append({
            "period_from": st["period"]["from"], "period_to": st["period"]["to"],
            "base_currency": base,
            "deposits_sgd_minor": deposits_sgd,
            "deposits_base_minor": cents(st.get("deposits_base")),
            "contributed_sgd_minor": contributed,
            "value_base_minor": value_base,
            # the rate to six places, for reading; the conversion uses the e9 one
            "sgd_per_base": round(1 / sgd, 6) if sgd else None,
            "fx_to_sgd_e9": fx.get(base),
            "value_sgd_minor": value_sgd,
            "cash_base_minor": cents(end.get("cash")),
            "holdings_base_minor": cents(st.get("holdings_value_base")),
            "dividends_base_minor": cents(st.get("dividends_base")),
            "fees_base_minor": cents(st.get("fees_base")),
            "withholding_base_minor": cents(st.get("withholding_tax_base")),
            "gain_sgd_minor": value_sgd - contributed,
        })
    return out


def holdings(rows):
    """The latest statement's positions, each with its SGD value beside it."""
    if not rows:
        return []
    latest = rows[-1]
    fx = rates(latest)
    out = []
    for h in latest.get("holdings") or ():
        row = {k: v for k, v in h.items() if k not in HOLDING_MONEY}
        for k in HOLDING_MONEY:
            if k in h:
                row[f"{k}_minor"] = None if h[k] is None else to_cents(h[k])
        row["value_sgd_minor"] = to_sgd(row.get("value_minor"), h.get("currency"), fx)
        out.append(row)
    return out


def bank_rows(snapshot):
    """The Transactions rows that say money went to the broker."""
    return [r for r in snapshot["transactions"]
            if r["payee"] == PAYEE and not r["synthetic"]]


def deposits(rows, snapshot):
    """Every deposit across every statement, once (`own_deposits`), each
    marked matched or not.

    A row can only pay for one deposit, so a matched row is used up: two
    deposits a day apart for the same amount need two bank rows, not one row
    counted twice. A row and a deposit are the same amount when they are
    within a cent of each other in SGD.
    """
    months = [r["month"] for r in snapshot["transactions"] if r["month"]]
    covered_from = min(months) if months else None
    covered_to = max(months) if months else None
    candidates = [r for r in bank_rows(snapshot) if r["date"]]
    used = set()
    out = []
    for st, own in own_deposits(rows):
        fx = rates(st)
        for d in own:
            amount = cents(d["amount"])
            amount_sgd = to_sgd(amount, d["currency"], fx)
            matched = None
            if covered_from and covered_from <= d["date"][:7] <= covered_to:
                matched = False
                for i, r in enumerate(candidates):
                    if i in used:
                        continue
                    if abs(c.days_between(d["date"], r["date"])) > MATCH_DAYS:
                        continue
                    # the row's own cents, or its dollars in an older snapshot
                    if abs(abs(stat_cents(r, "amount_sgd") or 0) - amount_sgd) > 1:
                        continue
                    used.add(i)
                    matched = True
                    break
            out.append({"date": d["date"], "amount_minor": amount,
                        "currency": d["currency"], "matched": matched})
    return sorted(out, key=lambda d: (d["date"], d["amount_minor"]))


def months_old(day, today):
    """How many whole months lie between a statement's last day and today."""
    if not day or not today:
        return 0
    return len(c.months_between(min(day[:7], today[:7]), max(day[:7], today[:7]))) - 1


def run(snapshot, benchmarks=None):
    block = snapshot.get("portfolio") or {}
    folder = block.get("folder") or "statements/tiger"
    rows, left_out = read(snapshot)
    today = (snapshot.get("meta", {}).get("generated_at") or "")[:10] \
        or c.last_date(snapshot) or ""
    params = {"folder": folder, "payee": PAYEE, "match_days": MATCH_DAYS,
              "stale_months": STALE_MONTHS, "currency": "SGD"}
    sources = [c.source("Tiger Brokers statements", len(rows),
                        f"annual exports in {folder}"),
               c.source("Transactions", len(bank_rows(snapshot)),
                        f"payee = {PAYEE}, to match each deposit to a bank row")]

    if not rows:
        summary = {"value_sgd_minor": 0, "value_base_minor": 0, "base_currency": None,
                   "as_of": None, "contributed_sgd_minor": 0, "gain_sgd_minor": 0,
                   "gain_pct": None, "dividends_base_latest_minor": 0, "statements": 0,
                   "cash_sgd_minor": 0, "cash_as_of": None, "net_worth_sgd_minor": 0,
                   "unmatched_deposits": 0}
        return c.result(sys.modules[__name__], snapshot, sources, params,
                        {"periods": [], "holdings": [], "deposits": [],
                         "summary": summary},
                        [f"No Tiger Brokers statements were found in {folder}, so "
                         f"nothing is counted as investments here. Export the "
                         f"annual statement and save it as "
                         f"{folder}/<year>/<year>-<first month>_<year>-<last month>.pdf."],
                        ["Investments are read from statements on disk, not from "
                         "Notion, so this stat says nothing until an export is "
                         "there to read."])

    period_rows = periods(rows)
    latest, held = period_rows[-1], holdings(rows)
    deposit_rows = deposits(rows, snapshot)
    unmatched = [d for d in deposit_rows if d["matched"] is False]

    cash = (savings_runway.run(snapshot, benchmarks)["data"]["summary"] or {})
    # savings-runway's cash, in whichever shape its result has (G20)
    cash_sgd, cash_as_of = stat_cents(cash, "cash") or 0, cash.get("as_of")
    value_sgd, contributed = latest["value_sgd_minor"], latest["contributed_sgd_minor"]
    gain = value_sgd - contributed
    summary = {"value_sgd_minor": value_sgd, "value_base_minor": latest["value_base_minor"],
               "base_currency": latest["base_currency"],
               "as_of": latest["period_to"], "contributed_sgd_minor": contributed,
               "gain_sgd_minor": gain,
               "gain_pct": round(c.share(gain, contributed) * 100, 1) if contributed else None,
               "dividends_base_latest_minor": latest["dividends_base_minor"],
               "statements": len(rows), "cash_sgd_minor": cash_sgd,
               "cash_as_of": cash_as_of,
               "net_worth_sgd_minor": cash_sgd + value_sgd,
               "unmatched_deposits": len(unmatched)}

    findings = [
        f"Tiger Brokers held {label(value_sgd)} at {c.date_label(summary['as_of'])}, "
        f"against {label(contributed)} paid in over "
        f"{len(deposit_rows)} deposits and {plural(len(rows), 'statement')}, so the gain "
        f"is {label(gain)}, {summary['gain_pct']} per cent of what went in.",
        f"Cash of {label(cash_sgd)} at {c.date_label(cash_as_of)} plus investments of "
        f"{label(value_sgd)} at {c.date_label(summary['as_of'])} is a net worth of "
        f"{label(summary['net_worth_sgd_minor'])}, the two dates being what each side "
        f"was last measured on.",
    ]
    if held:
        top = max(held, key=lambda h: h["value_sgd_minor"])
        findings.append(
            f"The largest position is {top.get('symbol') or top.get('name')} at "
            f"{label(top['value_sgd_minor'])}, "
            f"{round(c.share(top['value_sgd_minor'], value_sgd) * 100)} "
            f"per cent of the portfolio, across {len(held)} holdings.")
    if unmatched:
        named = ", ".join(f"{label(d['amount_minor'], d['currency'])} on {c.date_label(d['date'])}"
                          for d in unmatched[:4])
        one = len(unmatched) == 1
        findings.append(
            f"{len(unmatched)} of the {sum(1 for d in deposit_rows if d['matched'] is not None)} "
            f"deposits the bank statements could cover {'has' if one else 'have'} no "
            f"{PAYEE} row within {MATCH_DAYS} days of {'it' if one else 'them'} for the "
            f"same amount: {named}. Either the money came from an account this record "
            f"does not hold, or the rules gave that row another payee.")
    if left_out:
        named = "; ".join(f"{st['file']}, covered by {by['file']}" for st, by in left_out)
        one = len(left_out) == 1
        findings.append(
            f"{plural(len(left_out), 'statement')} {'is' if one else 'are'} left out "
            f"because another statement covers the whole of {'its' if one else 'each'} "
            f"period: {named}. Every deposit {'it prints' if one else 'they print'} is "
            f"counted from the statement that covers it.")
    age = months_old(summary["as_of"], today)
    if summary["as_of"] and today and summary["as_of"] < today and age > STALE_MONTHS:
        findings.append(
            f"The newest statement stops at {c.date_label(summary['as_of'])}, {age} months before "
            f"this snapshot, so the value above is that old: the statement is "
            f"stale, export the next one.")

    caveats = [
        "The value is the statement's own closing valuation on its last day, not "
        "a live price, so it is as old as the last export.",
        "Every figure is converted at the exchange rate that statement printed "
        "for its own period end, not at today's rate, so a period is compared "
        "with itself rather than restated.",
        "Contributions are the deposits the statements list, and nothing has "
        "been withdrawn; a withdrawal would have to be taken off before the gain "
        "means anything.",
        "Where two statements' periods overlap, a deposit both print is counted "
        "once, with the earlier statement.",
        "Dividends, fees and withholding tax stay in the account's base currency, "
        "US dollars, because that is how the statement reports them.",
        "Deposits made before the bank statements begin, or after the last month "
        "loaded, cannot be matched either way and are neither counted as matched "
        "nor as missing.",
    ]
    return c.result(sys.modules[__name__], snapshot, sources, params,
                    {"periods": period_rows, "holdings": held,
                     "deposits": deposit_rows, "summary": summary},
                    findings, caveats)
