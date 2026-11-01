"""G17: the shapes the API sends, written once, in Python.

Each TypedDict below is one JSON object an endpoint answers with, key for
key. `python3 -m finance.gen_types` (`make types`) turns this module into
apps/web/src/types.gen.ts, which apps/web/src/api.ts re-exports, so the browser's types
come from the same place as the server's answers. server/tests/test_types.py runs
every endpoint named in ENDPOINTS on a small record and checks its answer
against these at every level: no key missing, none extra, every value of the
type given. A server change that renames or drops a key fails that test, and
a change here fails it until types.gen.ts is regenerated.

Rules for writing one:
  * `Cents` for integer cents, never `int`, so the browser types it `Cents`.
  * An enum is a `type X = Literal[*VALUES]` over the tuple the code already
    checks against, so the list lives in one place.
  * `NotRequired` only where the handler really leaves the key out, and
    `X | None` where it sends null.
  * The docstring becomes the type's comment in types.gen.ts, and a string
    right under a key becomes that key's; one that starts "Deprecated" marks
    the key @deprecated, so the editor strikes out a page that still reads it.
  * G17: a row the app can write to (a trip, a category, a bucket) carries its
    `id`, and the writes name it by that id.
"""
from typing import Any, Literal, NewType, NotRequired, TypedDict

from . import account_names, ai, categories, home, pending, planning, recurring, scam_checks, upload_log
from .rules_app import categorise

Cents = NewType("Cents", int)

# ---------------------------------------------------------------- enums

type Bank = Literal[*account_names.BANKS]
"""The bank whose mark goes beside an account (apps/web/src/components/BankMark.tsx)."""

type GroupKind = Literal[*categories.KINDS]
"""What a bucket's categories are."""

type NeedClass = Literal[*planning.NEED_CLASSES]
"""A category's need class."""

type BucketIcon = Literal[*categories.BUCKET_ICONS]
"""G17: the lucide icons a bucket may have, by their kebab-case names."""

type SeriesColour = Literal[*categories.SERIES_COLOURS]
"""G17: a chart series colour token, `var(--c1)` to `var(--c12)` in index.css."""

type Tier = Literal[*categorise.MATCH_ON]
"""The field a rule matches on, in the order the categoriser tries them."""

type PayeeKind = Literal[*categorise.KINDS]
"""What a payee is."""

type Cadence = Literal[*recurring.BANDS]
"""How often a recurring series charges (the detector's cadence bands)."""

type ComingStatus = Literal[*recurring.STATUSES]
"""Where this month's expected payment stands."""

type SeriesState = Literal[*recurring.SERIES_STATES]
"""G16: whether a live series is charging (active) or has gone quiet for one and a half to three cadences (paused)."""

type SeriesViewState = Literal[*recurring.STATES, "not_recurring"]
"""G16: the list a series is in, or not recurring when the owner said so."""

type SlotStatus = Literal[*recurring.SLOT_STATUSES]
"""G16: where one expected payment of a series' schedule stands; skipped when a later one came."""

type ScamCode = Literal[*scam_checks.REASONS]
"""Which scam check flagged a row."""

type StepName = Literal[*upload_log.STEPS]
"""One step of an upload attempt."""

type StepStatus = Literal[*upload_log.STATUSES]
"""How one upload step ended."""

type Origin = Literal[*upload_log.ORIGINS]
"""The page that started an upload attempt."""

type OffStatus = Literal[*home.OFF]
"""How a bill did not come as usual."""

type EyeKind = Literal[*home.EYE_KINDS]
"""G18: what a line of Needs your eye is about, in the order the lines come."""

type EyeTone = Literal[*home.EYE_TONES]
"""G18: a line's colour: danger for money that may be lost or a load that cannot run."""

type UploadAlertState = Literal[*home.UPLOAD_STATES]
"""G18: how the latest upload stopped short."""


# ---------------------------------------------------------------- transactions

class ScamReason(TypedDict):
    """G15: one reason the scam checks flag a row."""
    code: ScamCode
    text: str


class Txn(TypedDict):
    """One ledger row. `account` is the account's short name, never a full
    number; `raw` is every text the statement printed (the hover);
    `description` says what a row with no payee is ("PayLah! top-up"), else "";
    `category_group` is the category's bucket; `flags` are the scam checks'
    reasons, empty when none fired."""
    key: str
    date: str
    amount: Cents
    amount_sgd: Cents | None
    currency: str
    account: str
    bank: Bank
    merchant: str
    raw: str
    description: str
    payee: str
    category: str
    category_group: str
    flow: str
    trip: str
    flags: list[ScamReason]


