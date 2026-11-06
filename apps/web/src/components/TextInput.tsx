import { forwardRef, type InputHTMLAttributes } from "react";

// Audit: the one text box: the height, border, panel and padding every form's
// text box drew by hand. `invalid` draws the border in the danger colour and
// marks the box invalid; `className` adds the box's width and text size.
// (A money or rate box is MoneyInput; a search box keeps its own wider padding.)
export const TEXT_BOX = "h-8 rounded-md border bg-panel px-2";

export const TextInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }>(
  function TextInput({ invalid = false, className = "", ...rest }, ref) {
    return (
      <input ref={ref} aria-invalid={invalid || undefined} {...rest}
        className={`${TEXT_BOX} ${invalid ? "border-danger" : "border-line"} ${className}`} />
    );
  },
);
