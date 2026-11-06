"""A small snapshot with one of every awkward row in it.

Twelve months, two accounts, one wallet in a foreign currency. It carries, on
purpose, every shape that has broken an analysis of the real data:

  * a recurring series that stops (Gym Co, five months then silence);
  * a refund landing inside a spend category (Big Shop, +200 against -500);
  * a synthetic YouTrip balance-jump row;
  * a foreign-currency row on a period block that prints an FX rate, and
    another on a block that does not;
  * a trip, with rows on it;
  * an External row with no category and no merchant text;
  * a FAST Collection row, which is somebody pulling money on a schedule;
  * internal legs and Transfer-bucket rows that must never count as spend.

`SETTINGS` below splits the months into two invented phases, the way a user's
own [analytics] phases split their record, and `settings()` puts it in force.
"""
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from finance.money import to_cents

CATEGORIES = [
    {"name": "Salary", "bucket": "Income", "group": "Fixed",
     "need_class": "Income", "sort_order": 10},
    {"name": "Refunds and cashback", "bucket": "Income", "group": "Variable",
     "need_class": "Income", "sort_order": 70},
    {"name": "Rent", "bucket": "Spend", "group": "Fixed",
     "need_class": "Need", "sort_order": 80},
    {"name": "Groceries", "bucket": "Spend", "group": "Variable",
     "need_class": "Need", "sort_order": 100},
    {"name": "Transport", "bucket": "Spend", "group": "Variable",
     "need_class": "Need", "sort_order": 110},
    {"name": "Eating out", "bucket": "Spend", "group": "Variable",
     "need_class": "Want", "sort_order": 150},
    {"name": "Coffee", "bucket": "Spend", "group": "Variable",
     "need_class": "Want", "sort_order": 160},
    {"name": "Shopping", "bucket": "Spend", "group": "Variable",
     "need_class": "Want", "sort_order": 170},
    {"name": "Travel", "bucket": "Spend", "group": "Variable",
     "need_class": "Want", "sort_order": 190},
    {"name": "Fitness", "bucket": "Spend", "group": "Fixed",
     "need_class": "Want", "sort_order": 200},
    {"name": "Donations", "bucket": "Spend", "group": "Variable",
     "need_class": "Want", "sort_order": 220},
    {"name": "Investments", "bucket": "Transfer", "group": "Savings",
     "need_class": "Investment", "sort_order": 230},
    {"name": "Person to person", "bucket": "Transfer", "group": "Variable",
     "need_class": "Transfer", "sort_order": 260},
]
BY_CATEGORY = {c["name"]: c for c in CATEGORIES}

MONTHS = [f"2025-{m:02d}" for m in range(1, 13)]
WALLET_MONTHS = MONTHS[2:]              # the wallet's statements start in March
NO_SALARY = ("2025-04", "2025-09")
NO_RATE_MONTH = "2025-06"               # the one wallet block with no FX rate
NOK_RATE = 0.13

MAIN, WALLET = "A1", "W1"
MAIN_NAME, WALLET_NAME = "Main Bank · A1", "Travel wallet · W1"
TRIP = "Weekend away"

RECURRING_PAYEES = {"Kind Fund", "Gym Co", "Tiger", "Ghost Sub"}
PAYEE_KIND = {"Acme": "Employer", "Lender": "Person", "Tiger": "Institution"}