class TxnDay(TypedDict):
    """G13: one day on the page with the whole day's money in and out
    (negative) under the current filters, counted over all its rows;
    `continued` is true when the day began on the page before, `continues`
    when it goes on onto the next page."""
    date: str
    count: int
    in_sgd: Cents
    out_sgd: Cents
    continued: bool
    continues: bool


class TxnAccount(TypedDict):
    """One choice of the account filter: `value` is what it sends (the
    account's id), `label` what the list shows."""
    value: str
    label: str
    bank: Bank


class TxnPage(TypedDict):
    """GET /api/transactions: one page of rows and the filters' choices.
    `in_sgd` and `out_sgd` (negative) add up to `sum_sgd`."""
    rows: list[Txn]
    days: list[TxnDay]
    total: int
    sum_sgd: Cents
    in_sgd: Cents
    out_sgd: Cents
    months: list[str]
    categories: list[str]
    accounts: list[TxnAccount]


# ---------------------------------------------------------------- trips

class TripCategory(TypedDict):
    """A category a trip spent the most on."""
    name: str
    spend: Cents


class Trip(TypedDict):
    """G13: one trip and what was spent on it (External, a Spend category,
    not synthetic, with an SGD value; a refund lowers it), the rows the
    travel stat counts. `flags` runs beside `countries`, "" where a code has
    no flag. G17: `id` is what a save or a remove sends."""
    id: int
    name: str
    start: str
    end: str
    days: int | None
    countries: list[str]
    flags: list[str]
    notes: str
    extra_keys: list[str]
    spend_sgd: Cents
    rows: int
    per_day: Cents | None
    long_stay: bool
    top_categories: list[TripCategory]


class TripList(TypedDict):
    """GET /api/trips. `last_txn` is the record's last transaction day ("" for
    an empty record): a trip that starts after it has not happened yet."""
    trips: list[Trip]
    long_stay_days: int
    last_txn: str


# ---------------------------------------------------------------- categories and budgets

class CategoryEntry(TypedDict):
    """One category on the Categories page, with what a change to it
    touches. `cost_type` is the older Fixed / Variable / Savings; the bucket
    is the CategoryGroup it sits in. G17: `id` is what every write sends."""
    id: int
    name: str
    need_class: NeedClass | None
    cost_type: str | None
    group: str | None
    """Deprecated: the same value as `cost_type`, kept for one release. Not the bucket."""
    transactions: int
    monthly: Cents | None
    budgets: int
    rules: int
    personal_rules: int


class BucketStyle(TypedDict):
    """G17: how a bucket is drawn. `icon` is its own, else its kind's; `colour`
    is the series token the owner chose, or null for the colour its place in
    the order gives it. G18: `shown` is the token it is drawn in, the same on
    every page and never another bucket's (categories.shown_colours)."""
    icon: BucketIcon
    colour: SeriesColour | None
    shown: SeriesColour


class CategoryGroup(BucketStyle):
    """A bucket and its categories, in order. G17: `id` is what every write
    sends, and `icon` and `colour` are its style (POST /api/category-groups/style)."""
    id: int
    name: str
    kind: GroupKind
    fixed: bool
    categories: list[CategoryEntry]


class CategoriesState(TypedDict):
    """GET /api/categories: buckets in order, and the need classes each kind allows."""
    groups: list[CategoryGroup]
    need_classes: dict[GroupKind, list[NeedClass]]
    months: int


class Category(TypedDict):
    """A category as the plan lists it (GET /api/plan). `bucket` is the older
    Spend / Income / Transfer, which the stats read; `cost_type` the older
    Fixed / Variable / Savings; `category_group` is its bucket on the
    Categories page."""
    id: int
    name: str
    bucket: str | None
    cost_type: str | None
    group: str | None
    """Deprecated: the same value as `cost_type`, kept for one release. Not the bucket."""
    need_class: NeedClass | None
    sort_order: float | None
    category_group: str | None


class BudgetRow(TypedDict):
    """G8: one Spend or Income category's planned and actual for the month.
    G17: a change to it sends `category_id`, the category's id (a budget is
    one category in one month, so it has no id of its own)."""
    category_id: int
    category: str
    need_class: NeedClass | None
    bucket: str
    category_group: str
    kind: GroupKind
    planned: Cents | None
    actual: Cents


class BudgetGroup(TypedDict):
    """A bucket the budget rows sit in, in order. `id` is null for the rows
    of a record from before buckets, which sit in "(no bucket)"."""
    id: int | None
    name: str
    kind: GroupKind


class Budgets(TypedDict):
    """GET /api/budgets?month=."""
    month: str
    rows: list[BudgetRow]
    groups: list[BudgetGroup]
    months: list[str]
    need_classes: list[NeedClass]


# ---------------------------------------------------------------- rules

