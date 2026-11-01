import {
  ArrowUpDown, Ban, BriefcaseBusiness, Calendar1, CalendarClock, CalendarDays, CalendarPlus, CalendarRange, CalendarSync,
  CalendarCheck2, CalendarFold, CalendarMinus2, CalendarPlus2, CircleCheck, CirclePause, CirclePlus, CircleStop, CircleX,
  ClockAlert, CloudUpload, Coins, Copy, CreditCard, Database, EqualNot, EyeOff, FileCheck, FileClock, FileQuestion,
  FilePlus, FileWarning, Flag, FlaskConical, FolderSearch, Globe, HandCoins, Heart, Hourglass, Landmark, LoaderCircle, MapPinHouse,
  OctagonX, PiggyBank, Plane, PlugZap, ReceiptText, Repeat, SearchCheck, ServerCrash, ShieldAlert, ShoppingBasket, Shuffle,
  SkipForward, SlidersHorizontal, Smartphone, Store, Tag, TicketsPlane, TrendingDown, TrendingUp, TriangleAlert, Unplug,
  User, UserPlus, Zap,
  type LucideIcon,
} from "lucide-react";
import type { Cadence, ComingStatus, EyeKind, GroupKind, NeedClass, PayeeKind, ScamCode, SeriesViewState, StepName, StepStatus } from "./types.gen";

// G18: one icon for every fixed kind of thing the app names, so a glance says
// what a row is. The icon leads and the word stays: an icon never stands in for
// its label. Each map is a Record over its union, so a value added in
// finance/types.py fails the typecheck until it has an icon here. One icon has
// one meaning across every map (icons.test.tsx checks it); the same meaning in
// two maps (income as a kind and as a need class) shares its icon. Buckets get
// their own icons from the record, not from here.

/** What a row counts as: the Transactions filters and a bucket's kind. */
export const KIND_ICON: Record<GroupKind, LucideIcon> = {
  spend: CreditCard,
  income: HandCoins,
  transfer: ArrowUpDown,
};

/** A category's need class. */
export const NEED_ICON: Record<NeedClass, LucideIcon> = {
  Need: ShoppingBasket,
  Want: Heart,
  Savings: PiggyBank,
  Investment: TrendingUp,
  Income: KIND_ICON.income,
  Transfer: KIND_ICON.transfer,
};

/** How often a recurring payment charges. */
export const CADENCE_ICON: Record<Cadence, LucideIcon> = {
  weekly: CalendarDays,
  fortnightly: CalendarFold,
  "twice-monthly": CalendarCheck2,
  monthly: Calendar1,
  "two-monthly": CalendarPlus2,
  quarterly: CalendarRange,
  "half-yearly": CalendarMinus2,
  yearly: CalendarSync,
};

/**
 * Where this month's expected payment stands; `later` is a series with nothing due this month,
 * `paused` (G16) one that has gone quiet for one and a half to three cadences. G16: on a series'
 * own view, `skipped` is an expected payment passed over when a later one came, `stopped` a
 * series that ended, `not_recurring` one the owner said is not.
 */
export const COMING_ICON: Record<ComingStatus | "later" | "skipped" | Exclude<SeriesViewState, "active">, LucideIcon> = {
  paid: ReceiptText,
  due: Hourglass,
  late: ClockAlert,
  different: EqualNot,
  extra: CirclePlus,
  later: CalendarClock,
  paused: CirclePause,
  skipped: SkipForward,
  stopped: CircleStop,
  not_recurring: EyeOff,
};

/** The upload steps: Add (the upload), Check, Load and Mirror. */
export const STEP_ICON: Record<StepName, LucideIcon> = {
  upload: FilePlus,
  check: SearchCheck,
  load: Database,
  mirror: CloudUpload,
};

/** How an upload step ended. */
export const STEP_STATUS_ICON: Record<StepStatus, LucideIcon> = {
  ok: CircleCheck,
  duplicate: FileCheck,
  refused: Ban,
  problems: TriangleAlert,
  blocked: OctagonX,
  failed: CircleX,
  error: ServerCrash,
};