def txn(day, amount_sgd, category="", payee="", *, account=MAIN, currency="SGD",
        amount=None, bank_txn_type="Debit Card Transaction", flow="External",
        merchant=None, trip="", synthetic=False, memo="", counterparty="",
        country="SGP", period=None):
    """One transaction row in the snapshot's shape: G20, its amounts in cents only."""
    cat = BY_CATEGORY.get(category, {})
    month = day[:7]
    return {
        "key": f"{day}-{payee or merchant or 'x'}-{amount_sgd}".replace(" ", ""),
        "date": day, "month": month,
        "amount_minor": to_cents(amount if amount is not None else amount_sgd),
        "amount_sgd_minor": to_cents(amount_sgd),
        "currency": currency,
        "account": MAIN_NAME if account == MAIN else WALLET_NAME,
        "account_no": account, "bank_txn_type": bank_txn_type,
        "direction": "Out" if amount_sgd < 0 else "In", "flow_class": flow,
        "merchant_raw": payee if merchant is None else merchant,
        "merchant_country": country, "counterparty": counterparty, "memo": memo,
        "reference": "", "payee": payee, "category": category,
        "need_class": cat.get("need_class"), "bucket": cat.get("bucket"),
        "group": cat.get("group"), "card": "", "trip": trip,
        "synthetic": synthetic,
        "period": period or (f"{account} · {currency} · {month}"),
    }


def transactions():
    rows = []
    for month in MONTHS:
        if month not in NO_SALARY:
            rows.append(txn(f"{month}-25", 3000.0, "Salary", "Acme",
                            bank_txn_type="GIRO Salary"))
        rows.append(txn(f"{month}-02", -800.0, "Rent", "Landlord",
                        bank_txn_type="Funds Transfer"))
        for dd, amount, payee in ((3, -12.0, "Cafe A"), (4, -25.0, "Cafe B"),
                                  (15, -18.0, "Cafe A"), (16, -40.0, "Cafe C")):
            rows.append(txn(f"{month}-{dd:02d}", amount, "Eating out", payee))
        rows.append(txn(f"{month}-10", -60.0, "Groceries", "Market"))
        rows.append(txn(f"{month}-12", -5.0, "Coffee", "Bean"))
        rows.append(txn(f"{month}-20", -18.0, "Donations", "Kind Fund",
                        bank_txn_type="Payments / Collections via GIRO"))
        rows.append(txn(f"{month}-08", -1000.0, "Investments", "Tiger",
                        bank_txn_type="FAST Collection"))
        rows.append(txn(f"{month}-22", -20.0, "Transport", "Bus"))
    # a subscription that stops after five months
    for month in MONTHS[:5]:
        rows.append(txn(f"{month}-05", -100.0, "Fitness", "Gym Co"))
    # a charge and, a month later, its refund, both inside a spend category
    rows.append(txn("2025-07-14", -500.0, "Shopping", "Big Shop"))
    rows.append(txn("2025-08-03", 200.0, "Shopping", "Big Shop",
                    memo="return of the second one"))
    # travel: two ordinary fares and one that dwarfs them
    rows.append(txn("2025-03-09", -80.0, "Travel", "Airline"))
    rows.append(txn("2025-10-09", -90.0, "Travel", "Airline"))
    rows.append(txn("2025-06-01", -1200.0, "Travel", "Airline", trip=TRIP,
                    country="NOR"))
    # the wallet: one row on a block with an FX rate, one on a block without
    rows.append(txn("2025-06-02", -100.0, "Eating out", "Harbour Cafe",
                    account=WALLET, currency="NOK", amount=-769.23, trip=TRIP,
                    country="NOR", bank_txn_type="YouTrip Payment"))
    rows.append(txn("2025-07-02", -13.0, "Eating out", "Old Town Cafe",
                    account=WALLET, currency="NOK", amount=-100.0, country="NOR",
                    bank_txn_type="YouTrip Payment"))
    # a balance jump the export omitted
    rows.append(txn("2025-05-15", -50.0, account=WALLET, currency="NOK",
                    amount=-384.62, bank_txn_type="YouTrip Unprinted",
                    synthetic=True, merchant="", country="",
                    memo="unprinted balance jump 2025-05-10 to 2025-05-15"))
    # an External row no rule can ever reach: the statement printed no text
    rows.append(txn("2025-11-05", -75.0, merchant="", country=""))
    # a bill somebody else pulls
    rows.append(txn("2025-02-18", -45.0, "Shopping", "Telco",
                    bank_txn_type="FAST Collection"))
    # money moved, not money spent
    rows.append(txn("2025-09-10", -300.0, "Person to person", "Lender",
                    bank_txn_type="PayNow"))
    rows.append(txn("2025-04-11", -500.0, flow="Internal - own account",
                    bank_txn_type="Funds Transfer", merchant="own transfer"))
    return sorted(rows, key=lambda r: (r["date"], r["key"]))