class Suggestion(TypedDict):
    """G7: a first guess at a rule from a row's text."""
    match_on: Tier
    pattern: str
    payee: str
    kind: PayeeKind


class NeedsGroup(TypedDict):
    """Rows no rule names, grouped by the text they print, with a suggested rule."""
    text: str
    field: str
    rows: int
    amount: Cents
    first: str
    last: str
    keys: list[str]
    suggestion: Suggestion


class RuleProblem(TypedDict):
    """A lint finding beside the rule it names."""
    check: str
    why: str
    detail: str


class RuleRow(TypedDict):
    """A row a key rule pins."""
    key: str
    date: str
    amount: Cents
    currency: str
    text: str


class Rule(TypedDict):
    """One rule, in the order the categoriser tries them. `kind` is "" for a
    rule that names no payee."""
    id: str
    file: str
    line: int
    revision: str
    shared: bool
    order: int
    tier: Tier
    pattern: str
    payee: str
    kind: PayeeKind | Literal[""]
    country: str
    category: str
    notes: str
    recurring: bool
    matched: int
    wins: int
    problems: list[RuleProblem]
    pins: list[RuleRow]


class RuleGroup(TypedDict):
    """The rules for one payee, or for one category when they name no payee."""
    id: str
    payee: str
    kind: PayeeKind | Literal[""]
    country: str
    category: str
    categories: list[str]
    patterns: int
    pins: int
    rows: int
    problems: int
    shared: int
    rules: list[str]


class RuleChange(TypedDict):
    """A rule write the undo list can take back."""
    id: int
    at: str
    action: str
    summary: str


class RulesState(TypedDict):
    """GET /api/rules. `pending` is the rows the record holds differently from
    what the rules now say; `stale` whether Apply rules has anything to do,
    left out when the rules do not load (`rules_error` says why). `needs` is
    the biggest groups of rows no rule names; G18: `needs_total` is how many
    groups there are in all, the count Needs your eye shows."""
    needs: list[NeedsGroup]
    needs_total: int
    categories: list[str]
    kinds: list[PayeeKind]
    tiers: list[Tier]
    tier_files: dict[str, str]
    rules: list[Rule]
    groups: list[RuleGroup]
    history: list[RuleChange]
    pending: int
    stale: NotRequired[bool]
    rules_error: str | None


class PinnedRule(TypedDict):
    """The personal key rule that sets a row; `applied` once a load has run."""
    payee: str
    category: str
    kind: PayeeKind | Literal[""]
    country: str
    file: str
    line: int
    applied: bool


class GroupRow(TypedDict):
    """One row behind a needs-rule group, which a key rule can set on its own.
    `similar` is the pattern rule "Apply to similar rows" starts from, null
    when the row prints only a bank code. `bank` marks the account (G18)."""
    key: str
    date: str
    amount: Cents
    currency: str
    amount_sgd: Cents | None
    account: str
    bank: Bank
    text: str
    field: str
    payee: str
    category: str
    similar: Suggestion | None
    pinned: PinnedRule | None


class GroupRows(TypedDict):
    """GET /api/rules/rows?text= (or ?key=)."""
    rows: list[GroupRow]
    total: int
    categories: list[str]
    kinds: list[PayeeKind]
    tiers: list[Tier]
    tier_files: dict[str, str]


# ---------------------------------------------------------------- recurring

class RecurringBase(TypedDict):
    """G13: what every row on the Recurring page has. `link` is the
    Transactions query that lists its payments. G16: `id` is the detector's
    series id, which tells a payee's several series apart; `name` is what the
    page shows (the payee, with the usual amount when it has several)."""
    id: str
    name: str
    payee: str
    link: dict[str, str]
    cadence: Cadence | Literal[""]
    category: str
    investing: bool
    amount: Cents


class ComingUpRow(TypedDict):
    """One payment expected this month. `link` is the payment the row's
    figure is (a paid or different-amount row); a due or late row has none.
    G16: an extra row is a charge in the usual range that answers no expected
    payment; its `expected` is the day it came and its link that one charge."""
    id: str
    name: str
    payee: str
    link: dict[str, str] | None
    cadence: Cadence | Literal[""]
    category: str
    investing: bool
    amount: Cents
    expected: str
    estimated: bool
    status: ComingStatus
    paid_on: str | None
    paid_amount: Cents | None
    usual_low: Cents
    usual_high: Cents


class ComingUpTotals(TypedDict):
    committed: Cents
    paid: Cents
    investing: Cents
    investing_paid: Cents
    extra: Cents              # G16: the month's extra charges, kept out of committed and paid
    investing_extra: Cents


class ComingUp(TypedDict):
    """This month's expected recurring payments, the shape Home shows too."""
    month: str
    as_of: str
    rows: list[ComingUpRow]
    totals: ComingUpTotals


