import { render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it } from "vitest";
import { TextInput } from "./TextInput";

describe("TextInput", () => {
  it("is a plain text box that passes its props on and takes a ref", () => {
    const ref = createRef<HTMLInputElement>();
    render(<TextInput ref={ref} aria-label="Payee" placeholder="None" defaultValue="Kopi" className="w-40" />);
    const box = screen.getByRole("textbox", { name: "Payee" });
    expect(box).toHaveValue("Kopi");
    expect(box).toHaveAttribute("placeholder", "None");
    expect(ref.current).toBe(box);
    expect(box).not.toHaveAttribute("aria-invalid");
  });

  it("marks itself invalid when told to", () => {
    render(<TextInput aria-label="Name" invalid />);
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveAttribute("aria-invalid", "true");
  });
});
