"""A transaction as Transactions reads it when no payee names it (G13).

A row with no payee gets a short plain description in place of the bank line
it prints (`describe`): a top-up, a currency exchange, interest, a transfer
between the owner's accounts, or the line itself with its account numbers
masked and its value-date stamp left out. The line as printed stays on hover
(`raw_text`). Accounts are named and masked by server/finance/account_names.py, as
everywhere else in the app.
"""
import re

from .account_names import digits, mask

# something that reads as an account or card number: six or more digits,
# maybe with dashes and a letter or two in front (Y-1234567890, 123-456789-0).
# A bank line is masked with it, not only by the numbers the record knows: a
# transfer names other people's accounts too.
NUMBER = re.compile(r"(?<![\w@.])(?:[A-Za-z]{1,2}-?)?\d[\d-]{4,}\d(?![\d.])")
MIN_DIGITS = 6
# the value-date stamp DBS adds to a transfer's memo
VALUE_DATE = re.compile(r"VALUE DATE\s*:?\s*\d{1,2}/\d{1,2}/\d{2,4}", re.IGNORECASE)
CURRENCY = re.compile(r"\b[A-Z]{3}\b")


def numbers_in(text):
    """The account-like numbers in a piece of text."""
    return [m.group(0) for m in NUMBER.finditer(text or "") if len(digits(m.group(0))) >= MIN_DIGITS]


def mask_numbers(text):
    """`text` with every account-like number cut to its last four digits."""
    return NUMBER.sub(lambda m: mask(m.group(0)) if len(digits(m.group(0))) >= MIN_DIGITS else m.group(0),
                      text or "")


# the wallet each internal flow moves money into
WALLETS = {"Internal - PayLah": "PayLah!", "Internal - YouTrip": "YouTrip",
           "Internal - Revolut": "Revolut", "Internal - Wise": "Wise"}

# a bank's own name for a line, in plain words; (out, in) where the direction matters
TYPES = {
    "Interest Earned": "Interest",
    "Cashback": "Cashback",
    "Debit Card Transaction": "Card payment",
    "Point-of-Sale Transaction": "Card payment",
    "Payments / Collections via GIRO": ("GIRO payment", "GIRO receipt"),
    "FAST Payment / Receipt": ("FAST transfer out", "FAST transfer in"),
    "FAST Collection": "FAST collection",
    "PayNow": ("PayNow transfer out", "PayNow transfer in"),
    "Funds Transfer": ("Transfer out", "Transfer in"),
    "Inward Telegraphic Transfer": "Overseas transfer in",
    "Outward Telegraphic Transfer": "Overseas transfer out",
    "PayLah Send Money": "PayLah! transfer out",
    "PayLah Receive Money": "PayLah! transfer in",
    "Wise Fee": "Wise fee",
    "Advice": "Bank adjustment",
}

# words a bank line keeps in their own case once it is in sentence case
KEEP = {"fast": "FAST", "giro": "GIRO", "paynow": "PayNow", "paylah!": "PayLah!", "paylah": "PayLah!",
        "qr": "QR", "atm": "ATM", "nets": "NETS", "dbs": "DBS", "posb": "POSB", "ocbc": "OCBC",
        "youtrip": "YouTrip", "cpf": "CPF", "sgd": "SGD", "usd": "USD", "eur": "EUR"}


def _sentence(text):
    """An all-capitals bank line in sentence case, its acronyms and brands
    kept; anything else as it is."""
    if text.upper() != text or not re.search(r"[A-Z]", text):
        return text
    out = " ".join(KEEP.get(w, w) for w in text.lower().split(" "))
    return out[0].upper() + out[1:]


def _clean(text):
    """A bank line without its value-date stamp and separators, its account
    numbers masked; "" when nothing readable is left."""
    text = VALUE_DATE.sub(" ", text or "")
    text = re.sub(r"\s*\|\s*", " · ", text)
    text = mask_numbers(text)
    text = re.sub(r":IB\b", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ·:-|,")
    # a line that is only a masked number or punctuation reads as nothing
    if not re.sub(r"••\s?\d{1,4}|[\s·:\-|,@.\d]", "", text):
        return ""
    return _sentence(text)


def _type_text(t, amount):
    plain = TYPES.get(t)
    if isinstance(plain, tuple):
        return plain[0] if amount < 0 else plain[1]
    return plain


def describe(row, own=None):
    """A short plain description of a row with no payee, or "" to show its
    own text. `row` has bank_txn_type, flow_class, merchant, counterparty,
    memo, reference, category, currency, amount (minor units), account_no
    and synthetic; `own` is {account_no: display name} for the owner's
    accounts."""
    own = own or {}
    t = row.get("bank_txn_type") or ""
    flow = row.get("flow_class") or ""
    amount = row.get("amount") or 0
    memo, counterparty = row.get("memo") or "", row.get("counterparty") or ""
    if row.get("merchant"):
        return ""          # a merchant's own name reads well; a rule can name it
    if row.get("synthetic"):
        return "Balance gap, not printed on the statement"
    if "Exchange" in t or "Conversion" in t:
        other = next((c for c in CURRENCY.findall(memo) if c != row.get("currency")), "")
        return f"Exchange {'from' if amount > 0 else 'to'} {other}" if other else "Currency exchange"
    wallet = WALLETS.get(flow)
    if wallet:
        if t.endswith("Withdrawal"):
            return f"{wallet} withdrawal"
        if t.endswith("Top-up") or amount < 0:
            return f"{wallet} top-up"
        return f"Transfer from {wallet}"
    mine = digits(row.get("account_no"))
    others = [n for n in numbers_in(f"{counterparty} {memo}") if digits(n) != mine]
    # the reference can name one of the owner's accounts too; anything else
    # in it is a transaction reference, not an account, so only an exact
    # match with an account of theirs counts
    printed = others + [n for n in numbers_in(row.get("reference") or "") if digits(n) != mine]
    named = next((own[k] for n in printed for k in own if digits(k) and digits(k) == digits(n)), "")
    if flow == "Internal - own account" or named:
        if named:
            return f"Transfer {'from' if amount > 0 else 'to'} {named}"
        return "Transfer between your accounts"
    if t == "Interest Earned" or row.get("category") == "Interest":
        return "Interest"
    if t == "Cashback":
        return "Cashback"
    text = _clean(counterparty) or _clean(memo)
    if text:
        return text
    if others:
        return f"Transfer {'from' if amount > 0 else 'to'} account {mask(others[0])}"
    return _type_text(t, amount) or row.get("category") or t


def raw_text(row):
    """Every text the statement printed for a row, for the hover."""
    parts = [row.get(k) or "" for k in ("merchant", "counterparty", "memo", "reference")]
    return " · ".join(p for p in parts if p) or row.get("bank_txn_type") or ""
