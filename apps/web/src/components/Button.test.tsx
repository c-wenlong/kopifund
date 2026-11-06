import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { Button, buttonClass, buttonLook } from "./Button";

// G18 (U9): the one button look
describe("Button", () => {
  it("is a plain button: it clicks, and never submits a form by accident", () => {
    const onClick = vi.fn();
    const onSubmit = vi.fn((e: React.FormEvent) => e.preventDefault());
    render(<form onSubmit={onSubmit}><Button onClick={onClick}>Cancel</Button></form>);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClick).toHaveBeenCalledOnce();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("does nothing when disabled", () => {
    const onClick = vi.fn();
    render(<Button disabled onClick={onClick}>Save</Button>);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onClick).not.toHaveBeenCalled();
  });

  it("takes a ref, a submit type that sends the form it names, and layout classes", () => {
    const ref = createRef<HTMLButtonElement>();
    const onSubmit = vi.fn((e: React.FormEvent) => e.preventDefault());
    render(<><form id="f" onSubmit={onSubmit} /><Button ref={ref} variant="primary" size="sm" type="submit" form="f" className="ml-auto">Save</Button></>);
    const b = screen.getByRole("button", { name: "Save" });
    expect(ref.current).toBe(b);
    expect(b).toHaveClass("ml-auto");
    fireEvent.click(b);
    expect(onSubmit).toHaveBeenCalledOnce();
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
