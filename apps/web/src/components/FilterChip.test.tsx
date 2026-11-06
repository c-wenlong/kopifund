import { fireEvent, render, screen } from "@testing-library/react";
import { ShieldAlert } from "lucide-react";
import { describe, expect, it, vi } from "vitest";
import { FilterChip } from "./FilterChip";

describe("FilterChip", () => {
  it("says the filter and clears it", () => {
    const onClear = vi.fn();
    render(<FilterChip onClear={onClear}>Payee: Kopitiam</FilterChip>);
    expect(screen.getByText("Payee: Kopitiam")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(onClear).toHaveBeenCalledOnce();
  });

  it("leads a warn filter with its kind's icon, the words beside it", () => {
    const { container } = render(<FilterChip tone="warn" icon={ShieldAlert} onClear={() => {}}>Flagged as a possible scam</FilterChip>);
    const chip = screen.getByText("Flagged as a possible scam");
    expect(chip).toHaveClass("text-warn");
    expect(container.querySelector("svg")).not.toBeNull();
  });
});
