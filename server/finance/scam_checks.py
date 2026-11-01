"""G15: payments that look like a scam, each with the reason it was flagged.

Kopifund sees a row only once its statement is loaded, so this cannot stop a
payment; it points at one sooner than reading the ledger would. Most scams in
Singapore are the victim sending money by PayNow or FAST, so the first checks
look at money sent out of an account that was not a card payment:

  * new payee, big transfer: at least `big_transfer` to a payee first paid
    within the last `new_payee_days`;
  * repeated transfers: `repeat_count` or more transfers to such a new payee
    within `repeat_days` that add up to at least `big_transfer`.

The rest look at card and shop payments too:

  * abroad, no trip: at least `abroad_min` at a new merchant no rule names,
    in another country, on a row no trip covers (the pipeline links a foreign
    charge inside a trip's dates to that trip);
  * test charge: a charge of at most `test_max` at a new merchant no rule
    names, then a larger one there within `test_days`, both flagged;
  * charged twice: two payments of at least `twice_min` to the same payee, of
    the same amount, on the same day;
  * new subscription: a payee the rules mark recurring, or one charged the
    same amount (within a tenth) about a month apart, first paid within
    `new_sub_days` of the record's last day. The owner's edits on Recurring
    are read over both, as Recurring reads them (server/finance/recurring.py, G16):
    a payee or series marked not recurring is no subscription, nor are a
    series' payments on or before the day the owner says it ended.

These are plain rules, not a model: there are no labelled scam rows to learn
from, and a flag has to say why it fired. A payee is "new" only where the
record goes back far enough to tell, so the record's first weeks flag nothing.
A person a people rule names (a counterparty, memo or reference rule) is
known and never flagged, and so is any payee with a row the user marked fine.

The thresholds are saved in the record's `setting` table and edited on the
Settings page; nothing here is one person's figure. Money is integer cents.
"""
import json
import re
from collections import defaultdict
from datetime import date

from . import recurring, series
from .db import now, transaction
from .row_text import NUMBER, VALUE_DATE, mask_numbers

# what a new record starts with; the user changes them on the Settings page
DEFAULTS = {"big_transfer": 50000, "repeat_count": 3, "repeat_days": 14, "new_payee_days": 30,
            "abroad_min": 2000, "test_max": 200, "test_days": 7, "twice_min": 2000, "new_sub_days": 60}
# (lowest, highest) each setting may take; money in cents
LIMITS = {"big_transfer": (100, 100_000_000), "repeat_count": (2, 50),
          "repeat_days": (1, 365), "new_payee_days": (1, 365),
          "abroad_min": (0, 100_000_000), "test_max": (1, 10_000), "test_days": (1, 60),
          "twice_min": (0, 100_000_000), "new_sub_days": (1, 365)}
# a subscription's charges come about a month apart, at about the same amount:
# within a tenth, as whole cents (ten times the difference at most the charge)
MONTH_GAP = (25, 35)
SAME_AMOUNT = 10
PREFIX = "scam_checks."
# G17: the checks a reason can come from, as each reason's `code` names it (server/finance/types.py types it from here)
REASONS = ("new_payee", "repeated", "abroad", "test_charge", "twice", "new_subscription")
# the reasons that point at a scam, which Home words as one in the danger colour: money sent to a
# new payee (one big transfer or a run of them) and a new merchant abroad on no trip. A test charge,
# a double charge and a new subscription want a look but are most often an ordinary payment
SCAMS = ("new_payee", "repeated", "abroad")
# the rule tiers people.csv uses: a payee they name is someone the user knows
PEOPLE_TIERS = ("counterparty:", "memo:", "reference:")

# every payment out (not money moving between the user's own accounts and
# wallets, which flow_class marks): a payee's history, whether they are known
# and whether they are trusted come from all of them
PAID = """SELECT v.txn_key, v.txn_date, v.amount_sgd_minor, v.payee_id, v.payee_name,
                 py.kind AS payee_kind, v.rule, v.counterparty, v.memo, v.merchant_raw,
                 v.bank_txn_type, v.category_id, v.bucket, v.need_class, v.card_id, v.card_last4,
                 v.merchant_country, v.trip_id, COALESCE(py.recurring, 0) AS payee_recurring
          FROM v_txn v LEFT JOIN payee py ON py.id = v.payee_id
          WHERE v.flow_class = 'External' AND v.is_synthetic = 0 AND v.amount_sgd_minor < 0
          ORDER BY v.txn_date, v.txn_key"""


