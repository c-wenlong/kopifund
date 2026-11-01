"""The CPF rules the growth projection runs on, in one place, with their date.

Every figure here is a published CPF Board rule as at 1 October 2026:

  * contribution rates for private-sector employees by age band, from
    1 January 2026, as a share of the Ordinary Wage up to the ceiling;
  * the allocation of each contribution between OA, SA (RA from 55) and MA,
    by age band, from 1 January 2026;
  * the employee's share of those rates by age band, from 1 January 2026
    (G14: the take-home on the Net worth page);
  * the Ordinary Wage ceiling of S$8,000 a month (2026), and the annual
    salary ceiling of S$102,000 that caps the Additional Wages (a bonus)
    attracting CPF at S$102,000 less the year's Ordinary Wages subject to CPF;
  * interest: OA 2.5 per cent, SA, MA and RA 4 per cent (the 4 per cent floor
    is confirmed to 31 December 2027), plus extra interest of 1 per cent on
    the first S$60,000 of combined balances below 55, and 2 per cent on the
    first S$30,000 and 1 per cent on the next S$30,000 from 55, with at most
    S$20,000 of it from the OA; the extra interest goes to the SA, or the RA
    from 55;
  * the Basic Healthcare Sum, S$79,000 in 2026: MA above it flows to the SA
    (the RA from 55) until the Full Retirement Sum, then to the OA;
  * the Full Retirement Sum for members turning 55 in 2026, S$220,400. At 55
    the SA closes, and the SA and then the OA move into a new RA up to the
    member's cohort FRS; what is left of the SA goes to the OA.

G14 adds self-employed MediSave on net trade income (side income), from the
IRAS table for net trade income earned in 2025 (Year of Assessment 2026) and
the CPF Board's income limit for 2026 onwards; see `self_employed_medisave`
for the two assumptions it makes.

What is not a rule is an assumption and is named as one where it is used: the
FRS of a later cohort and the BHS of a later year are grown from the 2026
figures at a rate the caller passes (G8). Ages follow CPF's own convention: a
member is "above 55" from the first day of the month after the month of their
55th birthday, and the same for every band.

Money is integer cents throughout: every ceiling, cap and sum above is a cents
constant, and the rates and shares are fractions. Interest accrues monthly on
the month's opening balances and is credited at the end of December, as CPF
credits it once a year. G20: a contribution and its shares round half up to
the cent (`cents`), the rule this module has always used, and so does each
month's interest on each account (the extra interest too), once, as it
accrues: the year's credit is the sum of twelve whole-cent months, never a
fraction carried from month to month. The CPF Board's payroll rounding (the
total to the nearest dollar, the employee's share down to the dollar) is not
modelled.

Source: cpf.gov.sg, "CPF contribution and allocation rates from 1 January
2026", "CPF interest rates" (October to December 2026), "Basic Healthcare Sum"
and "Retirement sums" (2026). Rates as at 1 October 2026.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

AS_OF = "2026-10-01"
SOURCE = ("CPF Board (cpf.gov.sg): contribution and allocation rates from 1 January 2026, "
          "interest rates for October to December 2026, the 2026 Basic Healthcare Sum and "
          "the Full Retirement Sum for members turning 55 in 2026")

OW_CEILING = 800_000                      # cents a month, the Ordinary Wage ceiling (2026)
ANNUAL_CEILING = 10_200_000               # cents a year, the annual salary ceiling (OW plus AW)
SUMS_YEAR = 2026                          # the year the two sums below are for
BHS = 7_900_000                           # cents, Basic Healthcare Sum 2026
FRS = 22_040_000                          # cents, Full Retirement Sum, members turning 55 in 2026

OA_RATE = Decimal("0.025")
SMRA_RATE = Decimal("0.04")               # SA, MA and RA
OA_EXTRA_CAP = 2_000_000                  # cents of OA that count towards extra interest
EXTRA_BELOW_55 = ((6_000_000, Decimal("0.01")),)
EXTRA_FROM_55 = ((3_000_000, Decimal("0.02")), (3_000_000, Decimal("0.01")))

# (last age of the band, total rate): "55" is 55 and below, the next band is
# above 55 to 60, and None is everything above the last threshold.
CONTRIBUTION_RATES = (
    (55, Decimal("0.37")),
    (60, Decimal("0.34")),
    (65, Decimal("0.25")),
    (70, Decimal("0.165")),
    (None, Decimal("0.125")),
)

# G14: the employee's share of the total above, same bands, from 1 January 2026
# (monthly wages above S$750; below that the employee share is phased in,
# which the projection does not model).
EMPLOYEE_RATES = (
    (55, Decimal("0.20")),
    (60, Decimal("0.18")),
    (65, Decimal("0.125")),
    (70, Decimal("0.075")),
    (None, Decimal("0.05")),
)

# (last age of the band, (OA, SA or RA, MA)) as shares of the contribution.
ALLOCATION = (
    (35, (Decimal("0.6217"), Decimal("0.1621"), Decimal("0.2162"))),
    (45, (Decimal("0.5677"), Decimal("0.1891"), Decimal("0.2432"))),
    (50, (Decimal("0.5136"), Decimal("0.2162"), Decimal("0.2702"))),
    (55, (Decimal("0.4055"), Decimal("0.3108"), Decimal("0.2837"))),
    (60, (Decimal("0.353"), Decimal("0.3382"), Decimal("0.3088"))),
    (65, (Decimal("0.14"), Decimal("0.44"), Decimal("0.42"))),
    (70, (Decimal("0.0607"), Decimal("0.303"), Decimal("0.6363"))),
    (None, (Decimal("0.08"), Decimal("0.08"), Decimal("0.84"))),
)


def cents(x):
    """A Decimal amount of cents, rounded half up to a whole cent."""
    return int(Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def birthday_month(birth, years):
    """The month ("YYYY-MM") of the member's birthday at `years` of age."""
    return f"{birth.year + years:04d}-{birth.month:02d}"