class ActiveSeries(RecurringBase):
    usual_low: Cents
    usual_high: Cents
    amount_varies: bool
    occurrences: int
    last_paid: str
    next_expected: str
    late: bool
    state: SeriesState
    yearly: Cents
    flagged: bool


class StoppedSeries(RecurringBase):
    first_seen: str | None
    last_seen: str | None
    ended_on: str | None
    """G16: the day the owner says it ended, when they set one."""
    occurrences: int
    yearly: Cents


class NotRecurring(TypedDict):
    """G16: what the owner said is not recurring, which the page lists so it can be
    taken back: a series (`id`, its view opens it) or a payee's own line (`id` null)."""
    id: str | None
    name: str
    payee: str
    cadence: Cadence | Literal[""]
    category: str
    amount: Cents | None
    last_seen: str | None


class SeriesEdit(TypedDict):
    """G16: what the owner set on a series (server/finance/series.py), each null for as detected.
    `day` is the day of the month, or for weekly and fortnightly the weekday, 1 Monday."""
    recurring: bool | None
    amount: Cents | None
    cadence: Cadence | None
    day: int | None
    ended_on: str | None


class SeriesDetected(TypedDict):
    """G16: what the detector found, under the owner's edit."""
    cadence: Cadence | Literal[""]
    amount: Cents
    day: int | None


class SeriesSlot(TypedDict):
    """G16: one expected payment of a series' schedule, or one of its extra charges;
    `link` is the payment that answered it."""
    expected: str
    status: SlotStatus
    paid_on: str | None
    paid_amount: Cents | None
    link: dict[str, str] | None


class SeriesPayment(TypedDict):
    """G16: one payment of a series: its own (`regular`), or an odd charge it answered with."""
    date: str
    amount: Cents
    regular: bool
    link: dict[str, str] | None


class SeriesView(RecurringBase):
    """G16: GET /api/recurring/series?id=, one series' own view on Recurring. Its figures
    are with the owner's edit read over what was detected; `next_expected` is null for a
    series that expects nothing more."""
    state: SeriesViewState
    late: bool
    usual_low: Cents
    usual_high: Cents
    amount_varies: bool
    day: int | None
    first_seen: str | None
    last_paid: str | None
    next_expected: str | None
    ended_on: str | None
    occurrences: int
    yearly: Cents
    detected: SeriesDetected
    edit: SeriesEdit
    schedule: list[SeriesSlot]
    payments: list[SeriesPayment]


class IrregularPayee(TypedDict):
    """A payee the rules mark recurring that shows no regular spacing."""
    payee: str
    link: dict[str, str]
    payments: int
    first: str | None
    last: str | None
    dates: list[str]
    total: Cents


class RecurringTotals(TypedDict):
    yearly_spending: Cents
    yearly_investing: Cents
    active: int
    paused: int               # G16: listed, in no total
    stopped: int


class RecurringPage(TypedDict):
    """GET /api/recurring."""
    ready: bool
    as_of: str | None
    generated_at: str | None
    coming_up: ComingUp
    active: list[ActiveSeries]
    paused: list[ActiveSeries]
    stopped: list[StoppedSeries]
    irregular: list[IrregularPayee]
    not_recurring: list[NotRecurring]
    totals: RecurringTotals


# ---------------------------------------------------------------- home: needs your eye

class RecurringOff(TypedDict):
    """A bill that did not come as usual: its most urgent payment this month. G16:
    `name` is its series' (the payee, with its usual amount when it has several),
    `id` its series' id, which its line opens on Recurring."""
    id: str
    payee: str
    name: str
    status: OffStatus
    expected: str


class OneOff(TypedDict):
    """The biggest one-off this month; `link` is the Transactions query for that payment."""
    payee: str
    date: str
    category: str
    amount: Cents
    multiple: float | None
    link: dict[str, str]


class ScamLine(TypedDict):
    """G15: how many rows the scam checks flag, how many of them for a reason
    that points at a scam (`scams`), and the one Home names: the newest scam,
    else the newest flag, with the reason it is named for."""
    count: int
    scams: int
    payee: str
    date: str
    amount: Cents
    code: ScamCode
    reason: str


class UploadAlert(TypedDict):
    """G18: an upload that stopped short: `id` is its attempt, `problems` the
    ones that stop a load (0 unless `state` is "problems")."""
    id: int
    state: UploadAlertState
    problems: int


class StaleAccount(TypedDict):
    """G18: an account missing statements, as Your profile marks it; `months`
    is how many, `last` its latest statement or row."""
    name: str
    last: str
    months: int


class EyeLine(TypedDict):
    """G18: one line of Needs your eye, most urgent first; `to` is its link."""
    kind: EyeKind
    tone: EyeTone
    text: str
    to: str


