import {
  ArrowUpDown, Baby, BookOpen, Briefcase, Bus, Car, Coffee, Dog, Dumbbell, Gamepad2, Gift, GraduationCap, HeartPulse, HousePlug,
  Music, PiggyBank, Plane, Shapes, Shirt, ShoppingBag, Smartphone, Sprout, Utensils, Wallet, type LucideIcon,
} from "lucide-react";
import type { BucketIcon, BucketStyle, SeriesColour } from "../types.gen";
import { seriesColour } from "./chartKit";

// G18: how a bucket looks, its icon and its colour, read in one place. The
// record holds both (category_group.icon and .colour, set on the Categories
// page) and the server works out `shown`, the colour each bucket is drawn in,
// once for every bucket (finance/categories.py, shown_colours), so a bucket is
// the same colour on Categories, Spending and Home and never another's. Every
// page that names a bucket with a colour or an icon goes through `bucketLook`.

/** The icons a bucket may have, in the order the picker shows them (BUCKET_ICONS in finance/categories.py). */
export const BUCKET_ICON: Record<BucketIcon, LucideIcon> = {
  utensils: Utensils,
  "house-plug": HousePlug,
  bus: Bus,
  "heart-pulse": HeartPulse,
  sprout: Sprout,
  "shopping-bag": ShoppingBag,
  plane: Plane,
  gift: Gift,
  shapes: Shapes,
  wallet: Wallet,
  "arrow-up-down": ArrowUpDown,
  car: Car,
  baby: Baby,
  dog: Dog,
  "graduation-cap": GraduationCap,
  music: Music,
  "gamepad-2": Gamepad2,
  shirt: Shirt,
  coffee: Coffee,
  dumbbell: Dumbbell,
  "book-open": BookOpen,
  "piggy-bank": PiggyBank,
  briefcase: Briefcase,
  smartphone: Smartphone,
};
export const BUCKET_ICONS = Object.keys(BUCKET_ICON) as BucketIcon[];

/** The colours a bucket may have: the chart series tokens, --c1 to --c12 in index.css. */
export const BUCKET_COLOURS: readonly SeriesColour[] = ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9", "c10", "c11", "c12"];
export const tokenColour = (token: SeriesColour) => `var(--${token})`;

/** A bucket with no icon of its own and none from the server (an answer from before G17). */
export const DEFAULT_BUCKET_ICON: LucideIcon = Shapes;

export type BucketLook = { icon: LucideIcon; colour: string };

/**
 * A bucket's icon and colour, from its style as /api/categories (each bucket) and /api/spending
 * (`bucket_style`) send it. The colour is `shown`, the one the server drew it in; an answer kept from
 * before G18 has none, and then the colour the owner chose, else the series colour of `place`.
 */
export function bucketLook(style: Partial<BucketStyle> | undefined, place: number): BucketLook {
  const token = style?.shown ?? style?.colour;
  return {
    icon: (style?.icon && BUCKET_ICON[style.icon]) || DEFAULT_BUCKET_ICON,
    colour: token ? tokenColour(token) : seriesColour(Math.max(0, place)),
  };
}

/** A bucket's icon in its colour, beside its name (the name says it, so the icon is hidden from screen readers). */
export function BucketMark({ look, size = 14 }: { look: BucketLook; size?: number }) {
  const Icon = look.icon;
  return <Icon size={size} aria-hidden focusable={false} className="shrink-0" style={{ color: look.colour }} data-bucket-icon="" />;
}