def periods():
    out = []
    for i, month in enumerate(MONTHS):
        closing = 100_000 + 10_000 * i          # cents (G20)
        out.append({"name": f"{MAIN} · SGD · {month}", "account": MAIN_NAME,
                    "account_no": MAIN, "period_end": f"{month}-28",
                    "currency": "SGD", "opening_minor": closing - 10_000, "closing_minor": closing,
                    "total_in_minor": 300_000, "total_out_minor": 290_000, "fx_to_sgd": None,
                    "closing_sgd_minor": closing, "reconciled": True,
                    "has_balances": True})
    for month in WALLET_MONTHS:
        rate = None if month == NO_RATE_MONTH else NOK_RATE
        out.append({"name": f"{WALLET} · NOK · {month}", "account": WALLET_NAME,
                    "account_no": WALLET, "period_end": f"{month}-28",
                    "currency": "NOK", "opening_minor": 100_000, "closing_minor": 100_000,
                    "total_in_minor": 0, "total_out_minor": 0, "fx_to_sgd": rate,
                    "closing_sgd_minor": None if rate is None else to_cents(round(1000 * rate, 2)),
                    "reconciled": True, "has_balances": True})
    return sorted(out, key=lambda b: b["name"])


def payees(rows):
    counts = {}
    for r in rows:
        if r["payee"]:
            counts[r["payee"]] = counts.get(r["payee"], 0) + 1
    names = sorted(set(counts) | RECURRING_PAYEES)
    return [{"name": n, "kind": PAYEE_KIND.get(n, "Merchant"), "country": "SGP",
             "default_category": "", "recurring": n in RECURRING_PAYEES,
             "txn_count": counts.get(n, 0), "account_no": ""} for n in names]