# the bank types of a purchase at a shop. Wise and YouTrip print no card
# number on a card purchase, and a PayLah! QR payment is paid to a merchant
PURCHASE_TYPES = frozenset({"Debit Card Transaction", "Point-of-Sale Transaction", "Wise Card Payment",
                            "YouTrip Payment", "Revolut Payment", "PayLah Payment"})


def purchase(r):
    """A card or shop payment: a card number, or a purchase type that prints none."""
    return r["card_id"] is not None or bool(r["card_last4"]) or r["bank_txn_type"] in PURCHASE_TYPES


def candidate(r):
    """A transfer the new-payee and repeated checks may flag: money sent out of
    an account, not a card or shop payment, not investing, and uncategorised,
    a transfer or to a person."""
    return (not purchase(r) and (r["need_class"] or "") != "Investment"
            and (r["category_id"] is None or r["bucket"] == "Transfer" or r["payee_kind"] == "Person"))


def abroad(r):
    """A charge at a merchant in another country (alpha-3, as the record keeps it)."""
    return (r["merchant_country"] or "SGP") != "SGP"


def known(r):
    """A person a people rule names: someone the user knows."""
    return r["payee_kind"] == "Person" and (r["rule"] or "").startswith(PEOPLE_TIERS)


class SettingsRefused(ValueError):
    """A threshold out of range or not a whole number."""


def settings(conn):
    """The thresholds: what the record saved, the default for the rest."""
    saved = {r["key"].removeprefix(PREFIX): json.loads(r["value"]) for r in conn.execute(
        "SELECT key, value FROM setting WHERE key LIKE ?", (PREFIX + "%",))}
    return {k: saved[k] if isinstance(saved.get(k), int) else v for k, v in DEFAULTS.items()}