class NeedsYourEye(TypedDict):
    """GET /api/overview's `needs_your_eye`: what each line is built from, and
    the lines, one for each part that is true (server/finance/home.py)."""
    needs_category: int
    recurring_off: list[RecurringOff]
    one_off: OneOff | None
    cash_trend_6m: Cents | None
    irregular: int
    scam: ScamLine | None
    upload: UploadAlert | None
    needs_rule: int
    missing_fx: int
    stale_accounts: list[StaleAccount]
    pending: int
    """G25: phone rows still waiting for a statement to confirm them."""
    lines: list[EyeLine]


# ---------------------------------------------------------------- profile

class Folder(TypedDict):
    """A statement folder the upload can save into; `wallet` is the currency
    of a wallet folder, else "". `title` and `bank` name and mark the account
    kept in it (G21: from the layout registry, server/finance/account_names.py)."""
    path: str
    name: str
    parser: str
    wallet: str
    title: str
    bank: Bank


class ProfileFolder(Folder):
    statements: int


class ProfileAccount(TypedDict):
    """G13: an account by display name, bank and number masked to its last
    four digits ("•• 4821", or ""), never the full number. `folder` is the
    upload picker's key for its statement folder and `pdfs` the PDFs in it,
    both null when no folder is its."""
    name: str
    bank: Bank
    number: str
    statements: int
    transactions: int
    first: str | None
    last: str | None
    folder: str | None
    pdfs: int | None


class About(TypedDict):
    """G10: what no statement prints; `problem` says when profile.json is damaged."""
    birth_date: str | None
    problem: str | None


class Profile(TypedDict):
    """GET /api/profile."""
    data_folder: str
    notion_enabled: bool
    about: About
    accounts: list[ProfileAccount]
    folders: list[ProfileFolder]
    transactions: int


# ---------------------------------------------------------------- scam checks

class ScamSettings(TypedDict):
    """G15: the scam checks' thresholds (server/finance/scam_checks.py, DEFAULTS)."""
    big_transfer: Cents
    repeat_count: int
    repeat_days: int
    new_payee_days: int
    abroad_min: Cents
    test_max: Cents
    test_days: int
    twice_min: Cents
    new_sub_days: int


class ScamFlag(TypedDict):
    """A flagged row; `amount` is the SGD sent, positive."""
    key: str
    date: str
    payee: str
    amount: Cents
    reasons: list[ScamReason]


class ScamFine(TypedDict):
    """A row the owner marked fine."""
    key: str
    date: str
    payee: str
    amount: Cents


class ScamChecks(TypedDict):
    """GET /api/scam-checks."""
    settings: ScamSettings
    flags: list[ScamFlag]
    marked_fine: list[ScamFine]


# ---------------------------------------------------------------- upload history

class UploadStepOutline(TypedDict):
    id: int
    step: StepName
    started_at: str
    finished_at: str
    status: StepStatus
    summary: str


class UploadStep(UploadStepOutline):
    """A step with what went in and what came out."""
    input: dict[str, Any]
    output: dict[str, Any]


class UploadAttempt(TypedDict):
    """An upload attempt without its steps; apps/web/src/api.ts adds them, as
    outlines in the list and in full for one attempt."""
    id: int
    origin: Origin
    started_at: str


class UploadAttemptOutline(UploadAttempt):
    steps: list[UploadStepOutline]


class UploadAttemptDetail(UploadAttempt):
    """GET /api/uploads/<id>."""
    steps: list[UploadStep]


class UploadPage(TypedDict):
    """GET /api/uploads: newest first. `next` is the `before` that fetches
    the page after this one, null at the oldest."""
    attempts: list[UploadAttemptOutline]
    next: int | None
    total: int


# ---------------------------------------------------------------- the plan (G17: money in cents)
#
# The plan's money crosses as integer cents under the field's name plus
# `_minor` (server/finance/planning.py, plan_out and plan_in). Rows with a `from`
# key are written in the functional form, since `from` is a Python keyword.

type IncomeType = Literal[*planning.INCOME_TYPES]
"""An income row's type: a job, or side income."""

type Horizon = Literal[*planning.HORIZONS]
"""How far the Net worth projection runs: 10, 20 or 30 years, or to 65."""

type Dollars = Literal[*planning.DOLLARS]
"""Whether the Net worth projection is in today's dollars or future ones."""

IncomeRow = TypedDict("IncomeRow", {
    "name": str, "type": IncomeType, "from": str, "to": str | None, "gross_minor": Cents, "cpf": bool,
    "bonus_months": str | float | None, "bonus_month": int | None})
IncomeRow.__doc__ = """G14: one income row as the Net worth page edits it, however old the
stored row: `to` null runs to the horizon; `bonus_months` is months of pay
as text ("1.5") and `bonus_month` the month of the year it lands in, both
null for a side income row or a job with no bonus."""