def above(month, birth, years):
    """Whether, in `month`, the member is above `years` of age as CPF counts it:
    from the month after the month of that birthday."""
    return month > birthday_month(birth, years)


def _band(table, month, birth):
    if birth is None:
        return table[0][1]               # no date of birth: the youngest band
    for last, value in table:
        if last is None or not above(month, birth, last):
            return value
    raise AssertionError("the last band is open")


def contribution_rate(month, birth):
    return _band(CONTRIBUTION_RATES, month, birth)


def allocation(month, birth):
    return _band(ALLOCATION, month, birth)


def employee_rate(month, birth):
    return _band(EMPLOYEE_RATES, month, birth)


def liable(wage, additional=0):
    """The month's wages that attract CPF, in cents: the Ordinary Wage up to
    the ceiling, plus the Additional Wage the caller has already capped by the
    year's AW ceiling (`aw_ceiling`)."""
    return min(max(wage, 0), OW_CEILING) + max(additional, 0)


def aw_ceiling(ordinary_liable_in_year):
    """How much Additional Wage in a calendar year attracts CPF: the annual
    salary ceiling less the year's Ordinary Wages subject to CPF."""
    return max(0, ANNUAL_CEILING - ordinary_liable_in_year)


def employee_contribution(wage, month, birth, additional=0):
    """The employee's own CPF in cents, taken out of pay, on the same liable
    wages as `contribution`."""
    return cents(liable(wage, additional) * employee_rate(month, birth))


def contribution(wage, month, birth, additional=0):
    """(OA, SA or RA, MA) in cents for one month's Ordinary Wage in cents and,
    G14, the month's Additional Wage already capped by the AW ceiling.

    Only the wage up to the ceiling attracts CPF. The SA (RA) and MA shares are
    rounded and the OA takes the rest, so the three always add up to the total."""
    total = cents(liable(wage, additional) * contribution_rate(month, birth))
    oa_share, mid_share, ma_share = allocation(month, birth)
    mid, ma = cents(total * mid_share), cents(total * ma_share)
    return total - mid - ma, mid, ma


def grown(base, year, growth):
    """A 2026 sum in a later (or earlier) year at `growth` a year: an assumption."""
    return cents(Decimal(base) * (1 + Decimal(str(growth))) ** (year - SUMS_YEAR))


def frs(year, growth):
    """The Full Retirement Sum for a member turning 55 in `year`."""
    return grown(FRS, year, growth)


def bhs(year, birth, growth):
    """The Basic Healthcare Sum in `year`. It is fixed for life in the year the
    member turns 65."""
    return grown(BHS, min(year, birth.year + 65), growth)