/** A step still running (it spins), and a file waiting for its account. */
export const RUNNING_ICON: LucideIcon = LoaderCircle;
export const NEEDS_ACCOUNT_ICON: LucideIcon = FolderSearch;
/** G21: a statement no layout reads, so Kopifund kept nothing of it. */
export const NOT_SUPPORTED_ICON: LucideIcon = FileQuestion;

/** Why the scam checks flag a row. */
export const SCAM_ICON: Record<ScamCode, LucideIcon> = {
  new_payee: UserPlus,
  repeated: Repeat,
  abroad: Globe,
  test_charge: FlaskConical,
  twice: Copy,
  new_subscription: CalendarPlus,
};

/** A row the scam checks flag, as a whole. */
export const SCAM_FLAG_ICON: LucideIcon = ShieldAlert;

/** What a payee is. */
export const PAYEE_KIND_ICON: Record<PayeeKind, LucideIcon> = {
  Merchant: Store,
  Person: User,
  Employer: BriefcaseBusiness,
  Institution: Landmark,
};

/** A trip, and the two labels a trip card can wear. */
export type TripTag = "long_stay" | "planned";
export const TRIP_ICON: LucideIcon = Plane;
export const TRIP_TAG_ICON: Record<TripTag, LucideIcon> = {
  long_stay: MapPinHouse,
  planned: TicketsPlane,
};

/** The "Needs your eye" lines (Home's card and the sidebar's list), by what each one is about. */
export const EYE_ICON: Record<EyeKind, LucideIcon> = {
  scam: SCAM_FLAG_ICON,
  upload: FileWarning,
  needs_category: Tag,
  recurring_off: COMING_ICON.late,
  missing_fx: Coins,
  // a payment the scam checks flag for a reason that is no scam (a new subscription, say)
  flagged: Flag,
  needs_rule: SlidersHorizontal,
  one_off: Zap,
  cash_trend: TrendingDown,
  stale_accounts: FileClock,
  irregular: Shuffle,
  pending: Smartphone,
};

/** The local server, as the Settings header shows it. */
export type ServerState = "checking" | "connected" | "offline";
export const SERVER_ICON: Record<ServerState, LucideIcon> = {
  checking: RUNNING_ICON,
  connected: PlugZap,
  offline: Unplug,
};

export type IconTone = "current" | "ink" | "ink-2" | "ink-3" | "good" | "warn" | "danger" | "info";

const TONE: Record<IconTone, string> = {
  current: "",
  ink: "text-ink",
  "ink-2": "text-ink-2",
  "ink-3": "text-ink-3",
  good: "text-good",
  warn: "text-warn",
  danger: "text-danger",
  info: "text-info",
};

/**
 * One kind icon. Beside its word (the usual case) it is hidden from screen
 * readers, since the word says it; with no word beside it, `label` gives it a
 * tooltip and an accessible name. The tone is a colour token; `current` takes
 * the text colour around it, so a chip's icon matches its words.
 */
export function KindIcon({ icon: Icon, size = 12, tone = "current", label, spin = false, className = "" }: {
  icon: LucideIcon; size?: number; tone?: IconTone; label?: string; spin?: boolean; className?: string;
}) {
  const cls = ["shrink-0", TONE[tone], spin ? "animate-spin" : "", className].filter(Boolean).join(" ");
  if (label) {
    // the title on a wrapping span is a tooltip in every browser, which an SVG's own title is not
    return (
      <span role="img" aria-label={label} title={label} className={`inline-flex ${cls}`} data-kind-icon="">
        <Icon size={size} aria-hidden focusable={false} />
      </span>
    );
  }
  return <Icon size={size} aria-hidden focusable={false} className={cls} data-kind-icon="" />;
}

/** An icon and its word side by side, for a word that sits in running text (a cadence, a payee's kind). */
export function KindLabel({ icon, children, tone = "current", size = 12, className = "" }: {
  icon: LucideIcon; children: React.ReactNode; tone?: IconTone; size?: number; className?: string;
}) {
  return (
    <span className={`inline-flex items-center gap-1 align-[-1px] ${className}`}>
      <KindIcon icon={icon} size={size} tone={tone} />{children}
    </span>
  );
}