InvestingStep = TypedDict("InvestingStep", {"from": str | None, "amount_minor": Cents})
InvestingStep.__doc__ = """G14: the monthly transfer to investments from a month (YYYY-MM) on;
`from` null is from the start."""


class ProjectionSettings(TypedDict):
    """G14: the Net worth projection's saved settings with the defaults filled
    in. Rates are per cent a year as text ("6.5"); `spend_growth_pct` null
    follows inflation; `spend_minor` null is the record's own average and
    `milestone_minor` null is no milestone."""
    pessimistic_pct: str
    expected_pct: str
    optimistic_pct: str
    inflation_pct: str
    salary_growth_pct: str
    sums_growth_pct: str
    spend_growth_pct: str | None
    horizon: Horizon
    dollars: Dollars
    spend_minor: Cents | None
    milestone_minor: Cents | None


class GrowthInputs(TypedDict):
    """The Net worth page's inputs, as GET /api/growth answers them under
    `inputs` and POST /api/growth/preview and /api/growth/inputs take them."""
    income: list[IncomeRow]
    investing: list[InvestingStep]
    projection: ProjectionSettings


PlanIncomeRow = TypedDict("PlanIncomeRow", {
    "name": NotRequired[str], "type": NotRequired[IncomeType], "from": str, "to": NotRequired[str | None],
    "gross_minor": Cents, "cpf": NotRequired[bool], "note": NotRequired[str],
    "bonus_months": NotRequired[str | float], "bonus_month": NotRequired[int]})
PlanIncomeRow.__doc__ = """An income row as the plan stores it. A segment from before G14 has a
`note` and no name, type or CPF; GET /api/growth's IncomeRow is the same
row with those filled in."""

PlanTrip = TypedDict("PlanTrip", {
    "name": NotRequired[str], "from": str, "to": str, "per_day_minor": NotRequired[Cents],
    "flights_minor": NotRequired[Cents], "flights_month": NotRequired[str], "note": NotRequired[str]})
PlanTrip.__doc__ = """A planned trip the forward view costs: spending a day (left out for
the record's own rate on past trips) and flights in `flights_month`, its
first month unless set."""


class PlanOneOff(TypedDict):
    """A one-off payment in a month."""
    month: str
    amount_minor: Cents
    label: NotRequired[str]


class PlanProjection(TypedDict):
    """The projection settings as the plan stores them: only what was typed.
    GET /api/growth answers them with the defaults filled in."""
    pessimistic_pct: NotRequired[str]
    expected_pct: NotRequired[str]
    optimistic_pct: NotRequired[str]
    inflation_pct: NotRequired[str]
    salary_growth_pct: NotRequired[str]
    sums_growth_pct: NotRequired[str]
    spend_growth_pct: NotRequired[str]
    horizon: NotRequired[Horizon]
    dollars: NotRequired[Dollars]
    spend_minor: NotRequired[Cents]
    milestone_minor: NotRequired[Cents]


class Plan(TypedDict):
    """The plan, every key optional. `to` is the forward view's last month;
    `cpf_employer_rate` is a fraction (0.17), not money; `dca_minor` is the
    single investing transfer from before G14's investing steps. The money
    sits in cents under the `_minor` names; POST /api/plan takes the same
    shape (and, for one release, dollars under the bare names)."""
    to: NotRequired[str]
    dca_minor: NotRequired[Cents | None]
    insurance_minor: NotRequired[Cents | None]
    other_income_minor: NotRequired[Cents | None]
    repayments_minor: NotRequired[Cents | None]
    cpf_employer_rate: NotRequired[float]
    spend_rate: NotRequired[str]
    spend_exclude_keys: NotRequired[list[str]]
    income: NotRequired[list[PlanIncomeRow]]
    investing: NotRequired[list[InvestingStep]]
    trips: NotRequired[list[PlanTrip]]
    one_offs: NotRequired[list[PlanOneOff]]
    projection: NotRequired[PlanProjection]


class PlanState(TypedDict):
    """GET /api/plan: the plan, the categories, the need classes and the
    forward view's spending rates."""
    plan: Plan
    categories: list[Category]
    need_classes: list[NeedClass]
    spend_rates: list[str]


# What each GET answers, for server/tests/test_types.py. A path ending in /<id>
# stands for any id.
# ---------------------------------------------------------------- G22: AI settings and the copilot

type AiProvider = Literal[*ai.PROVIDERS]
"""G22: a provider server/finance/ai.py knows (ai.PROVIDERS)."""

type KeyPlace = Literal["keychain", "env_file", "environment", "key_file", "key_command", "none"]
"""G22: where the provider's API key was found. `keychain` and `env_file` are
the two places Settings saves one; `none` when there is no key."""


