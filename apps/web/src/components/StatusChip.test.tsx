import { render, screen } from "@testing-library/react";
import { StatusChip } from "./StatusChip";

describe("StatusChip", () => {
  it("sets its counts in tabular figures, as a plain pill and as a button", () => {
    render(<><StatusChip>4 to apply</StatusChip><StatusChip onClick={() => {}}>3 problems</StatusChip></>);
    expect(screen.getByText("4 to apply")).toHaveClass("num");
    expect(screen.getByRole("button", { name: "3 problems" })).toHaveClass("num");
  });
});