def save_settings(conn, data):
    """Save any of the thresholds; the others stay as they are."""
    unknown = set(data) - set(DEFAULTS)
    if unknown:
        raise SettingsRefused(f"Not a scam check setting: {', '.join(sorted(unknown))}")
    for k, v in data.items():
        low, high = LIMITS[k]
        if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
            raise SettingsRefused(f"{k} is a whole number from {low} to {high}")
    with transaction(conn):
        for k, v in data.items():
            conn.execute("INSERT INTO setting(key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (PREFIX + k, json.dumps(v)))
    return settings(conn)


def marked_fine(conn):
    """The txn_keys the user marked fine, followed through any re-key."""
    keys = {r[0] for r in conn.execute("SELECT txn_key FROM txn_marked_fine")}
    keys |= {r[0] for r in conn.execute("""SELECT t.txn_key FROM txn_marked_fine f
                                           JOIN txn_key_alias a ON a.old_key = f.txn_key
                                           JOIN txn t ON t.id = a.txn_id""")}
    return keys


def mark_fine(conn, key, fine=True):
    """Mark a row fine (or take that back). Refused for a key the record lacks."""
    if not isinstance(key, str) or not conn.execute(
            "SELECT 1 FROM txn WHERE txn_key = ? UNION SELECT 1 FROM txn_key_alias WHERE old_key = ?",
            (key, key)).fetchone():
        raise SettingsRefused("No transaction has that key")
    with transaction(conn):
        if fine:
            conn.execute("INSERT OR IGNORE INTO txn_marked_fine(txn_key, marked_at) VALUES (?, ?)", (key, now()))
        else:
            # the key the page lists is the row's current one; the mark may sit under an old one
            conn.execute("""DELETE FROM txn_marked_fine WHERE txn_key = ? OR txn_key IN
                            (SELECT a.old_key FROM txn_key_alias a JOIN txn t ON t.id = a.txn_id
                             WHERE t.txn_key = ?)""", (key, key))


def _plain(text):
    return " ".join(re.sub(r"[|]", " ", VALUE_DATE.sub(" ", text or "")).split()).casefold()


def who(r):
    """Who the money went to, as one comparable string: the payee a rule
    named, else the counterparty as printed (its numbers are who it is: a
    PayLah! transfer names only a phone number), else the memo or bank line
    with its numbers taken out (there they are references that differ from one
    transfer to the next). The value-date stamp is always taken out. "" when
    the row names no one."""
    if r["payee_id"] is not None:
        return "payee:" + r["payee_name"]
    # a shop payment is the shop its line names; a Wise card payment's memo carries the amount
    if purchase(r) and (text := _plain(NUMBER.sub(" ", r["merchant_raw"] or ""))):
        return text
    if text := _plain(r["counterparty"]):
        return text
    for field in ("memo", "merchant_raw"):
        if text := _plain(NUMBER.sub(" ", r[field] or "")):
            return text
    return ""


def _name(r):
    """The payee as the page shows it, with any account-like number masked."""
    return mask_numbers(r["payee_name"] or r["counterparty"] or r["memo"] or r["merchant_raw"]
                        or r["bank_txn_type"])


def _money(cents):
    text = f"S${cents / 100:,.2f}"
    return text[:-3] if text.endswith(".00") else text


def _date(d):
    """"3 Oct 2026", the app's day format."""
    d = date.fromisoformat(d)
    return f"{d.day} {d:%b %Y}"


def _day(d):
    return (date.fromisoformat(d) - date(1970, 1, 1)).days


def flags(conn, cfg=None, results=None):
    """Every flagged row, newest first: {key, date, payee, amount, reasons}.
    `amount` is the SGD cents sent (positive); each reason is {code, text}.
    `results` are the stat results, whose recurring series the owner's edits
    are read over (G16); without them only a payee's own Not recurring is."""
    cfg = cfg or settings(conn)
    first = conn.execute("SELECT MIN(txn_date) FROM v_txn").fetchone()[0]
    if not first:
        return []
    rows = [dict(r) for r in conn.execute(PAID)]
    fine = marked_fine(conn)
    # G16: a payee marked not recurring has no running recurring charge, whatever the rules say
    off = series.payees_off_in(conn)
    out_keys = recurring.edited_out(conn, results) | {r["txn_key"] for r in rows if r["payee_name"] in off}
    by_who = defaultdict(list)
    for r in rows:
        w = who(r)
        if w:
            by_who[w].append(r)
    # a payee is trusted once any row of theirs is marked fine, whatever that
    # row has become since (a category of its own, say)
    trusted = {who(r) for r in rows if r["txn_key"] in fine}

    new_days, repeat_days = cfg["new_payee_days"], cfg["repeat_days"]
    big, count = cfg["big_transfer"], cfg["repeat_count"]
    last = _day(conn.execute("SELECT MAX(txn_date) FROM v_txn").fetchone()[0])
    out = {}

    def flag(r, code, text):
        reasons = out.setdefault(r["txn_key"], {"key": r["txn_key"], "date": r["txn_date"], "payee": _name(r),
                                                "amount": -r["amount_sgd_minor"], "reasons": []})["reasons"]
        if all(x["code"] != code for x in reasons):
            reasons.append({"code": code, "text": text})

    for w, group in by_who.items():
        if w in trusted or any(known(r) for r in group):
            continue
        twice(group, cfg, flag)
        subscription(group, cfg, _day(first), last, flag, out_keys)
        start = _day(group[0]["txn_date"])
        if start - _day(first) < new_days:
            continue                    # the record does not go back far enough to call them new
        fresh = [r for r in group if _day(r["txn_date"]) - start <= new_days]
        overseas(fresh, start, cfg, flag)
        test_charge(group, cfg, flag)
        new = [r for r in fresh if candidate(r)]
        for r in new:
            sent = -r["amount_sgd_minor"]
            if sent >= big:
                ago = _day(r["txn_date"]) - start
                since = "the first payment to them" if ago == 0 else f"first paid {ago} days before"
                flag(r, "new_payee", f"{_money(sent)} to a new payee, {since}")
        # every window of `repeat_days` holding `count` or more of them, adding up to `big`
        shown = set()
        for i, r in enumerate(new):
            end = _day(r["txn_date"]) + repeat_days
            window = [x for x in new[i:] if _day(x["txn_date"]) < end]
            total = -sum(x["amount_sgd_minor"] for x in window)
            if len(window) < count or total < big:
                continue
            amounts = [-x["amount_sgd_minor"] for x in window]
            rising = all(a < b for a, b in zip(amounts, amounts[1:]))
            span = _day(window[-1]["txn_date"]) - _day(window[0]["txn_date"]) + 1
            text = (f"{len(window)} transfers to a new payee in {span} "
                    f"{'day' if span == 1 else 'days'}, {_money(total)} in all"
                    + (", each larger than the last" if rising else ""))
            for x in window:
                if x["txn_key"] not in shown:
                    shown.add(x["txn_key"])
                    flag(x, "repeated", text)
    return sorted(out.values(), key=lambda f: (f["date"], f["key"]), reverse=True)


def twice(group, cfg, flag):
    """Two payments of at least `twice_min` to one payee, same amount, same day."""
    seen = defaultdict(list)
    for r in group:
        if -r["amount_sgd_minor"] >= cfg["twice_min"]:
            seen[(r["txn_date"], r["amount_sgd_minor"])].append(r)
    for (_, amount), rows in seen.items():
        if len(rows) > 1:
            for r in rows:
                times = "twice" if len(rows) == 2 else f"{len(rows)} times"
                flag(r, "twice", f"Charged {_money(-amount)} {times} the same day")


def subscription(group, cfg, first, last, flag, out=frozenset()):
    """A payee charging every month (the rules say so, or two charges of about
    the same amount about a month apart) whose first charge is recent, where
    the record (from day `first`) goes back far enough to call it new. `out`
    are the keys the owner's edits say are no running recurring charge."""
    # G16: what is left once the owner's Not recurring and ended on are read, dated from its own
    # first charge, so a series that ended and charges again after its end is a new one
    mine = [r for r in group if r["txn_key"] not in out]
    if not mine:
        return
    start = _day(mine[0]["txn_date"])
    # a shop's charge, not money to a person (a new landlord is not a subscription)
    if start - first < cfg["new_payee_days"] or last - start > cfg["new_sub_days"] or candidate(mine[0]):
        return
    monthly = any(r["payee_recurring"] for r in mine) or any(
        MONTH_GAP[0] <= _day(b["txn_date"]) - _day(a["txn_date"]) <= MONTH_GAP[1]
        and SAME_AMOUNT * abs(a["amount_sgd_minor"] - b["amount_sgd_minor"]) <= -a["amount_sgd_minor"]
        for i, a in enumerate(mine) for b in mine[i + 1:])
    if monthly:
        r = mine[0]
        flag(r, "new_subscription", f"A new monthly payment, first charged {_money(-r['amount_sgd_minor'])}")


def overseas(fresh, start, cfg, flag):
    """A charge at a new merchant no rule names, in another country, on a day
    no trip covers. A merchant a rule names is one the user knows."""
    for r in fresh:
        if abroad(r) and r["trip_id"] is None and r["payee_id"] is None and -r["amount_sgd_minor"] >= cfg["abroad_min"]:
            ago = _day(r["txn_date"]) - start
            flag(r, "abroad", f"{_money(-r['amount_sgd_minor'])} at a new merchant in {r['merchant_country']} "
                              f"with no trip then" + ("" if ago == 0 else f", first paid {ago} days before"))


def test_charge(group, cfg, flag):
    """A charge of at most `test_max` on a new merchant's first day, then a
    larger one within `test_days`: how a stolen card is often tried before it
    is used. Every small charge of that first day is a possible test, since the
    record keeps no time of day to say which came first."""
    day0 = group[0]["txn_date"]
    # a merchant a rule names is one the user knows, however small its first charge
    tests = [r for r in group if r["txn_date"] == day0 and r["payee_id"] is None
             and -r["amount_sgd_minor"] <= cfg["test_max"]]
    if not tests:
        return
    r0 = min(tests, key=lambda r: -r["amount_sgd_minor"])
    small = -r0["amount_sgd_minor"]
    later = [r for r in group if r is not r0 and _day(r["txn_date"]) - _day(day0) <= cfg["test_days"]
             and -r["amount_sgd_minor"] > small]
    if later:
        flag(r0, "test_charge", f"A {_money(small)} first charge, then {_money(-later[0]['amount_sgd_minor'])} "
                                f"within {cfg['test_days']} days")
        for r in later:
            flag(r, "test_charge", f"After a {_money(small)} first charge on {_date(r0['txn_date'])}")


def page(conn, results=None):
    """GET /api/scam-checks: the thresholds, the flagged rows, and the rows the
    user marked fine (newest first), so a click by mistake can be taken back."""
    fine = [{"key": r["txn_key"], "date": r["txn_date"], "payee": _name(r), "amount": -(r["amount_sgd_minor"] or 0)}
            for r in conn.execute("""SELECT v.* FROM txn_marked_fine f
                                     JOIN txn_key_alias a ON a.old_key = f.txn_key
                                     JOIN v_txn v ON v.id = a.txn_id
                                     UNION SELECT v.* FROM txn_marked_fine f JOIN v_txn v ON v.txn_key = f.txn_key
                                     ORDER BY txn_date DESC, txn_key""")]
    return {"settings": settings(conn), "flags": flags(conn, results=results), "marked_fine": fine}