def extra_interest(oa, sa, ma, ra, from_55):
    """One month's extra interest, in fractional cents, on opening balances.

    It is earned on the first S$60,000 of combined balances, of which at most
    S$20,000 may be OA: 1 per cent below 55; 2 per cent on the first S$30,000
    and 1 per cent on the next S$30,000 from 55. All of it is credited to the
    SA, or the RA from 55."""
    eligible = min(oa, OA_EXTRA_CAP) + sa + ma + ra
    out, left = Decimal(0), eligible
    for size, rate in (EXTRA_FROM_55 if from_55 else EXTRA_BELOW_55):
        part = max(0, min(left, size))
        out += Decimal(part) * rate / 12
        left -= part
    return out


@dataclass
class Accounts:
    """A member's four CPF accounts in cents, with the interest accrued this
    year and not yet credited, in whole cents (G20)."""
    oa: int
    sa: int
    ma: int
    ra: int = 0
    ra_open: bool = False
    accrued: dict = field(default_factory=lambda: {"oa": 0, "sa": 0, "ma": 0, "ra": 0})

    @property
    def total(self):
        return self.oa + self.sa + self.ma + self.ra

    def worth(self, k):
        """One account with the interest it has accrued this year: what it
        would hold if the year ended now. A chart of this rises month by month
        instead of jumping each December."""
        return getattr(self, k) + self.accrued[k]


def open_ra(acc, cohort_frs):
    """At 55 the SA closes: the SA, then the OA, move into a new RA up to the
    cohort's Full Retirement Sum, and what is left of the SA goes to the OA.
    SA interest accrued this year is credited to the RA at the year end."""
    from_sa = min(acc.sa, cohort_frs)
    from_oa = min(acc.oa, cohort_frs - from_sa)
    acc.ra += from_sa + from_oa
    acc.oa += acc.sa - from_sa - from_oa
    acc.sa = 0
    acc.accrued["ra"] += acc.accrued["sa"]
    acc.accrued["sa"] = 0
    acc.ra_open = True


def _to_retirement(acc, amount, cap):
    """Put `amount` into the SA (the RA from 55) up to `cap`, the rest into the OA."""
    if acc.ra_open:
        room = max(0, cap - acc.ra)
        acc.ra += min(amount, room)
    else:
        room = max(0, cap - acc.sa)
        acc.sa += min(amount, room)
    acc.oa += amount - min(amount, room)


def overflow_ma(acc, bhs_cents, frs_cents):
    """MA above the Basic Healthcare Sum flows to the SA (the RA from 55) until
    the Full Retirement Sum, then to the OA."""
    excess = acc.ma - bhs_cents
    if excess > 0:
        acc.ma = bhs_cents
        _to_retirement(acc, excess, frs_cents)


def accrue(acc):
    """One month's interest on the opening balances, held until December. G20:
    each account's month, and the month's extra interest, rounds half up to the
    cent once (`cents`), so what accrues is whole cents."""
    extra = extra_interest(acc.oa, acc.sa, acc.ma, acc.ra, acc.ra_open)
    acc.accrued["oa"] += cents(Decimal(acc.oa) * OA_RATE / 12)
    acc.accrued["sa"] += cents(Decimal(acc.sa) * SMRA_RATE / 12)
    acc.accrued["ma"] += cents(Decimal(acc.ma) * SMRA_RATE / 12)
    acc.accrued["ra"] += cents(Decimal(acc.ra) * SMRA_RATE / 12)
    acc.accrued["ra" if acc.ra_open else "sa"] += cents(extra)


def credit(acc):
    """The year's interest, credited at the end of December."""
    for k in ("oa", "sa", "ma", "ra"):
        setattr(acc, k, getattr(acc, k) + acc.accrued[k])
        acc.accrued[k] = 0