class AiProviderInfo(TypedDict):
    """G22: one provider the AI card offers."""
    name: AiProvider
    label: str
    local: bool
    """True for a provider on this machine (Ollama): no key, and nothing leaves the machine."""
    default_model: str
    """The model used when none is picked; "" when the provider has no stable default."""
    copilot_default_model: str
    """The Ask Kopifund model used when none is picked; "" to use the payee suggestions model."""


class AiKey(TypedDict):
    """G22: the provider's API key as the page may see it: never the key."""
    where: KeyPlace
    last4: str | None
    """The key's last four characters, or null when there is no key."""
    can_remove: bool
    """True when Settings saved it (keychain or env_file), so Remove can take it out."""


class AiSettings(TypedDict):
    """GET /api/ai: the AI card on Settings."""
    enabled: bool
    """True when a provider is picked (Settings or [ai] in config.toml) and AI is not turned off in Settings."""
    provider: str | None
    """A name from `providers`, or a custom provider config.toml names (with its base_url); null when none."""
    providers: list[AiProviderInfo]
    model: str
    """The payee suggestions model; "" for the provider's default."""
    copilot_model: str
    """The Ask Kopifund model; "" to use `model`."""
    model_in_use: str
    """G22: the payee suggestions model requests really use (a saved choice, [ai] in config.toml, or the provider's default); "" when there is none."""
    copilot_model_in_use: str
    """G22: the Ask Kopifund model requests really use; "" when there is none, so the copilot cannot run."""
    key: AiKey
    send_rows: bool
    """The copilot consent switch (`copilot.send_rows` in the setting table)."""
    local: bool
    """True when the provider runs on this machine, so the copilot needs no consent."""
    from_config: bool
    """True when the provider comes from [ai] in config.toml rather than Settings."""
    config_provider: str | None
    """G22: the provider [ai] in config.toml names, built-in or custom (with its base_url), even when Settings has picked another or turned AI off; null when config.toml names none. Saving it as `provider` clears Settings' choice so config.toml decides again; a custom one's model is set only in config.toml."""


class AiModel(TypedDict):
    """G22: one model a provider lists."""
    id: str
    label: str


class AiModels(TypedDict):
    """GET /api/ai/models?provider=&q=: the provider's models matching q, from a day-old cache at most."""
    provider: AiProvider
    models: list[AiModel]
    fetched_at: str | None
    """When the server last fetched the provider's list (ISO time), or null when it could not."""
    error: str | None
    """Why the list could not be fetched (no key, the provider refused); the field still takes typing."""


class AiTest(TypedDict):
    """POST /api/ai/test: whether the provider answered one short request."""
    ok: bool
    message: str
    ms: int | None


class CopilotPage(TypedDict):
    """G22: the page a question was asked on, and its filters."""
    path: str
    title: str
    filters: dict[str, str]


class CopilotTool(TypedDict):
    """G22: one tool the copilot ran, as the quiet line the panel shows."""
    name: str
    label: str


class CopilotLink(TypedDict):
    """G22: a link into the app, e.g. Transactions for exactly the rows a figure counted."""
    label: str
    href: str


class CopilotChart(TypedDict):
    """G22: a chart in the stats' chart hint shape (server/analytics/SPEC.md, "The chart hint")."""
    title: str
    chart: dict[str, Any]


type CardKind = Literal["rule", "category", "budget", "trip"]
"""G22: what a confirm card would change."""


class CopilotRequest(TypedDict):
    """G22: a write the page sends on Confirm (or Undo) to an existing endpoint."""
    path: str
    body: dict[str, Any]


class CopilotCard(TypedDict):
    """G22: a change the model suggests; nothing is saved until the owner presses Confirm."""
    id: str
    kind: CardKind
    title: str
    summary: str
    preview: list[str]
    """Lines the card shows under its summary (for a rule, the rows it would change)."""
    request: CopilotRequest
    undo: CopilotRequest | None
    """The write that puts things back; null when the endpoint's own answer carries its Undo, or (a budget) when the page reads the amount to put back at Confirm."""


type CopilotStepKind = Literal["thinking", "tools", "text"]
"""G22: one step of an answer: the model's summarized thinking for a round, the tools one round ran, or the text a round wrote before calling tools."""


class CopilotStep(TypedDict):
    """G22: one step of an answer, shown in order above the final answer, collapsed."""
    kind: CopilotStepKind
    text: str
    """The thinking summary or the interim text; "" for a tools step."""
    tools: list[CopilotTool]
    """The tools one round ran; [] for a thinking or text step."""