def tiger_statements():
    """Three invented Tiger Brokers statements, in the parser's shape.

    They are arithmetically consistent with each other and with the fixture's
    own rows: the holdings and the cash add up to the period's closing total at
    the rate that period prints, each period opens where the one before it
    closed, and two of the 2025 deposits fall on the same day as one of the
    fixture's monthly 1,000.00 transfers to the broker while a third does not.
    The 2023 and 2024 deposits are older than the first month the fixture's
    transactions cover, so no bank row can decide them either way.
    """
    return [
        {"file": "2023/1-12.pdf", "account": "50000001", "base_currency": "USD",
         "period": {"from": "2023-01-01", "to": "2023-12-31"},
         "overview": {"begin": {"cash": 0.0, "stock": 0.0, "dividend_accruals": 0.0,
                                "total": 0.0},
                      "end": {"cash": 80.0, "stock": 700.0, "dividend_accruals": 0.0,
                              "total": 780.0}},
         "cash_report": {"Starting Cash": 0.0, "Deposits": 740.0,
                         "Ending Cash": 80.0},
         "deposits": [{"date": "2023-06-15", "amount": 1000.0, "currency": "SGD"}],
         "withdrawals": [], "deposits_base": 740.0,
         "dividends_base": 0.0, "withholding_tax_base": 0.0,
         "fees_base": -2.0,
         "holdings": [{"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust",
                       "quantity": 1.0, "cost_price": 690.0, "close_price": 700.0,
                       "value": 700.0, "unrealized_pl": 10.0, "currency": "USD"}],
         "holdings_value_base": 700.0,
         "fx_to_base": {"SGD": 0.74, "USD": 1.0}, "fx_date": "2023-12-31"},
        {"file": "2024/1-12.pdf", "account": "50000001", "base_currency": "USD",
         "period": {"from": "2024-01-01", "to": "2024-12-31"},
         "overview": {"begin": {"cash": 80.0, "stock": 700.0, "dividend_accruals": 0.0,
                                "total": 780.0},
                      "end": {"cash": 100.0, "stock": 2475.0, "dividend_accruals": 0.0,
                              "total": 2575.0}},
         "cash_report": {"Starting Cash": 80.0, "Deposits": 1500.0,
                         "Ending Cash": 100.0},
         "deposits": [{"date": "2024-03-10", "amount": 1000.0, "currency": "SGD"},
                      {"date": "2024-09-10", "amount": 1000.0, "currency": "SGD"}],
         "withdrawals": [], "deposits_base": 1500.0,
         "dividends_base": 0.0, "withholding_tax_base": 0.0,
         "fees_base": -4.0,
         "holdings": [{"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust",
                       "quantity": 3.0, "cost_price": 660.0, "close_price": 700.0,
                       "value": 2100.0, "unrealized_pl": 120.0, "currency": "USD"},
                      {"symbol": "C6L.SI", "name": "SIA", "quantity": 100.0,
                       "cost_price": 4.6, "close_price": 5.0, "value": 500.0,
                       "unrealized_pl": 40.0, "currency": "SGD"}],
         "holdings_value_base": 2475.0,
         "fx_to_base": {"SGD": 0.75, "USD": 1.0}, "fx_date": "2024-12-31"},
        {"file": "2025/1-12.pdf", "account": "50000001", "base_currency": "USD",
         "period": {"from": "2025-01-01", "to": "2025-12-31"},
         "overview": {"begin": {"cash": 100.0, "stock": 2475.0,
                                "dividend_accruals": 0.0, "total": 2575.0},
                      "end": {"cash": 240.0, "stock": 4560.0,
                              "dividend_accruals": 0.0, "total": 4800.0}},
         "cash_report": {"Starting Cash": 100.0, "Deposits": 2000.0,
                         "Dividends": 17.6, "Platform Fees": -8.0, "GST": -0.72,
                         "Withholding Tax": -5.4, "Ending Cash": 240.0},
         "deposits": [{"date": "2025-03-08", "amount": 1000.0, "currency": "SGD"},
                      {"date": "2025-04-08", "amount": 1000.0, "currency": "SGD"},
                      {"date": "2025-05-20", "amount": 500.0, "currency": "SGD"}],
         "withdrawals": [], "deposits_base": 2000.0,
         "dividends_base": 17.6, "withholding_tax_base": -5.4, "fees_base": -8.72,
         "holdings": [{"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust",
                       "quantity": 5.0, "cost_price": 670.0, "close_price": 720.0,
                       "value": 3600.0, "unrealized_pl": 250.0, "currency": "USD"},
                      {"symbol": "C6L.SI", "name": "SIA", "quantity": 200.0,
                       "cost_price": 5.35, "close_price": 6.0, "value": 1200.0,
                       "unrealized_pl": 130.0, "currency": "SGD"}],
         "holdings_value_base": 4560.0,
         "fx_to_base": {"SGD": 0.80, "USD": 1.0}, "fx_date": "2025-12-31"},
    ]


def portfolio(statements=None, folder="statements/tiger"):
    """The snapshot's brokerage block around a list of statements."""
    return {"source": "tiger", "folder": folder,
            "statements": list(statements if statements is not None
                               else tiger_statements())}


