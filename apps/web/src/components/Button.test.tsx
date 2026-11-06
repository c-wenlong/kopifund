import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { vi } from "vitest";
import { Button, buttonClass, buttonLook, IconButton } from "./Button";

// G18 (U9): the one button look
describe("Button", () => {
  it("is a plain button (never a form submit by accident) in the secondary look by default", () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Cancel</Button>);
    const b = screen.getByRole("button", { name: "Cancel" });
    expect(b).toHaveAttribute("type", "button");
    expect(b.className).toBe(buttonClass("secondary", "md"));
    fireEvent.click(b);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("takes a variant, a size, a submit type, a ref and layout classes", () => {
    const ref = createRef<HTMLButtonElement>();
    render(<Button ref={ref} variant="primary" size="sm" type="submit" form="f" className="ml-auto">Save</Button>);
    const b = screen.getByRole("button", { name: "Save" });
    expect(ref.current).toBe(b);
    expect(b).toHaveAttribute("type", "submit");
    expect(b).toHaveAttribute("form", "f");
    expect(b.className).toBe(buttonClass("primary", "sm", "ml-auto"));
  });

  it("draws every variant from the colour tokens and every size from the type scale", () => {
    for (const v of ["primary", "secondary", "ghost"] as const) {
      for (const s of ["md", "sm", "icon"] as const) {
        const cls = buttonClass(v, s);
        expect(cls.startsWith(buttonLook(v))).toBe(true);
        // no raw colour: only the app's token utilities
        expect(cls).not.toMatch(/#|rgb|\b(?:bg|text|border)-(?:black|white|gray|slate|zinc|neutral|blue|red)\b/);
        expect(cls).toMatch(/text-\[1[1-4]px\]/);
      }
    }
  });
});

describe("IconButton", () => {
  it("is a button named by its label, never a submit, and stays off while disabled", () => {
    const onClick = vi.fn();
    render(<form><IconButton label="Delete Kopitiam" onClick={onClick} disabled><svg /></IconButton></form>);
    const b = screen.getByRole("button", { name: "Delete Kopitiam" });
    expect(b).toHaveAttribute("type", "button");
    fireEvent.click(b);
    expect(onClick).not.toHaveBeenCalled();
  });
});