class CopilotMessage(TypedDict):
    """G22: one turn of a thread."""
    role: Literal["user", "assistant"]
    text: str
    """For an answer, the final answer; the text written between rounds is in `steps`."""
    at: str
    page: CopilotPage | None
    tools: list[CopilotTool]
    steps: list[CopilotStep]
    """The answer's steps in order ([] for a question). A thread saved before steps existed loads with []; the panel then shows `tools` as one tools step."""
    charts: list[CopilotChart]
    links: list[CopilotLink]
    cards: list[CopilotCard]
    followups: list[str]
    """Short questions the panel offers as chips under the answer."""


class CopilotThreadOutline(TypedDict):
    """G22: a thread in the panel's list."""
    id: str
    title: str
    updated_at: str
    turns: int


class CopilotThreads(TypedDict):
    """GET /api/copilot/threads: newest first."""
    threads: list[CopilotThreadOutline]


class CopilotThread(TypedDict):
    """GET /api/copilot/threads/<id>."""
    id: str
    title: str
    updated_at: str
    messages: list[CopilotMessage]


type CopilotEventType = Literal["thread", "thinking", "tool", "say", "text", "chart", "link", "card", "followups", "error", "done"]
"""G22: one line of POST /api/copilot/ask's answer."""


class CopilotEvent(TypedDict):
    """G22: one line of JSON in POST /api/copilot/ask's answer, sent as it happens."""
    type: CopilotEventType
    thread: NotRequired[CopilotThreadOutline]
    tool: NotRequired[CopilotTool]
    text: NotRequired[str]
    """For `thinking`: a round's thinking summary; for `say`: the text a round wrote before calling tools; for `text`: the final answer."""
    chart: NotRequired[CopilotChart]
    link: NotRequired[CopilotLink]
    card: NotRequired[CopilotCard]
    followups: NotRequired[list[str]]
    message: NotRequired[str]
    """For `error`: what went wrong, in the app's words."""


# ---------------------------------------------------------------- G25: the phone link

class LinkDevice(TypedDict):
    """One paired phone (server/finance/link.py). The bearer never leaves the
    registry: only its hash is kept, and nothing of either is sent."""
    id: str
    name: str
    paired_at: str
    revoked: bool


class LinkDevices(TypedDict):
    """GET /api/link/devices: the paired phones, and the tailnet URL the
    pair QR carries ("" when tailscale cannot say)."""
    devices: list[LinkDevice]
    url: str


class PairToken(TypedDict):
    """POST /api/link/pair-token: the QR payload and what it encodes."""
    payload: str
    url: str
    expires_in: int


class PairedDevice(TypedDict):
    """POST /api/pair/redeem: the credentials the phone keeps."""
    device_id: str
    name: str
    token: str


type PendingState = Literal[*pending.STATES]
"""G25: where a phone row stands: waiting for its statement row, matched to
it, or dismissed as no spend."""


class PendingRow(TypedDict):
    """G25: one spend the phone told the record about (server/finance/pending.py).
    `amount_minor` is signed the ledger's way: spending negative."""
    import_id: str
    device_id: str
    device: str
    """The paired phone's name, "" when its registry row is gone."""
    date: str
    amount_minor: Cents
    currency: str
    payee: str
    state: PendingState
    matched_txn_key: str | None


class LinkPending(TypedDict):
    """GET /api/link/pending: every pending row, waiting first."""
    rows: list[PendingRow]


class LinkPosted(TypedDict):
    """POST /api/link/transactions: how many of the rows sent were new or
    corrected, and how many waiting rows a confirm matched after them."""
    accepted: int
    matched: int


class AppIconSet(TypedDict):
    """One icon set in app-icons/sets.json; its files are under /app-icons/<name>/."""
    name: str
    label: str
    about: str
    "a few words on what it shows"


class AppIconPage(TypedDict):
    """GET and POST /api/app-icon: the icon sets in picker order, the one the
    apps open with, and the one picked in Settings (finance/app_icon.py)."""
    sets: list[AppIconSet]
    default: str
    chosen: str


ENDPOINTS = {
    "/api/plan": PlanState,
    "/api/transactions": TxnPage,
    "/api/trips": TripList,
    "/api/categories": CategoriesState,
    "/api/budgets": Budgets,
    "/api/rules": RulesState,
    "/api/rules/rows": GroupRows,
    "/api/recurring": RecurringPage,
    "/api/recurring/series": SeriesView,
    "/api/profile": Profile,
    "/api/scam-checks": ScamChecks,
    "/api/uploads": UploadPage,
    "/api/uploads/<id>": UploadAttemptDetail,
    "/api/ai": AiSettings,
    "/api/copilot/threads": CopilotThreads,
    "/api/link/devices": LinkDevices,
    "/api/link/pending": LinkPending,
    "/api/app-icon": AppIconPage,
}