def cpf_statements():
    """Three invented CPF yearly statements, one per thing that breaks the stat.

    2023 splits the employment figure into the two halves and is a year the
    Government put more in than the wages did; 2024 is a year with no
    employment at all and only Government money, and it prints no split, so
    `employee` and `employer` are null the way the 2016 to 2019 layouts leave
    them; 2025 is a proper working year with an outflow in it. The three chain
    exactly: each opening is the year before's closing, and every closing total
    is the opening plus the inflow less the outflow, to the cent.
    """
    return [
        {"file": "2023/2023.pdf", "account": "T0000000A", "year": 2023,
         "as_of": "2023-12-31",
         "opening": {"oa": 100.00, "sa": 40.00, "ma": 300.00, "total": 440.00},
         "closing": {"oa": 251.40, "sa": 86.25, "ma": 978.20, "total": 1315.85},
         "inflow": {"employee": 152.00, "employer": 128.00, "employment": 280.00,
                    "government": 595.85, "base_interest": 81.20,
                    "extra_interest": 14.65, "interest": 95.85,
                    "government_support": 500.00, "voluntary": 0.0,
                    "total": 875.85},
         "outflow": {"housing": 0.0, "healthcare": 0.0, "retirement": 0.0,
                     "education": 0.0, "investment": 0.0, "others": 0.0,
                     "total": 0.0},
         "transactions": [], "refs": {}},
        {"file": "2024/2024.pdf", "account": "", "year": 2024,
         "as_of": "2024-12-31",
         "opening": {"oa": 251.40, "sa": 86.25, "ma": 978.20, "total": 1315.85},
         "closing": {"oa": 262.10, "sa": 94.75, "ma": 1448.72, "total": 1805.57},
         "inflow": {"employee": None, "employer": None, "employment": 0.0,
                    "government": 489.72, "base_interest": 158.30,
                    "extra_interest": 31.42, "interest": 189.72,
                    "government_support": 300.00, "voluntary": 0.0,
                    "total": 489.72},
         "outflow": {"housing": 0.0, "healthcare": 0.0, "retirement": 0.0,
                     "education": 0.0, "investment": 0.0, "others": 0.0,
                     "total": 0.0},
         "transactions": [], "refs": {}},
        {"file": "2025/2025.pdf", "account": "T0000000A", "year": 2025,
         "as_of": "2025-12-31",
         "opening": {"oa": 262.10, "sa": 94.75, "ma": 1448.72, "total": 1805.57},
         "closing": {"oa": 1661.47, "sa": 486.20, "ma": 2024.66, "total": 4172.33},
         "inflow": {"employee": 1210.00, "employer": 1035.00, "employment": 2245.00,
                    "government": 141.76, "base_interest": 112.40,
                    "extra_interest": 29.36, "interest": 141.76,
                    "government_support": 0.0, "voluntary": 0.0,
                    "total": 2386.76},
         "outflow": {"housing": 0.0, "healthcare": 0.0, "retirement": 0.0,
                     "education": 0.0, "investment": 0.0, "others": 20.00,
                     "total": 20.00},
         "transactions": [{"date": "2025-09-15", "code": "DPS", "for_month": None,
                           "ref": "", "oa": -20.0, "sa": 0.0, "ma": 0.0}],
         "refs": {}},
    ]


def cpf(statements=None, folder="statements/cpf"):
    """The snapshot's CPF block around a list of statements."""
    return {"source": "cpf", "folder": folder,
            "statements": list(statements if statements is not None
                               else cpf_statements())}


