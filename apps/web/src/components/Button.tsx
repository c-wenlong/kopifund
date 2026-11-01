import { forwardRef, type ButtonHTMLAttributes } from "react";

// G18 (U9): the one button look. Three variants: `primary` is the one action a
// form or a card leads with (Save, Apply rules, Add), `secondary` every other
// action (Cancel, Discard, Undo, Add rule), `ghost` a quiet action with no box
// (History, Add category). Three sizes: `md` is 32px, the height of the app's
// fields and Select, so a button sits level with the boxes beside it; `sm` is
// for an action inside a table or list row; `icon` is a 32px square for an icon
// alone (it carries an aria-label). A link that looks like a button
// takes `buttonClass` instead of the component.

export type ButtonVariant = "primary" | "secondary" | "ghost";
export type ButtonSize = "md" | "sm" | "icon";

const BASE =
  "inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md no-underline hover:no-underline disabled:opacity-40 pointer-coarse:min-h-10";
const LOOK: Record<ButtonVariant, string> = {
  primary: "bg-ink text-page",
  secondary: "border border-line bg-panel text-ink hover:bg-hover",
  ghost: "text-ink-2 hover:bg-hover hover:text-ink",
};
const SIZE: Record<ButtonSize, string> = {
  md: "h-8 px-3 text-[13px]",
  sm: "h-6 px-2 text-[12px]",
  icon: "size-8 text-[13px]",
};

/** The classes of a button of this variant and size, for a link that looks like one. `extra` adds layout (a margin, an order). */
export function buttonClass(variant: ButtonVariant = "secondary", size: ButtonSize = "md", extra = "") {
  return `${BASE} ${LOOK[variant]} ${SIZE[size]}${extra ? ` ${extra}` : ""}`;
}

/** The look alone, with no size, for a control that sizes itself (the Pager's page buttons, whose width follows the number). */
export function buttonLook(variant: ButtonVariant = "secondary") {
  return `${BASE} ${LOOK[variant]}`;
}

type Props = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; size?: ButtonSize };

/** A button in the app's look. `className` is for layout only (a margin, an order, a width), never a colour or a size. */
export const Button = forwardRef<HTMLButtonElement, Props>(function Button(
  { variant = "secondary", size = "md", className = "", type = "button", ...rest }, ref,
) {
  return <button ref={ref} type={type} className={buttonClass(variant, size, className)} {...rest} />;
});