def step(acc, month, wage, birth, sums_growth, additional=0, medisave=0):
    """One month: the RA opens in the month of the 55th birthday, interest
    accrues on the opening balances, the month's wage is contributed and
    allocated, MA above the BHS flows on, and December credits the year's
    interest. `wage` is the CPF-liable Ordinary Wage in cents, `additional`
    the month's Additional Wage already capped by the AW ceiling, and
    `medisave` a self-employed MediSave contribution, all of it to the MA."""
    year = int(month[:4])
    if not acc.ra_open and month >= birthday_month(birth, 55):
        open_ra(acc, frs(birth.year + 55, sums_growth))
    accrue(acc)
    current_frs, current_bhs = frs(year, sums_growth), bhs(year, birth, sums_growth)
    oa, mid, ma = contribution(wage, month, birth, additional)
    acc.oa += oa
    acc.ma += ma + max(medisave, 0)
    if acc.ra_open:
        _to_retirement(acc, mid, current_frs)
    else:
        acc.sa += mid
    overflow_ma(acc, current_bhs, current_frs)
    if month.endswith("-12"):
        credit(acc)
        overflow_ma(acc, current_bhs, current_frs)
    return acc


def age_on(birth, day):
    """Whole years completed on `day`."""
    return day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))


def parse_birth(text):
    return date.fromisoformat(text) if text else None


# ------------------------------------------------------------ G14: self-employed MediSave
#
# A self-employed person whose net trade income (NTI) for a year is above
# S$6,000 pays MediSave on it, at a rate by age as at 1 January of that year.
# Source: IRAS, "Compulsory and voluntary MediSave contributions", the table
# for NTI earned in 2025 (Year of Assessment 2026), and the CPF Board, "What
# are the conditions for applying to limit my MediSave payable as a
# self-employed person", for the yearly income limit. Two assumptions, named:
#
#   * IRAS prints the band from S$12,000 to S$18,000 as a range (4 to 8 per
#     cent below 35, and so on) without its formula; the rate here rises in a
#     straight line across the band, from the lower rate to the upper.
#   * The maximum is the upper rate on the year's income limit. That is how the
#     2025 maximums work out (8 per cent of S$88,800 is S$7,104, IRAS's figure
#     below 35); 2026 on uses the CPF Board's S$96,000, and the 2025 rates are
#     assumed to hold in later years.

MEDISAVE_SE_AS_OF = "2026-10-01"
MEDISAVE_SE_SOURCE = (
    "https://www.iras.gov.sg/taxes/individual-income-tax/basics-of-individual-income-tax/"
    "tax-reliefs-rebates-and-deductions/tax-reliefs/compulsory-and-voluntary-medisave-contributions",
    "https://www.cpf.gov.sg/service/article/"
    "what-are-the-conditions-for-applying-to-limit-my-medisave-payable-as-a-self-employed-person",
)
MEDISAVE_SE_FLOOR = 600_000               # cents a year: NTI at or below this pays none
MEDISAVE_SE_LOW_TOP = 1_200_000           # the lower rate up to here
MEDISAVE_SE_HIGH_FROM = 1_800_000         # the upper rate above here
# (last age of the band, (lower rate, upper rate)), age as at 1 January
MEDISAVE_SE_RATES = (
    (34, (Decimal("0.04"), Decimal("0.08"))),
    (44, (Decimal("0.045"), Decimal("0.09"))),
    (49, (Decimal("0.05"), Decimal("0.10"))),
    (None, (Decimal("0.0525"), Decimal("0.105"))),
)
MEDISAVE_SE_INCOME_LIMIT = {2022: 7_200_000, 2023: 7_320_000, 2024: 8_160_000,
                            2025: 8_880_000, 2026: 9_600_000}      # cents; 2026 on: S$96,000


def income_limit(year):
    years = sorted(MEDISAVE_SE_INCOME_LIMIT)
    return MEDISAVE_SE_INCOME_LIMIT[min(max(year, years[0]), years[-1])]


def self_employed_medisave(nti, year, birth):
    """The MediSave due on a year's net trade income, in cents. Without a date
    of birth the youngest band is used."""
    if nti <= MEDISAVE_SE_FLOOR:
        return 0
    age = age_on(birth, date(year, 1, 1)) if birth else 0
    low, high = next(r for last, r in MEDISAVE_SE_RATES if last is None or age <= last)
    if nti <= MEDISAVE_SE_LOW_TOP:
        rate = low
    elif nti <= MEDISAVE_SE_HIGH_FROM:
        span = MEDISAVE_SE_HIGH_FROM - MEDISAVE_SE_LOW_TOP
        rate = low + (high - low) * Decimal(nti - MEDISAVE_SE_LOW_TOP) / span
    else:
        rate = high
    return min(cents(Decimal(nti) * rate), cents(Decimal(income_limit(year)) * high))