def snapshot(source="notion", portfolio=None, cpf=None):
    """The whole fixture, in the shape server/finance/snapshot.py produces.

    `portfolio` and `cpf` are left out by default, so the default fixture is a
    snapshot from before the brokerage and CPF statements were loaded and every
    stat has to cope with the keys being absent. Pass one of the blocks above
    to put it in.
    """
    rows = transactions()
    out = {
        "meta": {"generated_at": "2026-01-01T00:00:00+08:00", "source": source,
                 "file": "snapshot-2026-01-01.json",
                 "databases": [{"name": "Transactions", "data_source_id": "test",
                                "rows": len(rows), "filter": "all"}],
                 "fx_note": "test fixture: the wallet rate is one number a month",
                 "notes": []},
        "transactions": rows,
        "periods": periods(),
        "payees": payees(rows),
        "categories": CATEGORIES,
        "accounts": [
            {"name": MAIN_NAME, "account_no": MAIN, "institution": "Test Bank",
             "class": "Asset", "subclass": "Cash", "base_currency": "SGD",
             "statements_from": "2025-01-28", "statements_to": "2025-12-28",
             "data_coverage": "Full", "txn_count": len(rows)},
            {"name": WALLET_NAME, "account_no": WALLET, "institution": "Test Wallet",
             "class": "Asset", "subclass": "Wallet", "base_currency": "SGD",
             "statements_from": "2025-03-28", "statements_to": "2025-12-28",
             "data_coverage": "Full", "txn_count": 3},
        ],
        "cards": [{"name": "Test Debit · 1234", "last4": "1234", "network": "Visa",
                   "type": "Debit", "state": "Active"}],
        "budgets": [{"category": "Eating out", "month": "2025-01", "planned_minor": 30000,
                     "actual_minor": 9500}],
        "trips": [{"name": TRIP, "start": "2025-06-01", "end": "2025-06-03",
                   "countries": ["NOR"]}],
    }
    if portfolio is not None:
        out["meta"]["portfolio_files"] = [st["file"] for st in portfolio["statements"]]
        out["portfolio"] = portfolio
    if cpf is not None:
        out["meta"]["cpf_files"] = [st["file"] for st in cpf["statements"]]
        out["cpf"] = cpf
    return out


BENCHMARKS = {"generated_at": "2026-01-01", "items": [
    {"id": "hes-2023-monthly-expenditure", "value": 4322,
     "unit": "SGD per household per month",
     "population": "households headed by 25 to 34 year olds", "as_of": "2023",
     "source": "SingStat Household Expenditure Survey 2023",
     "url": "https://www.singstat.gov.sg/", "notes": "per household"},
    {"id": "mom-2024-fresh-graduate-median-gross-salary", "value": 4313,
     "unit": "SGD per month", "population": "fresh graduates", "as_of": "2024",
     "source": "MOM Graduate Employment Survey", "url": "https://www.mom.gov.sg/",
     "notes": ""},
    {"id": "moneysense-emergency-fund-months", "value": 6,
     "unit": "months of expenses",
     "population": "working adults", "as_of": "2024", "source": "MoneySense",
     "url": "https://www.moneysense.gov.sg/", "notes": "three to six months"},
    {"id": "unmatchable-reference", "value": 1, "unit": "widgets",
     "population": "nobody", "as_of": "2024", "source": "Nowhere",
     "url": "https://example.invalid/", "notes": ""},
]}


# The [analytics] settings the analytics suite runs under (G10): two invented
# phases splitting the fixture's year, and the fixture's own lender as the
# repayment payee. Nothing here is anybody's real life.
SETTINGS = """
[analytics]
repayment_payee = "Lender"
repayment_category = "Person to person"
phases = [
  { name = "Before the move", from = "2025-01", to = "2025-05" },
  { name = "After the move", from = "2025-06" },
]
"""

# A plan for the forward view, as run() parameters: pay with a one-month gap
# in it, an investing transfer and an end month. Invented figures.
PLAN = {
    "to": "2027-03",
    "dca": True,
    "dca_amount_minor": 25_000,
    "income_segments": [
        {"from": "2026-01-01", "to": "2026-08-31", "gross_minor": 260_000, "cpf": True},
        {"from": "2026-10-01", "to": "2027-03-31", "gross_minor": 520_000, "cpf": True},
    ],
}


@contextmanager
def settings(text=SETTINGS):
    """Run the stats under `text` as config.toml, then put things back.

    An empty string is a config with no [analytics] table at all, which is
    what a new user starts with."""
    from finance import settings as finance_settings
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.toml"
        path.write_text(text, encoding="utf-8")
        before = finance_settings.SETTINGS_FILE
        finance_settings.SETTINGS_FILE = path
        try:
            yield path
        finally:
            finance_settings.SETTINGS_FILE = before


def is_iso_day(value):
    try:
        date.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False
