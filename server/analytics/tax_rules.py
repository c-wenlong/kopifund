"""Singapore resident income tax, the rules the Net worth projection estimates
take-home with (G14), in one place, with their date.

Every figure here is a published IRAS rule as at 1 October 2026:

  * the resident tax rates from Year of Assessment 2024 onwards: nothing on the
    first S$20,000 of chargeable income, then 2, 3.5, 7, 11.5, 15, 18, 19,
    19.5, 20, 22, 23 and 24 per cent on each band above it;
  * earned income relief: S$1,000 below 55, S$6,000 from 55 to 59 and S$8,000
    from 60, by age on 31 December of the year the income is earned, never
    more than the earned income itself;
  * CPF relief: the employee's compulsory CPF, and a self-employed person's
    compulsory MediSave, come off assessable income;
  * a cap of S$80,000 on all reliefs together in a Year of Assessment.

The tax on a year's income is assessed the year after (the Year of
Assessment). Rebates a Budget announces for one year are not modelled, and
neither is any relief but the three above, so the estimate leans high for
anyone who claims others.

Money is integer cents. Sources: IRAS (iras.gov.sg), "Individual income tax
rates", "Earned income relief", "CPF relief for employees" and "Compulsory and
voluntary MediSave contributions".
"""
from decimal import ROUND_HALF_UP, Decimal

AS_OF = "2026-10-01"
SOURCE = ("https://www.iras.gov.sg/taxes/individual-income-tax/basics-of-individual-income-tax/"
          "tax-residency-and-tax-rates/individual-income-tax-rates")
RELIEF_SOURCE = ("https://www.iras.gov.sg/taxes/individual-income-tax/basics-of-individual-income-tax/"
                 "tax-reliefs-rebates-and-deductions/tax-reliefs/earned-income-relief")

# (top of the band in cents, rate on the band); None is everything above
BRACKETS = (
    (2_000_000, Decimal("0")),
    (3_000_000, Decimal("0.02")),
    (4_000_000, Decimal("0.035")),
    (8_000_000, Decimal("0.07")),
    (12_000_000, Decimal("0.115")),
    (16_000_000, Decimal("0.15")),
    (20_000_000, Decimal("0.18")),
    (24_000_000, Decimal("0.19")),
    (28_000_000, Decimal("0.195")),
    (32_000_000, Decimal("0.20")),
    (50_000_000, Decimal("0.22")),
    (100_000_000, Decimal("0.23")),
    (None, Decimal("0.24")),
)

# (last age of the band, relief in cents), age on 31 December of the income year
EARNED_INCOME_RELIEF = ((54, 100_000), (59, 600_000), (None, 800_000))
RELIEF_CAP = 8_000_000


def cents(x):
    return int(Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def tax(chargeable):
    """The tax on a year's chargeable income, both in cents."""
    out, below = Decimal(0), 0
    for top, rate in BRACKETS:
        if chargeable <= below:
            break
        part = (chargeable if top is None else min(chargeable, top)) - below
        out += Decimal(part) * rate
        if top is None:
            break
        below = top
    return cents(out)


def earned_income_relief(age, earned):
    """The relief on `earned` cents of earned income at `age` (None: below 55)."""
    age = 0 if age is None else age
    full = next(v for last, v in EARNED_INCOME_RELIEF if last is None or age <= last)
    return min(full, max(earned, 0))


def year(earned, cpf_relief, age):
    """{assessable, reliefs, chargeable, tax} for a year's earned income (pay,
    bonuses and side income together) and its CPF relief, all cents."""
    reliefs = min(RELIEF_CAP, earned_income_relief(age, earned) + max(cpf_relief, 0))
    chargeable = max(0, earned - reliefs)
    return {"assessable": earned, "reliefs": reliefs, "chargeable": chargeable, "tax": tax(chargeable)}
